#!/usr/bin/env python3
"""Apply pending Supabase migrations from scripts/migrations/ in version order."""

import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_applied(cur) -> set[str]:
    cur.execute(
        "SELECT version FROM supabase_migrations.schema_migrations ORDER BY version"
    )
    return {row[0] for row in cur.fetchall()}


def apply_migration(cur, version: str, name: str, sql: str) -> None:
    print(f"  Applying {version}_{name}...")
    cur.execute("SET statement_timeout = 0;")
    cur.execute("SET lock_timeout = 0;")
    cur.execute(sql)
    cur.execute(
        "INSERT INTO supabase_migrations.schema_migrations (version, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (version, name),
    )
    print(f"  Done.")


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Try loading from .env
        env_file = Path(__file__).parents[1] / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip()
                    break
    if not db_url:
        print("ERROR: DATABASE_URL not set and not found in .env")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            applied = get_applied(cur)

        sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        pending = []
        for path in sql_files:
            version = path.stem.split("_")[0]
            name = "_".join(path.stem.split("_")[1:])
            if version not in applied:
                pending.append((version, name, path))

        if not pending:
            print("No pending migrations.")
            return

        print(f"{len(pending)} pending migration(s):")
        for version, name, path in pending:
            print(f"  - {version}_{name}")

        for version, name, path in pending:
            sql = path.read_text()
            with conn.cursor() as cur:
                apply_migration(cur, version, name, sql)
            conn.commit()
            print(f"  Committed {version}_{name}")

        print(f"\nAll migrations applied.")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
