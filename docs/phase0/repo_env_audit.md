# repo_env_audit.md — Phase 0 Environment Readiness

Generated: 2026-05-09T20:50:00+08:00
Task: P0-PREP-001

---

## 1. Git Repository

| Item | Command | Result | Evidence | Status |
|------|---------|--------|----------|--------|
| Root directory | `git rev-parse --show-toplevel` | `E:/code/eternalai` | stdout | passed |
| Current branch | `git branch --show-current` | `phase0/P0-PREP-001` | stdout | passed |
| Working tree status | `git status --short` | `.gitignore` added; `phase0_execution_pack_v1_0_11.zip` deleted (extracted to repo root); `docs/phase0/` outputs added | stdout | passed |
| Recent commits | `git log --oneline -5` | b2e2769, 5b87f29, 77ed139, be22190, 2499488 | stdout | passed |
| Remote | `git remote -v` | `origin https://github.com/evarainy/eternalai.git` | stdout | passed |

---

## 2. Claude Code / Codex Configuration

| Item | Command | Result | Evidence | Status |
|------|---------|--------|----------|--------|
| Project settings | `cat .claude/settings.json` | File does not exist | filesystem check | not_applicable |
| Local settings | `cat .claude/settings.local.json` | Exists. Contains Bash permission allowlist for `uv`, `pnpm`, `docker`, `git`, `pip config`, `npm config` commands. No sandbox/network/security configuration found. | file content | passed |
| Hooks | `ls .claude/` | No hooks.json found. Only `agents/`, `settings.example.json`, `settings.local.json` present. | directory listing | not_applicable |
| Codex OTel | env / config check | Not configured. No OTel environment variables or Codex audit configuration found. | env check | blocked |
| Audit alternative | rule check | Git history + CI + Task Record serving as audit fallback per AGENTS.md rule. | AGENTS.md | passed |

### 2a. Codex Sandbox / Approval / Network Posture — Detailed

| Item | Command | Result | Evidence | Status | Conclusion |
|------|---------|--------|----------|--------|------------|
| Sandbox mode | `cat .claude/settings.local.json` | `permissions.allow` list present; 7 Bash commands whitelisted (`uv`, `pnpm`, `docker`, `docker compose`, `git`, `pip config`, `npm config`). No `deny` list. No `sandbox` key found. | `.claude/settings.local.json` content | blocked | Sandbox mode not explicitly configured; Claude Code defaults to interactive approval for non-whitelisted commands. No restrictive sandbox policy found. |
| Approval posture | `cat .claude/settings.local.json` | No `auto_accept`, `yolo`, or `dangerously_skip_confirmation` key found. Approval is per-command via the allowlist. | `.claude/settings.local.json` content | passed | Standard interactive approval mode active. Each non-allowlisted tool call requires human confirmation. |
| Network posture | env + config check | No proxy, no VPN, no network restriction config found in `.claude/` or environment variables. Docker available (Docker 29.4.0) suggesting host network access. `npm config get registry` returns public `https://registry.npmjs.org/`. | env check + npm config | passed | Network is open to public registries. No internal network isolation detected. Public PyPI/npm accessible. |
| OTel / audit | env + config check | `OTEL_*` environment variables not set. No Codex OTel integration configured. No CI workflow file triggered by this task. | env check | blocked | Codex OTel not configured. Audit fallback: Git commit history + this Task Record. Acceptable for Phase 0 preparation tasks; may need resolution before implementation tasks. |

---

## 3. Development Toolchain

| Tool | Command | Result | Evidence | Status |
|------|---------|--------|----------|--------|
| Python | `python --version` | Python 3.12.10 | stdout | passed |
| uv | `uv --version` | Command not found | stderr: `uv: command not found` | blocked |
| Node.js | `node --version` | v24.15.0 | stdout | passed |
| pnpm | `pnpm --version` | Command not found | stderr: `pnpm: command not found` | blocked |
| Docker | `docker --version` | Docker version 29.4.0, build 9d7ad9f | stdout | passed |
| Docker Compose | `docker compose version` | Docker Compose version v5.1.2 | stdout | passed |
| Git | `git --version` | git version 2.53.0.windows.3 | stdout | passed |

---

## 4. GPU / CUDA / vLLM

| Item | Command | Result | Evidence | Status |
|------|---------|--------|----------|--------|
| GPU | `nvidia-smi` | NVIDIA GeForce 940MX, 2048 MiB, WDDM mode, no running processes | nvidia-smi stdout | passed |
| Driver version | `nvidia-smi` | 531.79 | nvidia-smi stdout | passed |
| CUDA version | `nvidia-smi` | CUDA 12.1 | nvidia-smi stdout | passed |
| PyTorch / CUDA runtime | `python -c "import torch; ..."` | ModuleNotFoundError: No module named 'torch' | stderr | blocked |
| vLLM | `pip show vllm` | Not installed (no pyproject.toml, no pip packages for vLLM) | filesystem check | blocked |

**Note**: NVIDIA GeForce 940MX with 2GB VRAM is insufficient for LLM inference workloads. Even with vLLM installed, this GPU cannot serve production models.

**Execution pack note**: The original execution ZIP (`phase0_execution_pack_v1_0_11.zip`) is intentionally not kept as a tracked repository artifact after extraction. Unpacked files remain in the repository.

---

## 5. Internal Mirror Configuration

| Item | Command | Result | Evidence | Status |
|------|---------|--------|----------|--------|
| PyPI mirror | `pip config list` | No output (no index-url configured) | stdout (empty) | blocked |
| npm registry | `npm config get registry` | `https://registry.npmjs.org/` (default public registry) | stdout | blocked |
| Project .npmrc | `test -f .npmrc` | File does not exist | filesystem check | blocked |
| pyproject.toml | `test -f pyproject.toml` | File does not exist | filesystem check | not_applicable |
| uv mirror config | filesystem check | uv not installed; no `~/.config/uv/uv.toml` or project config | n/a | blocked |

---

## 6. Summary

### Passed items
- Git repository: functional, correct branch, clean-ish working tree
- Claude Code: operational with local settings permission allowlist
- Python 3.12.10: available
- Node.js v24.15.0: available
- Docker 29.4.0 + Compose v5.1.2: available
- Git 2.53.0: available
- GPU hardware detected (NVIDIA GeForce 940MX)
- CUDA 12.1 available from driver

### Blocked items (must resolve before dependent tasks)
- **uv**: not installed. Required for Python dependency management in Phase 0 tasks.
- **pnpm**: not installed. Required for frontend/Node dependency management.
- **PyTorch**: not installed. Required for GPU / vLLM spike tasks.
- **vLLM**: not installed. Blocked by PyTorch and GPU VRAM constraints.
- **GPU VRAM**: 2048 MiB (940MX) insufficient for LLM inference. P0-SPIKE tasks requiring GPU will be blocked.
- **Codex OTel**: not configured. Using Git + CI + Task Record as audit fallback.
- **PyPI mirror**: no internal mirror configured. All pip installs will use public PyPI (network-dependent).
- **npm mirror**: default public registry. No internal mirror configured.

### Not applicable
- `.claude/settings.json` (project-level): does not exist, using local settings only
- `.claude/hooks.json`: does not exist (hooks are optional enhancement)
- `pyproject.toml`: does not exist (project not yet packaged)
