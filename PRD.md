# Evidence-Driven Financial Research Agent

## 1. Product and scope

Build a locally runnable Streamlit app that helps students and other readers investigate financial claims. A user asks a question in ordinary language and receives a conclusion with an explicit verdict, key figures, and supporting evidence. They can inspect how the agent reached its conclusion, return to saved investigations, and ask a follow-up that uses relevant evidence from the same session.

The final course submission covers all three use cases with live integration across SEC EDGAR, FRED, and news search. A given investigation uses the sources relevant to its claims. [Original course proposal](<Financial_Investigation_Agent_Proposal (1).docx>)

| Use case | Required outcome |
|---|---|
| Claim verification | Match the entity, metric, and reporting periods, retrieve the inputs, calculate the result, and issue a source-linked verdict. |
| Company event investigation | Identify the event date, explain relevant financial changes, and build a dated timeline from disclosures and relevant independent coverage. Attribute explanations to their sources. Add macroeconomic context when relevant. |
| Conflict detection | Compare the sources' periods, units, and measurement bases. Explain an apparent mismatch or identify the evidence for a genuine contradiction. |

**Coverage.** Support Microsoft, Apple, NVIDIA, Amazon, Alphabet, Meta, Tesla, and Costco. Accept English and Chinese questions about up to two covered companies from FY2022 onward, with earlier years available as calculation inputs when needed. Support annual and quarterly consolidated revenue, gross profit, operating income, net income, and diluted EPS. Calculate absolute change, year-over-year growth, growth acceleration, and gross, operating, and net margins. An explicitly disclosed adjusted or segment measure can explain a conflict when its definition and scope are available. The application is for educational research and limits its outputs to historical evidence and financial analysis.

## 2. Approved interface

The reviewed preview is the reference for layout, visual hierarchy, and interactions. Its examples use scripted evidence. The Streamlit app implements the same experience with live tools and structured model output. [Interactive preview](workflow-preview.html)

| Area | Required behavior |
|---|---|
| **Language** | Let users switch interface labels between English and Simplified Chinese and remember their selection in the browser. Answer each question in its own language and retain the language of saved answers and source quotations. |
| **Connection settings** | Provide bilingual model and data source settings in the top bar. Accept the OpenRouter or OpenAI-compatible model settings, SEC project name and contact email, and masked FRED and Tavily keys. Show which sources have been configured. Apply changes for the current browser session and let users restore the file and environment configuration for each section. |
| **Question area** | A full-width question area sits above the workspace. It contains the question field, **Run investigation**, example prompts, and an applicable follow-up suggestion. |
| **Research sessions** | A left sidebar starts below the question area, aligned with the **Investigation workflow** heading. It contains **New session**, saved sessions, and the active session's questions. Selecting a question reopens its saved report. On narrow screens, the sidebar becomes a collapsible **Sessions** control below the question area. |
| **Investigation workflow** | A clickable strip shows **Parse → Plan → Retrieve → Verify → Report**. Selecting a step reveals its output. **Plan** shows which sources were chosen, what was requested, and which claim each lookup serves. When the verifier requests a follow-up, the strip visibly returns to **Plan**. Both planner passes remain inspectable. |
| **Clarification** | When a material ambiguity remains, display the clarification beneath **Parse**. Ask only for missing information. In the reviewed profit example the form asks for a metric and a fiscal year. If the question already specifies FY2024, ask only for the metric. The app renders permitted input controls from a structured clarification request and resumes the same turn when the user answers. |
| **Conclusion** | Lead the final report with a clearly labeled **Conclusion**, a direct answer, key figures, and the applicable overall and claim-level verdicts. Include material uncertainty in the visible conclusion. |
| **Supporting details** | Below a divider, place calculations, source evidence, the event timeline when applicable, and limitations under one **Supporting details** disclosure, collapsed by default. Calculations and source records remain individually inspectable and keyboard-accessible. |
| **Run details** | A panel on the right shows the selected question, sources used, new lookups, reused sources, and evidence follow-ups. Its expandable **Investigation history** reads the saved run log and lists events in order. Select any stage entry to inspect its recorded output, including earlier passes through the same step. On narrow screens, place the panel below the selected step or report. |

