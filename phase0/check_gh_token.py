"""Check that the GH_TOKEN stored in the Vast account still works, on the cheapest tiny instance (~$0.01).

GPU boxes can only send results back through GitHub; an expired token cost a whole batch launch on 2026-11-13.
Run this before a big launch:   python3 phase0/check_gh_token.py      -> prints API 200 (ok) or 401 (renew it)
"""
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import launch  # noqa: E402

ONSTART = r'''#!/bin/bash
exec >> /proc/1/fd/1 2>&1
[ -f /etc/environment ] && set -a && . /etc/environment && set +a
[ -n "${GH_TOKEN2:-}" ] && GH_TOKEN="$GH_TOKEN2"
apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq curl >/dev/null 2>&1
echo "GHTOKEN_LEN ${#GH_TOKEN} ${GH_TOKEN:0:4}"; curl -s -o /dev/null -w "GHTOKEN_API %{http_code}\n" -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/repos/$GH_REPO
'''


def main(repo: str = "otakubooster/ai-animation-pipeline") -> int:
    q = {"num_gpus": {"eq": 1}, "rentable": {"eq": True}, "verified": {"eq": True}, "type": "on-demand",
         "disk_space": {"gte": 10}, "reliability2": {"gte": 0.98}, "inet_down": {"gte": 300},
         "order": [["dph_total", "asc"]], "limit": 3}
    offer = launch.call("POST", "/bundles/", q)["offers"][0]
    iid = launch.call("PUT", f"/asks/{offer['id']}/", {"client_id": "me", "image": "ubuntu:22.04", "disk": 10, "runtype": "ssh",
                                                         "label": "gh-token-check", "onstart": ONSTART, "env": {"GH_REPO": repo}})["new_contract"]
    try:
        for _ in range(40):
            time.sleep(20)
            url = launch.call("PUT", f"/instances/request_logs/{iid}/", {"tail": "20"})["result_url"]
            time.sleep(5)
            try:
                log = urllib.request.urlopen(url, timeout=30).read().decode()
            except Exception:
                continue
            for line in log.splitlines():
                if line.startswith("GHTOKEN_LEN"):
                    print("token length / prefix:", line.split(None, 1)[1])
                if line.startswith("GHTOKEN_API"):
                    code = line.split()[-1]
                    print(f"GH_TOKEN check: GitHub API {code} ({'ok' if code == '200' else 'RENEW the token in the Vast account env vars'})")
                    return 0 if code == "200" else 1
        print("no answer from the check box")
        return 2
    finally:
        launch.call("DELETE", f"/instances/{iid}/")


if __name__ == "__main__":
    raise SystemExit(main())
