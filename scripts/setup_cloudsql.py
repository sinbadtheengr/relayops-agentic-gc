"""F-2 — provision the database, role and secret, then hand back a DSN.

Idempotent: safe to re-run. Creates nothing that already exists, and rotates
no password unless asked.

    python scripts/setup_cloudsql.py [--rotate]

The generated password is written to Secret Manager and to the gitignored
.env, and is never printed. `deploy.sh` reads it from Secret Manager; a
developer reads it from .env; neither reads it from a terminal transcript.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = "relayops-fleet"
INSTANCE = "relayops-fleet-db"
DB_NAME = "relayops"
DB_USER = "relayops"
SECRET_ID = "relayops-db-password"
REPO_ROOT = Path(__file__).resolve().parents[1]


def sh(args: list[str], *, check: bool = True, quiet_stderr: bool = False) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, shell=True, check=False)
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip()
        # "already exists" is success for an idempotent script.
        if "already exists" in stderr.lower() or "alreadyexists" in stderr.lower():
            return ""
        if not quiet_stderr:
            print(f"FAILED: {' '.join(args)}\n{stderr}", file=sys.stderr)
        raise SystemExit(1)
    return proc.stdout.strip()


def secret_exists() -> bool:
    out = sh(
        ["gcloud", "secrets", "list", "--project", PROJECT, "--filter", f"name:{SECRET_ID}",
         "--format", "value(name)"],
        check=False,
    )
    return SECRET_ID in out


def read_secret() -> str:
    return sh(
        ["gcloud", "secrets", "versions", "access", "latest", "--secret", SECRET_ID,
         "--project", PROJECT]
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true", help="generate a new password")
    args = ap.parse_args()

    state = sh(
        ["gcloud", "sql", "instances", "describe", INSTANCE, "--project", PROJECT,
         "--format", "value(state)"]
    )
    if state != "RUNNABLE":
        raise SystemExit(f"instance is {state}, not RUNNABLE")

    print(f"instance {INSTANCE}: {state}")

    # 1. Password: reuse the stored one unless rotating.
    if secret_exists() and not args.rotate:
        password = read_secret()
        print(f"secret {SECRET_ID}: reusing existing version")
    else:
        password = secrets.token_urlsafe(32)
        if not secret_exists():
            sh(["gcloud", "secrets", "create", SECRET_ID, "--project", PROJECT,
                "--replication-policy", "automatic"], check=False)
        proc = subprocess.run(
            ["gcloud", "secrets", "versions", "add", SECRET_ID, "--project", PROJECT,
             "--data-file", "-"],
            input=password, capture_output=True, text=True, shell=True, check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(1)
        print(f"secret {SECRET_ID}: new version stored")

    # 2. Database + role.
    sh(["gcloud", "sql", "databases", "create", DB_NAME, "--instance", INSTANCE,
        "--project", PROJECT], check=False)
    print(f"database {DB_NAME}: ready")

    proc = subprocess.run(
        ["gcloud", "sql", "users", "set-password", DB_USER, "--instance", INSTANCE,
         "--project", PROJECT, "--password", password],
        capture_output=True, text=True, shell=True, check=False,
    )
    if proc.returncode != 0:
        # set-password fails when the role does not exist yet; create it.
        subprocess.run(
            ["gcloud", "sql", "users", "create", DB_USER, "--instance", INSTANCE,
             "--project", PROJECT, "--password", password],
            capture_output=True, text=True, shell=True, check=True,
        )
    print(f"user {DB_USER}: ready")

    # 3. Authorize this machine's egress IP so migrations can run from here.
    #    Cloud Run reaches the instance over its Unix socket and needs none of
    #    this; the allow-list is a development convenience only.
    with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=10) as resp:
        my_ip = json.load(resp)["ip"]
    existing = sh(
        ["gcloud", "sql", "instances", "describe", INSTANCE, "--project", PROJECT,
         "--format", "value(settings.ipConfiguration.authorizedNetworks[].value)"],
        check=False,
    )
    if my_ip not in existing:
        nets = [n for n in existing.split(";") if n] + [f"{my_ip}/32"]
        sh(["gcloud", "sql", "instances", "patch", INSTANCE, "--project", PROJECT,
            "--authorized-networks", ",".join(nets), "--quiet"])
    print(f"authorized network: {my_ip}/32")

    host = sh(
        ["gcloud", "sql", "instances", "describe", INSTANCE, "--project", PROJECT,
         "--format", "value(ipAddresses[0].ipAddress)"]
    )
    dsn = f"postgresql+psycopg://{DB_USER}:{password}@{host}:5432/{DB_NAME}?sslmode=require"

    # 4. Write DATABASE_URL into the gitignored .env.
    env_path = REPO_ROOT / ".env"
    lines = []
    if env_path.exists():
        lines = [
            ln for ln in env_path.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("DATABASE_URL=")
        ]
    lines.append(f"DATABASE_URL={dsn}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote DATABASE_URL to {env_path.name} (gitignored; password not printed)")
    print("\nNext:  python -m alembic upgrade head")


if __name__ == "__main__":
    main()