The conflict demo must expose a readable loop in **Investigation history**: **Parse → Plan → Retrieve → Verify → Follow-up Plan → Follow-up Retrieve → Follow-up Verify → Report**. The first Verify entry shows the missing measurement basis. Follow-up Plan shows the request for the earnings release. A normal run with sufficient initial evidence proceeds directly from Verify to Report.

## 3. Saved sessions, follow-ups, and evidence reuse

- **Persist research locally.** Store sessions, ordered questions, clarification answers, report snapshots, evidence references, calculations, and investigation history in SQLite. Save completed work automatically and keep the current selection across app restarts. Persist pending clarification so a user can return to the same question. Opening a prior report reads the stored snapshot and makes zero model or provider calls.
- **Follow up from the selected report.** Use that question's resolved context, such as company, metric, fiscal period, and comparison. An explicit value in the follow-up takes precedence. Ask for clarification when carrying context forward could change the answer. Show the fields carried forward, and store which turn the follow-up refers to. **New session** starts with a separate question history and evidence set.
- **Use valid existing evidence first.** Before a provider call, check the session's evidence for the required entity, metric, period, definition, and as-of cutoff. A saved immutable filing can be reused by accession number when it contains the necessary facts. Fetch missing evidence when a new operand, source, or fresher observation is needed. For a request for the latest information, refresh source discovery or observations when its cached result is older than 15 minutes. Historical FRED requests use the vintage available at the requested cutoff.
- **Make reuse visible.** Show **Reused from this session** on the relevant evidence and distinguish cache hits from live lookups in the Planner and Run details. Count a cache hit separately from live provider calls. Reusing evidence avoids its retrieval call, while a new follow-up may still need a model call for parsing or synthesis.

**Reference follow-up.** After verifying Microsoft FY2024 revenue growth, the user asks **“What about operating income?”** Carry forward Microsoft, FY2024, and the growth-acceleration comparison. If the saved FY2024 10-K contains all three operating income figures, report **6.16%** FY2023 growth, **23.62%** FY2024 growth, and **17.46 percentage points** of acceleration. Show one reused filing and zero new provider lookups. Reopening either report preserves its original figures, verdict, and history. [Microsoft FY2024 10-K](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm)

## 4. Agent design and verification

Use Python 3.12, LangGraph `StateGraph`, OpenRouter, HTTPX, Pydantic, Streamlit, SQLite, and pytest. LangGraph runs one investigation per turn and controls all stage transitions. [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)

Send model calls through the server-side Chat Completions adapter using OpenRouter or a configured OpenAI-compatible endpoint. The parser and planner request a strict JSON Schema response with `response_format.type = "json_schema"`. OpenRouter also receives `provider.require_parameters = true`. Validate the response in Pydantic before using it. Reject invalid plans before tool execution and surface a clear error if the selected model has no compatible route. Record the configured model ID and the routed provider when available for evaluation. [Chat Completions API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion) · [Structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)

| Step | Responsibility and output |
|---|---|
| **Parse** | Resolve company names or tickers, identify the metric or event, extract dates, comparisons, and individual assertions, and incorporate relevant prior context. Return structured claims or a clarification request. |
| **Plan** | The LLM proposes source requests tied to claims. Each action names a source, operation, parameters, and a short user-facing purpose. On follow-up, the planner uses the verifier's specific evidence gap and the prior action history. |
| **Retrieve** | Validate requested entities, dates, operations, and sources. Check session evidence, execute approved calls, and run independent lookups concurrently where practical. Return normalized evidence or structured failures. |
| **Verify** | Match definitions, accounting bases, units, currencies, and dates. Calculate results deterministically. Issue claim verdicts or request a specific lookup that could change the verdict. Allow at most one verification-driven follow-up. |
| **Report** | Render verified numbers and verdicts directly from the verification output. Use the model for source-grounded explanation and event synthesis. Persist the report and the ordered trace. |

