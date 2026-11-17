"""Check the Claude request/response handling with a fake client (no network, no key).

    python -m tests.test_planner_mock
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

from app import planner


class FakeMessages:
    def __init__(self, reply: dict):
        self.reply = reply
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        block = types.SimpleNamespace(type="tool_use", name="submit_edit_plan", input=self.reply)
        return types.SimpleNamespace(content=[block], stop_reason="tool_use",
                                     usage=types.SimpleNamespace(input_tokens=1234, output_tokens=321))


def main() -> int:
    d = Path(tempfile.mkdtemp())
    (d / "frames").mkdir()
    (d / "frames" / "c1_000150.jpg").write_bytes(b"\xff\xd8fakejpeg")
    clips = [
        {"id": "c1", "name": "a.mp4", "kind": "video", "duration": 9.0, "has_audio": True, "orientation": "portrait",
         "scenes": [[0, 3], [3, 9]], "frames": [{"file": "c1_000150.jpg", "t": 1.5}],
         "transcript": [{"start": 0.5, "end": 2, "text": "ສະບາຍດີ"}]},
        {"id": "c2", "name": "b.jpg", "kind": "image", "duration": 0, "orientation": "landscape", "frames": []},
    ]
    reply = {  # deliberately messy AI output to test normalisation
        "summary": "ທົດສອບ", "title": "t",
        "segments": [
            {"clip": "c1", "start": 3.2, "end": 5.0, "transition": "cut", "speed": 1},
            {"clip": "c9", "start": 0, "end": 2},                       # unknown clip -> dropped
            {"clip": "c1", "start": 8.8, "end": 20, "transition": "boom"},  # end clamped, bad transition -> cut
            {"clip": "c2", "start": 0, "end": 3, "effect": "zoom_in", "transition": "fade"},
            {"clip": "c1", "start": 1, "end": 1.1},                      # too short -> extended to 0.4 s
        ],
        "texts": [{"text": "ປ້າຍໄຟ LED", "at": 0.3, "duration": 2, "position": "middle", "style": "huge"}],
        "music": {"file": "nope.mp3", "volume": 3},
        "outro": {"show": True, "headline": "ສັ່ງເລີຍ", "duration": 30},
    }
    fake = FakeMessages(reply)
    import anthropic

    orig = anthropic.Anthropic
    anthropic.Anthropic = lambda **kw: types.SimpleNamespace(messages=fake)  # type: ignore
    try:
        raw = planner.plan_with_claude(clips, d, {"aspect": "9:16", "duration": 15, "captions": True},
                                       {"api_key": "x", "model": "claude-sonnet-5-5", "shop_name": "X Media"},
                                       [], "ໂຄສະນາ 15 ວິ")
        raw2 = planner.plan_with_claude(clips, d, {"aspect": "9:16", "duration": 15}, {"api_key": "x"}, [],
                                        "ສັ້ນລົງ", previous=raw)
    finally:
        anthropic.Anthropic = orig
    kw = fake.kwargs
    json.dumps(kw)  # request must be JSON-serialisable
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_edit_plan"}
    kinds = [b["type"] for b in kw["messages"][0]["content"]]
    assert "image" in kinds and kinds.count("text") >= 3, kinds
    assert "Previous plan" in json.dumps(kw, ensure_ascii=False)
    plan, warnings = planner.normalize(raw, clips, [])
    assert [s["clip"] for s in plan["segments"]] == ["c1", "c1", "c2", "c1"], plan["segments"]
    assert plan["segments"][3]["end"] - plan["segments"][3]["start"] >= 0.399
    s2 = plan["segments"][1]
    assert s2["end"] <= 9.0 and s2["transition"] == "cut" and s2["end"] - s2["start"] >= 0.35, s2
    assert plan["segments"][2]["end"] == 3 and plan["segments"][2]["effect"] == "zoom_in"
    assert plan["texts"][0]["position"] == "top" and plan["texts"][0]["style"] == "title"
    assert plan["music"] == {"file": "", "volume": 1.0}
    assert plan["outro"]["duration"] == 6.0
    assert raw2["_source"] == "claude" and raw["_usage"]["input_tokens"] == 1234
    print("planner mock OK | warnings:", warnings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
