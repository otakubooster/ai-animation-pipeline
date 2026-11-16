"""Word timings for a take whose text is known exactly (our own ElevenLabs narration), without a transcription call.

The take is cut into sentences at its pauses (the silences nearest to where each sentence boundary is expected from the
text lengths, chosen in order by dynamic programming, as voice/split_take.py does for segments); inside a sentence the
words are spread over its speech span in proportion to their letters. Output matches the Scribe words.json the editor
reads, with the script's own tokens as text, so captions match the script and the editor's transcript check passes.
A "[pause]" in the text marks a segment boundary (several lines of one speaker in one take); each is emitted as a
{"text": "[pause]", "type": "spacing"} entry at its cut, like Scribe does, so the take can be cut into lines.

  python voice/known_words.py TAKE.mp3 TEXT.txt OUT.words.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_take import duration, silences  # noqa: E402

END = re.compile(r"[.!?:…][\"'“”„«»)]*$")


def letters(tok: str) -> int:
    return len(re.sub(r"\W", "", tok)) + 1


def sentences(text: str) -> list[list[str]]:
    """Token lists, one per sentence; a [pause] marker becomes its own one-token 'sentence'."""
    out, cur = [], []
    for tok in text.split():
        if tok == "[pause]":
            if cur:
                out.append(cur); cur = []
            out.append(["[pause]"])
            continue
        cur.append(tok)
        if END.search(tok):
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return out


def choose_cuts(texts: list[str], sil: list[tuple[float, float]], lead: float, tail: float) -> list[tuple[float, float]]:
    """Pick len(texts)-1 silences in order so that every sentence's duration is close to rate x its letters
    (local durations, so an early error does not drift into the rest) and long pauses are preferred."""
    w = [letters(t) for t in texts]
    k, n = len(w) - 1, len(sil)
    rate = (tail - lead) * 0.88 / sum(w)
    INF = float("inf")

    def seg_cost(i, a, b):
        exp = rate * w[i]
        return ((b - a - exp) / (0.3 * exp + 0.3)) ** 2

    # cost[i][j]: sentences 0..i placed, sentence i ends at silence j (j=-1 is impossible for i>=0)
    cost = [[INF] * n for _ in range(k)]
    back = [[-1] * n for _ in range(k)]
    for j in range(n):
        cost[0][j] = seg_cost(0, lead, sil[j][0]) - 3.0 * (sil[j][1] - sil[j][0])
    for i in range(1, k):
        for j in range(i, n):
            pen = -3.0 * (sil[j][1] - sil[j][0])
            for jj in range(i - 1, j):
                if cost[i - 1][jj] == INF:
                    continue
                c = cost[i - 1][jj] + seg_cost(i, sil[jj][1], sil[j][0]) + pen
                if c < cost[i][j]:
                    cost[i][j], back[i][j] = c, jj
    j = min(range(k - 1, n), key=lambda jj: cost[k - 1][jj] + seg_cost(k, sil[jj][1], tail))
    picks = []
    for i in range(k - 1, -1, -1):
        picks.append(sil[j]); j = back[i][j]
    return picks[::-1]


def words_for(take: Path, text: str, noise_db: int = -32, min_sil: float = 0.12) -> list[dict]:
    sents = [s for s in sentences(text)]
    spoken = [s for s in sents if s != ["[pause]"]]
    pause_after = set()            # index in `spoken` after which a [pause] marker sits
    k = -1
    for s in sents:
        if s == ["[pause]"]:
            pause_after.add(k)
        else:
            k += 1
    total = duration(take)
    sil = silences(take, noise_db, min_sil)
    lead = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    tail = sil[-1][0] if sil and sil[-1][1] > total - 0.05 else total
    inner = [s for s in sil if s[0] > lead + 0.05 and s[1] < tail - 0.05]
    texts = [" ".join(s) for s in spoken]
    if len(spoken) > 1:
        # boundaries next to a [pause] marker count double (they are the long gaps)
        if len(inner) < len(spoken) - 1:
            raise ValueError(f"{take.name}: {len(inner)} pauses for {len(spoken) - 1} sentence boundaries")
        cuts = choose_cuts(texts, inner, lead, tail)
    else:
        cuts = []
    spans, start = [], lead
    for a, b in cuts:
        spans.append((start, a)); start = b
    spans.append((start, tail))
    out = []
    for i, (toks, (a, b)) in enumerate(zip(spoken, spans)):
        w = [letters(t) for t in toks]
        t, tot = a, sum(w)
        for tok, x in zip(toks, w):
            d = (b - a) * x / tot
            out.append({"text": tok, "type": "word", "start": round(t, 3), "end": round(t + d * 0.92, 3)})
            t += d
        if i in pause_after and i + 1 < len(spans):
            out.append({"text": "[pause]", "type": "spacing", "start": round(b, 3), "end": round(spans[i + 1][0], 3)})
    return out


if __name__ == "__main__":
    ws = words_for(Path(sys.argv[1]), Path(sys.argv[2]).read_text())
    Path(sys.argv[3]).write_text(json.dumps({"words": ws, "source": "known_words (script text, pause-cut sentences)"},
                                            ensure_ascii=False, indent=0))
    print(f"{len([w for w in ws if w['type'] == 'word'])} words, {ws[-1]['end']:.1f} s")
