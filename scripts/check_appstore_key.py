#!/usr/bin/env python3
"""Does the App Store Connect API key in the environment work? Run by the TestFlight job before the long build.

Reads KEY_ID, ISSUER_ID and KEY_FILE (path of the .p8). Prints only shapes (lengths, whether the file looks like a
key) and Apple's answer, never the values. Exit 1 with a plain reason when the key cannot sign in.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import jwt   # pyjwt + cryptography

key_id, issuer = os.environ.get("KEY_ID", ""), os.environ.get("ISSUER_ID", "")
pem = open(os.environ["KEY_FILE"], encoding="utf-8", errors="replace").read()
problems = []
print(f"Key ID: {len(key_id)} characters" + ("" if re.fullmatch(r"[A-Z0-9]{10}", key_id) else "  <- expected 10 letters/digits"))
if not re.fullmatch(r"[A-Z0-9]{10}", key_id):
    problems.append("APPSTORE_KEY_ID does not look like a Key ID (10 capital letters and digits, e.g. the X's in "
                    "AuthKey_XXXXXXXXXX.p8). Check for a pasted space, the key's name, or the Issuer ID.")
uuid = re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", issuer)
print(f"Issuer ID: {len(issuer)} characters" + ("" if uuid else "  <- expected 36 (a UUID with dashes)"))
if not uuid:
    problems.append("APPSTORE_ISSUER_ID does not look like an Issuer ID (36 characters with dashes).")
lines = [ln for ln in pem.strip().splitlines() if ln.strip()]
looks = bool(lines) and lines[0].strip() == "-----BEGIN PRIVATE KEY-----" and lines[-1].strip() == "-----END PRIVATE KEY-----"
print(f"Private key: {len(lines)} lines, {'looks like a .p8 key' if looks else 'does NOT look like a .p8 key'}")
if not looks:
    problems.append("APPSTORE_PRIVATE_KEY is not the contents of a .p8 file (it must start with -----BEGIN PRIVATE KEY-----).")
if problems:
    sys.exit("\n".join("::error::" + p for p in problems))

now = int(time.time())
try:
    token = jwt.encode({"iss": issuer, "iat": now, "exp": now + 600, "aud": "appstoreconnect-v1"}, pem,
                       algorithm="ES256", headers={"kid": key_id})
except Exception as e:
    sys.exit(f"::error::The private key could not be used to sign ({type(e).__name__}): the .p8 contents are damaged.")
req = urllib.request.Request("https://api.appstoreconnect.apple.com/v1/apps?limit=5&fields[apps]=bundleId",
                             headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        apps = [a["attributes"]["bundleId"] for a in json.load(r)["data"]]
    print(f"Apple accepted the key. Apps it can see: {apps}")
except urllib.error.HTTPError as e:
    detail = ""
    try:
        detail = "; ".join(f"{x.get('title')}: {x.get('detail')}" for x in json.load(e)["errors"])
    except Exception:
        pass
    print(f"Apple answered {e.code}. {detail}")
    if e.code == 401:
        sys.exit("::error::Apple rejected the API key (401). The Key ID, Issuer ID and .p8 file do not belong together, "
                 "the key was revoked, or it was created minutes ago and is not active yet.")
    sys.exit(f"::error::Apple answered {e.code} to a signed request.")
