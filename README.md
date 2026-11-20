# Evidence-Driven Financial Research Agent

Ask a historical financial question and inspect the figures, verdict, source evidence, and decisions behind the result. The app covers Microsoft, Apple, NVIDIA, Amazon, Alphabet, Meta, Tesla, and Costco. It runs locally with Streamlit, LangGraph, and SQLite.

## Run locally

From the project folder in PowerShell:

```powershell
uv sync
uv run streamlit run app.py
```

Open [the local app](http://127.0.0.1:8501). The lockfile selects Python 3.12 and pinned packages. Without an OpenRouter key, the app defaults to **Fixture replay**. Choose an example, submit it, and select a workflow step or history entry. Saved sessions and pending clarifications are restored on restart.

To try evidence reuse, run **Revenue growth** and then select **What about operating income?** above the same report. The second investigation uses the saved Microsoft filing. **Source conflict** shows the verifier requesting an earnings release to identify the missing basis.

## Enable live providers

If the keys are already in your environment, launch Streamlit from a terminal that inherits them. Restart Streamlit after changing environment variables. The app uses the following variables when the corresponding TOML setting is blank or missing.

| Environment variable | TOML setting | Purpose |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | `openrouter.api_key` | Parse and plan investigations, identify relevant passages, and run the evaluation baseline. |
| `SEC_USER_AGENT` | `sec.user_agent` | Project name and real contact email for SEC access. |
| `FRED_API_KEY` | `fred.api_key` | Historical and current macroeconomic observations. |
| `TAVILY_API_KEY` | `tavily.api_key` | Search for dated news and retrieve article text. |

To set the SEC identification for the current PowerShell session, substitute your project name and contact email:

```powershell
$env:SEC_USER_AGENT = "NLP Group Research <your-email@example.com>"
uv run streamlit run app.py
```

If you want to customize settings in a file, copy the template:

```powershell
Copy-Item config.example.toml config.local.toml
```

For example, put this complete configuration in `config.local.toml`:

```toml
[openrouter]
api_key = "${OPENROUTER_API_KEY}"
base_url = "https://openrouter.ai/api/v1"
model = "openai/gpt-5-mini"

[fred]
api_key = "${FRED_API_KEY}"

[tavily]
api_key = "${TAVILY_API_KEY}"

[sec]
user_agent = "NLP Group Research <research@example.com>"
```

Set the three API key variables in the environment that launches Streamlit. The app replaces each `${VARIABLE_NAME}` reference with the value of that variable. You can use your own variable names if they match the references in the file. Replace the SEC example with your project name and real contact email, or use `user_agent = "${SEC_USER_AGENT}"` to read it from the environment.

To configure a provider without an environment variable, replace its reference with the value itself, for example `api_key = "replace-with-your-fred-key"` under `[fred]`. You can mix direct values and environment references across providers. Keep real keys in your local environment or in the Git-ignored `config.local.toml`.

Keep the OpenRouter base URL as shown. The app appends `/chat/completions` when it sends a request.

Blank fields still use the default names in the table. The default `openrouter.model` is `openai/gpt-5-mini`. Choose a model with strict structured output if you change it.

Select **Live** in the app after filling in the settings. An unconfigured retrieval provider appears in **Provider setup**. Findings from available sources still appear when a provider fails. SEC Company Facts and the SEC filing archive use different endpoints. If the archive rejects access, the run log records the response and affected claims remain unresolved.

A live investigation has a two-minute active budget. Each model request attempt can use up to two minutes, bounded by the remaining investigation time. Time spent waiting for a clarification answer is excluded.

The app binds to loopback and uses one local process per SQLite database. Session data and graph checkpoints live under `.data/`. Class exercises can set `FINANCIAL_RESEARCH_DB` to use a separate SQLite file. The configuration file, database, and checkpoints are ignored by Git.

## Evidence and calculation conventions

- Filing facts use actual fiscal dates. A saved report retains its evidence cutoff, citations, operands, and the ordered run log. A historical as-of date ends at midnight U.S. Eastern time.
- Year-over-year growth compares the same fiscal quarter or a full fiscal year with the prior year. Growth requires a positive baseline. Acceleration is the difference between unrounded growth rates in percentage points.
- A standalone quarter can use compatible cumulative figures. Diluted EPS requires a directly disclosed quarterly amount. Gross profit is labeled as derived when calculated from consolidated revenue and filed cost of revenue.
- The verifier uses disclosed accounting bases and units. If evidence cannot establish the requested comparison or causal relationship, the result is unresolved.
- A session can reuse compatible filing evidence. Latest requests refresh lookups after 15 minutes. Historical FRED lookups use the vintage at the saved cutoff.

## Tests and course assessment

```powershell
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python -m financial_research.evaluate --mode replay --split held_out --output evaluation/replay-results.json
```

For browser acceptance, start Streamlit with a separate database, then run the browser check in another terminal:

```powershell
$env:FINANCIAL_RESEARCH_DB = ".data/browser-check.sqlite3"
uv run streamlit run app.py
```

```powershell
uv run python tests/browser_acceptance.py
```

The browser checks use installed Edge on Windows. Otherwise, install the bundled browser with `uv run playwright install chromium`. Set `RESEARCH_TEST_URL` if the test app uses a different local port.

The [assessment catalog](evaluation/cases.json) contains 10 development and 20 held-out cases. [Frozen evidence](evaluation/evidence.json) retains the original source links and fiscal periods. Replay uses curated parse and attribution outputs and runs the same graph, retrieval interfaces, calculations, and storage. Record live-model scores separately:

```powershell
uv run python -m financial_research.evaluate --mode frozen --split held_out --output evaluation/model-comparison.json
uv run python -m financial_research.evaluate --mode live-smoke --output evaluation/live-smoke.json
```

The model comparison uses the same configured model for the full workflow and for the baseline. The baseline sees the question, cutoff, and evidence collected by that run. It answers without Python verification. The smoke suite runs six live questions covering the three use cases, clarification, and standalone EPS. For the profit and Apple EPS cases, it submits preset clarification answers when the form offers compatible choices and records the answers used. It also times the operating-income follow-up in the same session.
