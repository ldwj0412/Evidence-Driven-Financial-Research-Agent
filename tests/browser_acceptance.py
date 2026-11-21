"""Browser acceptance checks against a running local Streamlit application."""

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def select_fixture_replay(page, label="Research mode"):
    mode = page.get_by_role("combobox", name=label)
    expect(mode).to_be_enabled(timeout=15000)
    if mode.input_value() != "replay":
        mode.select_option("replay")
        expect(mode).to_be_enabled()
        expect(mode).to_have_value("replay")


def check_bilingual_workspace(page):
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_role("button", name="New session").click()
    expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
    language = page.locator("#interface-language")
    expect(language).to_have_value("en")
    language.select_option("zh")
    expect(language).to_have_accessible_name("界面语言")
    expect(page.get_by_role("heading", name="查证财务主张")).to_be_visible()

    question = page.locator("#research-question")
    submit = page.locator('#research-form button[type="submit"]')
    answer = page.locator(".report-answer").first
    page.get_by_role("button", name="营收增长", exact=True).click()
    expect(question).to_have_value("微软 FY2024 的营收增速是否加快？")
    submit.click()
    expect(page.get_by_role("heading", name="结论", exact=True)).to_be_visible(timeout=10000)
    expect(answer).to_contain_text("8.79 个百分点")
    expect(answer.locator(".report-citation")).to_have_text("[1]")

    page.reload()
    expect(language).to_have_value("zh")
    expect(page.get_by_role("heading", name="结论", exact=True)).to_be_visible(timeout=10000)
    language.select_option("en")
    expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible()
    expect(answer).to_contain_text("8.79 个百分点")
    expect(answer.locator("p")).to_have_attribute("lang", "zh")

    language.select_option("zh")
    select_fixture_replay(page, "研究模式")
    question.fill("Did Microsoft’s revenue growth accelerate in FY2024?")
    submit.click()
    expect(answer).to_contain_text("revenue growth accelerated", timeout=10000)
    expect(answer.locator("p")).to_have_attribute("lang", "en")
    expect(page.get_by_role("heading", name="结论", exact=True)).to_be_visible()


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
        backdrop = page.locator(".research-app .market-texture")
        expect(backdrop).to_have_attribute("aria-hidden", "true")
        form = page.locator("#research-form")
        assert form.evaluate("el => parseFloat(getComputedStyle(el).borderTopLeftRadius)") >= 24
        assert (
            form.get_by_role("button", name="Run investigation").bounding_box()["x"]
            > (form.get_by_role("textbox", name="Research question").bounding_box()["x"])
        )
        page.get_by_role("button", name="New session").click()
        expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
        page.get_by_role("button", name="Source conflict", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        assert page.locator(".panel-main").evaluate(
            "el => getComputedStyle(el).backdropFilter.includes('blur')"
        )
        expect(page.locator(".report-answer").first).to_contain_text("The two figures don’t conflict.")
        expect(page.locator(".panel-topline .status")).to_have_text("Contradicted")
        expect(page.locator(".result-claim .status")).to_have_text("Contradicted")
        expect(
            page.get_by_text("16% as reported and 15% in constant currency", exact=False).first
        ).to_be_visible()
        sidebar = page.get_by_role("heading", name="Research sessions").bounding_box()
        workflow = page.get_by_role("heading", name="Investigation workflow").bounding_box()
        assert abs(sidebar["y"] - workflow["y"]) <= 6
        for selector in (".session-sidebar", ".workflow"):
            assert page.locator(selector).evaluate(
                "el => getComputedStyle(el).backdropFilter.includes('blur')"
            )
        expect(page.locator(".workflow .section-heading .status")).to_have_text("Complete")

        support = page.get_by_role("region", name="Supporting details")
        run = page.get_by_role("complementary", name="Run details")
        expect(run.get_by_text("Question asked", exact=True)).to_be_visible()
        run_activity = run.locator("details#run-activity")
        activity_toggle = run_activity.locator(":scope > summary")
        expect(activity_toggle).to_have_text("Run activity")
        expect(run_activity).to_have_attribute("open", "")
        activity_toggle.press("Enter")
        expect(run.locator(".side-facts")).to_be_hidden()
        expect(run.get_by_text("Question asked", exact=True)).to_be_visible()
        expect(run.get_by_text("Investigation history", exact=True)).to_be_visible()
        activity_toggle.press("Enter")
        expect(run.locator(".side-facts")).to_be_visible()
        expect(support).to_be_visible()
        sources = support.locator("details#support-sources")
        expect(sources.locator(":scope > summary")).to_contain_text("Sources")
        expect(sources).to_have_attribute("open", "")
        expect(page.locator(".conclusion-divider")).to_be_visible()
        expect(page.locator(".step .step-number")).to_have_text(["✓"] * 5)
        step_times = page.locator(".step .step-time")
        expect(step_times).to_have_count(5)
        saved_times = step_times.all_text_contents()
        assert all(value.endswith(" s") for value in saved_times)
        right, below = run.bounding_box(), support.bounding_box()
        assert abs(right["x"] - below["x"]) <= 1
        assert below["y"] > right["y"] + right["height"]
        calculations = support.get_by_text("Calculations", exact=True)
        expect(calculations.locator("..")).not_to_have_attribute("open", "")
        calculations.press("Enter")
        expect(calculations.locator("..")).to_have_attribute("open", "")
        activity = page.locator(".side-facts").text_content()
        page.get_by_role("button", name="Parse", exact=True).click()
        cutoff = page.locator(".panel-main time")
        expect(cutoff).to_be_visible()
        assert "T" in cutoff.get_attribute("datetime")
        assert cutoff.get_attribute("datetime") != cutoff.inner_text()
        page.get_by_role("button", name="Plan", exact=True).click()
        page.get_by_role("button", name="Initial plan", exact=True).click()
        expect(page.get_by_role("heading", name="Get reported financial figures")).to_be_visible()
        expect(page.locator(".plan-target")).to_contain_text("Microsoft")
        expect(page.locator(".plan-target")).to_contain_text("FY2023")
        expect(page.locator(".plan-target")).to_contain_text("FY2024")
        page.get_by_role("button", name="Verify", exact=True).click()
        audit = page.locator("details.verify-audit")
        expect(audit).not_to_have_attribute("open", "")
        audit.locator(":scope > summary").press("Enter")
        expect(audit.get_by_text("Compare measurement bases", exact=True)).to_be_visible()
        expect(audit.get_by_text("15.67%", exact=True)).to_be_visible()
        expect(audit.get_by_role("link", name="Microsoft FY2024 earnings release").first).to_be_visible()
        audit.locator(":scope > summary").press("Enter")
        page.get_by_role("button", name="Report", exact=True).click()
        page.get_by_role(
            "button", name="Show verification for The 16% and 15% growth figures contradict each other"
        ).press("Enter")
        expect(page.get_by_role("button", name="Verify", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        expect(page.get_by_role("heading", name="Follow-up verification", exact=True)).to_be_visible()
        expect(page.locator(".result-claim .status")).to_have_text("Contradicted")
        expect(audit).to_have_attribute("open", "")
        expect(audit.locator(":scope > summary")).to_be_focused()
        expect(page.locator(".result-claim.is-targeted")).to_contain_text("growth figures contradict")
        page.get_by_text("Investigation history", exact=True).click()
        run.locator("details#investigation-history").get_by_role("button", name="Verify", exact=True).click()
        expect(page.get_by_role("heading", name="Evidence verification", exact=True)).to_be_visible()
        audit.locator(":scope > summary").press("Enter")
        expect(audit.get_by_text("No eligible evidence matched this figure.", exact=False)).to_be_visible()
        expect(audit.get_by_role("link", name="Microsoft FY2024 earnings release")).to_have_count(0)
        page.get_by_role("button", name="Follow-up Plan", exact=True).click()
        expect(page.get_by_text("Read the earnings exhibit for FY2024.", exact=True)).to_be_visible()

        page.reload()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        expect(page.locator(".side-facts")).to_have_text(activity)
        expect(step_times).to_have_text(saved_times)
        page.set_viewport_size({"width": 390, "height": 844})
        report_card = page.locator(".panel-main").bounding_box()
        assert support.bounding_box()["y"] > report_card["y"] + report_card["height"]
        sidebar_heading = page.get_by_role("heading", name="Research sessions")
        expect(sidebar_heading).to_be_visible()
        show_sessions = page.get_by_role("button", name="Show sessions", exact=True)
        expect(show_sessions).to_have_attribute("aria-expanded", "false")
        show_sessions.press("Enter")
        expect(page.get_by_role("button", name="New session")).to_be_visible()
        hide_sessions = page.get_by_role("button", name="Hide sessions", exact=True)
        expect(hide_sessions).to_have_attribute("aria-expanded", "true")
        hide_sessions.press("Enter")
        expect(page.get_by_role("button", name="New session")).to_be_hidden()
        expect(sidebar_heading).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1440, "height": 960})
        select_fixture_replay(page)

        page.get_by_role("button", name="New session").click()
        expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
        page.get_by_role("button", name="Revenue growth", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_text("8.79 pp", exact=True)).to_be_visible(timeout=10000)
        expect(page.locator(".panel-topline .follow-up-label")).to_have_count(0)
        expect(support.locator("details.report-source")).to_have_count(1)
        assert set(page.locator(".panel-main .report-citation").all_text_contents()) == {"[1]"}
        page.get_by_role("button", name="What about operating income?", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_text("17.46 pp", exact=True)).to_be_visible(timeout=10000)
        expect(page.locator(".panel-topline .follow-up-label")).to_have_text("Follow-up")
        expect(page.get_by_text("Carried forward · company, period, comparison", exact=True)).to_be_visible()
        questions = page.get_by_role("navigation", name="Session questions")
        expect(questions.get_by_role("button").first).to_contain_text("What about operating income?")
        expect(questions.get_by_role("button").first.locator(".follow-up-label")).to_have_text("Follow-up")
        expect(questions.get_by_role("button").last.locator(".follow-up-label")).to_have_count(0)
        expect(questions.get_by_role("button").last.locator(".session-turn-meta")).not_to_contain_text(
            "0Complete"
        )
        expect(questions.locator("time")).to_have_count(2)
        question_section = page.locator("details#session-questions")
        question_toggle = question_section.locator(":scope > summary")
        expect(question_section).to_have_attribute("open", "")
        question_toggle.press("Enter")
        expect(questions).to_be_hidden()

        page.get_by_role("textbox", name="Research question").fill("Did Microsoft profit increase in FY2024?")
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_role("combobox", name="Profit metric", exact=True)).to_be_visible(timeout=10000)
        expect(page.locator(".step .step-number")).to_have_text(["1", "2", "3", "4", "5"])
        expect(page.get_by_role("combobox", name="Fiscal year", exact=True)).to_have_count(0)
        page.get_by_role("combobox", name="Profit metric", exact=True).select_option("operating_income")
        page.get_by_role("button", name="Resume investigation").click()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        expect(question_section).not_to_have_attribute("open", "")
        question_toggle.press("Enter")
        expect(questions.get_by_role("button").first).to_contain_text("Did Microsoft profit increase")

        page.set_viewport_size({"width": 390, "height": 844})
        if show_sessions.is_visible():
            show_sessions.click()
        session_list = page.get_by_role("navigation", name="Research sessions")
        selected_session = session_list.locator("button[aria-current=true]")
        title = selected_session.inner_text()
        remove = session_list.get_by_role("button", name="Delete session", exact=True)
        expect(remove).to_have_text("×")
        card_box = selected_session.bounding_box()
        icon_box = remove.bounding_box()
        assert icon_box["y"] - card_box["y"] <= 6
        assert card_box["x"] + card_box["width"] - (icon_box["x"] + icon_box["width"]) <= 6
        remove.focus()
        page.keyboard.press("Enter")
        confirmation = page.get_by_role("dialog", name="Delete session?")
        expect(confirmation).to_contain_text(title)
        expect(confirmation.get_by_role("button", name="Cancel")).to_be_focused()
        box = confirmation.bounding_box()
        assert abs(box["x"] + box["width"] / 2 - 390 / 2) <= 6
        assert abs(box["y"] + box["height"] / 2 - 844 / 2) <= 6
        page.keyboard.press("Enter")
        expect(confirmation).to_have_count(0)
        expect(remove).to_be_focused()
        expect(session_list.get_by_role("button", name=title, exact=True)).to_be_visible()

        remove.click()
        page.keyboard.press("Escape")
        expect(confirmation).to_have_count(0)
        expect(remove).to_be_focused()
        remove.click()
        confirmation.get_by_role("button", name="Delete permanently").click()
        expect(session_list.get_by_role("button", name=title, exact=True)).to_have_count(0)
        page.reload()
        show_sessions.click()
        expect(session_list.get_by_role("button", name=title, exact=True)).to_have_count(0)

        select_fixture_replay(page)
        page.get_by_role("button", name="New session").click()
        expect(page.get_by_role("heading", name="Start an investigation")).to_be_visible()
        page.get_by_role("button", name="Company event", exact=True).click()
        page.get_by_role("button", name="Run investigation").click()
        expect(page.get_by_role("heading", name="Conclusion", exact=True)).to_be_visible(timeout=10000)
        expect(page.get_by_text("Microsoft announced its FY2024 results on July 30, 2024.")).to_be_visible()
        expect(page.locator(".report-highlights .metric strong")).to_have_text(["$245.12B", "15.67%"])
        citation = page.locator(".report-answer").first.locator(".report-citation")
        expect(citation).to_have_text("[1]")
        source = support.locator("details.report-source").first
        expect(source).not_to_have_attribute("open", "")
        expect(sources).to_have_attribute("open", "")
        sources.locator(":scope > summary").press("Enter")
        expect(sources).not_to_have_attribute("open", "")
        expect(source).to_be_hidden()
        citation.press("Enter")
        expect(sources).to_have_attribute("open", "")
        expect(source).to_have_attribute("open", "")
        expect(source.locator("summary").first).to_be_focused()
        expect(source.get_by_role("link", name="Open original source")).to_have_attribute(
            "href", "https://www.sec.gov/Archives/edgar/data/789019/000095017024087835/msft-ex99_1.htm"
        )
        expect(source.get_by_text("Retrieved passages", exact=True)).to_have_count(0)
        details = source.locator("details").filter(has_text="Source details")
        expect(details).not_to_have_attribute("open", "")
        details.locator("summary").press("Enter")
        expect(details).to_have_attribute("open", "")
        expect(details.get_by_text("SEC EDGAR", exact=False)).to_be_visible()
        page.get_by_role("button", name="Show verification for Microsoft released", exact=False).click()
        expect(audit).to_have_count(2)
        expect(audit.first).not_to_have_attribute("open", "")
        expect(audit.last).to_have_attribute("open", "")
        expect(page.locator(".result-claim.is-targeted")).to_contain_text("Microsoft released")
        check_bilingual_workspace(page)
        browser.close()
    print("Browser acceptance passed.")


if __name__ == "__main__":
    main()
