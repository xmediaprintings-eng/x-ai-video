"""ASS subtitle builder for on-screen texts, captions and outro (Lao/Thai/English safe).

libass + HarfBuzz shape Lao and Thai correctly; we only need our own line breaking
because Lao/Thai do not put spaces between words.
"""
from __future__ import annotations

import re
import unicodedata

from .config import FONT_NAME

LEADING_VOWELS = set("เแโใไເແໂໃໄ")  # written before the consonant: never end a line on them
FOLLOWING = set("ะาำๅๆฯະາຳໆ")  # never start a line with them
# measured: rendered width per visible char ~0.31-0.36 x ASS font size (Noto, bold); keep a margin
CHAR_W = 0.38


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    return text


def units(s: str) -> int:
    """Visible width units: combining marks (Lao/Thai vowels & tones above/below) take no width."""
    return sum(0 if unicodedata.category(c) in ("Mn", "Me", "Cf") else 1 for c in s)


def _safe_break(s: str, i: int) -> bool:
    if i <= 0 or i >= len(s):
        return False
    c, prev = s[i], s[i - 1]
    if unicodedata.category(c) in ("Mn", "Me") or c in FOLLOWING:
        return False
    if prev in LEADING_VOWELS:
        return False
    return True


def _hard_split(word: str, max_units: int) -> list[str]:
    parts, cur = [], ""
    for i, ch in enumerate(word):
        if cur and units(cur) >= max_units and _safe_break(word, i):
            parts.append(cur)
            cur = ""
        cur += ch
    if cur:
        parts.append(cur)
    return parts


