"""Phone access: tailscale serve integration, /api/phone, /api/version."""
import json
import os
import stat

from mtgstack import cli


def _fake_tailscale(tmp_path, enabled=True):
    exe = tmp_path / "tailscale"
    serve = ('echo "Available within your tailnet:"; echo "https://gamepc.tail1234.ts.net/"' if enabled else
             'echo "Serve is not enabled on your tailnet."; echo "To enable, visit:"; '
             'echo "         https://login.tailscale.com/f/serve?node=abc"; exit 1')
    exe.write_text(f"""#!/bin/sh
echo "$@" >> {tmp_path}/calls
case "$1" in
  serve) {serve} ;;
  status) echo '{{"Self": {{"DNSName": "gamepc.tail1234.ts.net."}}}}' ;;
esac
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_tailscale_serve_publishes_localhost(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    _fake_tailscale(tmp_path)
    assert cli._tailscale_serve(8765) == {"url": "https://gamepc.tail1234.ts.net"}
    assert "serve --bg http://127.0.0.1:8765" in (tmp_path / "calls").read_text()


def test_tailscale_serve_explains_enable_link(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    _fake_tailscale(tmp_path, enabled=False)
    r = cli._tailscale_serve(8765)
    assert "url" not in r and "https://login.tailscale.com/f/serve?node=abc" in r["error"]


def test_phone_and_version_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mtgstack.server import create_app
    monkeypatch.setenv("MTGSTACK_PHONE_URLS", json.dumps([{"url": "https://gamepc.tail1234.ts.net", "kind": "tailscale"}]))
    c = TestClient(create_app(str(tmp_path / "t.db"), online=False), base_url="http://localhost")
    assert c.get("/api/phone").json() == {"urls": [{"url": "https://gamepc.tail1234.ts.net", "kind": "tailscale"}], "secure": True}
    # behind tailscale serve the request carries the ts.net host: not listed twice
    assert len(c.get("/api/phone", headers={"host": "gamepc.tail1234.ts.net"}).json()["urls"]) == 1
    assert c.get("/api/version").json()["build"]
    assert c.get("/").headers.get("cache-control") == "no-cache"
    assert c.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert c.get("/..%2f..%2fpyproject.toml").text.startswith("<!doctype html>")
