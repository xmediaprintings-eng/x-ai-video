"""The AI 'editor': turns clips + an instruction into an edit plan (JSON timeline).

Claude sees scene lists, sampled frames and speech transcripts, then returns the plan
through a forced tool call so the output is always valid JSON. Without an API key a
simple rule-based planner is used so the app still works (demo mode).
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

TRANSITIONS = ["cut", "fade", "fadeblack", "fadewhite", "dissolve", "slideleft", "slideright", "slideup",
               "slidedown", "wipeleft", "wiperight", "circleopen", "circleclose", "radial", "smoothleft",
               "smoothright", "zoomin", "pixelize", "hblur", "squeezeh"]
EFFECTS = ["none", "zoom_in", "zoom_out", "punch_in"]
POSITIONS = ["top", "center", "bottom"]
STYLES = ["title", "subtitle", "badge", "price"]

SYSTEM_PROMPT = """You are the AI video editor inside "X Media AI Video", a tool for X Media - a sign shop in Vientiane, Laos that makes LED lightbox signs (ກ່ອງໄຟ / ປ້າຍໄຟ), shop counters and cabinets, and installs them at customers' shops. Staff upload raw phone clips/photos and type what video they want. You decide the whole edit: which shots, in what order, how long, on-screen texts, music and transitions. You can't play the video; you get scene lists, sampled frames (each labelled with clip id and timestamp) and speech transcripts.

