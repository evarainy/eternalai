# Documentation Plan

## Purpose

Documentation should move in lockstep with implementation so the repository never has hidden contracts or hidden run steps.

## Required Documentation Outputs

- `contracts/http/*.yaml` and `contracts/events/*.yaml`
  Use as the source of truth for boundary definitions.
- `docs/architecture/README.md`
  Update when module boundaries, data flow, or async orchestration become real.
- `docs/runbooks/README.md`
  Expand into concrete host-first and later Docker runbooks as soon as workflows exist.
- `docs/observability/README.md`
  Keep current Langfuse-not-required semantics unless a verified rollout is added.
- service READMEs under `services/api`, `services/worker`, and `services/asr`
  Update each service README when its role grows beyond bootstrap.
- app READMEs under `apps/web` and `apps/miniapp`
  Document chosen stack, commands, and contract assumptions once clients are implemented.
- root `README.md`
  Update only after commands and feature boundaries are genuinely verified.

## Documentation Triggers

- New route or payload: update contract YAML in the same change.
- New operator command: update runbook and relevant README in the same change.
- New module or durable resource: update `docs/architecture/README.md`.
- Any Docker verification change: update root README and runbooks only after a real green run.

## Review Standard

Documentation is acceptable only when:
- it describes implemented behavior or explicitly says `planned`
- commands are copied from real, validated execution paths
- ownership and path boundaries match `spec/module_spec.yaml`
