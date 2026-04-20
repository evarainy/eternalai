# ASR Service Bootstrap

`services/asr` is the speech-service boundary in the EternalAI bootstrap scaffold.

Current scope:
- load ASR-related configuration
- expose a minimal `GET /health` endpoint
- reserve a stable service location for later speech features

Priority for future implementation:
- preferred backend: SenseVoice
- fallback backend: faster-whisper

Non-goals for this bootstrap:
- real recognition endpoints
- transcript generation
- streaming audio workflows
