# -*- coding: utf-8 -*-
"""Adversarial end-to-end checks against the RUNNING packaged app (HTTP only, no browser, no models).

Each check must be BLOCKED by the security layers. On a build WITHOUT them (e.g. v1.0.0) the same checks
must FAIL -- that is how we prove the tests bite (security-e2e.yml runs both). Exit code = number of failed checks.
Usage: python security_e2e.py <name> <engine-ignored>
"""
import http.client
import json
import os
import socket
import sys
import tempfile

HOST, PORT = "127.0.0.1", 8765
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def request(method: str, path: str, *, headers=None, body=None, host=HOST, port=PORT):
    c = http.client.HTTPConnection(host, port, timeout=15)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()[:300]
    finally:
        c.close()


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def main() -> int:
    status, _ = request("GET", "/")
    check("app serves the UI on 127.0.0.1", status == 200, f"status {status}")

    ip = lan_ip()
    if ip and not ip.startswith("127."):
        try:
            socket.create_connection((ip, PORT), timeout=5).close()
            reachable = True
        except OSError:
            reachable = False
        check(f"UI is NOT reachable on the LAN address {ip}", not reachable)
    else:
        check("UI is NOT reachable on the LAN address (no LAN interface on this runner; skipped)", True)

    status, _ = request("GET", "/", headers={"Host": "evil.example:8765"})
    check("request with a foreign Host header is refused (DNS rebinding)", status == 403, f"status {status}")
    status, _ = request("GET", "/", headers={"Origin": "https://evil.example"})
    check("request with a foreign Origin header is refused (cross-site)", status == 403, f"status {status}")

    def open_path(path: str) -> int:
        return request("POST", "/loma/open-path", headers={"Content-Type": "application/json"},
                       body=json.dumps({"path": path}))[0]

    if sys.platform == "win32":
        unoffered = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
        script_name = "loma_sec_probe.bat"
        script_body = "@echo off\r\nexit /b 0\r\n"
    else:
        unoffered = "/etc/hosts"
        script_name = "loma_sec_probe.command"
        script_body = "#!/bin/sh\nexit 0\n"
    check("forged link to an existing file the app never offered is refused",
          open_path(unoffered) == 403, f"status {open_path(unoffered)}")
    tmp = os.path.join(tempfile.gettempdir(), script_name)
    with open(tmp, "w", newline="") as f:
        f.write(script_body)
    check("a script/program path is refused (never launched from a link)", open_path(tmp) == 403, f"status {open_path(tmp)}")
    unc = chr(92) * 2 + "attacker" + chr(92) + "share" + chr(92) + "x.txt"
    check("a network (UNC) path is refused", open_path(unc) == 403, f"status {open_path(unc)}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nSECURITY RESULT: {len(results) - len(failed)}/{len(results)} checks blocked/passed; {len(failed)} failed")
    return len(failed)


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
