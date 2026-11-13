#!/usr/bin/env python3
"""
set_repo_secret.py — one-command rotation helper for GH_STATS_TOKEN.

Run this after rotating your Fine-Grained PAT so the vinukj/vinukj
daily stats pipeline keeps working:

    export GH_PAT="github_pat_..."            # the NEW token
    python3 scripts/set_repo_secret.py

Encrypts the token with the repo's public key (libsodium sealed box)
and uploads it as the GH_STATS_TOKEN repository secret. The new token
must have: Secrets = Read and write, Metadata = Read-only.

Stdlib + pynacl:  uv run --with pynacl scripts/set_repo_secret.py
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    from nacl import encoding, public
except ImportError:
    sys.exit("pynacl required: uv run --with pynacl scripts/set_repo_secret.py")

OWNER_REPO = "vinukj/vinukj"
SECRET_NAME = "GH_STATS_TOKEN"


def api(path: str, method: str = "GET", data: dict | None = None) -> dict:
    token = os.environ.get("GH_PAT", "").strip()
    if not token:
        sys.exit("export GH_PAT=<your new fine-grained token> first")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(data).encode() if data else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        sys.exit(f"[error] HTTP {exc.code} on {method} {path}: {exc.read().decode()}")


def main() -> int:
    pk = api(f"/repos/{OWNER_REPO}/actions/secrets/public-key")
    sealed = public.SealedBox(
        public.PublicKey(pk["key"].encode(), encoding.Base64Encoder())
    ).encrypt(os.environ["GH_PAT"].strip().encode())
    api(
        f"/repos/{OWNER_REPO}/actions/secrets/{SECRET_NAME}",
        "PUT",
        {
            "encrypted_value": base64.b64encode(sealed).decode(),
            "key_id": pk["key_id"],
        },
    )
    print(f"[ok] {SECRET_NAME} updated on {OWNER_REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
