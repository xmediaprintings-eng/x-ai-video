"""Paths and settings for X Media AI Video."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("XAV_DATA", ROOT / "data"))
JOBS = DATA / "jobs"
BRAND_DIR = DATA / "brand"
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
STATIC = Path(__file__).resolve().parent / "static"
SETTINGS_FILE = DATA / "settings.json"

FONT_NAME = "XMedia Sans"  # merged Noto Sans + Noto Sans Lao + Noto Sans Thai
VERSION = "0.1.0"

DEFAULT_SETTINGS = {
    "shop_name": "X Media",
    "tagline": "ປ້າຍໄຟ · ກ່ອງໄຟ · ຕູ້ເຄົາເຕີ້",
    "phones": "020 28039918\n020 29699897\n020 28945565",
    "address": "ບ້ານຄຳຮຸ່ງ ເມືອງໄຊທານີ ນະຄອນຫລວງວຽງຈັນ",
    "api_key": "",
    "model": "claude-sonnet-5-5",
    "whisper_model": "small",
}

MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]

_lock = threading.Lock()


def ensure_dirs() -> None:
    for p in (DATA, JOBS, BRAND_DIR, MUSIC_DIR):
        p.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_dirs()
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    # environment variables win (useful for servers / .env)
    if os.environ.get("ANTHROPIC_API_KEY"):
        s["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("CLAUDE_MODEL"):
        s["model"] = os.environ["CLAUDE_MODEL"]
    return s


def save_settings(update: dict) -> dict:
    with _lock:
        s = load_settings()
        for k, v in update.items():
            if k in DEFAULT_SETTINGS and v is not None:
                s[k] = v
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        return s


def logo_path() -> Path | None:
    for name in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp"):
        p = BRAND_DIR / name
        if p.exists():
            return p
    default = ASSETS / "brand" / "default_logo.png"
    return default if default.exists() else None


def load_dotenv() -> None:
    """Minimal .env loader (no extra dependency)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
