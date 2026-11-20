"""Browser acceptance checks against a running local Streamlit application."""

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def select_fixture_replay(page):
    mode = page.get_by_role("combobox", name="Research mode")
    expect(mode).to_be_enabled()
    if mode.input_value() != "replay":
        mode.select_option("replay")
        expect(mode).to_be_enabled()
        expect(mode).to_have_value("replay")


def main():
    with sync_playwright() as playwright:
        options = {"headless": True}
        edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        if edge.exists():
            options["executable_path"] = str(edge)
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.goto(os.environ.get("RESEARCH_TEST_URL", "http://127.0.0.1:8501/"))
        select_fixture_replay(page)
        page.get_by_role("button", name="New session").click()
        expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
        page.get_by_role("button", name="Source conflict", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        expect(
            page.get_by_text("16% as reported and 15% in constant currency", exact=False).first
        ).to_be_visible()
        sidebar = page.get_by_role("heading", name="Research sessions").bounding_box()
        workflow = page.get_by_role("heading", name="Investigation workflow").bounding_box()
        assert abs(sidebar["y"] - workflow["y"]) <= 6

        support = page.locator("summary").get_by_text("Supporting details", exact=True)
        details = page.locator("details.report-support")
        expect(details).not_to_have_attribute("open", "")
        support.focus()
        page.keyboard.press("Enter")
        expect(details).to_have_attribute("open", "")
        page.get_by_text("Investigation history", exact=True).click()
        page.get_by_role("button", name="Follow-up Plan", exact=True).click()
        expect(page.get_by_text("Read the earnings exhibit for FY2024.", exact=True)).to_be_visible()

        page.reload()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        page.set_viewport_size({"width": 390, "height": 844})
        sessions = page.get_by_role("button", name="Sessions", exact=True)
        expect(sessions).to_be_visible()
        expect(sessions).to_have_attribute("aria-expanded", "false")
        sessions.click()
        expect(page.get_by_role("button", name="New session")).to_be_visible()
        sessions.click()
        expect(page.get_by_role("button", name="New session")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1440, "height": 960})
        select_fixture_replay(page)

        page.get_by_role("button", name="New session").click()
        expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
        page.get_by_role("button", name="Revenue growth", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_text("8.79 pp", exact=True)).to_be_visible(timeout=10000)
        page.get_by_role("button", name="What about operating income?", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_text("17.46 pp", exact=True)).to_be_visible(timeout=10000)
        expect(page.get_by_text("Carried forward · company, period, comparison", exact=True)).to_be_visible()

        page.get_by_role("textbox", name="Research question").fill("Did Microsoft profit increase in FY2024?")
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_role("combobox", name="Profit metric", exact=True)).to_be_visible(timeout=10000)
        expect(page.get_by_role("combobox", name="Fiscal year", exact=True)).to_have_count(0)
        page.get_by_role("combobox", name="Profit metric", exact=True).select_option("operating_income")
        page.get_by_role("button", name="Resume investigation").click()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        browser.close()
    print("Browser acceptance passed.")


if __name__ == "__main__":
    main()
