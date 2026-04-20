# Worker Service Bootstrap

`services/worker` is the background execution boundary in the EternalAI bootstrap scaffold.

Current scope:
- load the shared runtime configuration profile plus worker queue settings
- initialize minimal logging
- expose a no-op bootstrap entrypoint

This service does not yet connect to queues, schedules, or business jobs.
