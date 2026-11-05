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
DOWNLOAD_TB = 0.15          # ~150 GB of weights for both engines (upper bound)
EST_HOURS = 2.0             # expected spike wall time
DISK_GB = 200


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


def search_offers() -> list[dict]:
    q = {"gpu_name": {"eq": "RTX 5090"}, "num_gpus": {"eq": 1}, "verified": {"eq": True},
         "rentable": {"eq": True}, "cpu_ram": {"gte": 58000}, "disk_space": {"gte": DISK_GB},
         "reliability2": {"gte": 0.98}, "cuda_max_good": {"gte": 13.0}, "type": "on-demand",
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
    offers = search_offers()
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
        "client_id": "me", "image": IMAGE, "disk": DISK_GB, "runtype": "ssh", "label": f"phase0-spike-{run_id}",
        "onstart": (HERE / "onstart.sh").read_text(),
        "env": {"GH_REPO": a.repo, "RUN_ID": run_id, "SPIKE_DPH": str(offer["dph_total"]),
                "SPIKE_MAX_SECONDS": str(max_seconds)},
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


def cmd_watch(a) -> None:
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    max_usd, max_hours = st.get("max_usd", a.max_usd), st.get("max_hours", a.max_hours)
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
        reason = over_cap(elapsed, dph, max_usd, max_hours)
        if reason or inst.get("actual_status") in ("exited", "offline"):
            print(f"Destroying: {reason or inst.get('actual_status')}")
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
