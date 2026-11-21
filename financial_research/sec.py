import hashlib
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .domain import COMPANIES, Document, Fact, Period
from .storage import utc_now

EASTERN = ZoneInfo("America/New_York")
TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
}
FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}


def stable_id(*fields) -> str:
    return hashlib.sha256("\x1f".join(str(field) for field in fields).encode()).hexdigest()[:24]


def date_end(day: date) -> datetime:
    return datetime.combine(day, time.max, EASTERN).astimezone(UTC)


def filing_time(metadata: dict, day: date) -> datetime:
    try:
        parsed = datetime.fromisoformat(metadata.get("acceptanceDateTime", ""))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=EASTERN)
    except (TypeError, ValueError):
        return date_end(day)


def filing_url(entity, accession, document=None):
    cik = COMPANIES[entity][1]
    archive = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    if document and re.fullmatch(r"[A-Za-z0-9][\w.-]*", document):
        return f"{archive}/{document}"
    return f"{archive}/{accession}-index.htm"


def filing_rows(columns):
    return {
        acc: {
            name: values[index]
            for name, values in columns.items()
            if isinstance(values, list) and index < len(values)
        }
        for index, acc in enumerate(columns.get("accessionNumber", []))
    }


def _facts(entity, payload, filings, cutoff, retrieved_at):
    tags = payload.get("facts", {}).get("us-gaap", {})
    results = []
    for metric, aliases in TAGS.items():
        unit_key = "USD/shares" if metric == "diluted_eps" else "USD"
        for tag in aliases:
            for row in tags.get(tag, {}).get("units", {}).get(unit_key, []):
                if row.get("form") not in FORMS:
                    continue
                try:
                    accession = row["accn"]
                    start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
                    filed = date.fromisoformat(row["filed"])
                    value = Decimal(str(row["val"]))
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    continue
                metadata = filings.get(accession, {})
                available = filing_time(metadata, filed)
                if available > cutoff or start > end or not value.is_finite():
                    continue
                fact = Fact(
                    id=stable_id("sec", entity, accession, metric, tag, start, end, value),
                    source="sec",
                    entity=entity,
                    accession=accession,
                    form=row["form"],
                    url=filing_url(entity, accession, metadata.get("primaryDocument")),
                    title=f"{COMPANIES[entity][0]} Form {row['form']}",
                    published=filed,
                    retrieved_at=retrieved_at,
                    available_at=available,
                    metric=metric,
                    start=start,
                    end=end,
                    currency="USD",
                    unit="USD/share" if metric == "diluted_eps" else "USD",
                    tag=tag,
                    value=value,
                )
                results.append((fact, row))
    return results


def _calendar(rows, filings):
    periods = {}
    for fact, raw in rows:
        fy, fp = raw.get("fy"), raw.get("fp")
        if not isinstance(fy, int) or not 2019 <= fy <= 2199 or fp not in {"FY", "Q1", "Q2", "Q3"}:
            continue
        native_end = filings.get(fact.accession, {}).get("reportDate")
        if native_end != fact.end.isoformat():
            continue
        quarter = int(fp[-1]) if fp != "FY" else None
        periods[(fy, quarter)] = fact.end
    for (year, quarter), end in list(periods.items()):
        if quarter is None:
            periods[(year, 4)] = end
    return periods


def _preferred(candidates, anchor_period_end, filings):
    """Keep a cohort from the requested filing unless a later comparable figure changes."""
    if not candidates:
        return None
    # Match each accession's best defined XBRL tag before looking for changes across accessions.
    best_by_filing = {}
    for candidate in candidates:
        existing = best_by_filing.get(candidate.accession)
        if existing is None or TAGS[candidate.metric].index(candidate.tag) < TAGS[existing.metric].index(
            existing.tag
        ):
            best_by_filing[candidate.accession] = candidate
    ordered = sorted(best_by_filing.values(), key=lambda fact: (fact.available_at, fact.accession))
    anchor = [
        fact
        for fact in ordered
        if filings.get(fact.accession, {}).get("reportDate") == anchor_period_end.isoformat()
        and not fact.form.endswith("/A")
    ]
    selected = anchor[-1] if anchor else ordered[0]
    original = ordered[0]
    for fact in ordered:
        if (
            fact.tag == selected.tag
            and fact.available_at >= selected.available_at
            and (
                fact.value != selected.value
                or fact.form.endswith("/A")
                and fact.available_at > selected.available_at
            )
        ):
            selected = fact
    if selected.value != original.value:
        selected = selected.model_copy(
            update={
                "restatement": f"Later comparative figure differs from the amount in accession {original.accession}.",
            }
        )
    return selected


