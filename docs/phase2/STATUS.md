# Phase 2 当前状态

## 当前状态（2026-11-19）

| 活跃面 | 当前结果与指针 |
|---|---|
| 治理 | `P2-GOV-SYNC-20261119-2`（2026-11-19，GOV-SYNC 串行，承担 A 类同步；常设授权与读取权限部分按 B 档高风险、文档移动与压缩按 D 档）整理启动上下文；开发助手分工、型号与常设授权见 [11-19 裁决](DECISIONS.md#2026-11-19--裁决开发助手分工型号更新与常设授权)。 |
| 我的功能 | 11-18 第一版 PR #293、11-19 第二阶段第一批 PR #295 已合并；本人只读功能可保存、确定性运行并导出 Excel，该批记录支持跨后端重启恢复。待接手、到期/清理、组字及评审修复后复验见 PLAN 对应条。 |
| 综合业务 SSO | `P2-ZHYW-SYSTEM-ENTRY-001` 11-19 经 PR #296 合并，支持绑定/更新本人 OA 登录账号并免密跳转，MCP 连接状态独立。PR 未声明档位；凭证加密保存与导航凭证签发按现役规则属高风险。实际目标环境绑定、更新、免登待验收。 |
| OA 会话与提示 | PR #289/#291 已合并；11-16～17 真实验收覆盖本人 active 绑定的恢复链路及 V7 失效提示修复。恢复失败/竞态、剩余 V3b/V6/N1 及未绑定或撤销的概览提示见 PLAN。 |
| 工作台与首页 | 11-13～17 新版工作台、界面小修和本人布局/外观保存已合并；`P2-HOME-LAYOUT-PERSIST-001` 新增 schema/迁移，属 B 档高风险。11-17 本人偏好跨全新浏览器与双账号隔离验收有记录；读失败、业务授权/断开及其余 UI 欠债见 PLAN。 |
| Chat 上传、填表、报表与后台 | PR #253/#260/#263/#275/#276/#278/#286 已合并；11-09～17 已有样表填写、首份固定报表、产物发布下载和会话失效/恢复记录。Linux/麒麟 ARM、失败分支和剩余输入限制见 PLAN。 |
| IM | 11-05～08 V1/V2a/V3/V4 与 V2b 均已交付合并；V2b `P2-IM-FILE-SHARE-001` 11-08 经 PR #271 合并，11-09 已补第三人隔离及首个真实产物发布下载。PR #268 合成部署配置已验，11-16 PR #288 已修来源错误提示；正式环境、目标机及 D5B 关闭条件见 PLAN。 |
| IM 与客户端选型 | 自有 /messages、Zulip 自建首选继续验证，生产最终采用未定；成熟 Web 薄壳允许复用。范围见 [11-04 裁决](DECISIONS.md#2026-11-04--裁决im-消息功能机会层选型更新与聊天交互边界放宽)和 [ARCHITECTURE](ARCHITECTURE.md)。 |
| Runtime 与 Golden | M0/M1A/M1B/M2/M3 和 M6 已合并；11-03 起 AgentScope 为缺省路径，legacy 为显式兼容路径。required CI 的 AgentScope 与 legacy Golden 各有入口，旧 M1B/legacy 成绩只作来源证据。目标环境集成和 M3/M5 未完成验收见 PLAN。 |
| Chat / D / 固定查询与远程接管 | 11-01 M3 改为人工交接，11-02 PR #246 实测启动失败后释放与再次报修；11-06 PR #270 合并远程画面/接管。真实交接终态、重启自动回收、远程兼容与续填仍待核，见 [开发交接](../dev/chat_browser_mcp_handoff.md)和 PLAN。 |
| MCP 平台与接续 | `P2-MCP-PLATFORM-001`（09-30，PR #218）和 `P2-MCP-PR218-FIX-001`（10-08）均已合并；PR #229 已完成同用户换绑后的真实重授权。公司 13 工具确认、获准部署/真实 PostgreSQL 复验及续期 E1 见 PLAN，合并事实不核销这些义务。 |
| 任务派发与组织目录 | PR #225 已交付 B01–B12，附件上传、关联、授权下载及宿主接线已存在；专用库迁移往返及 10-24/25 合成验证有记录。真实页面/环境 E01–E10、生产目录来源 G2/X01 与账号停用判定仍开放，见 [派发合同](WORK_DISPATCH.md)和 [PLAN 活欠债](PHASE2_PLAN.md#既有活欠债)。 |
| 租户与管理 | 09-23 租户身份 S1、09-29 admin events 已合并；S2/S3 和真实目录身份链未闭合，第二租户禁止启用，见 PLAN。 |

## 必达链与阻塞

- 必达五项：OA 只读纵切、Work Object + 最小工作台、后台轮询已完成；低风险写入未完成；Golden 部分完成（`P2-GOLDEN-001` 已完成）。
- 唯一剩余必达链：`P2-LOW-RISK-WRITE-001 → P2-GOLDEN-002`。前者阻塞于真实 OA 审批提交协议结构；输入到位后才能开棒。
- Golden 覆盖 Runtime 观察边界；工作台、隔离和审计由 API 与单元层验证。其他外部输入、活义务与后继以 [PLAN](PHASE2_PLAN.md) 为准。
- 现场待核：IM 测试 CA 原到期点为 2026-11-11T01:44:24Z；续签、19412/19415 与获准客户端访问状态待核；D5B 仍需真实 pending 证据。第二真实 Adapter 和 Memory 仍待外部输入。
- Owner 待办：为 `phase0/main` 分支保护开启 **Do not allow bypassing the above settings**；修改须专项授权。

## 已登记验证基线

### P2-TENANT-IDENTITY-001 S1 来源验证基线（2026-09-23 实测；PR #212 于 2026-09-23 合并）

后端、架构与 Golden 数字均为该棒 2026-09-23 实测；合并事实以 PR #212 为准。完整证据见[原状态归档](archive/STATUS_history_to_20261119.md#p2-tenant-identity-001-s1-来源验证基线2026-09-23-实测pr-212-于-2026-09-23-合并)。

- pytest：`4170 passed, 0 failed, 0 skipped`（固定测试库后端全量，exit=0、后台 status=passed；无 `--ignore=`）。Windows access violation 按 2026-09-10 裁决记录。
- `tests/architecture/`：`135 passed`（本棒定向；全量亦覆盖。机械同步后另核 SSOT）。
- Golden Gate：`34/34 passed, 0 skipped, 0 failed`（本棒实测，positive 13/13、negative/boundary 21/21，含 GT-012）。

Windows 环境噪音判据见 [2026-09-10 裁决](DECISIONS.md#2026-09-10--裁决access-violation-按非致命环境噪音放行)。

## 指针

- [范围、DAG 与活欠债](PHASE2_PLAN.md)
- [架构及治理裁决](DECISIONS.md)
- [历史状态全文](archive/STATUS_history_to_20261119.md)
