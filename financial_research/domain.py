import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMPANIES = {
    "MSFT": ("Microsoft", 789019),
    "AAPL": ("Apple", 320193),
    "NVDA": ("NVIDIA", 1045810),
    "AMZN": ("Amazon", 1018724),
    "GOOGL": ("Alphabet", 1652044),
    "META": ("Meta", 1326801),
    "TSLA": ("Tesla", 1318605),
    "COST": ("Costco", 909832),
}
METRICS = {
    "revenue": "Revenue",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "diluted_eps": "Diluted EPS",
}

Ticker = Literal["MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "COST"]
Metric = Literal["revenue", "gross_profit", "operating_income", "net_income", "diluted_eps"]
Measure = Literal["value", "change", "growth", "acceleration", "margin"]
Source = Literal["sec", "fred", "news"]
Series = Literal["FEDFUNDS", "CPIAUCSL", "UNRATE"]
Verdict = Literal["Supported", "Contradicted", "Unresolved"]
Operation = Literal["financials", "filing", "release", "observations", "articles"]
Stage = Literal["Parse", "Plan", "Retrieve", "Verify", "Report"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Period(Record):
    year: int = Field(ge=2019, le=2199)
    quarter: Literal[1, 2, 3, 4] | None = None

    @property
    def label(self) -> str:
        return f"FY{self.year}" + (f" Q{self.quarter}" if self.quarter else "")


class FinancialSubject(Record):
    company: Ticker
    metric: Metric
    period: Period


_WRITTEN_PERCENT = re.compile(r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:%|percent)\s*", re.IGNORECASE)


class ConflictFigure(Record):
    value: Decimal
    unit: Literal["USD", "USD/share", "%", "pp"]
    scale: Literal["one", "thousand", "million", "billion"] = "one"
    period: Period | None = None
    measure: Measure = "growth"
    basis: str | None = Field(default=None, max_length=120)
    scope: str = Field(default="consolidated", max_length=120)


class Claim(Record):
    id: str = Field(min_length=1, max_length=24)
    label: str = Field(min_length=1, max_length=320)
    kind: Literal["financial", "conflict", "event", "causal", "macro"]
    entities: list[Ticker] = Field(max_length=2)
    metric: Metric | None = None
    period: Period | None = None
    comparison_period: Period | None = None
    measure: Measure = "value"
    assertion: Literal["describe", "positive", "negative", "equal", "greater", "less"] = "describe"
    target: Decimal | None = None
    target_unit: Literal["USD", "USD/share", "%", "pp"] | None = None
    target_precision: int = Field(default=2, ge=0, le=8)
    reported_values: list[Decimal] = Field(default_factory=list, max_length=2)
    figures: list[ConflictFigure] = Field(default_factory=list, max_length=2)
    event: str | None = Field(default=None, max_length=180)
    event_date: date | None = None
    series: Series | None = None
    window_start: date | None = None
    window_end: date | None = None
    latest: bool = False
    quarterly: bool = False
    basis: str = Field(default="reported", max_length=120)
    scope: str = Field(default="consolidated", max_length=120)

    @field_validator("reported_values", mode="before")
    @classmethod
    def parse_percentages(cls, values):
        if not isinstance(values, list):
            return values
        # The reported_values shorthand always represents percentages, in display units.
        return [
            matched[1] if isinstance(value, str) and (matched := _WRITTEN_PERCENT.fullmatch(value)) else value
            for value in values
        ]

    @model_validator(mode="after")
    def required_inputs(self):
        if not self.entities and self.kind != "macro":
            raise ValueError("This claim needs a covered company")
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("Entity list contains duplicates")
        if self.kind in {"financial", "conflict"} and (
            not self.metric or (not self.period and not self.latest)
        ):
            raise ValueError(
                "Financial claims need a metric and fiscal period, or an explicit latest request"
            )
        if self.period and self.period.year < 2022:
            raise ValueError("Requested fiscal periods must be FY2022 or later")
        if self.kind == "conflict" and (
            len(self.entities) != 1
            or (len(self.reported_values) != 2 and len(self.figures) != 2)
            or not self.period
        ):
            raise ValueError("A conflict needs one company, a period, and two figures")
        if self.kind in {"event", "causal"} and not self.event:
            raise ValueError("An event or causal claim needs a description")
        if self.kind == "macro" and not self.series:
            raise ValueError("A macro claim needs a supported series")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("The evidence date range is reversed")
        if self.target is not None and not self.target_unit:
            raise ValueError("Numerical assertions need a target unit")
        numbers = [self.target, *self.reported_values, *(item.value for item in self.figures)]
        if any(value is not None and abs(value) > Decimal("1e20") for value in numbers):
            raise ValueError("Claim figures exceed the supported financial magnitude")
        return self


class Choice(Record):
    value: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)


class ClarificationField(Record):
    name: Literal["company", "metric", "year", "quarter", "period", "comparison", "event", "basis"]
    label: str = Field(min_length=1, max_length=120)
    control: Literal["select", "text"]
    options: list[Choice] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_options(self):
        if self.control == "select" and not self.options:
            raise ValueError("A select field needs choices")
        return self


class Clarification(Record):
    message: str = Field(min_length=1, max_length=400)
    fields: list[ClarificationField] = Field(min_length=1, max_length=4)

    def validate_answers(self, answers: dict) -> dict[str, str]:
        if set(answers) != {field.name for field in self.fields}:
            raise ValueError("Answer each requested clarification field")
        for field in self.fields:
            value = answers[field.name]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 160:
                raise ValueError(f"Enter a value for {field.label}")
            if field.control == "select" and value not in {item.value for item in field.options}:
                raise ValueError(f"Choose a listed value for {field.label}")
        return {key: value.strip() for key, value in answers.items()}


class Parsed(Record):
    claims: list[Claim] = Field(default_factory=list, max_length=8)
    clarification: Clarification | None = None
    as_of: date | None = None
    carried: list[Literal["company", "metric", "period", "comparison", "basis", "scope"]] = Field(
        default_factory=list,
        max_length=6,
    )
    out_of_scope: str | None = Field(default=None, max_length=360)

    @model_validator(mode="after")
    def one_outcome(self):
        if sum(map(bool, (self.claims, self.clarification, self.out_of_scope))) != 1:
            raise ValueError("Return claims, a clarification, or an out-of-scope reason")
        if len({entity for claim in self.claims for entity in claim.entities}) > 2:
            raise ValueError("A question can cover up to two companies")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("Claim IDs must be unique")
        if self.clarification:
            names = [field.name for field in self.clarification.fields]
            if len(set(names)) != len(names):
                raise ValueError("Clarification fields must be unique")
        return self


MAX_ACTION_PERIODS = 8


class Action(Record):
    source: Source
    operation: Operation
    claim_ids: list[str] = Field(min_length=1, max_length=8)
    purpose: str = Field(min_length=1, max_length=240)
    entity: Ticker | None = None
    periods: list[Period] = Field(default_factory=list, max_length=MAX_ACTION_PERIODS)
    series: Series | None = None
    start: date | None = None
    end: date | None = None
    query: str | None = Field(default=None, max_length=240)


class Plan(Record):
    actions: list[Action] = Field(min_length=1, max_length=10)


class Evidence(Record):
    id: str
    source: Source
    url: str
    title: str
    published: date | None = None
    retrieved_at: datetime
    available_at: datetime | None = None
    entity: Ticker | None = None
    accession: str | None = None
    form: str | None = None
    vintage: date | None = None
    publisher: str | None = None


class Fact(Evidence):
    type: Literal["fact"] = "fact"
    metric: Metric | Series | Literal["cost_of_revenue"]
    fiscal_year: int | None = None
    quarter: Literal[1, 2, 3, 4] | None = None
    start: date
    end: date
    value: Decimal
    unit: str
    currency: str | None = None
    basis: str = "reported"
    scope: str = "consolidated"
    tag: str | None = None
    frequency: str | None = None
    restatement: str | None = None
    derivation: str | None = None
    derived_from: list[str] = Field(default_factory=list)


class Document(Evidence):
    type: Literal["document"] = "document"
    passages: list[str]
    period: Period | None = None
    independent: bool = False


EvidenceRecord = Fact | Document


class Operand(Record):
    label: str
    value: Decimal
    unit: str
    evidence_ids: list[str]


class Calculation(Record):
    label: str
    formula: str
    operands: list[Operand]
    result: Decimal
    unit: str
    evidence_ids: list[str]


class VerificationCheck(Record):
    label: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)
    excerpt: str | None = None


class Finding(Record):
    claim_id: str
    label: str
    verdict: Verdict
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    checks: list[VerificationCheck] = Field(default_factory=list)


class ConclusionAnswer(Record):
    index: int = Field(ge=0, le=7)
    verdict: Verdict
    text: str = Field(min_length=1, max_length=1200)


class ConclusionDraft(Record):
    answers: list[ConclusionAnswer] = Field(min_length=1, max_length=8)


class Gap(Record):
    claim_id: str
    operation: Operation
    reason: str


class Attribution(Record):
    claim_id: str
    evidence_id: str
    quote: str = Field(min_length=12, max_length=400)
    role: Literal["event", "explanation", "causal_evidence", "measurement"]


class DisclosureSummary(Record):
    claim_id: str
    text: str = Field(min_length=12, max_length=520)
    quote_indices: list[int] = Field(min_length=1, max_length=6)


class DisclosureSynthesis(Record):
    summaries: list[DisclosureSummary] = Field(default_factory=list, max_length=8)


class Attributions(DisclosureSynthesis):
    entries: list[Attribution] = Field(max_length=16)
