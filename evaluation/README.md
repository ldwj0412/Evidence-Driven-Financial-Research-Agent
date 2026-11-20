# Course assessment

The [bug and retest log](bug-fix-log.md) records observed failures, fixes, and the checks that verified them.

`cases.json` freezes the 10 development questions and the 20 held-out questions, with expected verdicts, allowed citation URLs, period ends, operands, and numerical tolerances. There are seven filing, five event, five conflict, and three uncertainty cases in the held-out split. `evidence.json` contains normalized public facts and selected filing passages. The Microsoft exhibit includes short summaries of the disclosed measurement bases. Release evidence for event cases uses a short source excerpt when available.

The replay report measures deterministic verification and the recorded workflow using curated parse and attribution data. Financial scoring checks the value, unit, actual period end, and operand values. Citation scoring checks claim-to-source pairs against the frozen source allowlists. A missing citation on a supported or contradicted finding counts as an error. Assess the model’s live source selection and attribution separately using the credentialed commands in the [project README](../README.md).

Set `OPENROUTER_API_KEY` for the model comparison. The six-case live smoke suite also needs SEC identification and keys for FRED and Tavily.