def _duration(fact):
    return (fact.end - fact.start).days + 1


def _matches_length(fact, quarter):
    return 70 <= _duration(fact) <= 126 if quarter else 335 <= _duration(fact) <= 395


def _gross_profit(records, entity):
    index = {
        (item.metric, item.fiscal_year, item.quarter, item.start, item.end, item.accession): item
        for item in records.values()
        if item.fiscal_year
    }
    disclosed = {
        (item.fiscal_year, item.quarter) for item in records.values() if item.metric == "gross_profit"
    }
    for revenue in list(records.values()):
        suffix = (revenue.fiscal_year, revenue.quarter, revenue.start, revenue.end, revenue.accession)
        if revenue.metric != "revenue" or not revenue.fiscal_year or suffix[:2] in disclosed:
            continue
        cost = index.get(("cost_of_revenue", *suffix))
        if not cost or cost.currency != revenue.currency:
            continue
        detail = "Consolidated revenue less filed cost of revenue."
        if entity == "COST":
            detail = (
                "Consolidated revenue less merchandise costs. Costco's merchandise margin uses net sales."
            )
        derivation = [revenue.id, cost.id, *revenue.derived_from, *cost.derived_from]
        gross = revenue.model_copy(
            update={
                "id": stable_id("gross profit", *derivation),
                "metric": "gross_profit",
                "value": revenue.value - cost.value,
                "tag": None,
                "derived_from": derivation,
                "derivation": detail,
                "restatement": revenue.restatement or cost.restatement,
            }
        )
        records[gross.id] = gross


def _derived_quarter(metric_rows, year, quarter, close, previous_close, lookup_anchor, filings):
    candidates = [
        item
        for item in metric_rows
        if item.end == close
        and (
            145 <= _duration(item) <= 206
            if quarter == 2
            else 228 <= _duration(item) <= 300
            if quarter == 3
            else 335 <= _duration(item) <= 395
        )
    ]
    for start in sorted({item.start for item in candidates}, reverse=True):
        current = _preferred([item for item in candidates if item.start == start], lookup_anchor, filings)
        prior = _preferred(
            [
                item
                for item in metric_rows
                if item.end == previous_close
                and item.start == start
                and item.currency == current.currency
                and item.unit == current.unit
                and item.tag == current.tag
                and item.available_at <= current.available_at
            ],
            lookup_anchor,
            filings,
        )
        if prior is None:
            continue
        start = previous_close + timedelta(days=1)
        references = [prior.id, current.id]
        note = current.restatement or prior.restatement
        derived = current.model_copy(
            update={
                "id": stable_id("quarter", year, quarter, *references),
                "start": start,
                "fiscal_year": year,
                "quarter": quarter,
                "value": current.value - prior.value,
                "derived_from": references,
                "restatement": note,
                "derivation": "Cumulative filed amount less the preceding cumulative amount.",
            }
        )
        return [derived, prior, current]
    return []


def normalize_company_facts(entity, payload, filings, periods, cutoff, retrieved_at=None):
    rows = _facts(entity, payload, filings, cutoff, retrieved_at or utc_now())
    calendar = _calendar(rows, filings)
    if not calendar:
        return []
    if not periods:
        latest_year = max(year for year, _ in calendar)
        periods = [
            Period(year=year, quarter=quarter)
            for year, quarter in calendar
            if year >= max(2019, latest_year - 3)
        ]
    anchor = max(calendar.get((period.year, period.quarter), date.min) for period in periods)
    by_metric = defaultdict(list)
    for fact, _ in rows:
        by_metric[fact.metric].append(fact)
    results = {}
    covered = set()
    for period in sorted(periods, key=lambda item: (item.year, item.quarter or 0)):
        year, quarter = period.year, period.quarter
        close = calendar.get((year, quarter))
        if not close:
            continue
        previous_close = calendar.get((year, quarter - 1)) if quarter and quarter > 1 else None
        for metric, candidates in by_metric.items():
            matching = [
                item
                for item in candidates
                if item.end == close
                and _matches_length(item, quarter)
                and (not previous_close or item.start == previous_close + timedelta(days=1))
            ]
            chosen = _preferred(matching, anchor, filings)
            if chosen:
                results[chosen.id] = chosen.model_copy(update={"fiscal_year": year, "quarter": quarter})
                covered.add((metric, year, quarter))
            elif (
                quarter
                and quarter > 1
                and previous_close
                and metric != "diluted_eps"
                and 70 <= (close - previous_close).days <= 126
            ):
                derived = _derived_quarter(candidates, year, quarter, close, previous_close, anchor, filings)
                if derived:
                    covered.add((metric, year, quarter))
                for item in derived:
                    results.setdefault(item.id, item)
    # Retain historical facts in each selected filing for subsequent same-session questions.
    accessions = {document.accession for document in results.values() if document.fiscal_year}
    by_end = defaultdict(list)
    for (year, quarter), end in calendar.items():
        by_end[end].append((year, quarter))
    extra = defaultdict(list)
    for fact, _ in rows:
        if fact.accession not in accessions or fact.id in results:
            continue
        for year, quarter in by_end[fact.end]:
            if (fact.metric, year, quarter) not in covered and _matches_length(fact, quarter):
                extra[(fact.metric, year, quarter)].append(fact)
    for (_, year, quarter), options in extra.items():
        fact = _preferred(options, anchor, filings)
        results[fact.id] = fact.model_copy(update={"fiscal_year": year, "quarter": quarter})
    _gross_profit(results, entity)
    return list(results.values())