- **EDGAR.** Use Company Facts for structured values, Submissions for filing metadata, and 10-Ks, 10-Qs, and earnings-related 8-K exhibits for supporting passages. Resolve tickers to CIKs. Use actual fiscal dates, distinguish standalone quarters from year-to-date data, and disclose restatements used. Each citation retains a direct link, filing date, form, and accession number. [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- **FRED.** Support `FEDFUNDS`, `CPIAUCSL`, and `UNRATE`. Record series ID, observation date, units, frequency, and vintage as applicable. Label derived measures such as year-over-year CPI growth. [FRED observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- **News.** Use Tavily to find and retrieve article text. Keep the publisher, publication date, URL, and relevant passage. Search snippets are leads. A paywalled, inaccessible, or undated article cannot substantiate a finding on its own. [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- **Calculations and verdicts.** Use Python `Decimal` and round at display time. Store the formula, operands, result, and units. Calculate growth acceleration as a difference in percentage points. A zero or negative growth baseline is **Unresolved** under the initial calculation convention. Assign **Supported**, **Contradicted**, or **Unresolved** per assertion, with **Partially supported** available for a compound result containing a supported and an unresolved assertion. Every supported or contradicted verdict has relevant evidence. Macro observations and coincident events provide context. Causal claims require evidence of the relationship.
- **Failures and timing.** Apply provider timeouts and bounded transport retries within a 30-second active-run budget, excluding time spent waiting for the user's clarification. Track the verifier-driven follow-up separately from transport retries. Retain confirmed findings if a provider fails or time expires. Mark any affected claim **Unresolved** when its evidence is insufficient.

The core records are **Session**, **Turn**, **Parsed claim**, **Clarification request**, **Retrieval plan**, **Evidence record**, **Calculation**, **Verdict**, and **Run event**. Each turn records a cutoff. Default to the start of the run, or use the end of the requested date in U.S. Eastern time for an as-of question. Evidence records carry a stable ID, source URL and dates, retrieval time, and the fact or passage. Numerical evidence includes its definition, period, unit, and currency when applicable. The report cites validated evidence IDs. Treat tool results and external pages as untrusted input.

### Durable run log

- **Write events as the run progresses.** Append events to SQLite with the session, turn, and run IDs, a run-local sequence number, a UTC timestamp, stage, pass, and outcome. Record stage starts and outcomes, clarification requests and resumes, model and tool calls, evidence reuse, transport retries, verification decisions, and the final status. Use sequence numbers to display events in recorded order when calls run concurrently.
- **Keep each action inspectable.** Store the tool name, sanitized request parameters, a short purpose, elapsed time, and an output summary or references to saved evidence and step output. For model calls, record the model, routed provider, and token usage when available. Capture a sanitized error message on failure. Use the log to distinguish a verification-driven loop from a network retry.
- **Restore the exact recorded chain.** Reopening a saved turn reads the log and recorded outputs directly from SQLite. If the app stops mid-run, show the events already saved and mark that run **Interrupted** on restart. A run waiting for clarification remains resumable. Opening the history makes zero model or provider calls.

## 5. Local configuration and credentials

Use **`config.local.toml`** and environment variables for persistent provider settings. Ship a matching **`config.example.toml`** with blank credential fields. Read the file with `tomllib` and validate settings with Pydantic.

The **Connection settings** dialog stores model and data source overrides in server memory for the current browser session. New and resumed investigations use the configuration captured when submitted. Blank data source fields keep the effective settings. Reuse a saved model credential only for the same provider and endpoint. Restore the model and data source sections independently to their file and environment defaults.

```toml
[model]
provider = "openrouter"

[openrouter]
api_key = ""
base_url = "https://openrouter.ai/api/v1"
model = "openai/gpt-6-luna"

[openai_compatible]
api_key = ""
base_url = ""
model = ""
token_limit_parameter = "max_completion_tokens"

[fred]
api_key = ""

[tavily]
api_key = ""

[sec]
user_agent = ""
```

- Use an OpenRouter-issued key for the OpenRouter service. Use the gateway's own key, base URL, and model ID for an OpenAI-compatible endpoint that supports Bearer authentication, Chat Completions, and strict JSON Schema output. A keyless loopback endpoint can omit the key.
- FRED provides macro observations and Tavily provides news search. SEC EDGAR uses the project contact identifying string in `sec.user_agent`, for example `NLP Group Research <team-email>`.
- Add `config.local.toml` and the local SQLite database to `.gitignore`. The example config contains placeholders. Keep submitted credentials and SEC contact details in server memory for the app session. Redact them from logs, error messages, tool traces, report snapshots, and browser responses.
- A missing key gives a clear setup message naming the affected provider. When a retrieval provider is unavailable, continue with relevant configured sources. Saved reports and fixture replay remain accessible. Document both configuration methods in the app README.

## 6. Evaluation and delivery

Build **30 evaluation cases**, with 10 for development and 20 frozen for assessment. The held-out set includes seven filing-verification cases, five event investigations, five conflict cases, and three clarification or unavailable-data cases. Include fiscal-period traps, historical FRED vintages, and unsupported causal assertions across those cases. Record expected claims, allowed sources, operands, numerical tolerances, and acceptable verdicts.

| Measure | Course target |
|---|---|
| Verdict accuracy | At least 85% of held-out assertions receive the expected verdict. |
| Financial accuracy | At least 95% of scored values have the correct amount, reporting period, and units. Deterministic calculation tests pass. |
| Citation quality | At least 95% of scored claim–citation pairs support the associated finding. Every **Supported** or **Contradicted** verdict has a relevant source link. |
| Conflict handling | Explain the mismatch correctly in at least four of five held-out conflict cases. |
| Appropriate uncertainty | Zero unsupported numerical assertions labeled **Supported** on the held-out run. Clarify or report unresolved as expected in all three designated cases. |
| Investigation speed | Target a 15-second median for live runs and enforce the 30-second active-run budget. Report live and cached timings, timeouts, model use, and provider call counts. |

**Required acceptance checks**

1. The Microsoft revenue case verifies **6.88%** FY2023 growth, **15.67%** FY2024 growth, and **8.79 percentage points** of acceleration from the filing.
2. The source conflict case explains **16% as reported** and **15% in constant currency**, with both Planner passes and the complete loop visible in Investigation history. [Microsoft earnings release](https://www.sec.gov/Archives/edgar/data/789019/000095017024087835/msft-ex99_1.htm)
3. The rates-and-earnings case supports the revenue comparison and keeps the causal claim **Unresolved** when the evidence provides only context.
4. An ambiguous profit question asks for the missing metric and period. A question supplying the period asks only for the missing metric. Answering the clarification resumes the same investigation.
5. The operating-income follow-up uses the saved filing with zero new provider lookups. A follow-up requiring a missing operand fetches the missing evidence. Reopening a finished turn makes zero model or provider calls, including after an app restart.
6. On desktop, the sessions sidebar aligns with the workflow; on mobile, the Sessions control expands and collapses. The final report shows the conclusion first, with Supporting details initially collapsed and accessible using a keyboard.
7. Empty, invalid, and partially configured local config files produce actionable setup or validation messages. Secrets never appear in reports, persisted evidence, or traces.
8. With the OpenRouter key configured, a live smoke test produces valid Parse and Plan outputs using the selected model. A simulated incompatible route or malformed response produces a clear failure before any invalid tool action runs.
9. After an app restart, reopening the source conflict case shows the same ordered event chain, both planner passes, and the saved evidence references. An interrupted run retains its partial chain, a failed lookup retains its retry or error event, and a resumed clarification appears in the original run history.

Compare the full workflow with the same model answering the same questions from the same frozen evidence without Python verification. Report raw counts alongside percentages. Use fixture-backed regression tests for fiscal-period selection, restatements, rounding, provider failures, and trace routing, then run at least six live smoke cases spanning all use cases and providers. The thresholds are acceptance targets to measure during the course evaluation.

| Milestone | Exit criteria |
|---|---|
| **1. Filing-backed verification and approved UI** | The Streamlit app runs the Microsoft reference case with live EDGAR and shows the conclusion, source, calculation, and trace in the approved layout. The parser supports a dynamic clarification. |
| **2. Sessions and live multi-source research** | Saved sessions reopen after a restart. The operating-income follow-up reuses the filing, and the demo shows live FRED and news, an event timeline, a verifier-driven loop, and a justified unresolved claim. |
| **3. Course submission** | Deliver the runnable app, README and config template, saved replay fixtures, evaluation set, baseline results, live and cached timings, and representative failures. |