Rules:
- Return the edit ONLY by calling the submit_edit_plan tool.
- Use only the clip ids given. For videos, start/end are seconds in the source clip with 0 <= start < end <= clip duration. Prefer ranges inside one scene; skip shaky, blurry, dark or repeated moments (judge from the frames). For photos use start=0 and end = how long to show it (1.5-4 s) and effect zoom_in or zoom_out.
- Hit the requested total length within about 15% (an outro card is appended automatically after your segments - don't count it).
- Social-video pacing: strong hook in the first 1.5 s (the most impressive shot, e.g. a finished sign lit up at night), most shots 1.2-3.5 s, end the main part on the best result shot. Never reuse the same moment.
- Transitions: "cut" for most joins, a few soft ones (fade, slideleft, slideup, circleopen, zoomin, dissolve) at meaningful moments. A segment's transition is how we enter that segment.
- speed: 1 normally; 1.5-2 for long process actions (cutting, bending, assembling); 0.6-0.8 only for a dramatic reveal.
- effect: punch_in (slight crop-in) to vary repeated framing; zoom_in/zoom_out for photos.
- texts: short punchy on-screen text, max about 26 characters per line, max 2 lines (use \\n). Write in the same language as the user's instruction unless they ask for another one (Lao instruction -> Lao text, Thai -> Thai). `at` and `duration` are seconds on the OUTPUT timeline (a segment's output length = (end-start)/speed; every non-cut transition overlaps 0.45 s). No text in the first 0.2 s or over the outro. Styles: title (big white), subtitle (yellow), badge (orange box, e.g. LED, NEW), price (red box). Place text where it won't cover the main subject (usually top or bottom). Usually 2-5 texts for a 20 s video.
- captions: true only if the transcript has meaningful speech and the user didn't refuse captions.
- original_audio_volume: 0 if the clips' audio is only noise and music is used; 1 if speech matters.
- music: pick a file name from the list, or "" for none. volume 0.15-0.3 under speech, 0.4-0.7 without speech.
- outro: show=true unless the user says otherwise; headline = short call to action in the user's language (e.g. ສັ່ງເຮັດໄດ້ແລ້ວມື້ນີ້). Shop name, phone numbers and address are added automatically - never invent phone numbers, prices, discounts or claims the user didn't give.
- summary: 1-2 sentences in the user's language describing the edit you made.
For a revision request, start from the previous plan and change only what is asked."""

PLAN_TOOL = {
    "name": "submit_edit_plan",
    "description": "Submit the complete edit plan for the video.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short internal name for this video"},
            "summary": {"type": "string"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clip": {"type": "string"},
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "speed": {"type": "number"},
                        "transition": {"type": "string", "enum": TRANSITIONS},
                        "effect": {"type": "string", "enum": EFFECTS},
                        "mute": {"type": "boolean"},
                        "why": {"type": "string", "description": "few words: what this shot shows"},
                    },
                    "required": ["clip", "start", "end"],
                },
            },
            "texts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "at": {"type": "number"},
                        "duration": {"type": "number"},
                        "position": {"type": "string", "enum": POSITIONS},
                        "style": {"type": "string", "enum": STYLES},
                    },
                    "required": ["text", "at", "duration"],
                },
            },
            "captions": {"type": "boolean"},
            "original_audio_volume": {"type": "number"},
            "music": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "volume": {"type": "number"}},
            },
            "outro": {
                "type": "object",
                "properties": {
                    "show": {"type": "boolean"},
                    "headline": {"type": "string"},
                    "duration": {"type": "number"},
                },
            },
            "logo": {"type": "boolean", "description": "Show shop logo watermark in the corner"},
        },
        "required": ["segments", "summary"],
    },
}


class PlanError(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def _clip_summary(clips: list[dict], max_speech: int = 40) -> list[dict]:
    out = []
    for c in clips:
        d = {
            "id": c["id"],
            "type": c["kind"],
            "file": c["name"],
            "duration": round(c.get("duration", 0), 2),
            "orientation": c.get("orientation"),
        }
        if c["kind"] == "video":
            d["has_audio"] = c.get("has_audio", False)
            d["scenes"] = c.get("scenes", [])
            if c.get("transcript"):
                d["speech"] = c["transcript"][:max_speech]
        out.append(d)
    return out


def _frames_content(clips: list[dict], job_dir: Path, limit: int) -> list[dict]:
    frames = [(c["id"], f) for c in clips for f in c.get("frames", [])]
    if len(frames) > limit:
        step = len(frames) / limit
        frames = [frames[int(i * step)] for i in range(limit)]
    blocks: list[dict] = []
    for cid, f in frames:
        p = job_dir / "frames" / f["file"]
        if not p.exists():
            continue
        label = f"{cid} @ {f['t']:.1f}s" if f.get("t") is not None else f"{cid} (photo)"
        blocks.append({"type": "text", "text": label})
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.b64encode(p.read_bytes()).decode("ascii")},
        })
    return blocks


def plan_with_claude(clips: list[dict], job_dir: Path, opts: dict, settings: dict, music_files: list[str],
                     instruction: str, previous: dict | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings["api_key"], timeout=180, max_retries=2)
    head = (
        f"User instruction:\n{instruction.strip() or '(none - make an attractive promo video)'}\n\n"
        f"Output format: aspect {opts.get('aspect', '9:16')}, target length about {opts.get('duration', 20)} s "
        f"(excluding outro). Captions requested by user: {'yes' if opts.get('captions') else 'no'}.\n"
        f"Shop: {settings.get('shop_name', '')} - {settings.get('tagline', '')}\n"
        f"Available music files: {json.dumps(music_files, ensure_ascii=False) if music_files else 'none'}\n\n"
        f"Clips (JSON):\n{json.dumps(_clip_summary(clips), ensure_ascii=False)}\n\nSampled frames follow."
    )
    content: list[dict] = [{"type": "text", "text": head}]
    content += _frames_content(clips, job_dir, limit=16 if previous else 28)
    if previous:
        prev = {k: v for k, v in previous.items() if not k.startswith("_")}
        content.append({"type": "text", "text": "Previous plan (JSON):\n" + json.dumps(prev, ensure_ascii=False)})
        content.append({"type": "text", "text": "Revision request from user:\n" + instruction.strip()})
    else:
        content.append({"type": "text", "text": "Now plan the edit and call submit_edit_plan."})

    resp = client.messages.create(
        model=settings.get("model") or "claude-sonnet-5-5",
        max_tokens=6000,
        system=SYSTEM_PROMPT,
        tools=[PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_edit_plan"},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_edit_plan":
            plan = dict(block.input)
            plan["_source"] = "claude"
            plan["_usage"] = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
            return plan
    raise PlanError(f"Claude did not return a plan (stop_reason={resp.stop_reason})")


# ------------------------------------------------------------------ offline planner
def plan_fallback(clips: list[dict], opts: dict, settings: dict, music_files: list[str], instruction: str = "") -> dict:
    target = float(opts.get("duration", 20))
    m = re.search(r"(\d{1,3})\s*(?:s|sec|วิ|ວິ|ວິນາທີ|วินาที)", instruction or "")
    if m:
        target = max(5.0, min(120.0, float(m[1])))
    shot = 2.4
    pools: list[list[dict]] = []
    for c in clips:
        cands = []
        if c["kind"] == "image":
            cands.append({"clip": c["id"], "start": 0, "end": 2.6, "effect": "zoom_in"})
        else:
            for a, b in c.get("scenes") or [[0, c["duration"]]]:
                ln = b - a
                if ln < 0.8:
                    continue
                n = max(1, int(ln // 5))  # long scenes give several shots
                for i in range(n):
                    sa = a + ln * i / n
                    sb = a + ln * (i + 1) / n
                    mid = (sa + sb) / 2
                    d = min(shot, sb - sa)
                    cands.append({"clip": c["id"], "start": round(mid - d / 2, 2), "end": round(mid + d / 2, 2)})
        if cands:
            pools.append(cands)
    segs: list[dict] = []
    total = 0.0
    i = 0
    while pools and total < target and i < 200:
        pool = pools[i % len(pools)]
        if pool:
            s = pool.pop(0)
            segs.append(s)
            total += s["end"] - s["start"]
        pools = [p for p in pools if p]
        i += 1
    for k, s in enumerate(segs):
        s["transition"] = "cut" if k % 3 else "fade"
        if s.get("effect") is None:
            s["effect"] = "punch_in" if k % 4 == 2 else "none"
        if s["effect"] == "zoom_in" and k % 2:
            s["effect"] = "zoom_out"
    has_speech = any(c.get("transcript") for c in clips)
    texts = [{"text": settings.get("shop_name", "X Media"), "at": 0.3, "duration": 2.2, "position": "top", "style": "title"}]
    if settings.get("tagline") and total > 6:
        texts.append({"text": settings["tagline"], "at": min(total - 2.5, 3.0), "duration": 2.4,
                      "position": "bottom", "style": "subtitle"})
    return {
        "title": "Auto edit",
        "summary": "ຕັດຊອດອັດຕະໂນມັດຕາມສາກ (ບໍ່ໄດ້ໃຊ້ AI). ໃສ່ Claude API key ໃນ ⚙ ການຕັ້ງຄ່າ ເພື່ອໃຫ້ AI ຕັດຕໍ່ແທ້.",
        "segments": segs,
        "texts": texts,
        "captions": bool(opts.get("captions") and has_speech),
        "original_audio_volume": 1.0 if has_speech else (0.3 if music_files else 1.0),
        "music": {"file": music_files[0] if music_files else "", "volume": 0.3 if has_speech else 0.6},
        "outro": {"show": True, "headline": "ສັ່ງເຮັດໄດ້ແລ້ວມື້ນີ້!", "duration": 3.2},
        "logo": True,
        "_source": "demo",
    }


# ------------------------------------------------------------------ validation
def _num(v, default: float) -> float:
    try:
        f = float(v)
        return f if f == f else default  # NaN guard
    except Exception:
        return default


def normalize(plan: dict, clips: list[dict], music_files: list[str]) -> tuple[dict, list[str]]:
    """Clamp everything to valid values so the renderer never fails on AI mistakes."""
    warnings: list[str] = []
    by_id = {c["id"]: c for c in clips}
    segs = []
    for s in plan.get("segments") or []:
        c = by_id.get(str(s.get("clip", "")))
        if not c:
            warnings.append(f"skip unknown clip {s.get('clip')}")
            continue
        speed = min(2.0, max(0.5, _num(s.get("speed"), 1.0)))
        if c["kind"] == "image":
            start = 0.0
            end = min(10.0, max(0.8, _num(s.get("end"), 2.5) - max(0.0, _num(s.get("start"), 0.0))))
            speed = 1.0
        else:
            dur = max(0.0, c.get("duration", 0) - 0.05)
            start = min(max(0.0, _num(s.get("start"), 0.0)), max(0.0, dur - 0.4))
            end = min(max(start + 0.4, _num(s.get("end"), start + 2.0)), dur)
            if end - start < 0.35:
                warnings.append(f"skip too-short segment {c['id']} {start:.2f}-{end:.2f}")
                continue
        tr = s.get("transition") if s.get("transition") in TRANSITIONS else "cut"
        eff = s.get("effect") if s.get("effect") in EFFECTS else ("zoom_in" if c["kind"] == "image" else "none")
        if c["kind"] == "video" and eff in ("zoom_in", "zoom_out"):
            eff = "punch_in"
        seg = {"clip": c["id"], "start": round(start, 3), "end": round(end, 3), "speed": round(speed, 3),
               "transition": tr, "effect": eff, "mute": bool(s.get("mute", False))}
        if s.get("why"):
            seg["why"] = str(s["why"])[:80]
        segs.append(seg)
    segs = segs[:60]
    if not segs:
        raise PlanError("plan has no usable segments")

    texts = []
    for t in (plan.get("texts") or [])[:16]:
        txt = str(t.get("text", "")).strip()
        if not txt:
            continue
        texts.append({
            "text": txt[:120],
            "at": round(max(0.0, _num(t.get("at"), 0.0)), 2),
            "duration": round(min(20.0, max(0.5, _num(t.get("duration"), 2.0))), 2),
            "position": t.get("position") if t.get("position") in POSITIONS else "top",
            "style": t.get("style") if t.get("style") in STYLES else "title",
        })

    music = plan.get("music") or {}
    mfile = str(music.get("file") or "")
    if mfile and mfile not in music_files:
        warnings.append(f"music '{mfile}' not found")
        mfile = ""
    outro = plan.get("outro") or {}
    out = {
        "title": str(plan.get("title") or "X Media video")[:80],
        "summary": str(plan.get("summary") or "")[:600],
        "segments": segs,
        "texts": texts,
        "captions": bool(plan.get("captions", False)),
        "original_audio_volume": round(min(2.0, max(0.0, _num(plan.get("original_audio_volume"), 1.0))), 2),
        "music": {"file": mfile, "volume": round(min(1.0, max(0.0, _num(music.get("volume"), 0.3))), 2)},
        "outro": {
            "show": bool(outro.get("show", True)),
            "headline": str(outro.get("headline") or "")[:80],
            "duration": round(min(6.0, max(2.0, _num(outro.get("duration"), 3.2))), 2),
        },
        "logo": bool(plan.get("logo", True)),
    }
    for k in ("_source", "_usage"):
        if k in plan:
            out[k] = plan[k]
    return out, warnings