def html_passages(html):
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, ix\\:hidden, noscript"):
        element.decompose()
    passages = []
    for node in soup.select("p, div, tr, li, h1, h2, h3, h4"):
        if node.name != "tr" and node.find(["p", "div", "tr", "li", "h1", "h2", "h3", "h4"]):
            continue
        passage = " ".join(node.get_text(" ", strip=True).split())
        if 10 <= len(passage) <= 1800:
            passages.append(passage)
    return list(dict.fromkeys(passages))


def exhibit_names(index):
    soup = BeautifulSoup(index, "html.parser")
    options = []
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 4 or not re.fullmatch(
            r"EX-99(?:\.\d+)?", cells[3].get_text(strip=True), re.IGNORECASE
        ):
            continue
        description = cells[1].get_text(" ", strip=True)
        link = cells[2].find("a", href=True)
        if not link:
            continue
        filename = PurePosixPath(link["href"].split("?", 1)[0]).name
        if re.fullmatch(r"[A-Za-z0-9][\w-]*\.html?", filename, re.IGNORECASE):
            options.append(
                (0 if re.search(r"earnings|release|results", description, re.IGNORECASE) else 1, filename)
            )
    return [name for _, name in sorted(options)]


def document_record(
    entity, accession, metadata, filename, raw_passages, cutoff, period=None, exhibit=False, period_end=None
):
    try:
        day = date.fromisoformat(metadata["filingDate"])
    except (KeyError, TypeError, ValueError):
        return None
    available = filing_time(metadata, day)
    if available > cutoff:
        return None
    company = COMPANIES[entity][0]
    form = metadata.get("form", "8-K")
    passages = raw_passages
    if exhibit and period is not None:
        # Use clearly labeled fiscal sections for period-specific measurements.
        passages = period_passages(raw_passages, period, period_end)
        if not passages:
            period = None
            passages = raw_passages
    return Document(
        id=stable_id("sec-doc", entity, accession, filename, period.label if period else ""),
        source="sec",
        entity=entity,
        accession=accession,
        form=f"{form} exhibit" if exhibit else form,
        publisher=company,
        url=filing_url(entity, accession, filename),
        published=day,
        available_at=available,
        retrieved_at=utc_now(),
        period=period,
        title=f"{company} {'earnings release' if exhibit else form}",
        passages=passages,
    )


_ANNUAL_RESULTS_HEADING = re.compile(
    r"(?:full[ -]year|fiscal year)\s+(20\d{2})(?:\s+(?:financial\s+)?results)?", re.IGNORECASE
)


def annual_results_heading(passage, period):
    match = _ANNUAL_RESULTS_HEADING.fullmatch(passage.strip())
    return bool(match and period and period.quarter is None and int(match[1]) == period.year)


