# Evaluation guide

The assessment has **10 development cases** and **20 held-out cases**. The tables below show the questions as submitted and the checks that determine a pass. For exact operands, fiscal period ends, tolerances, and eligible citations, see the [assessment catalog](cases.json) and [frozen evidence](evidence.json).

## Recorded results

| Run | Cases passing every check | Verdicts | Financial values | Citation pairs |
| --- | ---: | ---: | ---: | ---: |
| Final full live run with GPT-6 Luna, November 21, 2026 | 20/20 | 24/24 | 14/14 | 38/38 |
| Full held-out fixture replay after fixes | 20/20 | 24/24 | 14/14 | 38/38 |

The full live run passed **5/5** conflict cases and **3/3** clarification or uncertainty cases, with **zero unsupported numerical assertions**. C01 and E05 passed in the same run. U01 requested the profit metric and fiscal year, then completed after the evaluator supplied both.

Fixture replay uses frozen evidence and curated parse and attribution results. It exercises the workflow and verifier. Live runs also exercise the configured model and providers. See the [run commands](../README.md#tests-and-course-assessment) and [bug and retest log](bug-fix-log.md).

## Held-out cases

### Filing verification

| ID | Question | Expected check | Live result |
| --- | --- | --- | --- |
| F01 | Was Apple’s standalone FY2024 Q2 revenue $90.753 billion? | **Supported.** Use the standalone quarter and verify $90.753B. | **Pass** |
| F02 | Was NVIDIA’s FY2024 revenue $60.922 billion for the year ended January 28, 2024? | **Supported.** Verify $60.922B and use NVIDIA’s fiscal year end. | **Pass** |
| F03 | Did Amazon’s FY2024 operating income grow more than 80% year over year? | **Supported.** Compute 86.13% growth. | **Pass** |
| F04 | What was Alphabet’s consolidated operating margin in FY2024? | **Supported.** Compute 32.11% from operating income and revenue. | **Pass** |
| F05 | How much did Meta’s FY2024 net income grow year over year? | **Supported.** Compute 59.50% growth. | **Pass** |
| F06 | Did Tesla’s consolidated revenue fall in FY2024 compared with FY2023? | **Contradicted.** Revenue rose 0.95%. | **Pass** |
| F07 | Was Costco’s diluted EPS $16.56 for its fiscal year ending September 1, 2024? | **Supported.** Verify $16.56 per diluted share for the stated fiscal year. | **Pass** |

### Company events and causation

| ID | Question | Expected check | Live result |
| --- | --- | --- | --- |
| E01 | What happened when Microsoft released FY2024 results on July 30, 2024, and what was annual revenue growth? | **Supported** for the dated release and the 15.67% annual revenue growth calculation. | **Pass** |
| E02 | What did NVIDIA announce on February 21, 2024, and how much did FY2024 revenue grow year over year? | **Supported** for the dated release and the 125.85% annual revenue growth calculation. | **Pass** |
| E03 | When Amazon announced FY2024 results on February 6, 2025, what was the absolute change in annual operating income? | **Supported** for the dated release and the $31.741B increase. | **Pass** |
| E04 | How did Meta’s annual net income change in the FY2024 results announced January 29, 2025? | **Supported** for the dated release and the $23.262B increase. | **Pass** |
| E05 | As of August 15, 2024, did Microsoft’s FY2024 revenue growth accelerate, and did June interest rates cause it? | **Supported** for growth of 6.88% and 15.67%, with 8.79 percentage points of acceleration. **Unresolved** for interest-rate causation. | **Pass** |

### Source conflicts

| ID | Question | Expected check | Live result |
| --- | --- | --- | --- |
| C01 | Do Microsoft’s 16% and 15% FY2024 revenue growth figures contradict each other? | **Contradicted.** 16% is as reported and 15% is in constant currency. | **Pass** |
| C02 | Are Microsoft’s 24% and 22% FY2024 operating income growth figures inconsistent? | **Contradicted.** 24% is as reported and 22% is adjusted. | **Pass** |
| C03 | Is Microsoft’s net income growth for FY2024 22% or 20%, and do these conflict? | **Contradicted.** 22% is as reported and 20% is adjusted. | **Pass** |
| C04 | Do Apple’s FY2024 revenue figures of $391,035 million and $391.035 billion conflict? | **Contradicted.** Both express the same amount at different scales. | **Pass** |
| C05 | Is Apple’s $90.753 billion of FY2024 Q2 revenue inconsistent with $391.035 billion for the full fiscal year? | **Contradicted.** The figures cover different fiscal periods. | **Pass** |

For these cases, the verdict applies to the claim that the figures conflict.

### Clarification and unavailable evidence

| ID | Question | Expected check | Live result |
| --- | --- | --- | --- |
| U01 | Did Microsoft’s profit increase? | Ask for the profit metric and fiscal year. Use the prior year for comparison once the user answers. | **Pass** |
| U02 | As of September 30, 2024, what was Apple’s filed FY2024 annual revenue? | **Unresolved.** The annual filing was unavailable by the cutoff. | **Pass** |
| U03 | What was Amazon’s FY2023 year-over-year net income growth rate from its FY2022 loss? | **Unresolved.** The growth calculation requires a positive prior-year baseline. | **Pass** |

## Development cases

| ID | Question | Expected check | Replay result |
| --- | --- | --- | --- |
| D01 | Did Microsoft’s revenue growth accelerate in FY2024? | **Supported.** 6.88% and 15.67% growth, with 8.79 percentage points of acceleration. | **Pass** |
| D02 | Did Microsoft operating income growth accelerate in FY2024? | **Supported.** 6.16% and 23.62% growth, with 17.46 percentage points of acceleration. | **Pass** |
| D03 | What was Microsoft’s FY2024 gross margin? | **Supported.** 69.76%. | **Pass** |
| D04 | What was the absolute change in Apple’s diluted EPS between FY2023 and FY2024? | **Supported.** Diluted EPS fell by $0.05 per share. | **Pass** |
| D05 | What was Alphabet’s FY2024 gross margin using revenue and cost of revenue? | **Supported.** 58.20%, with gross profit derived from revenue less cost of revenue. | **Pass** |
| D06 | What was Apple’s reported standalone FY2024 Q4 diluted EPS? | **Supported.** Use the directly disclosed $0.97 per share. | **Pass** |
| D07 | For a Microsoft research report, what was the June 2024 effective federal funds rate using the August 15, 2024 vintage? | **Supported.** 5.33% using the requested FRED vintage. | **Pass** |
| D08 | Did Microsoft’s profit increase in FY2024? | Ask for the profit metric. The question supplies the year. | **Pass** |
| D09 | Was Costco’s FY2024 revenue larger than NVIDIA’s FY2024 revenue, each on its own fiscal calendar? | **Supported.** Compare Costco’s $254.453B with NVIDIA’s $60.922B using each company’s fiscal period. | **Pass** |
| D10 | Can you reconcile a claimed 12% FY2024 Microsoft revenue growth figure with the 16% reported result? | **Unresolved.** The evidence establishes the reported figure and lacks a match for the 12% figure. | **Pass** |
