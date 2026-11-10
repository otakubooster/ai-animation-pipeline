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


def test_subtitle_style_one_event_per_group_no_highlight(tmp_path):
    p = tmp_path / "s.ass"
    words = [W("Her", 0.0, 0.2), W("blood", 0.3, 0.5), W("tests", 0.6, 0.8), W("were", 0.9, 1.0), W("perfect.", 1.1, 1.5)]
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, max_words=4, style="subtitle")
    events = [l for l in p.read_text().splitlines() if l.startswith("Dialogue")]
    assert [e.split(",,")[-1] for e in events] == ["Her blood tests were", "perfect."]
    assert "\\c&H00E5FF&" not in p.read_text() and "Inter Display Bold" in p.read_text()


def test_music_only_shot_gets_a_fixed_slot_at_the_end():
    shots = [{"id": "s01", "vo_line": "Start it tomorrow."}, {"id": "s02", "vo_line": "(music only)"}]
    words = [W("Start", 0.0, 0.3), W("it", 0.4, 0.5), W("tomorrow.", 0.6, 1.2)]
    t = edit_ad.align_shots(shots, words)
    assert t[0]["end"] == pytest.approx(1.6) and t[1]["dur"] == pytest.approx(3.0)


def test_spoken_text_drops_directions_and_quotes():
    assert edit_ad.spoken_text("\"I'm Tartar! Ha ha!\" (laughs)") == "I'm Tartar! Ha ha!"


def test_hook_card_has_red_phrase(tmp_path):
    from PIL import Image
    from edit_ad import hook_png
    p = tmp_path / "hook.png"
    hook_png("3 mistakes that shorten your cat's life…", "3 mistakes", p, 1080)
    im = Image.open(p).convert("RGB")
    assert im.width <= 1092
    reds = sum(1 for r, g, b in im.getdata() if r > 180 and g < 60 and b < 80)
    assert reds > 500


def test_share_plan_splits_a_clip_between_shots():
    shots = [{"id": "s1"}, {"id": "s2", "clip_from": "s3"}, {"id": "s3"}, {"id": "s4", "fill_from": "s3"}]
    tl = [{"id": "s1", "dur": 2.0}, {"id": "s2", "dur": 1.0}, {"id": "s3", "dur": 2.0}, {"id": "s4", "dur": 1.0}]
    plan = edit_ad.share_plan(shots, tl, locate=lambda sid: Path(sid), length=lambda p: 6.5)
    assert plan["s1"] == ("s1", 0.5, None)
    # plenty of clip: consecutive pieces at real speed, in timeline order
    assert plan["s2"] == ("s3", 0.5, 1.5) and plan["s3"] == ("s3", 1.5, 3.5) and plan["s4"] == ("s3", 3.5, 4.5)
    short = edit_ad.share_plan(shots, tl, locate=lambda sid: Path(sid), length=lambda p: 2.5)
    assert short["s3"] == ("s3", 1.0, 2.0)          # 2 s usable for 4 s of lines: proportional halves


def test_caption_presets_caps_one_word_and_boxed(tmp_path):
    words = [W("Your", 0.0, 0.2), W("cat's", 0.3, 0.5), W("teeth.", 0.6, 0.9)]
    p = tmp_path / "a.ass"
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, style="caps-outline")
    ev = [l.split(",,")[-1] for l in p.read_text().splitlines() if l.startswith("Dialogue")]
    assert ev == ["YOUR CAT'S TEETH"]
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, style="one-word")
    assert len([l for l in p.read_text().splitlines() if l.startswith("Dialogue")]) == 3
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, style="phrase-key-red")
    assert "\\c&H2E10C8&" in p.read_text()
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, style="boxed")
    assert ",3,12," in p.read_text()       # opaque box border style


def test_on_screen_text_splits_parts_in_time(tmp_path):
    p = tmp_path / "o.ass"
    edit_ad.on_screen_ass([(0.0, 4.0, "First line / Second line"), (4.0, 6.0, "")], p, (1080, 1920), 70)
    ev = [l for l in p.read_text().splitlines() if l.startswith("Dialogue")]
    assert len(ev) == 2 and ev[0].endswith("First line") and "0:00:02.05" in ev[1]


def test_norm_keeps_arabic_words_and_drops_diacritics_and_punctuation():
    assert edit_ad.norm("الملين!") == "الملين"
    assert edit_ad.norm("تهدّنّي،") == "تهدني"
    assert edit_ad.norm("…") == ""
    assert edit_ad.norm("Don't,") == "don't"


def test_align_shots_matches_darija_lines():
    shots = [{"id": "s001", "vo_line": "شوفو شنو كتصيفطو ليا… الملين!"}, {"id": "s002", "vo_line": "النخالة؟ كتزيد النفخة"}]
    toks = "شوفو شنو كتصيفطو ليا… الملين! النخالة؟ كتزيد النفخة".split()
    words = [W(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(toks)]
    tl = edit_ad.align_shots(shots, words)
    assert [len(t["words"]) for t in tl] == [5, 3]


def test_arabic_caption_preset_is_rtl_bold_at_the_top(tmp_path):
    p = tmp_path / "c.ass"
    words = [W("شوفو", 0, 0.4), W("شنو", 0.4, 0.8), W("ليا…", 0.8, 1.2), W("الملين!", 1.3, 1.8)]
    edit_ad.captions_ass(words, p, (1080, 1920), 84, 470, style="ar-key-yellow")
    txt = p.read_text()
    style = next(l for l in txt.splitlines() if l.startswith("Style: Cap"))
    assert "Noto Kufi Arabic" in style and style.endswith(",-1")       # base direction auto (RTL)
    assert ",8,80,80,211," in style                                       # top centre, 11% down
    assert "ليا\\N" not in txt and "ليا…" not in txt                       # group ends at the ellipsis, stripped


def test_banner_png_renders_arabic(tmp_path):
    p = tmp_path / "b.png"
    edit_ad.banner_png("شريو جوج وخودو وحدة فابور 🎁", p, 1080)
    from PIL import Image
    assert Image.open(p).width > 400
