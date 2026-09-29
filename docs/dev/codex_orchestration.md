# Codex 主控使用说明

项目规则见 [AGENTS.md](../../AGENTS.md)，路由权威见 [DECISIONS.md](../phase2/DECISIONS.md) 2026-09-29 裁决。本页是操作速查；不新增人工 Gate。

| 环节 | 模型与 effort |
|---|---|
| 主控 | Astra ultra |
| A/B plan → 独立计划评审 | GPT Pro max → 全新 Astra ultra |
| A 施工 → 新上下文自审 → 静态审核 | Astra high → Astra medium → MiMo Pro max |
| B 施工 → 新上下文自审 → 静态审核 | Sol max → Sol xhigh → MiMo Pro max |
| C 施工 → 审核 | MiMo Pro max → 全新 Sol high |
| D 施工 | MiMo Flash max，无独立审核 |

A 和高风险 B 在新上下文自审后、静态审核前插入 Astra high 独立 Monitor；职责和轮次见 AGENTS。其余验证按真实改动选择。MiMo 的 max 是启用思考/high 的配置别名，不代表额外强度。

## 启动

`.codex/config.toml` 只设项目主控模型与 effort；原生 `.codex/agents/` 分别提供 plan-reviewer、a-worker、b-worker、a-self-reviewer、b-self-reviewer、c-reviewer、monitor。新会话才加载角色与模型，当前窗口不会自动切换。审查每次新开上下文，不使用 resume/continue。

手工调用时显式选择模型与 effort，例如（PowerShell；在目标 worktree 执行）：

```powershell
codex -m gpt-6-astra -c 'model_reasoning_effort="high"'
codex -m gpt-6-sol -c 'model_reasoning_effort="max"'
opencode run --pure --model mimo/mimo-v2.6-pro --variant max --title "TASK C implementation" "按已批准 Scope 施工"
opencode run --pure --model mimo/mimo-v2.6-flash --variant max --title "TASK D implementation" "按明确的精确修改清单施工"
```

OpenCode 使用现有个人 mimo provider 和凭证；项目只补 variants.max 与 packet-review，不改默认模型。D 档优先附必要片段和精确修改，避免反复通读长治理文档。

## GPT Pro 计划

codex-chatgpt-web 使用 Browser-only；用户本人完成 ChatGPT 登录与账号可用模型确认，不需要 MCP 或 API key。它会把本次 Codex 上下文发送给 ChatGPT，必须在仓库外新会话只给必要、已脱敏的计划输入。未取得真实 Pro 输出前不能把 Astra 草案标为 Pro 计划。

接入完成后，在仓库外临时目录执行（输入与输出路径替换为本任务路径）：

```powershell
Get-Content -Raw -LiteralPath '<packet.md>' | codex exec --skip-git-repo-check --sandbox read-only -m chatgpt-web/gpt-6-pro -c 'model_reasoning_effort="max"' -o '<plan.md>' -
```

Pro 不可用时记录阻塞，不自动换作者；输出交由全新 plan-reviewer 审查。A/B 通过后再施工。

## 静态审核

准备完整必要规则、合同、原始 diff、验证摘要和最终 base/head 的脱敏 packet；不附 .env、凭证或原始 OA。原生 OpenCode 示例：

```powershell
opencode run --pure --model mimo/mimo-v2.6-pro --variant max --agent packet-review --format json --title "TASK static review" --file '<packet.md>' "静态审查附件；证据不足返回 BLOCKED"
```

packet-review 禁用工具，仅审查附入材料。保存实际会话产物；PR 按 AGENTS 记录模型请求与实际证据，不能伪造桥脚本字段。自审、Monitor、静态审核与实际验证分别如实记录；D 不额外审核。集成仍走任务分支 PR，合并需有授权。
