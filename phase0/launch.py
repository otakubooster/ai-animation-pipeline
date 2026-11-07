"""Phase 0 launcher: runs on the orchestrating machine (no GPU needed).

  python phase0/launch.py offers                         # rank live 1x RTX 5090 offers by estimated spike cost
  python phase0/launch.py create --offer ID --yes        # rent it (paid!), with spend + time caps
  python phase0/launch.py watch ID                       # poll; destroy when over cap or finished
  python phase0/launch.py destroy ID

Cost guardrails: --max-usd (default 5) and --max-hours (default 2.5) are enforced twice: by `watch`
(destroys the instance) and on the box itself (run_spike.sh hard timeout + self-destroy).
Reads VAST_API_KEY from the environment (in the cloud session a proxy injects it, any placeholder works).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://console.vast.ai/api/v0"
IMAGE = "lightx2v/lightx2v:26052801-cu130-5090"
HERE = Path(__file__).resolve().parent
STATE = HERE / ".launch_state.json"
DOWNLOAD_TB = 0.05          # Engine A batch: ~15 GB image + ~30 GB weights (measured 2026-11-06)
EST_HOURS = 2.0             # expected spike wall time
DISK_GB = 120               # ~40 GB image + 29 GB Engine A weights + clips and their push copy (80 GB filled up on 2026-11-06)
MIN_DOWN_MBPS = 600         # slow links cost more in idle GPU time than they save (Nepal 2026-11-06: ~2 MB/s real)
MIN_DISK_MBPS = 1000


def call(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("VAST_API_KEY", "proxy-injected")
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Vast API {method} {path} -> {e.code}: {e.read().decode()[:500]}")


def est_cost(o: dict, hours: float = EST_HOURS) -> float:
    """Estimated spike cost: compute + storage for the run, plus weight download. Slow links add hours."""
    down_mbps = max(o.get("inet_down") or 1, 1)
    dl_hours = DOWNLOAD_TB * 8e6 / down_mbps / 3600
    dl_cost = DOWNLOAD_TB * 1024 * (o.get("inet_down_cost") or 0)  # inet_down_cost is $/GB
    return round((hours + dl_hours) * o["dph_total"] + dl_cost, 2)


def search_offers(disk: int = DISK_GB) -> list[dict]:
    q = {"gpu_name": {"eq": "RTX 5090"}, "num_gpus": {"eq": 1}, "verified": {"eq": True},
         "rentable": {"eq": True}, "cpu_ram": {"gte": 58000}, "disk_space": {"gte": disk},
         "reliability2": {"gte": 0.99}, "inet_down": {"gte": MIN_DOWN_MBPS}, "disk_bw": {"gte": MIN_DISK_MBPS}, "cuda_max_good": {"gte": 13.0}, "type": "on-demand",
         "order": [["dph_total", "asc"]], "limit": 60}
    offers = [o for o in call("POST", "/bundles/", q)["offers"] if not str(o.get("geolocation", "")).endswith("CN")]
    return sorted(offers, key=est_cost)


def over_cap(elapsed_s: float, dph: float, max_usd: float, max_hours: float) -> str | None:
    spent = elapsed_s / 3600 * dph
    if spent >= max_usd:
        return f"spend ${spent:.2f} >= cap ${max_usd:.2f}"
    if elapsed_s >= max_hours * 3600:
        return f"runtime {elapsed_s/3600:.2f} h >= cap {max_hours} h"
    return None


def cmd_offers(_a) -> None:
    for o in search_offers()[:12]:
        print(f"{o['id']:>9}  {str(o.get('geolocation'))[:20]:20} ${o['dph_total']:.3f}/h  "
              f"down {o.get('inet_down', 0):>5.0f} Mbps  dl ${(o.get('inet_down_cost') or 0)*1024:5.2f}/TB  "
              f"disk {o.get('disk_bw', 0):>5.0f} MB/s  ram {o.get('cpu_ram', 0)/1024:.0f} GB  "
              f"rel {o.get('reliability2', 0)*100:.1f}%  est spike ${est_cost(o):.2f}")


def cmd_create(a) -> None:
    if not a.yes:
        raise SystemExit("Refusing to rent without --yes (this costs money).")
    offers = search_offers(a.disk)
    offers = [o for o in offers if not any(x.lower() in str(o.get("geolocation", "")).lower() for x in a.avoid)]
    if a.offer:
        offers = [o for o in offers if o["id"] == a.offer]
    else:  # --best: try the cheapest few by estimated total cost, skipping any over the cost cap
        offers = [o for o in offers if est_cost(o) <= a.max_usd][: a.tries]
    if not offers:
        raise SystemExit("No matching offer available; run `offers` again.")
    for offer in offers:
        try:
            return _create(a, offer)
        except SystemExit as exc:
            print(f"offer {offer['id']} failed: {exc}")
    raise SystemExit("All candidate offers failed.")


def _create(a, offer: dict) -> None:
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    max_seconds = int(min(a.max_hours, a.max_usd / offer["dph_total"]) * 3600) - 300  # box stops 5 min early
    body = {
        "client_id": "me", "image": IMAGE, "disk": a.disk, "runtype": "ssh", "label": f"phase0-spike-{run_id}",
        "onstart": (HERE / "onstart.sh").read_text(),
        "env": {"GH_REPO": a.repo, "RUN_ID": run_id, "SPIKE_DPH": str(offer["dph_total"]),
                "SPIKE_MAX_SECONDS": str(max_seconds), "RUN_SCRIPT": a.script, **({"JOBS": " ".join(a.job)} if a.job else {}),
                **dict(kv.split("=", 1) for kv in a.env)},
    }
    res = call("PUT", f"/asks/{offer['id']}/", body)
    iid = res.get("new_contract")
    if not iid:
        raise SystemExit(f"Create failed: {res}")
    STATE.write_text(json.dumps({"instance": iid, "offer": offer["id"], "run_id": run_id, "dph": offer["dph_total"],
                                 "max_usd": a.max_usd, "max_hours": a.max_hours, "created": time.time()}, indent=2))
    print(f"Created instance {iid} on offer {offer['id']} ({offer.get('geolocation')}) (run {run_id}) at ${offer['dph_total']:.3f}/h; box self-stops after {max_seconds/3600:.2f} h.")


def get_instance(iid: int, retries: int = 5) -> dict | None:
    """Return the instance, None if Vast says it no longer exists. Transient API errors are retried."""
    for attempt in range(retries):
        try:
            return call("GET", f"/instances/{iid}/").get("instances") or None
        except SystemExit as exc:
            if "-> 404" in str(exc):
                return None
            time.sleep(10 * (attempt + 1))
        except OSError:
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Vast API unreachable after {retries} tries; instance {iid} state unknown")


def cmd_destroy(a) -> None:
    print(call("DELETE", f"/instances/{a.instance}/"))


EXITED_GRACE_S = 600  # an instance must stay exited/offline this long before watch destroys it


MAX_LOADING_S = 900         # a host that needs >15 min to pull the image is too slow (Taiwan 2026-11-06: 30+ min)


def should_destroy(status: str | None, exited_since: float | None, now: float, cap_reason: str | None,
                   loading_s: float = 0.0) -> str | None:
    """Caps always win. A stopped/exited state is only acted on after EXITED_GRACE_S, because instances
    report 'exited' briefly while starting or restarting. A box still loading after MAX_LOADING_S is dropped."""
    if cap_reason:
        return cap_reason
    if status in (None, "loading", "created") and loading_s >= MAX_LOADING_S:
        return f"still loading after {int(loading_s)} s (slow host)"
    if status in ("exited", "offline") and exited_since is not None and now - exited_since >= EXITED_GRACE_S:
        return f"{status} for {int(now - exited_since)} s"
    return None


def cmd_watch(a) -> None:
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    max_usd, max_hours = st.get("max_usd", a.max_usd), st.get("max_hours", a.max_hours)
    exited_since = None
    while True:
        inst = get_instance(a.instance)
        if not inst:
            print("Instance gone (destroyed).")
            return
        start = inst.get("start_date") or st.get("created") or time.time()
        elapsed = time.time() - start
        dph = inst.get("dph_total") or st.get("dph", 0)
        print(f"{time.strftime('%H:%M:%S')} status={inst.get('actual_status')} msg={str(inst.get('status_msg'))[:80]!r} "
              f"elapsed={elapsed/60:.1f} min spent≈${elapsed/3600*dph:.2f}", flush=True)
        status = inst.get("actual_status")
        exited_since = (exited_since or time.time()) if status in ("exited", "offline") else None
        if status == "running":
            st["ran"] = True
        loading_s = 0.0 if st.get("ran") else time.time() - st.get("created", time.time())
        reason = should_destroy(status, exited_since, time.time(), over_cap(elapsed, dph, max_usd, max_hours), loading_s)
        if reason:
            print(f"Destroying: {reason}")
            call("DELETE", f"/instances/{a.instance}/")
            return
        time.sleep(a.every)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("offers").set_defaults(fn=cmd_offers)
    c = sub.add_parser("create")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--offer", type=int)
    g.add_argument("--best", action="store_true", help="rent the cheapest live offer by est. total cost")
    c.add_argument("--tries", type=int, default=4)
    c.add_argument("--max-usd", type=float, default=5.0)
    c.add_argument("--max-hours", type=float, default=2.5)
    c.add_argument("--repo", default="otakubooster/ai-animation-pipeline")
    c.add_argument("--yes", action="store_true")
    c.add_argument("--env", nargs="*", default=[], help="extra KEY=VALUE for the box (e.g. SKIP_TALK=1 TALK_ONLY=1)")
    c.add_argument("--disk", type=int, default=DISK_GB, help="GB of disk to rent (only what the batch needs)")
    c.add_argument("--avoid", nargs="*", default=["Nepal", "Japan", "Korea", "Taiwan", "Hong Kong", "China"], help="skip offers whose location contains any of these (slow hosts)")
    c.add_argument("--script", default="phase0/run_spike.sh", help="repo-relative script the box runs")
    c.add_argument("--job", nargs="*", default=[], help="job folder(s) for jobs/run_job.sh; several = one batch on one GPU")
    c.set_defaults(fn=cmd_create)
    w = sub.add_parser("watch")
    w.add_argument("instance", type=int)
    w.add_argument("--every", type=int, default=60)
    w.add_argument("--max-usd", type=float, default=5.0)
    w.add_argument("--max-hours", type=float, default=2.5)
    w.set_defaults(fn=cmd_watch)
    d = sub.add_parser("destroy")
    d.add_argument("instance", type=int)
    d.set_defaults(fn=cmd_destroy)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
