"""Media analysis: probe, scene detection, keyframes, speech transcription."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".3gp", ".mts", ".ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def _find_ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:  # optional fallback: pip install imageio-ffmpeg
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _find_ffmpeg()
FFPROBE = shutil.which("ffprobe")


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], cwd: str | Path | None = None, check: bool = True, timeout: int = 3600):
    r = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    if check and r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-12:])
        raise MediaError(f"ffmpeg failed ({r.returncode}):\n{tail}")
    return r


def kind_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return "video"


# ---------------------------------------------------------------- probe
def _fps(rate: str | None) -> float:
    try:
        if rate and "/" in rate:
            a, b = rate.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(rate or 0)
    except Exception:
        return 0.0


def probe(path: str | Path) -> dict:
    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False, "has_video": False}
    if FFPROBE:
        r = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], check=False)
        try:
            data = json.loads(r.stdout or "{}")
        except Exception:
            data = {}
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not info["has_video"]:
                if (s.get("disposition") or {}).get("attached_pic"):
                    continue
                w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                rot = 0
                try:
                    rot = int(float((s.get("tags") or {}).get("rotate", 0)))
                except Exception:
                    pass
                for sd in s.get("side_data_list") or []:
                    if "rotation" in sd:
                        try:
                            rot = int(float(sd["rotation"]))
                        except Exception:
                            pass
                if abs(rot) % 180 == 90:
                    w, h = h, w
                info.update(width=w, height=h, has_video=True)
                f = _fps(s.get("avg_frame_rate")) or _fps(s.get("r_frame_rate"))
                if 1 <= f <= 240:
                    info["fps"] = f
            elif s.get("codec_type") == "audio":
                info["has_audio"] = True
        try:
            info["duration"] = float((data.get("format") or {}).get("duration") or 0)
        except Exception:
            pass
    else:  # parse `ffmpeg -i` output
        r = run([FFMPEG, "-hide_banner", "-i", str(path)], check=False)
        err = r.stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
        if m:
            info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
        if m:
            info.update(width=int(m[1]), height=int(m[2]), has_video=True)
        m = re.search(r"([\d.]+) fps", err)
        if m:
            info["fps"] = float(m[1])
        m = re.search(r"rotation of (-?[\d.]+)", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
        if m and abs(int(float(m[1]))) % 180 == 90:
            info["width"], info["height"] = info["height"], info["width"]
        info["has_audio"] = "Audio:" in err
    return info


def orientation(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    r = w / h
    return "portrait" if r < 0.9 else "landscape" if r > 1.1 else "square"


# ---------------------------------------------------------------- images
def normalize_image(src: Path, dst: Path, max_side: int = 3840) -> Path:
    """Apply EXIF rotation, convert HEIC/PNG/etc. to a sane-size JPEG."""
    from PIL import Image, ImageOps

    if src.suffix.lower() in (".heic", ".heif"):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception as e:  # pragma: no cover
            raise MediaError("HEIC needs: pip install pillow-heif") from e
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (0, 0, 0))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(dst, "JPEG", quality=92)
    return dst


# ---------------------------------------------------------------- scenes & frames
def detect_scenes(path: str | Path, duration: float, threshold: float = 0.3, min_len: float = 0.6) -> list[list[float]]:
    if duration <= 0:
        return []
    r = run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-an", "-sn", "-dn",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        check=False,
    )
    cuts = sorted({round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")})
    bounds = [0.0] + [t for t in cuts if 0.3 < t < duration - 0.3] + [round(duration, 2)]
    scenes: list[list[float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if scenes and (b - a) < min_len:
            scenes[-1][1] = b  # merge tiny scene into previous
        else:
            scenes.append([a, b])
    if len(scenes) > 1 and scenes[0][1] - scenes[0][0] < min_len:
        scenes[1][0] = scenes[0][0]
        scenes.pop(0)
    return scenes


def sample_times(scenes: list[list[float]], budget: int) -> list[float]:
    """Pick frame times: one per scene (middle), more for long scenes, capped by budget."""
    times: list[float] = []
    for a, b in scenes:
        n = max(1, min(4, int((b - a) // 4) + 1))
        for i in range(n):
            times.append(round(a + (b - a) * (i + 0.5) / n, 2))
    if len(times) > budget:
        step = len(times) / budget
        times = [times[int(i * step)] for i in range(budget)]
    return times


def extract_frame(path: str | Path, t: float | None, out: Path, size: int = 480) -> Path:
    seek = ["-ss", f"{t:.3f}"] if t and t > 0 else []  # seeking a still image yields no frame
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *seek, "-i", str(path),
         "-frames:v", "1", "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease", "-q:v", "5", str(out)])
    if not out.exists():
        raise MediaError(f"no frame extracted at {t}s from {Path(path).name}")
    return out


# ---------------------------------------------------------------- speech
_whisper_model = None
_whisper_name = None


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def transcribe(path: str | Path, work: Path, language: str | None, model_name: str = "small") -> list[dict]:
    global _whisper_model, _whisper_name
    if not whisper_available():
        return []
    from faster_whisper import WhisperModel

    wav = work / (Path(path).stem + "_16k.wav")
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    if _whisper_model is None or _whisper_name != model_name:
        try:
            _whisper_model = WhisperModel(model_name, device="auto", compute_type="int8")
        except Exception:
            _whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _whisper_name = model_name
    segs, _info = _whisper_model.transcribe(str(wav), language=language or None, vad_filter=True, beam_size=5)
    out = []
    for s in segs:
        text = (s.text or "").strip()
        if text:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
    try:
        wav.unlink()
    except Exception:
        pass
    return out
