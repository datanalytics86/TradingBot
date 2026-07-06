"""Habilita GitHub Pages desde la carpeta /docs en la rama del bot."""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "datanalytics86/TradingBot"
BRANCH = "claude/automated-trading-bot-strategy-k9rx4y"


def _token() -> str:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("No se obtuvo token de git credential")


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"https://api.github.com/{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if body else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"GitHub API {method} {path} -> {exc.code}: {detail}") from exc


def main() -> int:
    token = _token()
    body = {
        "build_type": "legacy",
        "source": {"branch": BRANCH, "path": "/docs"},
    }
    try:
        result = _api("POST", f"repos/{REPO}/pages", token, body)
        print("GitHub Pages habilitado:")
        print(f"  URL: {result.get('html_url', 'https://datanalytics86.github.io/TradingBot/')}")
        return 0
    except RuntimeError as exc:
        msg = str(exc)
        if "409" in msg or "already exists" in msg.lower():
            _api(
                "PUT",
                f"repos/{REPO}/pages",
                token,
                body,
            )
            status = _api("GET", f"repos/{REPO}/pages", token)
            print("GitHub Pages actualizado:")
            print(f"  URL: {status.get('html_url', 'https://datanalytics86.github.io/TradingBot/')}")
            print(f"  Estado: {status.get('status', 'n/d')}")
            return 0
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())