"""Exercise browser connection settings through an isolated app and a loopback mock."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ROUTER_KEY = "browser-test-router-key"
COMPANY_KEY = "browser-test-company-key"
FILE_KEY = "browser-test-file-key"
SEC_PROJECT = "Browser SEC Research Lab"
SEC_EMAIL = "browser-sec-contact@example.test"
FRED_KEY = "browser-test-fred-key"
TAVILY_KEY = "browser-test-tavily-key"
TEST_SECRETS = (ROUTER_KEY, COMPANY_KEY, FILE_KEY, SEC_EMAIL, FRED_KEY, TAVILY_KEY)
BASE_CONFIG = f'[openrouter]\napi_key = "{FILE_KEY}"\nmodel = "openai/file-default"\n'


class MockModel(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/v1/chat/completions":
            response = {
                "clarification": {
                    "message": (
                        f"Loopback model received {COMPANY_KEY}. FRED was {FRED_KEY}. "
                        f"Tavily was {TAVILY_KEY}. SEC contact was {SEC_EMAIL}."
                    ),
                    "fields": [
                        {
                            "name": "year",
                            "label": "Fiscal year",
                            "control": "select",
                            "options": [{"value": "2024", "label": "FY2024"}],
                        }
                    ],
                }
            }
        else:
            response = {
                "out_of_scope": f"New loopback endpoint received the clarification. FRED was {FRED_KEY}."
            }
        payload = json.dumps({"choices": [{"message": {"content": json.dumps(response)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def open_settings(page, label="Connection settings"):
    button = page.get_by_role("button", name=label, exact=True)
    expect(button).to_be_enabled(timeout=20000)
    button.click()
    dialog = page.locator("#connection-settings-dialog")
    expect(dialog).to_be_visible()
    return dialog


def open_data_sources(dialog, label="Data source settings"):
    dialog.get_by_role("tab", name=label, exact=True).click()
    form = dialog.locator("#data-source-settings-form")
    expect(form).to_be_visible()
    return form


def expect_data_sources(form, label):
    expect(form.locator(".data-source-status")).to_have_text([label] * 3)


def save_data_sources(page, dialog):
    page.locator("#provider-setup > summary").click()
    page.get_by_role("button", name="Configure data sources", exact=True).click()
    expect(dialog.get_by_role("tab", name="Data source settings")).to_have_attribute("aria-selected", "true")
    form = dialog.locator("#data-source-settings-form")
    expect_data_sources(form, "Not configured")
    expect(form.get_by_role("button", name="Restore defaults")).to_be_disabled()
    project = form.locator("#sec-project")
    project.fill(SEC_PROJECT)
    form.get_by_role("button", name="Save settings").click()
    expect(dialog.locator(".data-source-settings-error")).to_be_visible()
    project.fill(SEC_PROJECT)
    form.locator("#sec-email").fill(SEC_EMAIL)
    form.locator("#fred-api-key").fill(FRED_KEY)
    form.locator("#tavily-api-key").fill(TAVILY_KEY)
    form.get_by_role("button", name="Save settings").click()
    expect(dialog).not_to_be_visible()

    form = open_data_sources(open_settings(page))
    expect_data_sources(form, "Configured")
    for field in ("sec-project", "sec-email", "fred-api-key", "tavily-api-key"):
        expect(form.locator(f"#{field}")).to_have_value("")
    for field in ("fred-api-key", "tavily-api-key"):
        expect(form.locator(f"#{field}")).to_have_attribute("type", "password")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()


def check_model_settings(browser, app_url, mock_url, requests):
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    received = []
    page.on("websocket", lambda ws: ws.on("framereceived", lambda frame: received.append(frame)))
    page.goto(app_url)
    dialog = open_settings(page)
    model_form = dialog.locator("#model-settings-form")
    provider = model_form.locator("#model-provider")
    model = model_form.locator("#model-name")
    key = model_form.locator("#model-api-key")
    endpoint = model_form.locator("#model-base-url")
    token_limit = model_form.locator("#model-token-limit-parameter")
    save = model_form.get_by_role("button", name="Save settings")

    expect(provider).to_have_value("openrouter")
    expect(model).to_have_value("openai/file-default")
    expect(key).to_have_attribute("type", "password")
    expect(key).to_have_value("")
    model.fill("openai/browser-model")
    key.fill(ROUTER_KEY)
    save.click()
    expect(dialog).not_to_be_visible()

    open_settings(page)
    expect(model).to_have_value("openai/browser-model")
    expect(key).to_have_value("")
    provider.select_option("openai_compatible")
    expect(key).to_have_value("")
    model.fill("corp-model-one")
    endpoint.fill("http://company.example/v1")
    key.fill(COMPANY_KEY)
    save.click()
    expect(dialog.get_by_role("alert")).to_contain_text("HTTPS")
    expect(key).to_have_value("")

    endpoint.fill(f"{mock_url}/v1/")
    key.fill(COMPANY_KEY)
    token_limit.select_option("max_tokens")
    save.click()
    expect(dialog).not_to_be_visible()
    save_data_sources(page, dialog)
    page.locator("#research-mode").select_option("live")
    page.locator("#research-question").fill("Did Microsoft’s profit increase?")
    page.get_by_role("button", name="Run investigation").click()
    redacted = "Loopback model received [redacted]. FRED was [redacted]. Tavily was [redacted]. SEC contact was [redacted]."
    expect(page.get_by_text(redacted, exact=True).first).to_be_visible(timeout=20000)
    expect(page.get_by_role("heading", name="Clarify the claim")).to_be_visible()

    open_settings(page)
    expect(provider).to_have_value("openai_compatible")
    expect(endpoint).to_have_value(f"{mock_url}/v1")
    expect(key).to_have_value("")
    model.fill("corp-model-two")
    endpoint.fill(f"{mock_url}/gateway/v1")
    token_limit.select_option("max_completion_tokens")
    save.click()
    expect(dialog).not_to_be_visible()
    page.get_by_role("combobox", name="Fiscal year").select_option("2024")
    page.get_by_role("button", name="Resume investigation").click()
    resumed = "New loopback endpoint received the clarification. FRED was [redacted]."
    expect(page.get_by_text(resumed, exact=True).first).to_be_visible(timeout=20000)

    (first_path, first_auth, first_request), (second_path, second_auth, second_request) = requests
    assert first_path == "/v1/chat/completions"
    assert first_auth == f"Bearer {COMPANY_KEY}"
    assert first_request["model"] == "corp-model-one"
    assert first_request["max_tokens"] == 6500
    assert second_path == "/gateway/v1/chat/completions"
    assert second_auth is None
    assert second_request["model"] == "corp-model-two"
    assert second_request["max_completion_tokens"] == 6500
    assert json.loads(second_request["messages"][-1]["content"])["clarification_answers"] == {"year": "2024"}

    other = browser.new_page()
    other.goto(app_url)
    other_dialog = open_settings(other)
    expect(other_dialog.locator("#model-provider")).to_have_value("openrouter")
    expect(other_dialog.locator("#model-name")).to_have_value("openai/file-default")
    expect_data_sources(open_data_sources(other_dialog), "Not configured")
    other.close()

    data_form = open_data_sources(open_settings(page))
    data_form.get_by_role("button", name="Restore defaults").click()
    expect(dialog).not_to_be_visible()
    open_settings(page)
    expect(model).to_have_value("corp-model-two")
    data_form = open_data_sources(dialog)
    expect_data_sources(data_form, "Not configured")
    data_form.locator("#fred-api-key").fill(FRED_KEY)
    data_form.get_by_role("button", name="Save settings").click()
    expect(dialog).not_to_be_visible()

    open_settings(page)
    model_form.get_by_role("button", name="Restore defaults").click()
    expect(dialog).not_to_be_visible()
    open_settings(page)
    expect(model).to_have_value("openai/file-default")
    expect(key).to_have_value("")
    expect(model_form.get_by_role("button", name="Restore defaults")).to_be_disabled()
    page.keyboard.press("Escape")
    page.locator("#interface-language").select_option("zh")
    data_form = open_data_sources(open_settings(page, "连接设置"), "数据源设置")
    expect(data_form.locator("section[data-provider=fred] .data-source-status")).to_have_text("已配置")
    expect(data_form.locator("section[data-provider=sec] .data-source-status")).to_have_text("未配置")

    storage = page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
    assert received
    for secret in (*TEST_SECRETS, SEC_PROJECT):
        assert secret not in storage
        assert all(
            secret.encode() not in (frame.encode() if isinstance(frame, str) else frame) for frame in received
        )
    page.close()


def main():
    with (
        TemporaryDirectory(prefix="research-settings-") as directory,
        ThreadingHTTPServer(("127.0.0.1", 0), MockModel) as mock,
    ):
        workspace = Path(directory)
        shutil.copy2(ROOT / "app.py", workspace)
        shutil.copytree(
            ROOT / "financial_research",
            workspace / "financial_research",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        config = workspace / "config.local.toml"
        config.write_text(BASE_CONFIG, encoding="utf-8")
        environment = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "OPENROUTER_API_KEY",
                "OPENAI_COMPATIBLE_API_KEY",
                "FRED_API_KEY",
                "TAVILY_API_KEY",
                "SEC_USER_AGENT",
            }
        }
        mock_url = f"http://127.0.0.1:{mock.server_port}"
        # Keep even a regressed provider selection from reaching external services.
        environment.update(
            {
                "FINANCIAL_RESEARCH_DB": str(workspace / "test.sqlite3"),
                "NO_PROXY": "127.0.0.1",
                "HTTP_PROXY": mock_url,
                "HTTPS_PROXY": mock_url,
                "ALL_PROXY": mock_url,
            }
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        mock.requests = []
        thread = Thread(target=mock.serve_forever, daemon=True)
        thread.start()
        log = workspace / "streamlit.log"
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ]
        try:
            with (
                log.open("w", encoding="utf-8") as output,
                subprocess.Popen(
                    command, cwd=workspace, env=environment, stdout=output, stderr=subprocess.STDOUT
                ) as app,
            ):
                try:
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=1):
                                break
                        except (ConnectionRefusedError, TimeoutError):
                            if app.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError("The isolated test app did not start") from None
                            time.sleep(0.1)
                    with sync_playwright() as playwright:
                        options = {"headless": True}
                        edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
                        if edge.exists():
                            options["executable_path"] = str(edge)
                        browser = playwright.chromium.launch(**options)
                        check_model_settings(browser, f"http://127.0.0.1:{port}/", mock_url, mock.requests)
                        browser.close()
                finally:
                    app.terminate()
        except Exception:
            print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
        finally:
            mock.shutdown()
            thread.join()

        assert config.read_text(encoding="utf-8") == BASE_CONFIG
        for path in [log, *workspace.glob("*.sqlite3*")]:
            contents = path.read_bytes()
            assert all(secret.encode() not in contents for secret in TEST_SECRETS)
    print("Browser connection settings passed.")


if __name__ == "__main__":
    main()
