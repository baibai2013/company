"""
Migration: 创建 agent_task_runs 表（P1.4 执行历史持久化）。

运行方式：
    python -m backend.migrate_task_runs

幂等：表已存在时跳过（CREATE TABLE IF NOT EXISTS）。
保留策略：每个任务保留最近 200 条，建议每天跑一次清理（见 cleanup() 函数）。
"""
import psycopg

from backend.core.config import settings

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_task_runs (
    id              BIGSERIAL PRIMARY KEY,
    employee_key    VARCHAR(64)  NOT NULL,
    task_id         VARCHAR(64)  NOT NULL,
    task_name       VARCHAR(256),
    triggered_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    status          VARCHAR(16)  NOT NULL,  -- success / error / timeout / skipped
    result_text     TEXT,
    output_to       VARCHAR(64),
    execution_mode  VARCHAR(32)  DEFAULT 'agent'
);

CREATE INDEX IF NOT EXISTS idx_task_runs_lookup
    ON agent_task_runs (employee_key, task_id, triggered_at DESC);
"""

CLEANUP_SQL = """
DELETE FROM agent_task_runs
WHERE id NOT IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY employee_key, task_id
                   ORDER BY triggered_at DESC
               ) AS rn
        FROM agent_task_runs
    ) ranked
    WHERE rn <= 200
);
"""


def main() -> None:
    dsn = (
        settings.database_url_sync
        .replace("postgresql+psycopg://", "postgresql://")
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
    print("Migration complete: agent_task_runs table ready.")


def cleanup() -> None:
    """清理超出保留上限的旧记录（每个任务保留最近 200 条）。"""
    dsn = (
        settings.database_url_sync
        .replace("postgresql+psycopg://", "postgresql://")
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(CLEANUP_SQL)
            deleted = cur.rowcount
        conn.commit()
    print(f"Cleanup complete: {deleted} old records removed.")


if __name__ == "__main__":
    main()
