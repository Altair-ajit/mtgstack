#!/usr/bin/env python3
"""List the signing certificates on the Apple developer account, and optionally revoke the ones CI made.

Every TestFlight run happens on a fresh machine, so Xcode's automatic signing creates a new development certificate
each time ("Created via API"). An account may only hold a few, and when it is full the archive step fails with
"Your account has reached the maximum number of certificates".

    KEY_ID=... ISSUER_ID=... KEY_FILE=AuthKey.p8 python scripts/appstore_certs.py            # list only
    ... python scripts/appstore_certs.py --revoke-ci                                           # revoke CI's own

Only development certificates whose name says they were created through the API are ever revoked: never a
distribution certificate, and never one made by a person in Xcode or on the developer site. Revoking cannot be undone,
but a development certificate only signs test builds, and the next run makes the one it needs.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt   # pyjwt + cryptography

API = "https://api.appstoreconnect.apple.com/v1"


def token() -> str:
    now = int(time.time())
    pem = open(os.environ["KEY_FILE"], encoding="utf-8").read()
    return jwt.encode({"iss": os.environ["ISSUER_ID"], "iat": now, "exp": now + 600, "aud": "appstoreconnect-v1"},
                      pem, algorithm="ES256", headers={"kid": os.environ["KEY_ID"]})


def call(method: str, path: str):
    req = urllib.request.Request(path if path.startswith("http") else API + path, method=method,
                                 headers={"Authorization": f"Bearer {token()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"::error::Apple answered {e.code} to {method} {path.split('?')[0]}: {e.read()[:300]!r}")


def certificates() -> list[dict]:
    out, url = [], "/certificates?limit=200"
    while url:
        page = call("GET", url)
        out += page["data"]
        url = page.get("links", {}).get("next")
    return out


def made_by_ci(c: dict) -> bool:
    a = c["attributes"]
    return "DEVELOPMENT" in a["certificateType"] and "created via api" in (a.get("displayName") or a.get("name") or "").lower()


def main() -> None:
    revoke = "--revoke-ci" in sys.argv
    certs = certificates()
    print(f"{len(certs)} certificate(s) on the account:")
    for c in sorted(certs, key=lambda c: c["attributes"].get("expirationDate") or ""):
        a = c["attributes"]
        mark = "CI " if made_by_ci(c) else "   "
        print(f"  {mark}{a['certificateType']:28s} expires {str(a.get('expirationDate'))[:10]}  {a.get('displayName') or a.get('name')}")
    mine = [c for c in certs if made_by_ci(c)]
    print(f"{len(mine)} of them are development certificates created by CI runs (marked CI).")
    if not revoke:
        return
    for c in mine:
        call("DELETE", f"/certificates/{c['id']}")
        print(f"  revoked {c['attributes'].get('displayName') or c['id']}")
    print(f"Revoked {len(mine)}. {len(certs) - len(mine)} left untouched.")


if __name__ == "__main__":
    main()
