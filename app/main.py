"""FastAPI server for X Media AI Video."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs, media

config.load_dotenv()
config.ensure_dirs()

app = FastAPI(title="X Media AI Video", version=config.VERSION)

MAX_FILES = 30


def _safe_name(name: str, i: int) -> str:
    stem = Path(name or f"file{i}").stem
    ext = Path(name or "").suffix.lower()[:6]
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE)[:40] or f"file{i}"
    return f"{i:02d}_{stem}{ext}"


async def _save_upload(up: UploadFile, dst: Path) -> None:
    with dst.open("wb") as f:
        while chunk := await up.read(1024 * 1024):
            f.write(chunk)


def _public_job(job: dict) -> dict:
    j = dict(job)
    j["clips"] = [{k: v for k, v in c.items() if k != "path"} for c in job.get("clips", [])]
    return j


@app.get("/api/status")
def status() -> dict:
    s = config.load_settings()
    return {
        "version": config.VERSION,
        "ai_ready": bool(s.get("api_key")),
        "model": s.get("model"),
        "ffmpeg": bool(media.FFMPEG),
        "whisper": media.whisper_available(),
        "music": [p.name for p in config.MUSIC_DIR.glob("*") if p.suffix.lower() in media.AUDIO_EXT],
    }


@app.get("/api/settings")
def get_settings() -> dict:
    s = config.load_settings()
    key = s.pop("api_key", "")
    s["has_api_key"] = bool(key)
    s["api_key_hint"] = (key[:7] + "…" + key[-4:]) if len(key) > 12 else ""
    s["models"] = config.MODELS
    s["has_logo"] = any((config.BRAND_DIR / n).exists() for n in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp"))
    return s


@app.post("/api/settings")
async def post_settings(
    shop_name: str = Form(None), tagline: str = Form(None), phones: str = Form(None), address: str = Form(None),
    api_key: str = Form(None), model: str = Form(None), whisper_model: str = Form(None),
    remove_logo: bool = Form(False), logo: UploadFile | None = File(None),
) -> dict:
    upd = {"shop_name": shop_name, "tagline": tagline, "phones": phones, "address": address,
           "model": model, "whisper_model": whisper_model}
    if api_key is not None and api_key.strip() and "…" not in api_key:
        upd["api_key"] = api_key.strip()
    config.save_settings(upd)
    if remove_logo:
        for n in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp"):
            (config.BRAND_DIR / n).unlink(missing_ok=True)
    if logo is not None and logo.filename:
        ext = Path(logo.filename).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(400, "logo must be PNG/JPG/WEBP")
        for n in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp"):
            (config.BRAND_DIR / n).unlink(missing_ok=True)
        await _save_upload(logo, config.BRAND_DIR / f"logo{ext}")
    return get_settings()


@app.get("/api/brand/logo")
def brand_logo():
    p = config.logo_path()
    if not p:
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile] = File(...),
    instruction: str = Form(""),
    aspect: str = Form("9:16"),
    duration: float = Form(20),
    captions: bool = Form(False),
    language: str = Form("auto"),
    quality: str = Form("1080"),
    music: UploadFile | None = File(None),
) -> dict:
    if not media.FFMPEG:
        raise HTTPException(500, "FFmpeg not installed")
    files = [f for f in files if f.filename][:MAX_FILES]
    if not files:
        raise HTTPException(400, "no files")
    jid, d = jobs.new_job_dir()
    uploads = []
    for i, f in enumerate(files, 1):
        name = _safe_name(f.filename, i)
        if media.kind_of(name) == "audio":
            await _save_upload(f, d / "music" / name[3:])
            continue
        await _save_upload(f, d / "uploads" / name)
        uploads.append({"name": f.filename, "file": name})
    if music is not None and music.filename:
        await _save_upload(music, d / "music" / _safe_name(music.filename, 0)[3:])
    if not uploads:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, "no video or photo files")
    opts = {"aspect": aspect if aspect in ("9:16", "1:1", "4:5", "16:9") else "9:16",
            "duration": max(5.0, min(180.0, duration)), "captions": captions,
            "language": language if language in ("auto", "lo", "th", "en", "vi", "zh") else "auto",
            "quality": "720" if quality == "720" else "1080"}
    job = jobs.register(jid, uploads, opts, instruction.strip())
    return {"id": job["id"]}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return jobs.list_jobs()


@app.get("/api/jobs/{jid}")
def get_job(jid: str) -> dict:
    try:
        return _public_job(jobs.load(jid))
    except (KeyError, ValueError):
        raise HTTPException(404, "job not found")


@app.post("/api/jobs/{jid}/revise")
def revise(jid: str, body: dict) -> dict:
    instruction = (body.get("instruction") or "").strip()
    plan = body.get("plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON error: {e}")
    if not instruction and not plan:
        raise HTTPException(400, "empty")
    if instruction and not plan and not config.load_settings().get("api_key"):
        raise HTTPException(400, "ແກ້ໄຂດ້ວຍການພິມຕ້ອງໃຊ້ Claude API key — ໃສ່ໃນ ⚙ ກ່ອນ (ຫຼື ແກ້ JSON ເອງ)")
    try:
        return _public_job(jobs.revise(jid, instruction or None, plan))
    except KeyError:
        raise HTTPException(404, "job not found")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/{jid}/file/{name}")
def job_file(jid: str, name: str, download: bool = False):
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400)
    try:
        d = jobs.job_dir(jid)
    except ValueError:
        raise HTTPException(400)
    for sub in ("out", "frames"):
        p = d / sub / name
        if p.exists():
            return FileResponse(p, filename=name if download else None)
    raise HTTPException(404)


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str) -> dict:
    d = jobs.job_dir(jid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    return FileResponse(config.ASSETS / "brand" / "default_logo.png")


@app.get("/")
def index():
    return FileResponse(config.STATIC / "index.html")


app.mount("/static", StaticFiles(directory=config.STATIC), name="static")


@app.exception_handler(Exception)
async def on_error(_req, exc: Exception):  # pragma: no cover
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)