def wrap(text: str, max_units: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for word in para.split():
            pieces = _hard_split(word, max_units) if units(word) > max_units else [word]
            for p in pieces:
                cand = f"{cur} {p}" if cur else p
                if units(cand) <= max_units:
                    cur = cand
                else:
                    if cur:
                        lines.append(cur)
                    cur = p
        if cur:
            lines.append(cur)
    return lines


def fit(text: str, width_px: int, size: float, max_lines: int, min_size: float) -> tuple[list[str], float]:
    """Wrap text and shrink font until it fits within max_lines."""
    while True:
        max_units = max(4, int(width_px / (size * CHAR_W)))
        lines = wrap(text, max_units)
        if len(lines) <= max_lines or size <= min_size:
            return lines[: max_lines + 1], size
        size *= 0.9


def balanced_chunks(text: str, max_units: int) -> list[str]:
    """Split a long caption into screen-sized chunks (2 lines each)."""
    lines = wrap(text, max_units)
    return [" ".join(lines[i:i + 2]) for i in range(0, len(lines), 2)] or [text]


# ------------------------------------------------------------------ builder
class AssBuilder:
    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.u = min(W, H) / 1080.0
        self.portrait = H > W * 1.1
        self.events: list[str] = []
        u = self.u
        white, black = "#ffffff", "#000000"
        yellow, orange, red, ink, muted = "#ffc400", "#ff7a00", "#e8231b", "#120800", "#c9b9a6"
        # name, size, primary, outline colour, back colour, bold, borderstyle, outline, shadow
        self.styles = {
            "title": (124 * u, white, black, black, 1, 1, 7 * u, 3 * u),
            "subtitle": (92 * u, yellow, black, black, 1, 1, 6 * u, 2 * u),
            "badge": (80 * u, ink, orange, orange, 1, 3, 16 * u, 0),
            "price": (104 * u, white, red, red, 1, 3, 18 * u, 0),
            "caption": (76 * u, white, black, black, 1, 1, 6 * u, 2 * u),
            "o_shop": (150 * u, white, black, black, 1, 1, 0, 0),
            "o_tag": (64 * u, muted, black, black, 0, 1, 0, 0),
            "o_head": (100 * u, yellow, black, black, 1, 1, 0, 0),
            "o_phone": (84 * u, white, black, black, 1, 1, 0, 0),
            "o_addr": (54 * u, muted, black, black, 0, 1, 0, 0),
        }

    def header(self) -> str:
        fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
               "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
               "Alignment, MarginL, MarginR, MarginV, Encoding")
        rows = []
        m = int(self.W * 0.06)
        for name, (size, prim, outl, back, bold, bs, outline, shadow) in self.styles.items():
            rows.append(
                f"Style: {name},{FONT_NAME},{size:.0f},{_ass_color(prim)},&H000000FF,{_ass_color(outl)},"
                f"{_ass_color(back, 0x60)},{-1 if bold else 0},0,0,0,100,100,0,0,{bs},{outline:.1f},{shadow:.1f},"
                f"5,{m},{m},0,1"
            )
        return (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {self.W}\nPlayResY: {self.H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            f"[V4+ Styles]\n{fmt}\n" + "\n".join(rows) + "\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    def _event(self, start: float, end: float, style: str, text: str, layer: int = 1) -> None:
        if end - start < 0.05:
            return
        self.events.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    # y positions (centre of the text block) for top / center / bottom placements
    def _y(self, position: str, captions_on: bool) -> float:
        H = self.H
        if position == "top":
            return H * (0.17 if self.portrait else 0.14)
        if position == "center":
            return H * 0.5
        # bottom: keep above Reels/TikTok UI and above captions
        if self.portrait:
            return H * (0.66 if captions_on else 0.72)
        return H * (0.70 if captions_on else 0.82)

    def add_text(self, text: str, start: float, end: float, position: str, style: str, captions_on: bool) -> None:
        text = escape(text).strip()
        if not text:
            return
        size0 = self.styles.get(style, self.styles["title"])[0]
        width = self.W * (0.84 if style in ("badge", "price") else 0.88)
        lines, size = fit(text, width, size0, max_lines=3, min_size=size0 * 0.55)
        x, y = self.W / 2, self._y(position, captions_on)
        anim = {
            "title": r"\fad(150,200)\fscx82\fscy82\t(0,220,\fscx100\fscy100)",
            "subtitle": r"\fad(200,200)",
            "badge": r"\fad(120,150)\fscx70\fscy70\t(0,180,\fscx100\fscy100)",
            "price": r"\fad(120,150)\fscx70\fscy70\t(0,200,\fscx108\fscy108)\t(200,320,\fscx100\fscy100)",
        }.get(style, r"\fad(150,150)")
        tag = f"{{\\an5\\pos({x:.0f},{y:.0f})\\fs{size:.0f}{anim}}}"
        self._event(start, end, style if style in self.styles else "title", tag + r"\N".join(lines), layer=2)

    def add_caption(self, text: str, start: float, end: float) -> None:
        text = escape(text).replace("\n", " ").strip()
        if not text:
            return
        size = self.styles["caption"][0]
        max_units = max(8, int(self.W * 0.86 / (size * CHAR_W)))
        chunks = balanced_chunks(text, max_units)
        total_units = sum(max(1, units(c)) for c in chunks)
        t = start
        y = self.H * (0.78 if self.portrait else 0.88)
        for c in chunks:
            d = (end - start) * max(1, units(c)) / total_units
            lines = wrap(c, max_units)
            self._event(t, t + d, "caption", f"{{\\an5\\pos({self.W / 2:.0f},{y:.0f})}}" + r"\N".join(lines), layer=1)
            t += d

    def add_outro(self, start: float, end: float, brand: dict, headline: str, logo_bottom_y: float) -> None:
        W, H = self.W, self.H
        phones = [p.strip() for p in re.split(r"[\n/,·|]+", brand.get("phones", "")) if p.strip()][:4]
        rows: list[tuple[str, str, int, int, float]] = []  # style, text, delay, max_lines, gap_after
        rows.append(("o_shop", brand.get("shop_name", ""), 0, 1, 0.01))
        rows.append(("o_tag", brand.get("tagline", ""), 120, 2, 0.03))
        rows.append(("o_head", headline, 250, 2, 0.03))
        for i, ph in enumerate(phones):
            rows.append(("o_phone", ("ໂທ: " + ph) if i == 0 else ph, 400 + i * 100, 1, 0.006))
        rows.append(("o_addr", brand.get("address", ""), 750, 2, 0))
        rows = [r for r in rows if escape(r[1]).strip()]

        def layout(k: float):
            out, y = [], logo_bottom_y + H * 0.05 * k
            for style, text, delay, ml, gap in rows:
                size0 = self.styles[style][0] * k
                lines, size = fit(escape(text).strip(), W * 0.88, size0, max_lines=ml, min_size=size0 * 0.5)
                block = size * 1.1 * len(lines)
                out.append((style, lines, size, y + block / 2, delay))
                y += block + H * gap * k
            return out, y

        k = 1.0
        placed, bottom = layout(k)
        while bottom > H * 0.95 and k > 0.4:
            k *= 0.92
            placed, bottom = layout(k)
        for style, lines, size, yc, delay in placed:
            tag = (f"{{\\an5\\pos({W / 2:.0f},{yc:.0f})\\fs{size:.0f}\\alpha&HFF&"
                   f"\\t({delay},{delay + 250},\\alpha&H00&)}}")
            self._event(start, end, style, tag + r"\N".join(lines), layer=3)

    def render(self) -> str:
        return self.header() + "\n".join(self.events) + "\n"
