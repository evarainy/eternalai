"""Bounded single-attempt HTTP decision adapter. No real client is created on import."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Literal, Self
from urllib.parse import urlsplit

import httpx2
from pydantic import model_validator

from app.browser_skill.models import (
    Contract,
    DecisionCallContext,
    DecisionRequest,
    DecisionResult,
    DecisionSource,
    Digest,
)
from app.infra.browser.decision_adapters import DecisionCodec, LocalChoiceCodec, ModelMismatch


def _http_exception_diagnostic(exc: httpx2.HTTPError) -> dict[str, str]:
    # Fixed labels only: exception messages, requests and dynamic class names
    # can contain private data. A generic timeout does not prove a wait phase.
    for error_type, kind, phase in (
        (httpx2.ConnectTimeout, "connect_timeout", "connect"),
        (httpx2.ReadTimeout, "read_timeout", "read"),
        (httpx2.WriteTimeout, "write_timeout", "write"),
        (httpx2.PoolTimeout, "pool_timeout", "pool"),
        (httpx2.TimeoutException, "timeout", "unknown"),
        (httpx2.ConnectError, "connect_error", "none"),
        (httpx2.ReadError, "read_error", "none"),
        (httpx2.WriteError, "write_error", "none"),
        (httpx2.CloseError, "close_error", "none"),
        (httpx2.ProxyError, "proxy_error", "none"),
        (httpx2.LocalProtocolError, "local_protocol_error", "none"),
        (httpx2.RemoteProtocolError, "remote_protocol_error", "none"),
        (httpx2.UnsupportedProtocol, "unsupported_protocol", "none"),
        (httpx2.DecodingError, "decoding_error", "none"),
    ):
        if isinstance(exc, error_type):
            return {"http_exception_kind": kind, "http_timeout_phase": phase}
    return {"http_exception_kind": "http_error", "http_timeout_phase": "none"}


class DecisionDeployment(Contract):
    """Operator-verified registry, not request-controlled flags or ENV labels.

    Cloud entries must be registered public synthetic fixtures. This M0 adapter
    accepts only that registered class; real/local data needs the M2 egress gate.
    """

    disposition: Literal["cloud_synthetic", "local_synthetic"]
    endpoint_origin: str
    manifest_digest: Digest
    registered_sources: tuple[DecisionSource, ...]

    @model_validator(mode="after")
    def canonical(self) -> Self:
        for origin in (self.endpoint_origin, *(item.origin for item in self.registered_sources)):
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or parsed.netloc != parsed.netloc.lower()
            ):
                raise ValueError("decision_origin_invalid")
        if self.disposition == "cloud_synthetic" and not self.endpoint_origin.startswith(
            "https://"
        ):
            raise ValueError("decision_cloud_requires_tls")
        ids = [source.source_id for source in self.registered_sources]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("decision_source_registry_invalid")
        return self


class DecisionHTTPProvider:
    """Caller owns client lifecycle, pinned endpoint and protected authorization.

    Production clients must disable redirects and environment proxy inheritance.
    This adapter never retries and never includes error bodies in its result.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        codec: DecisionCodec,
        deployment: DecisionDeployment,
    ) -> None:
        if str(client.base_url).rstrip("/") != deployment.endpoint_origin:
            raise ValueError("decision_endpoint_mismatch")
        self._client = client
        self._codec = codec
        self._deployment = deployment
        # Cancellation is a request, not proof that a transport has settled.
        # Retain late tasks and discard their answers without holding the caller.
        self._pending: set[asyncio.Task[DecisionResult]] = set()

    def _settled(self, task: asyncio.Task[DecisionResult]) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()  # Consume without exposing transport diagnostics.

    async def decide(
        self,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        return await self._decide_with_collector(request, context, {})

    async def _decide_with_collector(
        self, request: DecisionRequest, context: DecisionCallContext,
        collector: dict[str, str | int],
    ) -> DecisionResult:
        # One invocation owns this collector. The trusted caller freezes a copy;
        # a late task can only update this discarded invocation's dictionary.
        collector.update(http_status="unknown", http_headers_received=False, http_exception="none")
        def failure(error: str, reason: str) -> DecisionResult:
            return DecisionResult.model_validate(
                {
                    "request_id": request.request_id,
                    "scope": request.scope,
                    "error": error,
                    "reason": reason,
                }
            )

        if context.cancellation.is_set():
            return failure("cancelled", "cancelled")
        if (
            context.source is None
            or context.source not in self._deployment.registered_sources
            or context.manifest.manifest_digest != self._deployment.manifest_digest
            or context.current_targets is None
        ):
            return failure("input_unsupported", "bad_input")

        def current() -> bool:
            assert context.current_targets is not None
            refs = context.current_targets()
            return len(refs) == len(request.candidates) and set(refs) == {
                candidate.ref for candidate in request.candidates
            }

        if not current():
            return failure("invalid_response", "malformed")
        remaining = context.deadline_monotonic - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            return failure("timeout", "deadline")
        if len(request.candidates) > context.budget.max_candidates:
            return failure("input_unsupported", "budget")
        body = self._codec.encode(request, context)
        if len(body) > context.budget.max_request_bytes:
            return failure("input_unsupported", "budget")

        async def exchange() -> DecisionResult:
            async with self._client.stream(
                "POST",
                self._codec.path,
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=remaining,
                follow_redirects=False,
            ) as response:
                collector["http_headers_received"] = True
                status = response.status_code
                collector["http_status"] = (
                    status if type(status) is int and 100 <= status <= 599 else "unknown"
                )
                if response.status_code == 401:
                    return failure("unavailable", "unauthorized")
                if response.status_code == 422:
                    return failure("input_unsupported", "bad_input")
                if response.status_code == 429:
                    return failure("overloaded", "rate_limited")
                if response.status_code == 529:
                    return failure("overloaded", "capacity")
                if response.status_code == 504:
                    return failure("timeout", "deadline")
                if response.status_code == 499:
                    return failure("cancelled", "cancelled")
                if response.status_code == 412 and isinstance(self._codec, LocalChoiceCodec):
                    return failure("model_mismatch", "model")
                if response.status_code != 200:
                    return failure("unavailable", "transport")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > context.budget.max_response_bytes:
                        return failure("invalid_response", "budget")
                    data.extend(chunk)
                try:
                    return self._codec.decode(bytes(data), request, context)
                except ModelMismatch:
                    return failure("model_mismatch", "model")
                except (ValueError, TypeError, RecursionError):
                    return failure("invalid_response", "malformed")

        async def send() -> DecisionResult:
            try:
                return await exchange()
            except httpx2.TimeoutException as exc:
                collector["http_exception"] = "timeout"
                collector.update(_http_exception_diagnostic(exc))
                raise
            except httpx2.HTTPError as exc:
                collector["http_exception"] = "transport"
                collector.update(_http_exception_diagnostic(exc))
                raise
            except asyncio.CancelledError:
                collector["http_exception"] = "cancelled"
                raise

        operation = asyncio.create_task(send())
        cancellation = asyncio.create_task(context.cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancellation},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if context.cancellation.is_set():
                return failure("cancelled", "cancelled")
            if operation not in done or time.monotonic() >= context.deadline_monotonic:
                return failure("timeout", "deadline")
            result = operation.result()
            if not current():
                return failure("invalid_response", "malformed")
            result.validate_for(request, request.scope)
            return result
        except httpx2.TimeoutException:
            return failure("timeout", "deadline")
        except httpx2.HTTPError:
            return failure("unavailable", "transport")
        finally:
            for task in (operation, cancellation):
                if not task.done():
                    task.cancel()
            await asyncio.wait((operation, cancellation), timeout=0.05)
            if operation.done():
                self._settled(operation)
            else:
                self._pending.add(operation)
                operation.add_done_callback(self._settled)
            await asyncio.gather(cancellation, return_exceptions=True)
