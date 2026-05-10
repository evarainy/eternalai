# ADR-P0-SPIKE-004 — Redis + ARQ Baseline Spike

- task_id: P0-SPIKE-004
- date: 2026-05-10
- status: accepted
- decision_makers: [Phase 0 Spike]

## 1. Context

Phase 1 及后续阶段需要异步任务队列能力（长耗时推理任务、批量处理、定时任务等）。主蓝图要求任务队列抽象，不锁死具体产品。Phase 0 Spike 阶段需要验证 Redis + ARQ 在单机 Docker Compose 环境下的任务入队、执行、失败重试、超时和结果记录能力，为 Phase 1 决策提供依据。

**spike-only**: 本 ADR 中的 Docker Compose 配置不是生产部署方案，不包含生产级 Redis ACL / TLS / 密码 / 备份 / 监控 / 资源限制完整设计。Phase 1 若采用 Redis + ARQ，必须单独设计并补全这些能力。

## 2. ARQ Overview

ARQ 是一个基于 Redis 的 Python 异步任务队列库，特点：
- 原生 async/await 支持
- 基于 Redis list 的任务入队/出队
- 支持 job timeout、retry、result recording
- 轻量级，无 broker 中间件依赖
- 活跃维护（GitHub: samuelcolvin/arq）

评估版本：arq >= 0.26, < 1；redis-py >= 5, < 6；Redis 7 Alpine

## 3. Spike Execution Results

### 3.1 Environment

| Component | Version |
|-----------|---------|
| Python | 3.12.10 |
| pip | 25.0.1 |
| Docker Engine | 29.4.0 |
| Docker Compose | v5.1.2 |
| Redis image | redis:7-alpine |
| arq | 0.28.0 |
| redis-py | 5.3.1 |

### 3.2 Test Results (Real Execution)

| AC | 场景 | 预期行为 | 实际结果 | 状态 |
|----|------|---------|---------|------|
| AC-1 | `enqueue_job("task_success", "hello-spike")` | 成功执行，返回 `{status: "ok", input: "hello-spike", output: "processed: hello-spike"}` | PASS — `result={'status': 'ok', 'input': 'hello-spike', 'output': 'processed: hello-spike'}` | passed |
| AC-2 | `enqueue_job("task_failure")` | 捕获 `RuntimeError: simulated spike failure for AC-2` | PASS — `RuntimeError: simulated spike failure for AC-2` recorded via `job.result_info()` | passed |
| AC-3 | `enqueue_job("task_timeout")` (job_timeout=5s, sleep 60s) | 任务被终止，产生 TimeoutError | PASS — `TimeoutError` detected after 5.01s | passed |

### 3.3 Spike Code Artifacts

| 文件 | 用途 |
|------|------|
| `experiments/phase0/redis_arq/worker.py` | ARQ worker 函数定义（task_success / task_failure / task_timeout） |
| `experiments/phase0/redis_arq/test_spike.py` | 自动化测试脚本，使用 ARQ native Worker burst mode，覆盖 AC-1/2/3 |
| `experiments/phase0/redis_arq/requirements.txt` | spike-only 依赖清单 |
| `infra/docker/docker-compose.redis-spike.yml` | spike-only Redis Docker Compose |

## 4. Known Limitations (Based on Code Design and Public Documentation)

| Limitation | 说明 | Phase 1 影响 |
|------------|------|-------------|
| ARQ 仅支持 Redis 后端 | 无 RabbitMQ / PostgreSQL 等替代 backend | 如果 Phase 1 不采用 Redis，需要替换整个任务队列方案 |
| ARQ job_timeout 在 worker 端执行 | 超时通过 `asyncio.wait_for` 实现，非分布式超时协调 | 多 worker 场景下超时语义需验证 |
| ARQ 无内置 dead-letter queue | 失败任务需要自行实现重试/DLQ 逻辑 | Phase 1 需设计失败任务处理策略 |
| ARQ 无内置任务优先级 | 基于 Redis list FIFO，无优先级队列 | 如需优先级，需额外设计 |
| Redis 持久化需单独配置 | 默认 RDB 快照，AOF 需显式启用 | Phase 1 必须配置持久化策略 |
| 无内置任务监控/指标 | ARQ 不暴露 Prometheus metrics 等 | Phase 1 需接入可观测性体系 |

