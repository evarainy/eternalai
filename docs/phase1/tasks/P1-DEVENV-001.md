# P1-DEVENV-001 — 固定本地测试数据库

status: ready
batch: development-environment
spec_anchor: none

## 执行方式授权

本任务遵循 `_scratch/B4_EXECUTION_METHODOLOGY.md`，在隔离分支
`phase1/P1-DEVENV-001` 执行。风险为 Q2；雨爷明确指定只需 Codex high effort
只读自审，不调用 Opus bridge。任务不在 B 批 spec 链上，不影响 B4-005。

## 实施前 Gap Map

- 清库主路径固定为 `DROP SCHEMA public CASCADE`、`CREATE SCHEMA public` 后执行
  `uv run alembic upgrade head`，不修改迁移。
- 清库脚本先用 SQLAlchemy URL 解析，精确限制数据库名 `eternalai_test`、本机 host 与
  本地宿主端口 `15432`，并拒绝 query 参数；守卫测试断言被拒绝时 DDL 与迁移调用均为零。
- 旧容器、卷、镜像只列清单供雨爷判断；不复用根 `docker-compose.yml`，也不删除任何资产。

## 目标（done_when）

1. 独立 Compose 项目以 CI 的 pgvector 镜像、用户、公开测试口令、库名与 healthcheck
   启动固定测试库；仅本地宿主映射使用 15432，容器内仍为 5432，并保留具名数据卷。
2. 清库脚本仅操作该本机测试库，并在清空 `public` schema 后升级至最新 Alembic schema。
3. 库名、host、端口、query 参数的守卫在任何 DDL 前拒绝不安全 URL。
4. `tests/db/README.md` 说明固定库、清库方式与不删除数据卷的停止命令。

## Out of scope / 禁区

- 删除任何容器、卷、镜像；修改 `.env`、`app/`、`.github/`、根 Compose 或 Alembic 迁移。
- 修改 B4-005 的 Golden 脚本或测试。
- 备份、监控、资源限额、生产部署编排。

## 允许路径

- `infra/docker/docker-compose.test-db.yml`
- `scripts/reset_test_db.py`
- `tests/db/*`
- `tests/infra/persistence/*`
- `tests/db/README.md`
- `.env.example`
- `docs/phase1/tasks/P1-DEVENV-001.md`
- `docs/phase1/task_logs/P1-DEVENV-001_*.yaml`

## Git

- 分支：`phase1/P1-DEVENV-001`
- Commit：`phase1(P1-DEVENV-001): 固定本地测试数据库`
