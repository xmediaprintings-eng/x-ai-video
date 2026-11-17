"""Job store + background pipeline: analyze -> AI plan -> render."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, media, planner, render

EXEC = ThreadPoolExecutor(max_workers=1)  # one render at a time keeps the PC responsive
_lock = threading.Lock()

MSG = {
    "queued": "ລໍຖ້າຄິວ...",
    "analyzing": "AI ກຳລັງເບິ່ງຄລິບ...",
    "transcribing": "ກຳລັງຖອດສຽງເວົ້າ...",
    "planning": "AI ກຳລັງວາງແຜນການຕັດຕໍ່...",
    "rendering": "ກຳລັງສ້າງວິດີໂອ...",
    "done": "ສຳເລັດແລ້ວ!",
    "error": "ເກີດຂໍ້ຜິດພາດ",
}


def job_dir(job_id: str) -> Path:
    if not job_id.replace("-", "").isalnum():
        raise ValueError("bad job id")
    return config.JOBS / job_id


def load(job_id: str) -> dict:
    p = job_dir(job_id) / "job.json"
    if not p.exists():
        raise KeyError(job_id)
    return json.loads(p.read_text(encoding="utf-8"))


def save(job: dict) -> None:
    with _lock:
        p = job_dir(job["id"]) / "job.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)


def update(job_id: str, **kw) -> dict:
    job = load(job_id)
    job.update(kw)
    job["updated"] = time.time()
    save(job)
    return job


def list_jobs(limit: int = 20) -> list[dict]:
    out = []
    for p in sorted(config.JOBS.glob("*/job.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            last = (j.get("versions") or [{}])[-1]
            out.append({"id": j["id"], "created": j.get("created"), "status": j.get("status"),
                        "instruction": j.get("instruction", "")[:80], "poster": last.get("poster"),
                        "versions": len(j.get("versions") or [])})
        except Exception:
            continue
    return out


def music_files(job_id: str) -> list[str]:
    names = [p.name for p in (job_dir(job_id) / "music").glob("*") if p.suffix.lower() in media.AUDIO_EXT]
    names += [p.name for p in config.MUSIC_DIR.glob("*") if p.suffix.lower() in media.AUDIO_EXT]
    return sorted(set(names))


def new_job_dir() -> tuple[str, Path]:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = job_dir(jid)
    for sub in ("uploads", "frames", "music", "out"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return jid, d


def register(jid: str, uploads: list[dict], opts: dict, instruction: str) -> dict:
    job = {
        "id": jid, "created": time.time(), "updated": time.time(), "status": "queued", "message": MSG["queued"],
        "progress": 0.0, "instruction": instruction, "opts": opts, "uploads": uploads, "clips": [],
        "versions": [], "history": [{"role": "user", "text": instruction}], "warnings": [], "error": None,
    }
    save(job)
    EXEC.submit(_run_safe, jid, _pipeline, jid)
    return job


def revise(jid: str, instruction: str | None = None, plan: dict | None = None) -> dict:
    job = load(jid)
    if job["status"] not in ("done", "error") or not job.get("clips"):
        raise RuntimeError("job is busy or not analyzed yet")
    hist = job.get("history", [])
    hist.append({"role": "user", "text": instruction or "(JSON edit)"})
    update(jid, status="queued", message=MSG["queued"], progress=0.0, error=None, history=hist)
    EXEC.submit(_run_safe, jid, _revise, jid, instruction, plan)
    return load(jid)


# ------------------------------------------------------------------ pipeline steps
def _run_safe(jid: str, fn, *args) -> None:
    try:
        fn(*args)
    except Exception as e:  # report error to UI
        traceback.print_exc()
        update(jid, status="error", message=MSG["error"], error=f"{type(e).__name__}: {e}")


def _progress(jid: str, stage: str, base: float, span: float):
    def cb(p: float, _m: str = "") -> None:
        update(jid, status=stage, message=MSG.get(stage, stage), progress=round(base + span * p, 3))
    return cb


def _analyze(jid: str) -> list[dict]:
    job = load(jid)
    d = job_dir(jid)
    opts = job["opts"]
    settings = config.load_settings()
    ups = job["uploads"]
    clips: list[dict] = []
    budget = max(2, 28 // max(1, len(ups)))
    for i, up in enumerate(ups):
        update(jid, status="analyzing", message=f"{MSG['analyzing']} ({i + 1}/{len(ups)})",
               progress=round(0.02 + 0.2 * i / len(ups), 3))
        path = d / "uploads" / up["file"]
        kind = media.kind_of(path)
        if kind == "audio":
            continue
        cid = f"c{len(clips) + 1}"
        if kind == "image":
            norm = d / "uploads" / f"{path.stem}_norm.jpg"
            media.normalize_image(path, norm)
            info = media.probe(norm)
            clip = {"id": cid, "name": up["name"], "kind": "image", "path": str(norm), "duration": 0.0,
                    "width": info["width"], "height": info["height"], "has_audio": False}
            fr = d / "frames" / f"{cid}_photo.jpg"
            media.extract_frame(norm, 0, fr)
            clip["frames"] = [{"file": fr.name, "t": None}]
        else:
            info = media.probe(path)
            if not info["has_video"] or info["duration"] <= 0.2:
                job.setdefault("warnings", []).append(f"skip {up['name']}: no video")
                continue
            clip = {"id": cid, "name": up["name"], "kind": "video", "path": str(path),
                    "duration": round(info["duration"], 3), "width": info["width"], "height": info["height"],
                    "fps": info["fps"], "has_audio": info["has_audio"], "transfer": info.get("transfer", "")}
            clip["scenes"] = media.detect_scenes(path, info["duration"])
            clip["frames"] = []
            for t in media.sample_times(clip["scenes"], budget):
                fr = d / "frames" / f"{cid}_{int(t * 100):06d}.jpg"
                try:
                    media.extract_frame(path, t, fr)
                    clip["frames"].append({"file": fr.name, "t": t})
                except Exception:
                    pass
            if opts.get("captions") and info["has_audio"] and media.whisper_available():
                update(jid, status="transcribing", message=f"{MSG['transcribing']} ({up['name']})")
                lang = opts.get("language") or None
                clip["transcript"] = media.transcribe(path, d, None if lang == "auto" else lang,
                                                      settings.get("whisper_model", "small"))
        clip["orientation"] = media.orientation(clip["width"], clip["height"])
        clips.append(clip)
    if not clips:
        raise RuntimeError("ບໍ່ພົບຄລິບວິດີໂອ ຫຼື ຮູບ ທີ່ໃຊ້ໄດ້")
    update(jid, clips=clips, warnings=job.get("warnings", []))
    return clips


def _plan(jid: str, clips: list[dict], instruction: str, previous: dict | None) -> dict:
    job = load(jid)
    d = job_dir(jid)
    settings = config.load_settings()
    mf = music_files(jid)
    update(jid, status="planning", message=MSG["planning"], progress=0.25)
    warnings = list(job.get("warnings", []))
    raw = None
    if settings.get("api_key"):
        try:
            raw = planner.plan_with_claude(clips, d, job["opts"], settings, mf, instruction, previous)
        except Exception as e:
            warnings.append(f"AI error -> demo plan: {type(e).__name__}: {str(e)[:300]}")
    if raw is None:
        if previous:
            raw = dict(previous)  # can't revise without AI; keep the plan
            warnings.append("ບໍ່ມີ API key: ແກ້ໄຂດ້ວຍການພິມຕ້ອງໃຊ້ AI (ແກ້ JSON ເອງໄດ້)")
        else:
            raw = planner.plan_fallback(clips, job["opts"], settings, mf, instruction)
    plan, w = planner.normalize(raw, clips, mf)
    update(jid, warnings=(warnings + w)[-20:])
    return plan


def _render(jid: str, plan: dict, note: str) -> None:
    job = load(jid)
    d = job_dir(jid)
    v = len(job.get("versions") or []) + 1
    settings = config.load_settings()
    brand = {k: settings.get(k, "") for k in ("shop_name", "tagline", "phones", "address")}
    res = render.render(d, plan, job["clips"], job["opts"], brand, f"xmedia_v{v}.mp4",
                        _progress(jid, "rendering", 0.3, 0.7))
    job = load(jid)
    job["versions"].append({"v": v, **res, "plan": plan, "note": note, "created": time.time()})
    hist = job.get("history", [])
    if plan.get("summary"):
        hist.append({"role": "ai", "text": plan["summary"]})
    job.update(status="done", message=MSG["done"], progress=1.0, history=hist, updated=time.time())
    save(job)


def _pipeline(jid: str) -> None:
    clips = _analyze(jid)
    job = load(jid)
    plan = _plan(jid, clips, job["instruction"], None)
    _render(jid, plan, job["instruction"])


def _revise(jid: str, instruction: str | None, plan_json: dict | None) -> None:
    job = load(jid)
    clips = job["clips"]
    prev = (job.get("versions") or [{}])[-1].get("plan")
    if plan_json is not None:
        plan, w = planner.normalize(plan_json, clips, music_files(jid))
        plan["_source"] = "manual"
        update(jid, warnings=w)
    else:
        plan = _plan(jid, clips, instruction or "", prev)
    _render(jid, plan, instruction or "JSON")
