"""Create backend/.env with fresh random secrets, or fill in the ones still missing.

    python3 backend/make_env.py

Starts from backend/.env if it exists (so your other settings are kept),
otherwise from backend/.env.example. Every secret that is empty or still the
example's placeholder gets a new random value; secrets you have already set
are left alone, so running it again changes nothing. The API refuses to start
while a placeholder remains (app/config.py).

Prints the first admin account's password once. Only the standard library.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"
EXAMPLE = HERE / ".env.example"
PLACEHOLDER = "change-me"
# Secrets this script owns. DATABASE_URL is rebuilt from the new database password.
SECRETS = ("POSTGRES_PASSWORD", "TELEMETRY_API_KEY", "BOOTSTRAP_ADMIN_PASSWORD")
LINE = re.compile(r"^(?P<key>[A-Z_]+)=(?P<value>.*)$")


def needs_value(value: str) -> bool:
    return not value.strip() or PLACEHOLDER in value


def main() -> int:
    source = ENV if ENV.exists() else EXAMPLE
    lines = source.read_text().splitlines()
    values = {m["key"]: m["value"] for m in map(LINE.match, lines) if m}

    new = {key: secrets.token_urlsafe(32) for key in SECRETS if needs_value(values.get(key, ""))}
    old_password = values.get("POSTGRES_PASSWORD", "")
    if "POSTGRES_PASSWORD" in new and old_password and "DATABASE_URL" in values:
        new["DATABASE_URL"] = values["DATABASE_URL"].replace(
            old_password, new["POSTGRES_PASSWORD"]
        )
    if not new:
        print(f"{ENV.relative_to(HERE.parent)}: every secret is already set; nothing to do.")
        return 0

    written = set()
    out = []
    for line in lines:
        match = LINE.match(line)
        if match and match["key"] in new:
            line = f"{match['key']}={new[match['key']]}"
            written.add(match["key"])
        out.append(line)
    for key in new.keys() - written:
        out.append(f"{key}={new[key]}")

    # Readable by you only: it holds every credential the stack uses.
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(out) + "\n")
    os.chmod(ENV, 0o600)

    print(f"Wrote {ENV.relative_to(HERE.parent)} (from {source.name}). New values for:")
    for key in sorted(new):
        print(f"  {key}")
    if "BOOTSTRAP_ADMIN_PASSWORD" in new:
        user = values.get("BOOTSTRAP_ADMIN_USERNAME") or "admin"
        print(
            f"\nFirst sign-in: {user} / {new['BOOTSTRAP_ADMIN_PASSWORD']}\n"
            "(also in backend/.env; the account is created on the API's first start)"
        )
    if "POSTGRES_PASSWORD" in new and source == ENV:
        user = values.get("POSTGRES_USER", "postgres")
        print(
            "\nThe database keeps the password it was created with. If it already "
            "exists, give it the new one before restarting the API:\n"
            f"  docker compose exec db psql -U {user} -c "
            f"\"ALTER USER {user} PASSWORD '{new['POSTGRES_PASSWORD']}'\""
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
