"""Configura GitHub Actions (permisos + secrets) sin depender de gh auth interactivo."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "datanalytics86/TradingBot"
ENV_FILE = ROOT / ".env"

SECRET_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def _git_token() -> str:
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


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    try:
        from nacl import encoding, public
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pynacl", "-q"])
        from nacl import encoding, public

    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder)
    sealed = public.SealedBox(pk).encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(sealed).decode("utf-8")


def _load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"No existe {ENV_FILE}")
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if val and not val.startswith("tu_") and val != "PENDIENTE":
            out[key] = val
    return out


def main() -> int:
    token = _git_token()
    print(f"Configurando GitHub Actions en {REPO}...")

    _api(
        "PUT",
        f"repos/{REPO}/actions/permissions/workflow",
        token,
        {
            "default_workflow_permissions": "write",
            "can_approve_pull_request_reviews": False,
        },
    )
    print("  Permisos workflow: write")

    env = _load_env()
    secrets_set = 0
    for key in SECRET_KEYS:
        val = env.get(key, "")
        if not val:
            continue
        pk_data = _api("GET", f"repos/{REPO}/actions/secrets/public-key", token)
        encrypted = _encrypt_secret(pk_data["key"], val)
        _api(
            "PUT",
            f"repos/{REPO}/actions/secrets/{key}",
            token,
            {"encrypted_value": encrypted, "key_id": pk_data["key_id"]},
        )
        print(f"  Secret configurado: {key}")
        secrets_set += 1

    if secrets_set == 0:
        print("\nAVISO: No hay secrets reales en .env (faltan keys de Alpaca).")
        print("Edita .env y vuelve a ejecutar este script.")
        return 1

    _api(
        "POST",
        f"repos/{REPO}/actions/workflows/trading-bot.yml/dispatches",
        token,
        {"ref": "claude/automated-trading-bot-strategy-k9rx4y"},
    )
    print("  Workflow disparado manualmente.")
    print(f"  Ver: https://github.com/{REPO}/actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())