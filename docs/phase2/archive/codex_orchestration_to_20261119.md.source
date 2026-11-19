# Codex 主控使用说明

项目规则见 [AGENTS.md](../../AGENTS.md)，路由权威见 [DECISIONS.md](../phase2/DECISIONS.md) 2026-09-30 最新补充（P2-GOV-SYNC-071）。本页是操作速查；不新增人工 Gate。

| 环节 | 模型与 effort |
|---|---|
| 主控 | Astra max |
| A/B plan → 独立计划评审 | GPT Pro max → 全新 Astra high |
| A 施工 → 新上下文自审 → 静态审核 | Astra high → Astra medium → MiMo Pro max |
| B 施工 → 新上下文自审 → 静态审核 | Sol max → Sol xhigh → MiMo Pro max |
| C 施工 → 审核 | MiMo Pro max → 全新 Sol high |
| D 施工 | MiMo Flash max，无独立审核 |

A 和高风险 B 在新上下文自审后、静态审核前插入 Astra high 独立 Monitor；职责和轮次见 AGENTS。其余验证按真实改动选择。MiMo 的 max 是启用思考/high 的配置别名，不代表额外强度。

## 启动

`.codex/config.toml` 只设项目主控模型与 effort；原生 `.codex/agents/` 分别提供 plan-reviewer、a-worker、b-worker、a-self-reviewer、b-self-reviewer、c-reviewer、monitor。新会话才加载角色与模型，当前窗口不会自动切换。审查每次新开上下文，不使用 resume/continue。 plan-reviewer 已设为 Astra high；已加载旧角色的窗口须在新会话显式使用 `-m gpt-6-astra -c 'model_reasoning_effort="high"' --sandbox read-only`，不能沿用旧的 ultra 角色。

手工调用时显式选择模型与 effort，例如（PowerShell；在目标 worktree 执行）：

```powershell
codex -m gpt-6-astra -c 'model_reasoning_effort="high"'
codex -m gpt-6-sol -c 'model_reasoning_effort="max"'
opencode run --pure --model mimo/mimo-v2.6-pro --variant max --title "TASK C implementation" "按已批准 Scope 施工"
opencode run --pure --model mimo/mimo-v2.6-flash --variant max --title "TASK D implementation" "按明确的精确修改清单施工"
```

OpenCode 使用现有个人 mimo provider 和凭证；项目只补 variants.max 与 packet-review，不改默认模型。D 档优先附必要片段和精确修改，避免反复通读长治理文档。

## 新窗口接续

新窗口按项目现役规则与模型路由，凭简短交接先核对本地候选、未提交修改、审查绑定与待授权事项，有效证据直接复用；仅因候选变化或具体问题重跑适用检查。已有窗口不保证热更新，新 worktree 不继承未提交配置，需自行核对后生效。 交接保存在仓库外，只列当前任务、候选与证据位置、未提交修改、待授权事项和下一步；不复制整段聊天作为任务输入。

## GPT Pro 计划

codex-chatgpt-web 使用 Browser-only；用户本人完成 ChatGPT 登录与账号可用模型确认，不需要 MCP 或 API key。它会把本次 Codex 上下文发送给 ChatGPT，必须在仓库外新会话只给必要、已脱敏的计划输入。未取得真实 Pro 输出前不能把 Astra 草案标为 Pro 计划。

接入完成后，在仓库外临时目录执行（输入与输出路径替换为本任务路径）：

```powershell
Get-Content -Raw -LiteralPath '<packet.md>' | codex exec --skip-git-repo-check --sandbox read-only -m chatgpt-web/gpt-6-pro -c 'model_reasoning_effort="max"' -o '<plan.md>' -
```

Pro 不可用时记录阻塞，不自动换作者；输出交由全新 plan-reviewer 审查。A/B 通过后再施工。

### 按执行者控制提示粒度

每次调用 Pro 前，主控填齐以下执行者参数。施工模型与 effort 按本页路由表和 DECISIONS 最新裁决填写。材料包含已核对的生成/批量命令实际写入范围；命令会触碰授权范围之外的文件时，按现役范围约束改在临时镜像生成、比较后仅同步获准输出。

一、计划提示模板（按执行者裁剪粒度）：【档位】任务难度档 A/B/C/D；【风险标记】普通/高风险、受影响边界与主要失败点；【executor_model】执行模型标识；【executor_effort】实际思考强度（按现役路由填写 high 或 max）；【Scope】要求修改与明确不修改的边界；【合同与验收标准】输入输出约定、可逐条检查的通过条件与验证命令。
二、粒度按执行者匹配：A 执行时，清楚写明架构选择、关键状态与接口、依赖关系、失败情形与验收边界，内部实现方式保留执行者裁量；B 执行时，写明具体文件与入口、关键分支、操作步骤、成功/失败/边界情形与验证命令。C、D 由主控直接下任务，不额外产出 Pro plan：C 得到有序的文件、步骤、预期结果与验证任务书；D 得到精确的修改/替换指令与最小核对清单。
三、具体位置、路径或命令若无事实依据，一律标注“待核对”，不虚构。
四、权限、范围、验证及审查义务不因执行模型不同而降低；计划以满足验收为准，不机械追求更长。

### Pro 额度提醒

每次 Pro 调用后检查结果是完成还是报错。只有出现明确的 quota、usage-limit、reached-limit、额度耗尽等信号，才判定为额度耗尽，并立即在当前会话提醒用户：有恢复时间就写明，没有就写“未知”；保留已有产物与脱敏证据，暂停依赖 Pro 的步骤，不静默更换模型。429 单独出现可能只是临时限流，登录或网络错误不等于额度耗尽；proAvailable 不是剩余次数，Codex 用量也不等于 ChatGPT Pro 额度。没有可靠读数时不报告剩余百分比，也不承诺提前预警。

## 静态审核

准备完整必要规则、合同、原始 diff、验证摘要和最终 base/head 的脱敏 packet；不附 .env、凭证或原始 OA。原生 OpenCode 示例：

```powershell
Get-Content -Raw -LiteralPath '<packet.md>' | opencode run --pure --model mimo/mimo-v2.6-pro --variant max --agent packet-review --format json --title "TASK static review"
```

大材料包通过标准输入传入，记录实际输入的字节数与 SHA256；不把文件附件的展示摘要当作完整原文。packet-review 禁用工具，仅审查附入材料。保存实际会话产物；PR 按 AGENTS 记录模型请求与实际证据，不能伪造桥脚本字段。自审、Monitor、静态审核与实际验证分别如实记录；D 不额外审核。集成仍走任务分支 PR，合并需有授权。
