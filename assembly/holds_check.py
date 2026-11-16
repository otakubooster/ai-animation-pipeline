"""Predict editor-v2 holds before rendering: shots whose usable footage (clip, clean window or parts, slowed at most
MAX_SLOW) is shorter than their VO, so the editor would freeze the last frame.

  python assembly/holds_check.py --clips-root DIR SLUG...   (clips in DIR/<slug>/clips or DIR/<slug>)
"""
import argparse
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "assembly"))
import edit_v2 as E  # noqa: E402


def holds(slug: str, clips_root: Path, min_s: float = 0.4) -> list[str]:
    jd = R / "jobs" / slug
    clips = clips_root / slug / "clips"
    if not clips.exists():
        clips = clips_root / slug
    shots = json.loads((jd / "job.json").read_text())["shots"]
    tl = E.align_shots(shots, E.spoken_words(jd / "vo/words.json"))
    loc = lambda sid: E.find_clip(clips, sid)  # noqa: E731
    plan = E.share_plan([s for s in shots if not s.get("skip_render") or loc(s["id"])], tl, loc)
    out = []
    for t, sh in zip(tl, shots):
        if sh.get("still"):
            continue
        if sh.get("parts"):
            usable = sum(min(q["end"], E.probe_duration(loc(q["clip"]))) - q["start"] for q in sh["parts"] if loc(q["clip"]))
            how = "parts"
        else:
            sid, head, end = plan.get(sh["id"], (sh["id"], E.HEAD_TRIM, None))
            src = loc(sid)
            if not src:
                out.append(f"  {sh['id']} MISSING clip")
                continue
            usable = min(end or 1e9, E.probe_duration(src)) - head
            how = f"{sid} {head}-{end}"
        held = t["dur"] - usable * E.MAX_SLOW
        if held > min_s:
            out.append(f"  {sh['id']} held {held:.2f}s dur {t['dur']:.1f} usable {usable:.1f} [{how}] "
                       f"talking={sh.get('talking')} | {sh.get('vo_line', '')[:60]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--clips-root", type=Path, required=True)
    a = ap.parse_args()
    for slug in a.slugs:
        h = holds(slug, a.clips_root)
        print(f"{slug}: {len(h)} holds")
        print("\n".join(h))


if __name__ == "__main__":
    main()
