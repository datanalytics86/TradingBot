"""Consulta permisos, secrets y últimos runs de GitHub Actions."""
from __future__ import annotations

import json
import subprocess
import urllib.request


REPO = "datanalytics86/TradingBot"


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


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    token = _token()
    perms = _get(f"repos/{REPO}/actions/permissions/workflow", token)
    print("Workflow permissions:", perms)

    secrets = _get(f"repos/{REPO}/actions/secrets", token)
    names = [s["name"] for s in secrets.get("secrets", [])]
    print("Secrets configurados:", names or "(ninguno)")

    runs = _get(
        f"repos/{REPO}/actions/workflows/trading-bot.yml/runs?per_page=3", token
    )
    for run in runs.get("workflow_runs", []):
        print(
            f"Run {run['id']}: {run['status']} / {run['conclusion']} "
            f"({run['created_at']}) -> {run['html_url']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())