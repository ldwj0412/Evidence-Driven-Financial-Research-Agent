import asyncio
import hashlib
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter, ValidationError

from .config import load_config
from .domain import Attributions, Document, EvidenceRecord, Fact, FinancialSubject, Finding, Gap, Parsed, Plan
from .fixtures import fixture_attributions, lookup_fixture, parse_fixture
from .planning import (
    ATTRIBUTION_PROMPT,
    FINANCIAL_SUBJECT_PROMPT,
    PARSE_PROMPT,
    PLAN_PROMPT,
    asks_for_as_of,
    candidates,
    clarification_asks_for_figures,
    explicit_financial_claim,
    fixture_plan,
    parameters,
    validate_plan,
)
from .providers import (
    INVESTIGATION_TIMEOUT_SECONDS,
    Budget,
    LiveProviders,
    OpenRouter,
    ProviderError,
    Transport,
)
from .sec import EASTERN, date_end
from .storage import Store, json_text, utc_now
from .verification import (
    overall,
    render_number,
    required_periods,
    resolve_period,
    select_fact,
    valid_attributions,
    verify,
)

log = logging.getLogger(__name__)
EVIDENCE = TypeAdapter(list[EvidenceRecord])
ROOT = Path(__file__).resolve().parent.parent


class State(TypedDict, total=False):
    question: str
    prior: dict
    cutoff: str
    answers: dict
    parsed: dict
    plan: dict
    evidence: list[dict]
    findings: list[dict]
    gaps: list[dict]
    attributions: list[dict]
    followup: int
    problem: str
    limitations: list[str]
    reused: list[str]


def dump(*items):
    return [item.model_dump(mode="json") for item in items]


def available(item, cutoff):
    if item.available_at:
        return item.available_at <= cutoff
    if item.published:
        return item.published <= cutoff.astimezone(EASTERN).date()
    return True


def source_id(item):
    return item.accession or item.url


