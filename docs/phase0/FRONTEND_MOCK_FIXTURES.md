# FRONTEND_MOCK_FIXTURES — Phase 0 v1.0.11

Phase 0 frontend mock fixtures are only for OpenAPI client generation checks and static SDUI rendering checks. They must not replace backend Golden Task validation.

Recommended paths:

```text
web/tests/fixtures/openapi/
web/tests/fixtures/sdui/
```

Rules:

- OpenAPI fixtures may be used before backend OpenAPI stabilizes.
- SDUI fixtures may cover `message`, `fallback_text`, `confirm_card`, `operator_handback_card`, and `binding_required_card`.
- Fixtures must not call real OA / U8 / Hik systems.
- Fixtures must not include real credentials, tokens, cookies, `api_key`, or private keys.
- Frontend mock fixtures are not acceptance evidence for Runtime / Gateway / Adapter behavior; Golden Task remains the backend integration evidence.