def period_passages(passages, period, period_end=None):
    selected = []
    section_matches = False
    heading = re.compile(
        r"\b(fiscal year|full.year|twelve months|year ended|quarter|three months|six months|nine months)\b",
        re.IGNORECASE,
    )
    month_names = r"(?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)"
    for passage in passages:
        short_heading = len(passage) < 330
        opening = passage if short_heading else re.split(r"(?<=[.!?])\s+", passage, maxsplit=1)[0]
        marker = heading.search(opening)
        if marker:
            day_pattern = month_names + r"\s+\d{1,2},?\s+20\d{2}"
            day_match = re.search(day_pattern, opening, re.IGNORECASE)
            annual = marker[1].lower() in {
                "fiscal year",
                "full-year",
                "full year",
                "twelve months",
                "year ended",
            }
            named = re.search(r"\b(?:q([1-4])|(first|second|third|fourth) quarter)\b", opening, re.IGNORECASE)
            named_quarter = None
            if named:
                named_quarter = (
                    int(named[1])
                    if named[1]
                    else ("first", "second", "third", "fourth").index(named[2].lower()) + 1
                )
            full_date_matches = False
            if day_match and period_end:
                for fmt in ("%B %d %Y", "%b %d %Y"):
                    try:
                        full_date_matches = (
                            datetime.strptime(day_match[0].replace(",", ""), fmt).date() == period_end
                        )
                        break
                    except ValueError:
                        continue
            quarter_matches = bool(
                period.quarter
                and (
                    (named_quarter == period.quarter and str(period.year) in opening)
                    or (
                        not named_quarter
                        and full_date_matches
                        and marker[1].lower() in {"quarter", "three months"}
                    )
                )
            )
            if short_heading:
                section_matches = (
                    quarter_matches
                    if period.quarter
                    else annual
                    and (
                        full_date_matches
                        or annual_results_heading(opening, period)
                        or not period_end
                        and str(period.year) in opening
                    )
                )
            elif quarter_matches and full_date_matches:
                # A dated opening sentence identifies this paragraph without starting a table section.
                selected.append(passage)
                continue
        if section_matches:
            selected.append(passage)
    return selected


