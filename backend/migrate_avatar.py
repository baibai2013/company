"""Add avatar_url column to employee table. Run once: python -m backend.migrate_avatar"""
import psycopg
from backend.core.config import settings

def main():
    dsn = settings.database_url_sync.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE employee ADD COLUMN IF NOT EXISTS avatar_url TEXT")
        conn.commit()
    print("Migration complete: avatar_url added.")

if __name__ == "__main__":
    main()