class ResearchService:
    """Commands that run the agent and read-only views that never call providers."""

    def __init__(self, db=None, config=None, *, recover=False, http_transport=None, replay=None):
        self.store = Store(Path(db) if db else ROOT / ".data" / "research.sqlite3")
        self.checkpoints = self.store.path.with_suffix(".checkpoints.sqlite3")
        self.config = Path(config) if config else ROOT / "config.local.toml"
        self.http_transport = http_transport
        self.replay = replay
        if recover:
            self.store.recover()

    @property
    def settings(self):
        return load_config(self.config)

    def start(self, session, question, mode, parent=None, *, cutoff=None):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 750:
            raise ValueError("Enter a research question between 1 and 750 characters.")
        if mode not in {"live", "replay"}:
            raise ValueError("Select Live or Fixture replay.")
        question = self.settings.redact(question.strip())
        return self.store.create_turn(session, question, mode, parent, cutoff or utc_now())

    def delete_session(self, session: str):
        try:
            self.store.delete_session(session, self.checkpoints)
        except sqlite3.OperationalError as exc:
            raise ValueError(
                "Could not delete this session. Try again when the database is available."
            ) from exc

    def view(self, turn):
        item = self.store.turn(turn)
        events = self.store.events(turn)
        counts = Counter()
        reused = set()
        for event in events:
            if event["kind"] == "tool" and event["outcome"] == "started":
                counts["lookups"] += 1
            if event["kind"] == "model" and event["outcome"] == "started":
                counts["models"] += 1
            if event["kind"] == "cache":
                reused.update(event["data"].get("sources", []))
                counts["cache_hits"] += 1
            if (
                event["kind"] == "stage"
                and event["stage"] == "Plan"
                and event["pass_no"] == 1
                and (event["outcome"] == "started")
            ):
                counts["followups"] += 1
            if event["outcome"] == "retry":
                counts["retries"] += 1
        counts["reused"] = len(reused)
        item["counts"] = {
            key: counts[key] for key in ("lookups", "models", "reused", "cache_hits", "followups", "retries")
        }
        item["events"] = events
        return item

    async def execute(self, turn, answers=None, *, seconds=INVESTIGATION_TIMEOUT_SECONDS):
        data = self.store.turn(turn)
        settings = self.settings
        status = "Waiting for clarification" if answers is not None else "Queued"
        if data["status"] != status:
            raise ValueError("This investigation is no longer available to run.")
        if answers is not None:
            parsed = Parsed.model_validate(data["parsed"])
            answers = settings.redact(parsed.clarification.validate_answers(answers))
        changes = {}
        if answers is not None:
            changes["answers"] = {**data.get("answers", {}), **answers}
        if not self.store.transition(turn, status, "Running", **changes):
            raise ValueError("This investigation has already started.")
        if answers is not None:
            self.store.event(turn, "Parse", 0, "clarification", "resumed", {"answers": answers})
        spent = data.get("active_seconds", 0.0)
        budget = Budget(seconds - spent)
        config = {"configurable": {"thread_id": data["run_id"]}}
        parent = self.store.turn(data["parent_id"]) if data["parent_id"] else None
        prior = (parent.get("resolved_context") or parent.get("parsed")) if parent else None
        try:
            async with (
                httpx.AsyncClient(transport=self.http_transport, follow_redirects=False) as client,
                # A separate SQLite writer keeps checkpoint commits independent of the durable run log.
                AsyncSqliteSaver.from_conn_string(str(self.checkpoints)) as saver,
            ):
                run = Investigation(self.store, data, settings, client, budget, self.replay)
                graph = run.build().compile(checkpointer=saver)
                state = (
                    Command(resume=changes["answers"])
                    if answers is not None
                    else {
                        "question": data["question"],
                        "prior": prior,
                        "cutoff": data["cutoff"],
                    }
                )
                result = await graph.ainvoke(state, config)
                elapsed = round(spent + budget.elapsed, 4)
                if result.get("__interrupt__"):
                    self.store.finish(
                        turn,
                        "Waiting for clarification",
                        {"active_seconds": elapsed, "clarification": result["parsed"]["clarification"]},
                        "Parse",
                        0,
                        "waiting",
                        {"message": "Waiting for clarification"},
                    )
                elif result.get("problem") and not result.get("findings"):
                    self.store.finish(
                        turn,
                        "Failed",
                        {"error": result["problem"], "active_seconds": elapsed},
                        run.stage,
                        run.pass_no,
                        "failed",
                        {"message": result["problem"]},
                    )
                else:
                    snapshot = run.report_snapshot(result)
                    self.store.finish(
                        turn,
                        "Complete",
                        {
                            "report": snapshot,
                            "active_seconds": elapsed,
                            "clarification": None,
                            "resolved_context": run.context_snapshot(result),
                        },
                        "Report",
                        0,
                        "complete",
                        {"verdict": snapshot["overall"], "active_seconds": elapsed},
                    )
        except Exception as exc:  # noqa: BLE001 - isolate failures at the persisted run boundary
            # Exception text from transports and validation may contain request data.
            log.error("Investigation %s ended with %s", data["run_id"], type(exc).__name__)
            error = "The run stopped unexpectedly. Inspect the recorded stages and try again."
            self.store.finish(
                turn,
                "Failed",
                {"error": error, "active_seconds": round(spent + budget.elapsed, 4)},
                "Report",
                0,
                "failed",
                {"message": error, "type": type(exc).__name__},
            )


