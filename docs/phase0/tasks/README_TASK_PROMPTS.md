# Per-task Prompt Directory — Phase 0 v1.0.11

Use these files to avoid context rot. Do not paste the full Phase 0 spec into every coding-agent session.

Each per-task prompt should contain:

1. global hard rules;
2. the source spec path;
3. the selected task YAML;
4. failure/blocking examples required by that task type;
5. step verification expectations;
6. touched_paths and forbidden_paths.

Current execution package includes per-task prompts for Batch 0 + Batch 1 only. Generate later Batch prompts immediately before those batches begin, after all prerequisite ADRs and interfaces are available.


## Progressive loading rule

A per-task prompt is the primary context unit. It should be self-sufficient enough for the selected task. If a task requires more context, reference the exact Tier 2 file and section rather than asking the agent to read the whole Phase 0 spec. If the prompt is incomplete, stop and create a task-prompt patch before coding.
