# Spec Freeze Check — P0-PREP-003

checked_at: 2026-05-09T23:00:00+08:00
checked_by_task_id: P0-PREP-003

frozen_files:
  - path: docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md
    sha256: 777BCB5DCF653D4D9F3E24574C9997A756379014E5A7F0E6667A3D192E263B54
  - path: docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md
    sha256: 4AE8563FEC396A96EE1FE49730451115214A92AE520E323080E6E30EF70B9093

freeze_rule: >
  后续任务只能引用冻结文件，不得直接修改；如需变更，必须新建 patch / ADR，不得改写冻结文件。

verification_commands:
  - "Get-FileHash docs/blueprint/enterprise_agent_runtime_blueprint_v3_2_4_freeze_final.md -Algorithm SHA256"
  - "Get-FileHash docs/blueprint/phase0_architecture_freeze_and_mvp_spec_v1_0_11.md -Algorithm SHA256"
  - "git diff --name-status -- docs/blueprint/"
  - "git status --short"
  - "Get-Content docs/phase0/spec_freeze_check.md"
