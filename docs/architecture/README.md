# Architecture Notes

The repository currently uses a Python-first monorepo-style bootstrap layout:
- `services/api` for API-facing service entrypoints
- `services/worker` for background execution
- `services/asr` for future speech-related integration
- `apps/web` and `apps/miniapp` as client boundaries

This is a starting point for later business repo planning, not the final production architecture.
