# Plan Review Checklist

- [ ] `planning/bootstrap_result.json` reflects the current verified host runtime and still marks Docker as unverified.
- [ ] `spec/product_spec.md` describes the intended business slice without claiming existing implementation.
- [ ] `spec/product_spec.yaml`, `spec/module_spec.yaml`, `spec/resource_spec.yaml`, and `spec/invariant_spec.yaml` agree on the same service boundaries and resource names.
- [ ] `contracts/http/api.yaml`, `contracts/http/asr.yaml`, and `contracts/events/worker.yaml` clearly separate implemented vs planned operations.
- [ ] `planning/dev_plan.yaml` tasks are granular, dependency-aware, and include all required fields.
- [ ] `planning/acceptance_spec.yaml` uses concrete commands, assertions, and evidence paths rather than vague prose.
- [ ] `planning/run_policy.yaml` keeps host-first verification as the baseline and does not overstate Docker readiness.
- [ ] `planning/implement.md` includes stop conditions and human review gates before execution.
- [ ] `planning/documentation.md` makes contract and runbook updates part of implementation, not an afterthought.
- [ ] `planning/phase0_result.json` distinguishes planning readiness from execution readiness.
- [ ] No business implementation leaked into service code, tests, or deployment assets during this planning round.