_EPS = re.compile(
    r"\b(?:diluted (?:earnings|net income) per share|diluted EPS|earnings per diluted share|"
    r"net income per diluted share)(?:\s+was|\s+of|\s*:)\s*\$\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ADJUSTED = re.compile(r"\badjusted\b|non.gaap|\bexclud\w*\b|\bexpected\b|\bguidance\b", re.IGNORECASE)


def _eps_table(html, period_end):
    if not html:
        return set()
    fiscal_day = re.compile(rf"\b{period_end:%B}\s+{period_end.day}\b", re.IGNORECASE)
    soup = BeautifulSoup(html, "html.parser")
    candidates = set()
    for table in soup.find_all("table"):
        rows = [
            [
                " ".join(cell.get_text(" ", strip=True).split())
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            for row in table.find_all("tr")
        ]
        header = " ".join(value for row in rows[:10] for value in row)
        if (
            not re.search(r"three months ended|quarter ended", header, re.IGNORECASE)
            or not fiscal_day.search(header)
            or _ADJUSTED.search(header)
        ):
            continue
        positions = re.findall(r"\b20\d{2}\b", header)
        span = re.search(r"three months ended|quarter ended", header, re.IGNORECASE)
        annual = re.search(r"twelve months ended|year ended", header, re.IGNORECASE)
        begin = 2 if annual and annual.start() < span.start() else 0
        years = positions[begin : begin + 2]
        if len(years) != 2 or years.count(str(period_end.year)) != 1:
            continue
        column = begin + years.index(str(period_end.year))
        in_eps = False
        for row in rows:
            cells = [value for value in row if value and value != "$"]
            if not cells:
                continue
            label = cells[0].lower()
            if "shares used" in label:
                in_eps = False
            if "earnings per share" in label and len(cells) == 1:
                in_eps = True
            is_diluted = label in {
                "diluted earnings per share",
                "net income per diluted share",
                "earnings per diluted share",
                "diluted eps",
            }
            if not (is_diluted or in_eps and label == "diluted"):
                continue
            numeric = [cell for cell in cells[1:] if re.fullmatch(r"-?\d+\.\d+", cell)]
            if len(numeric) > column:
                candidates.add(Decimal(numeric[column]))
    return candidates


def reported_quarter_eps(document, start, end, html=None):
    """Read standalone GAAP diluted EPS only from a period-identified earnings exhibit."""
    if not document.period or not document.period.quarter or not document.entity:
        return None
    if not 70 <= (end - start).days + 1 <= 126:
        return None
    values = set()
    for passage in document.passages:
        for sentence in re.split(r"(?<=[.!?])\s+", passage):
            for match in _EPS.finditer(sentence):
                if not _ADJUSTED.search(sentence) and re.search(
                    r"\bquarter(?:ly)?\b", sentence, re.IGNORECASE
                ):
                    values.add(Decimal(match[1]))
    if not values:
        values = _eps_table(html, end)
    if len(values) != 1:
        return None
    value = next(iter(values))
    return Fact(
        id=stable_id(document.id, "standalone diluted EPS", start, end, value),
        source="sec",
        entity=document.entity,
        accession=document.accession,
        form=document.form,
        url=document.url,
        title=document.title,
        published=document.published,
        publisher=document.publisher,
        available_at=document.available_at,
        retrieved_at=document.retrieved_at,
        metric="diluted_eps",
        fiscal_year=document.period.year,
        quarter=document.period.quarter,
        start=start,
        end=end,
        value=value,
        unit="USD/share",
        currency="USD",
        derived_from=[document.id],
        derivation="Directly disclosed GAAP quarterly diluted EPS.",
    )


def inline_facts(document, html, periods):
    """Recover consolidated signed iXBRL figures directly from a financial filing."""
    if document.form not in FORMS:
        return []
    soup = BeautifulSoup(html, "html.parser")
    fields = {
        node.get("name", "").split(":")[-1].lower(): node.get_text(strip=True)
        for node in soup.find_all("ix:nonnumeric")
        if node.get("name", "").lower().startswith("dei:")
    }
    try:
        focus_year = int(fields["documentfiscalyearfocus"])
        raw_date = fields["documentperiodenddate"]
        try:
            close = date.fromisoformat(raw_date)
        except ValueError:
            close = datetime.strptime(raw_date, "%B %d, %Y").date()
        label = fields["documentfiscalperiodfocus"]
        focus_quarter = int(label[1:]) if label.startswith("Q") else None
    except (KeyError, ValueError):
        return []
    by_year = {period.year for period in periods if period.quarter == focus_quarter}
    candidates = {}
    for context in soup.find_all("xbrli:context"):
        if context.find(["xbrli:segment", "xbrli:scenario", "xbrldi:explicitmember", "xbrldi:typedmember"]):
            continue
        start_at = context.find("xbrli:startdate")
        end_at = context.find("xbrli:enddate")
        if start_at and end_at:
            try:
                dates = (
                    date.fromisoformat(start_at.get_text(strip=True)),
                    date.fromisoformat(end_at.get_text(strip=True)),
                )
                candidates[context.get("id")] = dates
            except ValueError:
                continue
    aliases = {
        tag.lower(): metric for metric, tags in TAGS.items() for tag in tags if metric != "cost_of_revenue"
    }
    output = []
    for node in soup.find_all("ix:nonfraction"):
        name = node.get("name", "").lower()
        dates = candidates.get(node.get("contextref"))
        if not dates or not name.startswith("us-gaap:") or name.split(":", 1)[1] not in aliases:
            continue
        start, end = dates
        year = focus_year - (close.year - end.year)
        duration = (end - start).days + 1
        if year not in by_year or not (70 <= duration <= 126 if focus_quarter else 335 <= duration <= 395):
            continue
        try:
            comparative_end = close.replace(year=end.year)
        except ValueError:
            comparative_end = date(end.year, close.month, 28)
        if abs((end - comparative_end).days) > (0 if year == focus_year else 8):
            continue
        tag_key = name.split(":", 1)[1]
        metric = aliases[tag_key]
        units = node.get("unitref", "").lower()
        if "usd" not in units or (metric == "diluted_eps" and "share" not in units):
            continue
        try:
            scale = int(node.get("scale", 0))
            if abs(scale) > 12:
                continue
            value = Decimal(node.get_text(strip=True).replace(",", "")).scaleb(scale)
            if node.get("sign") == "-":
                value = -value
        except (ValueError, InvalidOperation):
            continue
        if not value.is_finite():
            continue
        tag = next(tag for tag in TAGS[metric] if tag.lower() == tag_key)
        output.append(
            Fact(
                id=stable_id(document.id, tag, start, end, value),
                source="sec",
                entity=document.entity,
                accession=document.accession,
                form=document.form,
                url=document.url,
                title=document.title,
                published=document.published,
                available_at=document.available_at,
                retrieved_at=document.retrieved_at,
                fiscal_year=year,
                quarter=focus_quarter,
                start=start,
                end=end,
                tag=tag,
                metric=metric,
                value=value,
                unit="USD/share" if metric == "diluted_eps" else "USD",
                currency="USD",
                derivation="Consolidated iXBRL fact read directly from the filing.",
            )
        )
    return list({item.id: item for item in output}.values())
