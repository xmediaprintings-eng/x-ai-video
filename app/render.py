"""Render an edit plan to MP4 with FFmpeg.

1. every segment -> normalised clip (same size, 30 fps, stereo 48 kHz)
2. outro card -> image segment
3. one final pass: xfade/acrossfade chain + ASS text (libass) + logo + music ducking + loudness
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from . import config
from .media import FFMPEG, MediaError, run
from .textlayout import AssBuilder

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
SOFT_TD = 0.45


def dims(aspect: str, quality: str) -> tuple[int, int]:
    W, H = ASPECTS.get(aspect, ASPECTS["9:16"])
    if str(quality) == "720":
        W, H = int(round(W * 2 / 3 / 2) * 2), int(round(H * 2 / 3 / 2) * 2)
    return W, H


def _frames(t: float, fps: int) -> float:
    return max(1, round(t * fps)) / fps


def timeline(plan: dict, fps: int) -> list[dict]:
    """Output start/duration of each segment (+ outro) including transition overlaps."""
    items = []
    for s in plan["segments"]:
        items.append({"kind": "seg", "seg": s, "dur": _frames((s["end"] - s["start"]) / s.get("speed", 1), fps),
                      "transition": s.get("transition", "cut")})
    if plan.get("outro", {}).get("show"):
        items.append({"kind": "outro", "seg": None, "dur": _frames(plan["outro"]["duration"], fps),
                      "transition": "fade"})
    start = 0.0
    for i, it in enumerate(items):
        if i == 0:
            it["td"] = 0.0
        else:
            want = 2 / fps if it["transition"] == "cut" else SOFT_TD
            td = min(want, items[i - 1]["dur"] * 0.45, it["dur"] * 0.45)
            it["td"] = _frames(td, fps)
            start = items[i - 1]["start"] + items[i - 1]["dur"] - it["td"]
        it["start"] = round(start, 4)
    return items


# ------------------------------------------------------------------ segments
CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def render_segment(clip: dict, seg: dict, dur: float, W: int, H: int, fps: int, out: Path, orig_vol: float) -> None:
    src = clip["path"]
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    if clip["kind"] == "image":
        args += ["-i", src]
        n = max(1, round(dur * fps))
        z = {"zoom_in": f"1+0.12*on/{n}", "zoom_out": f"1.12-0.12*on/{n}"}.get(seg.get("effect"), "1")
        vf = (f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase:flags=lanczos,crop={2 * W}:{2 * H},"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={fps},"
              f"setsar=1,format=yuv420p")
        has_audio = False
    else:
        args += ["-ss", f"{seg['start']:.3f}", "-t", f"{seg['end'] - seg['start']:.3f}", "-i", src]
        sp = seg.get("speed", 1.0)
        zf = 1.12 if seg.get("effect") == "punch_in" else 1.0
        zw, zh = int(W * zf) // 2 * 2, int(H * zf) // 2 * 2
        vf = (f"setpts=(PTS-STARTPTS)/{sp},scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W}:{H},fps={fps},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=1")
        has_audio = clip.get("has_audio") and not seg.get("mute") and orig_vol > 0
    args += ["-f", "lavfi", "-t", f"{dur + 0.5:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fc = f"[0:v]{vf}[v]"
    if has_audio:
        sp = seg.get("speed", 1.0)
        tempo = f"atempo={sp}," if abs(sp - 1) > 1e-3 else ""
        fc += (f";[0:a]asetpts=PTS-STARTPTS,{tempo}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
               f"volume={orig_vol},apad[a]")
        amap = "[a]"
    else:
        amap = "1:a"
    args += ["-filter_complex", fc, "-map", "[v]", "-map", amap, "-t", f"{dur:.4f}", "-r", str(fps), *CODEC, str(out)]
    run(args)


def outro_background(W: int, H: int, logo: Path | None, out: Path) -> float:
    """Draw the outro card background (no text - text is drawn by libass). Returns logo bottom y."""
    img = Image.new("RGB", (W, H), (5, 5, 5))
    d = ImageDraw.Draw(img)
    # soft orange glow
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    r = int(max(W, H) * 0.55)
    for i in range(60):
        rr = int(r * (1 - i / 60))
        gd.ellipse([W // 2 - rr, int(H * 0.3) - rr, W // 2 + rr, int(H * 0.3) + rr], fill=int(70 * i / 60))
    img.paste(Image.new("RGB", (W, H), (255, 110, 0)), mask=glow.point(lambda v: int(v * 0.55)))
    # brand gradient bars (yellow -> orange -> red)
    stops = [(255, 196, 0), (255, 122, 0), (232, 35, 27)]

    def grad_color(x: float):
        if x < 0.45:
            a, b, f = stops[0], stops[1], x / 0.45
        else:
            a, b, f = stops[1], stops[2], (x - 0.45) / 0.55
        return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))

    bar = max(6, H // 160)
    for x in range(W):
        c = grad_color(x / W)
        d.line([(x, 0), (x, bar)], fill=c)
        d.line([(x, H - bar), (x, H)], fill=c)
    top = H * (0.12 if H > W else 0.08)
    box_h = H * (0.2 if H > W else 0.26)
    if logo and logo.exists():
        with Image.open(logo) as lg:
            lg = lg.convert("RGBA")
            scale = min(W * 0.5 / lg.width, box_h / lg.height)
            lg = lg.resize((max(1, int(lg.width * scale)), max(1, int(lg.height * scale))), Image.LANCZOS)
            img.paste(lg, (int((W - lg.width) / 2), int(top + (box_h - lg.height) / 2)), lg)
    else:  # gradient "X" box
        s = int(min(box_h, W * 0.3))
        x0, y0 = (W - s) // 2, int(top + (box_h - s) / 2)
        box = Image.new("RGB", (s, s))
        bd = ImageDraw.Draw(box)
        for x in range(s):
            bd.line([(x, 0), (x, s)], fill=grad_color(x / s))
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 5, fill=255)
        img.paste(box, (x0, y0), mask)
        try:
            font = ImageFont.truetype(str(config.FONTS / "XMediaSans-Bold.ttf"), int(s * 0.72))
            d.text((W / 2, y0 + s / 2), "X", font=font, fill=(18, 8, 0), anchor="mm")
        except Exception:
            pass
    img.save(out, "PNG")
    return top + box_h


# ------------------------------------------------------------------ captions mapping
def captions_for(plan: dict, items: list[dict], clips: dict) -> list[tuple[float, float, str]]:
    caps = []
    for it in items:
        s = it["seg"]
        if not s or s.get("mute"):
            continue
        c = clips[s["clip"]]
        sp = s.get("speed", 1.0)
        for t in c.get("transcript") or []:
            a, b = max(t["start"], s["start"]), min(t["end"], s["end"])
            if b - a <= 0 or (b - a) < 0.5 * (t["end"] - t["start"]):
                continue
            oa = it["start"] + (a - s["start"]) / sp
            ob = min(it["start"] + (b - s["start"]) / sp, it["start"] + it["dur"])
            caps.append((oa, ob, t["text"]))
    return caps


# ------------------------------------------------------------------ main
def render(job_dir: Path, plan: dict, clips: list[dict], opts: dict, brand: dict, out_name: str,
           progress: Callable[[float, str], None] | None = None) -> dict:
    if not FFMPEG:
        raise MediaError("FFmpeg not found. Install it (Windows: winget install Gyan.FFmpeg).")
    progress = progress or (lambda p, m: None)
    fps = 30
    W, H = dims(opts.get("aspect", "9:16"), opts.get("quality", "1080"))
    work = job_dir / "work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    (work / "fonts").mkdir()
    for f in config.FONTS.glob("*.ttf"):
        shutil.copy(f, work / "fonts" / f.name)
    by_id = {c["id"]: c for c in clips}
    items = timeline(plan, fps)
    total = items[-1]["start"] + items[-1]["dur"]
    logo = config.logo_path()
    orig_vol = plan.get("original_audio_volume", 1.0)

    # 1) segments
    n = len(items)
    logo_bottom = H * 0.3
    for i, it in enumerate(items):
        progress(0.05 + 0.6 * i / n, f"seg {i + 1}/{n}")
        out = work / f"seg_{i:03d}.mp4"
        if it["kind"] == "outro":
            bg = work / "outro_bg.png"
            logo_bottom = outro_background(W, H, logo, bg)
            render_segment({"kind": "image", "path": str(bg)}, {"effect": "none"}, it["dur"], W, H, fps, out, 0)
        else:
            render_segment(by_id[it["seg"]["clip"]], it["seg"], it["dur"], W, H, fps, out, orig_vol)
        it["file"] = out.name

    # 2) subtitles / texts
    outro_start = items[-1]["start"] if items[-1]["kind"] == "outro" else total
    ass = AssBuilder(W, H)
    caps_on = bool(plan.get("captions"))
    for t in plan.get("texts", []):
        a = max(0.1, t["at"])
        b = min(a + t["duration"], outro_start - 0.05)
        if b - a > 0.3:
            ass.add_text(t["text"], a, b, t.get("position", "top"), t.get("style", "title"), caps_on)
    if caps_on:
        for a, b, txt in captions_for(plan, items, by_id):
            ass.add_caption(txt, a, min(b, outro_start))
    if items[-1]["kind"] == "outro":
        ass.add_outro(outro_start + 0.15, total, brand, plan["outro"].get("headline", ""), logo_bottom)
    (work / "subs.ass").write_text(ass.render(), encoding="utf-8")

    # 3) final pass
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-progress", "pipe:1"]
    for it in items:
        args += ["-i", it["file"]]
    fc: list[str] = []
    v, a = "0:v", "0:a"
    for i in range(1, n):
        it = items[i]
        tr = "fade" if it["transition"] == "cut" else it["transition"]
        fc.append(f"[{v}][{i}:v]xfade=transition={tr}:duration={it['td']:.4f}:offset={it['start']:.4f}[vx{i}]")
        fc.append(f"[{a}][{i}:a]acrossfade=d={it['td']:.4f}:c1=tri:c2=tri[ax{i}]")
        v, a = f"vx{i}", f"ax{i}"
    fc.append(f"[{v}]subtitles=filename=subs.ass:fontsdir=fonts[vs]")
    v = "vs"
    idx = n
    if plan.get("logo", True) and logo:
        shutil.copy(logo, work / ("logo" + logo.suffix))
        args += ["-i", "logo" + logo.suffix]
        lw = int(W * (0.17 if H > W else 0.11)) // 2 * 2
        m = int(min(W, H) * 0.04)
        fc.append(f"[{idx}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.92[lg]")
        fc.append(f"[{v}][lg]overlay=x=W-w-{m}:y={m}:enable='lt(t,{outro_start:.3f})'[vl]")
        v = "vl"
        idx += 1
    mfile = (plan.get("music") or {}).get("file")
    mpath = None
    if mfile:
        for cand in (job_dir / "music" / mfile, config.MUSIC_DIR / mfile):
            if cand.exists():
                mpath = cand
                break
    any_orig = orig_vol > 0 and any(
        it["seg"] and by_id[it["seg"]["clip"]].get("has_audio") and not it["seg"].get("mute") for it in items)
    if mpath:
        shutil.copy(mpath, work / ("music" + mpath.suffix))
        args += ["-stream_loop", "-1", "-i", "music" + mpath.suffix]
        mv = plan["music"].get("volume", 0.3)
        fc.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{total:.3f},"
                  f"asetpts=PTS-STARTPTS,volume={mv},afade=t=in:st=0:d=0.6,"
                  f"afade=t=out:st={max(0.0, total - 2):.3f}:d=2[mus]")
        if any_orig:
            fc.append(f"[{a}]asplit=2[ao][asc]")
            fc.append("[mus][asc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400[duck]")
            fc.append("[ao][duck]amix=inputs=2:duration=first:normalize=0[amix]")
        else:
            fc.append(f"[{a}][mus]amix=inputs=2:duration=first:normalize=0[amix]")
        a = "amix"
    fc.append(f"[{a}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    out_path = job_dir / "out" / out_name
    out_path.parent.mkdir(exist_ok=True)
    args += ["-filter_complex", ";".join(fc), "-map", f"[{v}]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out_path)]
    (work / "final_cmd.txt").write_text(subprocess.list2cmdline(args), encoding="utf-8")
    proc = subprocess.Popen(args, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    for line in proc.stdout:  # type: ignore[union-attr]
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                t = int(line.split("=")[1]) / 1e6
                progress(0.65 + 0.33 * min(1.0, t / total), "final")
            except ValueError:
                pass
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        tail = "\n".join(err.strip().splitlines()[-12:])
        raise MediaError(f"final render failed:\n{tail}")
    # poster
    poster = out_path.with_suffix(".jpg")
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(1.0, total / 3):.2f}", "-i", str(out_path),
         "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(poster)], check=False)
    progress(1.0, "done")
    tl = [{"kind": it["kind"], "start": round(it["start"], 2), "dur": round(it["dur"], 2),
           "clip": it["seg"]["clip"] if it["seg"] else None, "transition": it["transition"]} for it in items]
    return {"file": out_path.name, "poster": poster.name if poster.exists() else None, "duration": round(total, 2),
            "width": W, "height": H, "timeline": tl}
