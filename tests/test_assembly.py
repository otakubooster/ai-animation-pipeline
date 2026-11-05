"""Unit tests for the ad editor's timing logic (no ffmpeg needed)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assembly"))
import edit_ad  # noqa: E402


def W(text, start, end):
    return {"text": text, "type": "word", "start": start, "end": end}


def test_spoken_words_drops_elevenlabs_audio_tags(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"words": [W("[pained,", 0, 0.03), W("quietly]", 0.04, 0.07), W("I", 0.08, 0.16),
                                       W("[sad]", 0.2, 0.25), W("hurt.", 0.3, 0.6)]}))
    assert [w["text"] for w in edit_ad.spoken_words(p)] == ["I", "hurt."]


def test_align_shots_cuts_on_next_line_start():
    shots = [{"id": "s01", "vo_line": "I was in pain,"}, {"id": "s02", "vo_line": "so I stopped."}]
    words = [W("I", 0.1, 0.2), W("was", 0.3, 0.4), W("in", 0.5, 0.6), W("pain,", 0.7, 1.0),
             W("So", 1.4, 1.5), W("I", 1.6, 1.7), W("stopped.", 1.8, 2.2)]
    t = edit_ad.align_shots(shots, words)
    assert t[0]["start"] == 0.0 and t[0]["end"] == pytest.approx(1.35)
    assert t[1]["end"] == pytest.approx(3.2)          # last line holds 1 s after the final word


def test_align_shots_reports_script_audio_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        edit_ad.align_shots([{"id": "s01", "vo_line": "hello there"}], [W("hello", 0, 1), W("world", 1, 2)])


def test_captions_highlight_one_word_and_move_up_on_product_shots(tmp_path):
    p = tmp_path / "c.ass"
    words = [W("Buy", 0.0, 0.3), W("two", 0.4, 0.6), W("now.", 0.7, 1.0)]
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, high_spans=[(0.35, 2.0)], margin_high=1000)
    events = [l for l in p.read_text().splitlines() if l.startswith("Dialogue")]
    assert len(events) == 3 and all(e.count("\\c&H00E5FF&") == 1 for e in events)
    assert events[0].split(",")[7] == "0" and events[1].split(",")[7] == "1000"
