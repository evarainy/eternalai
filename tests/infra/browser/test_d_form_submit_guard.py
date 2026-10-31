"""Real loopback Chromium regression for Monitor r3 M3-1; no OA services."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import psutil
import pytest
from playwright.async_api import async_playwright

from app.infra.browser.d_form_execution import DFormRuntime
from app.infra.browser.d_form_flow import Flow

HTML = """<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,">
<form id="form" action="/submitted" method="post">
<div><label for="category">报修类别</label><select id="category">
<option></option><option>synthetic-category</option></select></div>
<div><label for="position">故障位置</label><input id="position"></div>
<div><label for="description">故障描述</label><textarea id="description"></textarea></div>
<button id="submit" type="submit">提交</button>
<button id="save" type="button" onclick="saveCount++;fetch('/saved',{method:'POST'})">
<span>保存</span></button></form>
<script>
window.submitCount=0;window.saveCount=0;
document.querySelector('form').onsubmit=e=>{
  e.preventDefault();window.submitCount++;fetch('/submitted',{method:'POST'});
};
</script>"""


@asynccontextmanager
async def synthetic_browser(tmp_path):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.path)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    process = None
    try:
        async with async_playwright() as pw:
            executable = (
                "C:/Program Files/Google/Chrome/Application/chrome.exe"
                if sys.platform == "win32" else pw.chromium.executable_path
            )
        process = subprocess.Popen(
            [
                executable, "--headless=new", "--no-sandbox", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-component-update", "--disable-sync", "--disable-extensions",
                "--no-proxy-server",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
                "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                "--user-data-dir=" + str(profile), "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        endpoint_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, "synthetic_chrome_exited"
            assert time.monotonic() < deadline, "synthetic_chrome_start_timeout"
            # Chromium creates this file before releasing its Windows write handle.
            # Existence alone is not the endpoint readiness signal.
            try:
                lines = endpoint_file.read_text(encoding="ascii").splitlines()
                if len(lines) == 2:
                    break
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(0.1)
        endpoint = f"ws://127.0.0.1:{lines[0]}{lines[1]}"
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(endpoint)
            context, page = browser.contexts[0], browser.contexts[0].pages[0]
            await page.goto(f"http://127.0.0.1:{server.server_port}/form")
            cdp = await context.new_cdp_session(page)
            target = (await cdp.send("Target.getTargetInfo"))["targetInfo"]
            await cdp.detach()
        binding = dict(
            pid=process.pid, created=psutil.Process(process.pid).create_time(),
            executable=executable, profile=str(profile), profile_root=str(tmp_path),
            port=int(lines[0]), context_id=target["browserContextId"],
            target_id=target["targetId"], host="127.0.0.1", scheme="http",
        )
        yield binding, endpoint, posts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if process is not None:
            parent = psutil.Process(process.pid)
            owned = [parent, *parent.children(recursive=True)]
            for child in reversed(owned):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(owned, timeout=5)
            for child in alive:
                child.kill()
            process.wait(timeout=5)
            assert process.poll() is not None


async def run_flow(tmp_path, binding):
    stages = {1: {"status": "PASS"}}

    def emit(number, status, _elapsed, code):
        stages[number] = {"status": status, "code": code}

    flow = Flow(
        dict(
            mode="FILL_STOP", binding=binding, local_gate=False,
            inputs=dict(category="synthetic-category", position="synthetic-place",
                        description="synthetic-fault"),
        ),
        emit, asyncio.Event(),
    )
    summary = await flow.run()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(summary), encoding="utf-8")
    state = SimpleNamespace(
        receipt_path=receipt, process=SimpleNamespace(returncode=0 if summary["passed"] else 2),
        output_invalid=False, connected=True, acquired_evidence=b"synthetic",
        attachment_cleanup_proven=stages.get(7, {}).get("status") == "PASS",
        owned_resource_cleanup_proven=False, stages=stages, began=time.monotonic(),
    )
    report = DFormRuntime._report(object.__new__(DFormRuntime), state)
    return summary, report, stages


@pytest.mark.parametrize("action", ["submit", "save", "request_submit", "native_submit"])
def test_fill_stop_submit_click_is_detected(tmp_path, monkeypatch, action):
    original = Flow.verify

    async def verify_after_injection(self):
        # Exactly the r3 trigger point: all three fills done, verification not yet run.
        if action in {"submit", "save"}:
            await self.page.locator("#" + action).click()
        elif action == "request_submit":
            await self.page.locator("#form").evaluate("form => form.requestSubmit()")
        else:
            # The #submit control shadows the form.submit property in HTML forms.
            await self.page.locator("#form").evaluate(
                "form => HTMLFormElement.prototype.submit.call(form)"
            )
        await original(self)

    monkeypatch.setattr(Flow, "verify", verify_after_injection)

    async def exercise():
        async with synthetic_browser(tmp_path) as (binding, endpoint, posts):
            summary, report, stages = await run_flow(tmp_path, binding)
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(endpoint)
                counts = await browser.contexts[0].pages[0].evaluate(
                    "({submit: window.submitCount, save: window.saveCount})"
                )
            print(json.dumps(dict(
                action=action, handler_counts=counts, server_posts=posts,
                flow_passed=summary["passed"], flow_code=summary["error_code"],
                submit_actions=report.submit_actions, status=report.execution_status,
                verification=report.verification,
            )))
            assert posts == [], "save_or_submit_request_must_not_reach_server"
            assert counts == {"submit": 0, "save": 0}
            assert summary["passed"] is False
            assert summary["error_code"] == "FORM_SUBMIT_BLOCKED"
            assert summary["submit_actions"] == 1
            assert report.submit_actions == 1
            assert report.execution_status == "unknown"
            assert report.verification == "unknown"
            assert report.error_code == "FORM_SUBMIT_BLOCKED"
            assert stages[8] == {"status": "FAIL", "code": "FORM_SUBMIT_BLOCKED"}

    asyncio.run(exercise())


def test_same_host_non_submit_post_is_allowed(tmp_path, monkeypatch):
    original = Flow.verify

    async def verify_after_homepage_post(self):
        status = await self.page.evaluate(
            "async () => (await fetch('/homepage-refresh', {method: 'POST'})).status"
        )
        assert status == 204
        assert await self.page.locator("#category").input_value() == "synthetic-category"
        assert await self.page.locator("#position").input_value() == "synthetic-place"
        assert await self.page.locator("#description").input_value() == "synthetic-fault"
        await original(self)

    monkeypatch.setattr(Flow, "verify", verify_after_homepage_post)

    async def exercise():
        async with synthetic_browser(tmp_path) as (binding, _endpoint, posts):
            summary, report, stages = await run_flow(tmp_path, binding)
            assert posts == ["/homepage-refresh"]
            assert summary["passed"] is True
            assert summary["executed_actions"] == [
                "CATEGORY_SELECT", "POSITION_INPUT", "DESCRIPTION_INPUT",
            ]
            assert summary["submit_actions"] == report.submit_actions == 0
            assert report.execution_status == "filled_stopped"
            assert report.verification == "verified"
            assert stages[8] == {"status": "PASS", "code": "OK"}

    asyncio.run(exercise())
