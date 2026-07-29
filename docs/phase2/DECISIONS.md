# Phase 2 Decisions

## 2026-07-24 — P2-AUTH-001 `cryptography` runtime dependency

`2026-07-24` 雨爷批准 `P2-AUTH-001` 新增运行时依赖 `cryptography`（OA 登录 RSA PKCS#1 v1.5 必需；`httpx` 已在，无新增 HTTP 依赖）。经 Claude 停点上报、雨爷显式批准。

## 2026-07-27 — P2-PILOT-FOUNDATION-001 `uvicorn` runtime dependency

`2026-07-27` 雨爷批准 `P2-PILOT-FOUNDATION-001` 新增运行时依赖 `uvicorn`，范围仅限 ASGI server 进程启动，不含部署编排。依赖白名单登记于 `docs/dev/dependency_policy.md` 的 Dependency Allowlist。
