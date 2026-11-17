"""End-to-end smoke test without the web server or an API key.

    python -m tests.smoke_test

Creates sample clips with FFmpeg, runs analyze -> demo plan -> render, then renders a
hand-written plan with Lao/Thai text, captions, music and every transition type.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("XAV_DATA", tempfile.mkdtemp(prefix="xav_test_"))

from app import config, jobs, media, planner  # noqa: E402


def make_samples(d: Path) -> list[dict]:
    ff = media.FFMPEG
    d.mkdir(parents=True, exist_ok=True)
    # landscape clip with 3 hard scene changes + tone
    media.run([ff, "-y", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
               "-f", "lavfi", "-i", "smptebars=s=1280x720:r=30:d=3",
               "-f", "lavfi", "-i", "mandelbrot=s=1280x720:r=30",
               "-f", "lavfi", "-i", "sine=f=440:d=9",
               "-filter_complex", "[2:v]trim=duration=3,setpts=PTS-STARTPTS[m];[0:v][1:v][m]concat=n=3:v=1:a=0[v]",
               "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
               str(d / "landscape.mp4")])
    # portrait clip, no audio
    media.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "life=s=540x960:r=30:mold=10:ratio=0.1",
               "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(d / "portrait.mp4")])
    # a photo
    media.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "gradients=s=1600x1200:d=1", "-frames:v", "1",
               str(d / "photo.jpg")])
    # music
    media.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
               "aevalsrc='0.2*sin(2*PI*220*t)+0.15*sin(2*PI*277*t)+0.1*sin(2*PI*330*t)':d=8", str(d / "music.mp3")])
    return [{"name": p, "file": p} for p in ("landscape.mp4", "portrait.mp4", "photo.jpg")]


def main() -> int:
    config.ensure_dirs()
    import shutil

    jid, d = jobs.new_job_dir()
    sd = Path(tempfile.mkdtemp(prefix="xav_samples_"))
    ups = make_samples(sd)
    for s in ups:
        shutil.copy(sd / s["file"], d / "uploads" / s["file"])
    shutil.copy(sd / "music.mp3", d / "music" / "music.mp3")

    opts = {"aspect": os.environ.get("XAV_ASPECT", "9:16"), "duration": 12, "captions": True, "language": "auto", "quality": "720"}
    job = {"id": jid, "created": 0, "updated": 0, "status": "queued", "message": "", "progress": 0,
           "instruction": "ວິດີໂອໂຄສະນາກ່ອງໄຟ 12 ວິ", "opts": opts, "uploads": ups, "clips": [], "versions": [],
           "history": [], "warnings": [], "error": None}
    jobs.save(job)
    jobs._pipeline(jid)  # synchronous
    job = jobs.load(jid)
    v1 = job["versions"][-1]
    print("v1:", v1["file"], v1["duration"], "s", v1["width"], "x", v1["height"], "| warnings:", job["warnings"])
    print("scenes c1:", job["clips"][0]["scenes"])

    # manual plan with Lao/Thai text, fake captions, music, all soft transitions
    clips = job["clips"]
    clips[0]["transcript"] = [{"start": 0.5, "end": 2.6, "text": "ສະບາຍດີ ພວກເຮົາຮັບເຮັດປ້າຍໄຟ LED ທຸກຂະໜາດ"},
                              {"start": 3.2, "end": 5.8, "text": "ป้ายไฟคุณภาพดี ติดตั้งถึงหน้าร้าน"}]
    jobs.update(jid, clips=clips)
    segs = []
    trs = ["cut", "fade", "slideleft", "circleopen", "zoomin", "dissolve", "slideup", "cut"]
    picks = [("c1", 0.4, 2.8), ("c2", 0, 2), ("c3", 0, 2.5), ("c1", 3.2, 5.8), ("c2", 3, 5), ("c1", 6.2, 8.5)]
    for k, (c, a, b) in enumerate(picks):
        segs.append({"clip": c, "start": a, "end": b, "transition": trs[k], "speed": 1.5 if k == 4 else 1,
                     "effect": "punch_in" if k == 3 else ("zoom_out" if c == "c3" else "none")})
    plan = {"summary": "test", "segments": segs, "captions": True,
            "texts": [{"text": "ປ້າຍໄຟ LED ສວຍງາມ\nທົນທານ 5 ປີ", "at": 0.3, "duration": 2.3, "position": "top", "style": "title"},
                      {"text": "NEW", "at": 2.6, "duration": 1.6, "position": "top", "style": "badge"},
                      {"text": "ราคาพิเศษ 1,500,000 กีบ", "at": 4.5, "duration": 2, "position": "bottom", "style": "price"},
                      {"text": "ຜະລິດເອງ ໂຮງງານ X Media ນະຄອນຫຼວງວຽງຈັນ ຮັບປະກັນຄຸນນະພາບ", "at": 7, "duration": 2.5,
                       "position": "center", "style": "subtitle"}],
            "music": {"file": "music.mp3", "volume": 0.4}, "original_audio_volume": 1,
            "outro": {"show": True, "headline": "ສັ່ງເຮັດໄດ້ແລ້ວມື້ນີ້!", "duration": 3}, "logo": True}
    jobs._revise(jid, None, plan)
    job = jobs.load(jid)
    v2 = job["versions"][-1]
    print("v2:", v2["file"], v2["duration"], "s | warnings:", job["warnings"])
    for v in (v1, v2):
        out = d / "out" / v["file"]
        info = media.probe(out)
        print(f"  {v['file']}: probed {info['duration']:.2f}s {info['width']}x{info['height']} audio={info['has_audio']}")
        assert abs(info["duration"] - v["duration"]) < 0.25, "duration mismatch"
        assert info["has_audio"]
    print("OUT_DIR", d / "out")
    print(json.dumps(v2["timeline"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