## 5. Production Readiness Gap (Phase 1 Prerequisites)

如果 Phase 1 采用 Redis + ARQ，必须补全以下能力：

| 能力 | 当前 Spike 状态 | Phase 1 要求 |
|------|----------------|-------------|
| Redis auth | 无密码 | 必须配置密码 + ACL |
| Redis TLS | 未配置 | 如跨网络传输必须启用 |
| Redis persistence | 默认 RDB | 明确 RDB/AOF 策略，验证数据安全 |
| Redis backup | 无 | 必须设计备份策略 |
| Redis monitoring | 无 | 接入 Prometheus / Grafana 或等价方案 |
| Resource limits | 无 | CPU / memory limits，防止 OOM |
| Dead-letter queue | 未实现 | 设计失败任务处理策略 |
| Task result cleanup | 未实现 | 设计 result TTL / 清理策略 |
| Multi-worker scaling | 未测试 | 验证多 worker 并发场景 |

## 6. Recommendation

**Phase 1 L1 候选评估**: ARQ 作为 L1 候选**推荐**，AC-1/2/3 均在真实环境下通过验证。

前置条件（Phase 1 启动前必须满足）:

1. **Redis 运维方案就绪**: Phase 1 启动前必须完成 Redis auth、ACL、persistence、backup、monitoring、resource limits 设计
2. **法律/合规确认**: Redis 社区版许可证（BSD 3-Clause）无合规风险
3. **失败任务策略确认**: 确认 dead-letter queue、retry 策略、result cleanup 设计
4. **多 worker 并发验证**: Phase 1 需验证多 worker 场景下的超时语义和任务竞争

**替代候选**（如 ARQ 不满足需求时）:
- Celery + Redis/RabbitMQ（更重但功能更全）
- BullMQ via Node.js sidecar（如果前端团队也有异步需求）
- 直接使用 Redis Streams + 自定义 consumer

## 7. Re-Test Status

Spike 验证已在真实环境下完成（Docker Engine 29.4.0 + Redis 7-alpine + arq 0.28.0）。无需重新执行。

如需复现，执行命令：
```powershell
docker compose -f infra/docker/docker-compose.redis-spike.yml up -d
python -m venv $env:TEMP\eternalai-p0-spike-004-venv
$env:TEMP\eternalai-p0-spike-004-venv\Scripts\pip install -r experiments/phase0/redis_arq/requirements.txt
$env:TEMP\eternalai-p0-spike-004-venv\Scripts\python experiments/phase0/redis_arq/test_spike.py
docker compose -f infra/docker/docker-compose.redis-spike.yml down -v
Remove-Item -Recurse -Force $env:TEMP\eternalai-p0-spike-004-venv -ErrorAction SilentlyContinue
```

## 8. Evidence Summary

| Evidence | Path / Value |
|----------|-------------|
| Docker Compose config validation | `docker compose config` — 输出合法 |
| Redis container start | `docker compose up -d` — Container started, health: healthy |
| Redis ping (inside container) | `docker exec docker-redis-1 redis-cli ping` — PONG |
| Redis ping (host Python) | `redis.Redis(host='127.0.0.1', port=6379).ping()` — True |
| pip install | `arq==0.28.0`, `redis==5.3.1`, `hiredis==3.3.1`, `click==8.3.3`, `pyjwt==2.12.1`, `colorama==0.4.6` |
| AC-1 test output | `PASS: task executed successfully, result={'status': 'ok', 'input': 'hello-spike', 'output': 'processed: hello-spike'}` |
| AC-2 test output | `PASS: exception recorded: RuntimeError: simulated spike failure for AC-2` |
| AC-3 test output | `PASS: timeout detected, exception=TimeoutError` (5.01s) |
| Worker syntax check | `python -m py_compile worker.py` — OK |
| Test script syntax check | `python -m py_compile test_spike.py` — OK |
| Spike code location | `experiments/phase0/redis_arq/` |
| Docker Compose location | `infra/docker/docker-compose.redis-spike.yml` |
| Cleanup | `docker compose down -v` — removed; temp venv deleted; `docker ps` — no containers |
| git status | No `.venv` / `__pycache__` / `.pyc` residual |
