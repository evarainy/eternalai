# PR 静态评审记录格式

本文件由 `AGENTS.md`「Git 与永久任务记录」按需引用，仅在实际采用下列评审方式、填写 PR 验证段时读取；规则权威仍是 `AGENTS.md`。本文件不新增评审门禁。普通跨模型静态 Review 记录实际评审方、结论、绑定候选及产物位置即可；下列三种情形按实际方式择一记录，均在合并前写入 PR body。

- **实际跑评审桥**：附桥生成的 JSON 摘要，标题随实际桥。字段闭集：`requested_model`、`observed_model`、`model_evidence`、`review_model_verified`、`requested_effort`、`verdict`、`base_sha`、`head_sha`、`provider_error`、`invalid_stream_lines`、`termination_reason`。证据为 `server`（服务端）或 `cli_session`（本次 Codex 会话，非服务端）；须核验模型一致、如实记录 observed_model，并满足 `review_model_verified=true`、`verdict=PASS`、`provider_error=false`、`termination_reason=completed`，绑定最终 base/head。不放响应原文或敏感值。
- **OpenCode 原生会话审核、未跑桥**：用 `### OpenCode 静态评审`，记请求模型/effort、取得的模型证据、verdict（PASS/FAIL/BLOCKED）、最终 base/head 与产物位置。**不伪造桥字段，不把模型自述当服务端证明**；实际跑桥仍用上一条闭集摘要。D 档不要求此记录。
- **桥因额度或服务故障不可用**：须雨爷当次明确授权替代静态评审方。PR 改用 `### 静态评审（替代评审桥）`，记授权来源/日期、实际模型、verdict、最终 base/head 和未跑桥原因；不造桥字段，沿用相同静态判据、只读边界与职责分离。
