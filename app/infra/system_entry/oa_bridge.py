"""Independent OA browser proxy. Run via python -m app.system_entry.oa_bridge."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlsplit

import httpx2
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse, Response, StreamingResponse

from app.infra.system_entry.tickets import RedisOAEntryTickets

_log = logging.getLogger(__name__)
OA_COOKIE_NAMES = frozenset({
    "ecology_JSessionid", "__clusterSessionIDCookieName", "__clusterSessionCookieName",
    "__randcode__", "loginidweaver", "languageidweaver", "loginuuids",
})
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def create_oa_bridge(
    *, tickets: RedisOAEntryTickets, upstream: str, public_base: str,
) -> FastAPI:
    upstream = upstream.rstrip("/")
    public_base = public_base.rstrip("/")
    public = urlsplit(public_base)
    secure = public.scheme == "https"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await tickets.aclose()

    app = FastAPI(
        title="OA entry bridge", docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=lifespan,
    )

    @app.middleware("http")
    async def host_and_errors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.headers.get("host") != public.netloc:
            return Response(status_code=403, headers=_NO_STORE)
        try:
            return await call_next(request)
        except Exception:
            # No traceback: exceptions and URLs can contain a ticket, cookie or query.
            _log.warning("oa_bridge stage=request result=unavailable")
            return Response(status_code=502, headers=_NO_STORE)

    @app.get("/oa-enter/{ticket}")
    async def enter(ticket: str) -> Response:
        cookies = await tickets.consume(ticket)
        if cookies is None:
            _log.info("oa_bridge stage=redeem result=expired_or_used")
            return Response(status_code=403, headers=_NO_STORE)
        response = RedirectResponse("/wui/index.html#/main", status_code=302, headers=_NO_STORE)
        for name in OA_COOKIE_NAMES - cookies.keys():
            response.delete_cookie(
                name, path="/", secure=secure, httponly=name == "ecology_JSessionid",
                samesite="lax",
            )
        for name, value in cookies.items():
            if name in OA_COOKIE_NAMES:
                response.set_cookie(
                    name, value, path="/", secure=secure, samesite="lax",
                    httponly=name == "ecology_JSessionid",
                )
        _log.info("oa_bridge stage=redeem result=accepted")
        return response

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(request: Request, path: str) -> Response:
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            source = origin if origin is not None else _origin(referer)
            if source != public_base:
                return Response(status_code=403, headers=_NO_STORE)
        connection_headers = {
            item.strip().lower() for item in request.headers.get("connection", "").split(",")
        }
        excluded = _HOP_HEADERS | connection_headers | {
            "host", "cookie", "origin", "referer", "authorization", "x-eternalai-csrf",
        }
        headers = {name: value for name, value in request.headers.items() if name not in excluded}
        outgoing_cookies = SimpleCookie()
        for name, value in request.cookies.items():
            if name in OA_COOKIE_NAMES:
                outgoing_cookies[name] = value
        cookie_header = outgoing_cookies.output(header="", sep=";").strip()
        if cookie_header:
            headers["cookie"] = cookie_header
        if origin == public_base:
            headers["origin"] = upstream
        if _origin(referer) == public_base:
            parsed = urlsplit(referer or "")
            headers["referer"] = (
                upstream + parsed.path + ("?" + parsed.query if parsed.query else "")
            )
        raw_path = request.scope["raw_path"]
        if request.scope["query_string"]:
            raw_path += b"?" + request.scope["query_string"]
        url = httpx2.URL(upstream).copy_with(raw_path=raw_path)
        # Per-request client: its upstream CookieJar never crosses browser users.
        client = httpx2.AsyncClient(timeout=60, follow_redirects=False, trust_env=False)
        try:
            upstream_request = client.build_request(
                request.method, url, headers=headers,
                content=(request.stream() if (
                    "content-length" in request.headers or "transfer-encoding" in request.headers
                ) else None),
            )
            result = await client.send(upstream_request, stream=True)
        except Exception:
            await client.aclose()
            raise

        async def close() -> None:
            await result.aclose()
            await client.aclose()

        try:
            excluded_response = _HOP_HEADERS | {
                item.strip().lower() for item in result.headers.get("connection", "").split(",")
            } | {"set-cookie", "cache-control"}
            response = StreamingResponse(
                result.aiter_raw(), status_code=result.status_code,
                background=BackgroundTask(close),
            )
            response.raw_headers = [
                (name, value) for name, value in result.headers.raw
                if name.decode("latin-1").lower() not in excluded_response
            ]
            response.headers["Cache-Control"] = "no-store"
            location = result.headers.get("location")
            if location is not None:
                absolute = urljoin(str(url), location)
                parsed = urlsplit(absolute)
                if _origin(absolute) == upstream:
                    response.headers["location"] = (
                        public_base + parsed.path + ("?" + parsed.query if parsed.query else "")
                        + ("#" + parsed.fragment if parsed.fragment else "")
                    )
            for raw_cookie in result.headers.get_list("set-cookie"):
                jar = SimpleCookie()
                jar.load(raw_cookie)
                for name, cookie in jar.items():
                    if name not in OA_COOKIE_NAMES:
                        continue
                    # Drop upstream Domain, normalize Path; preserve renew/delete and JS access.
                    response.set_cookie(
                        name, cookie.value, path="/",
                        max_age=int(cookie["max-age"]) if cookie["max-age"] else None,
                        expires=cookie["expires"] or None,
                        secure=secure or bool(cookie["secure"]), httponly=bool(cookie["httponly"]),
                        samesite=cookie["samesite"] or "lax",
                    )
            _log.info("oa_bridge stage=proxy status=%s", result.status_code)
            return response
        except Exception:
            await close()
            raise

    return app


def _origin(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"
