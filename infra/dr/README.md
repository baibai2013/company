# DR(灾备)— postgres 跨机备份 + 飞书幂等

提案 4 §5.5 落地。本目录提供:

| 文件 | 作用 |
|---|---|
| `pg_backup.sh` | `pg_dump --format=custom --compress=9` 出 dump |
| `pg_restore.sh` | `pg_restore --clean --if-exists` 恢复(支持 `--dry-run`) |
| `docker-compose.dr.yml` | MinIO(模拟 S3) + 备份 sidecar 容器 |

Python wrapper 在 `scripts/`:

| 文件 | 作用 |
|---|---|
| `scripts/dr_backup.py` | 调 pg_backup.sh → 上传 MinIO/S3 → 写 `dr_backups` 元数据 |
| `scripts/dr_restore.py` | 从 `dr_backups.latest()` 拉最新 → `pg_restore` |

## 启动 DR sidecar(dev)

```bash
docker compose -f infra/dr/docker-compose.dr.yml --env-file infra/.env up -d
# MinIO Console: http://localhost:9001 (默认 minioadmin/minioadmin)
# 创建 bucket: company-dr
```

## 手动一次备份

```bash
source .venv/bin/activate
python scripts/dr_backup.py
```

输出会:
1. 跑 `infra/dr/pg_backup.sh` 出 `/tmp/dr/<ts>.dump`
2. 上传到 `s3://${S3_BUCKET}/<ts>.dump`(boto3 走 `S3_ENDPOINT`)
3. 在 `dr_backups` 表写一条 `status='success'` 的元数据

## 手动恢复(干跑)

```bash
python scripts/dr_restore.py --dry-run
# 看到将执行的 pg_restore 命令但不真跑
```

去掉 `--dry-run` 真恢复。**注意会 DROP 现有库再 CREATE**(`--clean --if-exists`)。

## 降级路径

| 依赖 | 降级行为 |
|---|---|
| `boto3` 未装 | 只本地存到 `/tmp/dr/`,`s3_key` 写绝对路径,log.warning 一次 |
| MinIO/S3 不可达 | 同上 |
| `pg_dump` 缺失 | 直接报错退出码 2(不进入 wrapper) |
| dev pg 不可达 | wrapper 抛异常,`mark_failed` 也写不进去(只 log.warning) |
| redis 不可用(飞书幂等) | 进程内 dict 兜底,标 TODO(多进程不真幂等) |

## 验收标准(提案 4 §5.5)

- [ ] 拔电模拟 → postgres 数据无丢失,任务从断点续跑
- [ ] 飞书重放同一 event_id 100 次 → 仅处理 1 次
- [ ] 跨机:把 MinIO 容器换成另一台机器的 S3 桶,链路依然 work

## TODO(留主进程)

- 把 `docker-compose.dr.yml` 接进 ops 一键启动脚本
- `dr_backup` 单独 alembic revision(model 已就位)
- `boto3` 加进 `requirements.txt`(本 wave 故意不加,降级路径已验证)
- 真起 8 员工 A2A server 进程的 systemd / docker-compose unit