class Investigation:
    def __init__(self, store, turn, settings, client, budget, replay):
        self.store, self.turn, self.settings, self.budget = store, turn, settings, budget
        self.id = turn["id"]
        self.mode = turn["mode"]
        self.replay = replay
        self.stage, self.pass_no = "Parse", 0
        transport = Transport(client, budget, self.emit, settings)
        self.router = OpenRouter(transport, settings.openrouter)
        self.providers = LiveProviders(transport)

    def emit(self, kind, outcome, data):
        self.store.event(self.id, self.stage, self.pass_no, kind, outcome, self.settings.redact(data))

    def begin(self, stage, pass_no=0):
        self.stage, self.pass_no = stage, pass_no
        self.emit("stage", "started", {})

    def complete(self, output):
        self.emit("stage", "complete", output)

    def failure(self, message):
        safe = self.settings.redact(message)
        self.emit("stage", "error", {"message": safe})
        return {"problem": safe}

    def build(self):
        graph = StateGraph(State)
        graph.add_node("parse", self.parse)
        graph.add_node("clarify", self.clarify)
        graph.add_node("plan", self.plan)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("verify", self.verify)
        graph.add_node("report", self.report)
        graph.add_edge(START, "parse")
        graph.add_conditional_edges(
            "parse",
            self.after_parse,
            {
                "clarify": "clarify",
                "plan": "plan",
                "stop": END,
            },
        )
        graph.add_edge("clarify", "parse")
        graph.add_conditional_edges(
            "plan", self.after_plan, {"retrieve": "retrieve", "report": "report", "stop": END}
        )
        graph.add_edge("retrieve", "verify")
        graph.add_conditional_edges("verify", self.after_verify, {"plan": "plan", "report": "report"})
        graph.add_edge("report", END)
        return graph

    def after_parse(self, state):
        if state.get("problem"):
            return "stop"
        return "clarify" if state["parsed"]["clarification"] else "plan"

    def after_plan(self, state):
        if not state.get("problem"):
            return "retrieve"
        return "report" if state.get("findings") else "stop"

    def after_verify(self, state):
        if not state.get("followup") and state.get("gaps") and self.budget.remaining > 0.85:
            # A bounded, specific gap is the only route back to Plan.
            parsed = Parsed.model_validate(state["parsed"])
            allowed = self.allowed_actions(state, parsed, followup=True)
            attempted = {repr(parameters(item)) for item in Plan.model_validate(state["plan"]).actions}
            if any(repr(parameters(item)) not in attempted for item in allowed):
                return "plan"
        return "report"

    async def parse_live(self, state):
        question = state["question"]
        if (
            not state.get("answers")
            and not (state.get("prior") or {}).get("as_of")
            and (claim := explicit_financial_claim(question))
        ):
            expected = FinancialSubject(company=claim.entities[0], metric=claim.metric, period=claim.period)
            fields = await self.router.ask(
                FinancialSubject,
                "Parse",
                FINANCIAL_SUBJECT_PROMPT,
                {"question": question, "explicit_fields": expected.model_dump(mode="json")},
            )
            if fields != expected:
                raise ProviderError(
                    "Parse did not preserve the company, metric, or fiscal period in the question."
                )
            return Parsed(claims=[claim])
        context = {
            "question": question,
            "prior_resolved_context": state.get("prior"),
            "clarification_answers": state.get("answers"),
            "run_started": state["cutoff"],
            "rules": {"basis_default": "reported", "scope_default": "consolidated"},
        }
        parsed = await self.router.ask(Parsed, "Parse", PARSE_PROMPT, context)
        if not clarification_asks_for_figures(parsed.clarification):
            return parsed
        message = "The parser asked for figures that retrieval should supply."
        self.emit("validation", "retry", {"message": message})
        repaired = await self.router.ask(
            Parsed,
            "Parse",
            PARSE_PROMPT + "\n" + message + " Resolve the requested claims without asking for those figures.",
            context,
        )
        if clarification_asks_for_figures(repaired.clarification):
            raise ProviderError(message)
        return repaired

    async def parse(self, state):
        self.begin("Parse")
        try:
            if self.mode == "replay":
                prior = Parsed.model_validate(state["prior"]) if state.get("prior") else None
                parser = self.replay.parse if self.replay else parse_fixture
                parsed = parser(state["question"], prior, state.get("answers"))
            else:
                parsed = await self.parse_live(state)
        except (ProviderError, ValidationError) as exc:
            return self.failure(
                str(exc) if isinstance(exc, ProviderError) else "Parse produced an invalid claim."
            )
        cutoff = datetime.fromisoformat(state["cutoff"])
        prior_as_of = (state.get("prior") or {}).get("as_of")
        if (
            parsed.as_of
            and not asks_for_as_of(state["question"], state.get("answers"))
            and parsed.as_of.isoformat() != prior_as_of
        ):
            self.emit(
                "cutoff",
                "ignored",
                {
                    "message": "The parser supplied an unrequested as-of date.",
                    "as_of": parsed.as_of.isoformat(),
                },
            )
            parsed = parsed.model_copy(update={"as_of": None})
        if parsed.as_of:
            if parsed.as_of > cutoff.astimezone(EASTERN).date():
                return self.failure("Choose an as-of date on or before today in U.S. Eastern time.")
            cutoff = date_end(parsed.as_of)
            self.store.set_cutoff(self.id, cutoff)
        output = parsed.model_dump(mode="json")
        self.store.patch(self.id, parsed=output)
        self.complete({"output": output, "cutoff": cutoff.isoformat()})
        if parsed.out_of_scope:
            return {"parsed": output, "problem": parsed.out_of_scope}
        if parsed.clarification:
            self.emit("clarification", "requested", parsed.clarification.model_dump(mode="json"))
        return {"parsed": output, "cutoff": cutoff.isoformat()}

    def clarify(self, state):
        return {"answers": interrupt(state["parsed"]["clarification"])}

    def allowed_actions(self, state, parsed, followup):
        day = datetime.fromisoformat(state["cutoff"]).astimezone(EASTERN).date()
        gaps = [Gap.model_validate(item) for item in state.get("gaps", [])] if followup else None
        eligible = candidates(parsed.claims, day, gaps)
        if followup:
            used = {repr(parameters(item)) for item in Plan.model_validate(state["plan"]).actions}
            return [item for item in eligible if repr(parameters(item)) not in used]
        return eligible

    async def plan(self, state):
        pass_no = int(bool(state.get("findings")))
        self.begin("Plan", pass_no)
        parsed = Parsed.model_validate(state["parsed"])
        choices = self.allowed_actions(state, parsed, bool(pass_no))
        try:
            if self.mode == "replay":
                plan = fixture_plan(choices)
            else:
                plan = await self.router.ask(
                    Plan,
                    "Plan",
                    PLAN_PROMPT,
                    {
                        "claims": state["parsed"]["claims"],
                        "allowed_candidates": dump(*choices),
                        "verification_gaps": state.get("gaps", []),
                        "previous_actions": state.get("plan", {}).get("actions", []),
                    },
                )
            validate_plan(plan, choices, parsed.claims, bool(pass_no))
        except (ProviderError, ValueError) as exc:
            return self.failure(
                str(exc)
                if isinstance(exc, ProviderError)
                else "The retrieval plan failed validation. No actions from this plan were run."
            )
        output = plan.model_dump(mode="json")
        self.complete({"output": output, "gaps": state.get("gaps", [])})
        return {"plan": output, "followup": pass_no}

    def reuse(self, action, claims, cutoff):
        key = self.cache_key(action)
        historical = Parsed.model_validate(self.store.turn(self.id)["parsed"]).as_of is not None
        refreshing = any(claim.latest for claim in claims)
        facts = {}
        for entry in self.store.lookups(self.turn["session_id"], self.mode):
            if historical and datetime.fromisoformat(entry["cutoff"]) < cutoff:
                continue
            if refreshing and utc_now() - datetime.fromisoformat(entry["observed_at"]) > timedelta(
                minutes=15
            ):
                continue
            records = [item for item in EVIDENCE.validate_python(entry["records"]) if available(item, cutoff)]
            if entry["cache_key"] == key and records and action.operation != "financials":
                if action.source == "fred":
                    day = cutoff.astimezone(EASTERN).date()
                    if (self.mode == "live" or historical) and not all(
                        item.vintage == day for item in records
                    ):
                        continue
                    if not historical and utc_now() - datetime.fromisoformat(
                        entry["observed_at"]
                    ) > timedelta(minutes=15):
                        continue
                return records, True
            if action.operation == "financials":
                if refreshing and entry["cache_key"] != key:
                    continue
                facts.update(
                    {
                        item.id: item
                        for item in records
                        if item.source == "sec" and item.entity == action.entity
                    }
                )
        if action.operation != "financials" or not facts:
            return [], False
        values = list(facts.values())
        used = []
        complete = True
        for claim in claims:
            period = resolve_period(claim.model_copy(update={"entities": [action.entity]}), values)
            if not period:
                complete = False
                continue
            periods = (
                action.periods
                if claim.kind == "conflict"
                else required_periods(claim.model_copy(update={"period": period}))
            )
            requirements = [(item, claim.metric) for item in periods]
            if claim.measure == "margin":
                requirements.append((period, "revenue"))
            for period, metric in requirements:
                match = select_fact(values, action.entity, metric, period, claim.basis, claim.scope)
                if not match:
                    complete = False
                else:
                    used.append(match)
        included = {source_id(item) for item in used}
        derived = {ref for item in used for ref in item.derived_from}
        return [item for item in values if source_id(item) in included or item.id in derived], complete

    @staticmethod
    def cache_key(action):
        return hashlib.sha256(json_text(parameters(action)).encode()).hexdigest()

    async def action(self, action, claims, cutoff, known):
        sanitized = action.model_dump(mode="json")
        reused, fully_cached = self.reuse(action, claims, cutoff)
        sources = sorted({source_id(item) for item in reused})
        if reused:
            self.emit(
                "cache",
                "hit",
                {
                    "action": sanitized,
                    "sources": sources,
                    "partial": not fully_cached,
                    "evidence_ids": [item.id for item in reused],
                },
            )
            if fully_cached:
                return reused, sources, None, "cached"
        try:
            remaining = self.budget.remaining - 0.34
            if remaining <= 0:
                raise ProviderError("The active run budget expired.")
            async with asyncio.timeout(remaining):
                if self.mode == "replay" or self.replay:
                    tool = f"{'Fixture' if self.mode == 'replay' else 'Frozen'} {action.source}"
                    self.emit("tool", "started", {"tool": tool, "action": sanitized})
                    lookup = self.replay.lookup if self.replay else lookup_fixture
                    records = await lookup(action, cutoff)
                    self.emit("tool", "complete", {"tool": tool, "results": len(records)})
                else:
                    records = await self.providers.lookup(action, cutoff, [*known, *reused])
            records = EVIDENCE.validate_python(self.settings.redact(dump(*records)))
            records = [item for item in records if available(item, cutoff)]
        except (ProviderError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ProviderError)
                else "The active run budget expired."
                if isinstance(exc, TimeoutError)
                else "The provider data could not be normalized."
            )
            self.emit("lookup", "error", {"action": sanitized, "message": message})
            return reused, sources, message, "partial" if reused else "lookup"
        if records:
            self.store.save_lookup(
                self.turn["session_id"], self.mode, self.cache_key(action), cutoff, dump(*records)
            )
        record_ids = [item.id for item in records]
        self.emit(
            "lookup", "complete" if records else "empty", {"action": sanitized, "evidence_ids": record_ids}
        )
        combined = {item.id: item for item in [*reused, *records]}
        error = None if records else f"No eligible evidence for {action.purpose.rstrip('.')}."
        return list(combined.values()), sources, error, "partial" if reused else "lookup"

    async def retrieve(self, state):
        self.begin("Retrieve", state.get("followup", 0))
        parsed = Parsed.model_validate(state["parsed"])
        plan = Plan.model_validate(state["plan"])
        cutoff = datetime.fromisoformat(state["cutoff"])
        known = EVIDENCE.validate_python(state.get("evidence", []))
        results = await asyncio.gather(
            *(
                self.action(
                    action, [claim for claim in parsed.claims if claim.id in action.claim_ids], cutoff, known
                )
                for action in plan.actions
            )
        )
        combined = {item.id: item for item in known}
        reused = set(state.get("reused", []))
        limitations = list(state.get("limitations", []))
        summaries = []
        for action, (records, cached, error, retrieval) in zip(plan.actions, results):
            combined.update({item.id: item for item in records})
            reused.update(cached)
            if error:
                limitations.append(error)
            summaries.append(
                {
                    "action": action.model_dump(mode="json"),
                    "reused": bool(cached),
                    "retrieval": retrieval,
                    "records": len(records),
                    "evidence_ids": [item.id for item in records],
                    "error": error,
                }
            )
        evidence = dump(*combined.values())
        self.store.patch(self.id, evidence=evidence)
        self.complete({"lookups": summaries})
        return {"evidence": evidence, "limitations": limitations, "reused": sorted(reused)}

    async def verify(self, state):
        self.begin("Verify", state.get("followup", 0))
        parsed = Parsed.model_validate(state["parsed"])
        evidence = EVIDENCE.validate_python(state["evidence"])
        old = Attributions.model_validate({"entries": state.get("attributions", [])}).entries
        documents = [item for item in evidence if isinstance(item, Document) and item.published]
        relevant = [claim for claim in parsed.claims if claim.kind in {"event", "causal"}]
        limitations = list(state.get("limitations", []))
        if relevant and documents:
            try:
                if self.mode == "replay":
                    annotator = self.replay.attributions if self.replay else fixture_attributions
                    proposed = annotator(relevant, evidence)
                elif self.budget.remaining > 1.2:
                    proposed = await self.router.ask(
                        Attributions,
                        "source attribution",
                        ATTRIBUTION_PROMPT,
                        {
                            "claims": dump(*relevant),
                            "documents": [
                                {
                                    "id": item.id,
                                    "date": item.published.isoformat(),
                                    "publisher": item.publisher,
                                    "passages": [text[:800] for text in item.passages[:12]],
                                }
                                for item in documents[:12]
                            ],
                        },
                    )
                else:
                    proposed = Attributions(entries=[])
                old += proposed.entries
            except ProviderError as exc:
                limitations.append(str(exc))
        checked = valid_attributions(parsed.claims, evidence, old)
        unique = {(item.claim_id, item.evidence_id, item.quote, item.role): item for item in checked}
        findings, gaps = verify(parsed.claims, evidence, list(unique.values()))
        output = {"findings": dump(*findings), "gaps": dump(*gaps), "attributions": dump(*unique.values())}
        self.store.patch(self.id, verification=output)
        self.complete(output)
        return {**output, "limitations": limitations}

    async def report(self, state):
        self.begin("Report")
        snapshot = self.report_snapshot(state)
        self.complete(
            {
                "overall": snapshot["overall"],
                "conclusion": snapshot["conclusion"],
                "evidence_ids": snapshot["evidence_ids"],
            }
        )
        return {}

    def context_snapshot(self, state):
        parsed = Parsed.model_validate(state["parsed"])
        evidence = EVIDENCE.validate_python(state.get("evidence", []))
        claims = []
        for claim in parsed.claims:
            period = resolve_period(claim, evidence) if claim.kind in {"financial", "conflict"} else None
            if period and claim.latest:
                claim = claim.model_copy(update={"period": period, "latest": False})
            claims.append(claim)
        return parsed.model_copy(update={"claims": claims}).model_dump(mode="json")

    def report_snapshot(self, state):
        findings = [Finding.model_validate(item) for item in state.get("findings", [])]
        evidence = EVIDENCE.validate_python(state.get("evidence", []))
        refs = list(dict.fromkeys(ref for finding in findings for ref in finding.evidence_ids))
        lookup = {item.id: item for item in evidence}
        attributions = state.get("attributions", [])
        timeline = []
        for item in attributions:
            document = lookup[item["evidence_id"]]
            timeline.append(
                {
                    "date": document.published.isoformat(),
                    "title": document.publisher or document.title,
                    "passage": item["quote"],
                    "evidence_id": item["evidence_id"],
                    "role": item["role"],
                }
            )
        if any(claim["kind"] in {"event", "causal"} for claim in state["parsed"]["claims"]):
            for metric in {
                item.metric for item in evidence if isinstance(item, Fact) and item.source == "fred"
            }:
                recent = max(
                    (item for item in evidence if isinstance(item, Fact) and item.metric == metric),
                    key=lambda item: item.end,
                )
                timeline.append(
                    {
                        "date": recent.end.isoformat(),
                        "title": recent.title,
                        "role": "context",
                        "passage": render_number(recent.value, recent.unit),
                        "evidence_id": recent.id,
                    }
                )
        sorted_timeline = sorted(timeline, key=lambda item: (item["date"], item["title"], item["passage"]))
        limits = list(state.get("limitations", []))
        if state.get("problem"):
            limits.append(state["problem"])
        if not self.budget.remaining:
            limits.append("The active investigation budget was exhausted.")
        conclusion = []
        for finding in findings:
            conclusion.append(finding.reason)
            event = next(
                (
                    item
                    for item in sorted_timeline
                    if item["role"] == "event"
                    and any(
                        attribution["evidence_id"] == item["evidence_id"]
                        and attribution["claim_id"] == finding.claim_id
                        for attribution in attributions
                    )
                ),
                None,
            )
            if event and finding.verdict == "Supported":
                conclusion[-1] = f"{event['date']} · {event['title']} reported “{event['passage']}”"
        return {
            "overall": overall(findings),
            "conclusion": " ".join(conclusion),
            "findings": dump(*findings),
            "evidence_ids": refs,
            "timeline": sorted_timeline,
            "limitations": list(dict.fromkeys(limits)),
            "carried": state["parsed"].get("carried", []),
            "reused_sources": state.get("reused", []),
            "cutoff": state["cutoff"],
        }
