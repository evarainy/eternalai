# Phase 2 Decisions

## 2026-07-24 — P2-AUTH-001 `cryptography` runtime dependency

`2026-07-24` 雨爷批准 `P2-AUTH-001` 新增运行时依赖 `cryptography`（OA 登录 RSA PKCS#1 v1.5 必需；`httpx` 已在，无新增 HTTP 依赖）。经 Claude 停点上报、雨爷显式批准。

## 2026-07-27 — P2-PILOT-FOUNDATION-001 `uvicorn` runtime dependency

`2026-07-27` 雨爷批准 `P2-PILOT-FOUNDATION-001` 新增运行时依赖 `uvicorn`，范围仅限 ASGI server 进程启动，不含部署编排。依赖白名单登记于 `docs/dev/dependency_policy.md` 的 Dependency Allowlist。

## 2026-08-03 — 决定一：写操作凭证与问责

写操作使用用户自己的 OA 凭证执行，OA 审批记录上是用户本人；每次写操作必须人工确认，AI 拟好、用户点确认才执行，确认动作本身留痕；与 `P2-CONFIRM-RESUME-001` 方向一致。

## 2026-08-03 — 决定二：数据库访问边界

数据库不算“目标系统”：不进外部系统名册、不做 IdentityMapping、不加 `db` 枚举值，因此不需要 schema 变更；但 AI 对数据库的每次访问必须在 Trace 里可查。

## 2026-08-03 / 2026-08-12 修订 — 决定三：企业级密钥

企业级密钥的责任人为运维；纯内网不设定期轮换；通过配置文件手工更新，不建设运行时管理页面。

## 2026-08-03 — 决定四：Golden 策略（逐字）

负向、边界和安全拒绝用例的题面、预期、禁止项、分类及判卷契约冻结，修改需雨爷明确批准。所有既有正向题面同样不可原地改写，只能新增后继题并在题外生命周期清单中停止旧题运行。判卷契约或运行选择规则变更时，必须按同一版本包全量回放并明确披露影响。每修复一个真实缺陷，必须新增一条能在未修代码上失败、修复后通过、且走原缺陷路径的永久回归证据；缺陷属于 Golden Runtime 观察边界时才新增 Golden Task，否则放在最小且忠实的单元/集成/API/浏览器层。
