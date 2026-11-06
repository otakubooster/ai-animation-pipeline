"""Word timings for a voiceover whose text is known (our own TTS), offline: pocketsphinx forced alignment per sentence.
Output matches the Scribe words.json the editor reads: {"words": [{"text", "type": "word", "start", "end"}]}.
A sentence that will not align falls back to timings spread by syllable count inside its known span.

  pip install pocketsphinx
  python voice/align.py part.mp3            # needs part.sentences.json from voice/kokoro_tts.py; writes part.words.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ALIGN_SR = 16000
EXTRA = {  # words missing from CMUdict that our scripts use
    "flavour": "F L EY V ER", "flavoured": "F L EY V ER D", "gumline": "G AH M L AY N", "scaler": "S K EY L ER",
    "resorption": "R IY S AO R P SH AH N", "purred": "P ER D", "chemist's": "K EH M AH S T S",
    "mouth's": "M AW TH S", "xray": "EH K S R EY", "meow": "M IY AW", "anaesthetic":
    "AE N AH S TH EH T IH K", "recognise": "R EH K AH G N AY Z", "colour": "K AH L ER", "behaviour": "B IH HH EY V Y ER",
}
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
       "seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def spell(n: int) -> list[str]:
    if n < 20:
        return [ONES[n]]
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [ONES[n // 100], "hundred"] + (spell(n % 100) if n % 100 else [])
    return spell(n // 1000) + ["thousand"] + (spell(n % 1000) if n % 1000 else [])


def to_dict_words(token: str) -> list[str]:
    """'£1,500' -> ['one', 'thousand', 'five', 'hundred', 'pounds']; '80%' -> ['eighty', 'percent']."""
    t = token.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9'%£\-]", "", t).strip("'-")
    if not t:
        return []
    m = re.fullmatch(r"(£?)(\d[\d,]*)(%|am|pm|st|nd|rd|th)?", t)
    if m:
        out = spell(int(m.group(2).replace(",", "")))
        suffix = {"%": ["percent"], "am": ["a", "m"], "pm": ["p", "m"]}.get(m.group(3) or "", [])
        return out + (["pounds"] if m.group(1) else []) + suffix
    return [w for w in t.split("-") if w]


def syllables(word: str) -> int:
    return max(1, len(re.findall(r"[aeiouy]+", word.lower())))


def decoder():
    import pocketsphinx
    from pocketsphinx import Decoder
    model = Path(pocketsphinx.__file__).parent / "model" / "en-us"
    d = Decoder(hmm=str(model / "en-us"), dict=str(model / "cmudict-en-us.dict"), samprate=ALIGN_SR, loglevel="FATAL")
    for w, ph in EXTRA.items():
        try:
            d.add_word(w, ph, True)
        except RuntimeError:            # already in the dictionary
            pass
    return d


def align_sentence(d, pcm: bytes, tokens: list[str]) -> list[tuple[float, float]] | None:
    """(start, end) seconds for each token, relative to the sentence audio; None if alignment fails."""
    groups = [to_dict_words(t) for t in tokens]
    flat = [w for g in groups for w in g]
    if not flat:
        return None
    try:
        d.set_align_text(" ".join(flat))
        d.start_utt(); d.process_raw(pcm, full_utt=True); d.end_utt()
        d.set_alignment()
        d.start_utt(); d.process_raw(pcm, full_utt=True); d.end_utt()
        seg = [(w.name, w.start / 100, (w.start + w.duration) / 100) for w in d.get_alignment()
               if w.name not in ("<s>", "</s>", "<sil>") and not w.name.startswith("+")]
    except Exception:
        return None
    if len(seg) != len(flat):
        return None
    out, i = [], 0
    for g in groups:
        if not g:                       # punctuation-only token: zero-length at the previous end
            p = out[-1][1] if out else 0.0
            out.append((p, p))
            continue
        out.append((seg[i][1], seg[i + len(g) - 1][2]))
        i += len(g)
    return out


def spread(tokens: list[str], start: float, end: float) -> list[tuple[float, float]]:
    weights = [sum(syllables(w) for w in to_dict_words(t)) or 1 for t in tokens]
    total, t, out = sum(weights), start, []
    for w in weights:
        dt = (end - start) * w / total
        out.append((t, t + dt))
        t += dt
    return out


def align(audio: Path, spans: list[dict]) -> dict:
    pcm = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(ALIGN_SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    d, words, fallbacks = decoder(), [], 0
    for sp in spans:
        a, b = int(sp["start"] * ALIGN_SR) * 2, int(sp["end"] * ALIGN_SR) * 2
        tokens = sp["text"].split()
        times = align_sentence(d, pcm[a:b], tokens)
        if times is None:
            fallbacks += 1
            times = [(s - sp["start"], e - sp["start"]) for s, e in spread(tokens, sp["start"], sp["end"])]
        for tok, (s, e) in zip(tokens, times):
            words.append({"text": tok, "type": "word", "start": round(sp["start"] + s, 3), "end": round(sp["start"] + e, 3)})
    return {"words": words, "aligner": "pocketsphinx", "fallback_sentences": fallbacks, "sentences": len(spans)}


def main(path: str) -> None:
    audio = Path(path)
    spans = json.loads(audio.with_suffix(".sentences.json").read_text())
    res = align(audio, spans)
    out = audio.with_suffix(".words.json")
    out.write_text(json.dumps(res, ensure_ascii=False))
    print(f"{out}: {len(res['words'])} words, {res['fallback_sentences']}/{res['sentences']} sentences spread")


if __name__ == "__main__":
    main(sys.argv[1])
