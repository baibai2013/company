"""
One-time migration: add parent_id / requester / executor / verifier to task table.
Run once: python -m backend.migrate
Safe to re-run (uses ADD COLUMN IF NOT EXISTS).
"""
import psycopg

from backend.core.config import settings


def main() -> None:
    dsn = (
        settings.database_url_sync
        .replace("postgresql+psycopg://", "postgresql://")
    )
    new_cols = [
        ("parent_id", "VARCHAR(36) REFERENCES task(id) ON DELETE SET NULL"),
        ("requester",  "VARCHAR(50) DEFAULT 'CEO'"),
        ("executor",   "VARCHAR(50)"),
        ("verifier",   "VARCHAR(50)"),
    ]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for col, defn in new_cols:
                cur.execute(
                    f"ALTER TABLE task ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
        conn.commit()
    print("Migration complete.")


if __name__ == "__main__":
    main()
