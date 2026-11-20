const stages = ["Parse", "Plan", "Retrieve", "Verify", "Report"];
const sourceNames = {sec: "SEC EDGAR", fred: "FRED", news: "News search"};
const operationNames = {
    financials: "Consolidated financial facts", release: "Earnings release", filing: "Filing passages",
    observations: "Macro observations", articles: "Article text",
};

function node(tag, attributes = {}, ...children) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
        if (value !== undefined && value !== null && value !== false) {
            element.setAttribute(name, value === true ? "" : String(value));
        }
    }
    for (const child of children.flat(Infinity)) {
        if (child !== null && child !== undefined && child !== false) element.append(child);
    }
    return element;
}

function button(label, action, value, attributes = {}, ...children) {
    return node("button", {type: "button", "data-action": action, "data-value": value,
        "data-focus": action + "-" + (value || ""), ...attributes}, label, children);
}

function badge(label) {
    const color = ["Contradicted", "Failed", "Interrupted"].includes(label) ? "negative"
        : ["Unresolved", "Partially supported", "Waiting for clarification"].includes(label) ? "warning"
        : ["Supported", "Complete", "Running"].includes(label) ? "" : "neutral";
    return node("span", {class: "status " + color}, label);
}

function period(value) {
    if (!value) return "";
    return "FY" + value.year + (value.quarter ? " Q" + value.quarter : "");
}

function disclosure(state, id, label, content, className = "disclosure", bodyClass = "disclosure-body") {
    return node("details", {id, class: className, open: state.open.has(id)},
        node("summary", {}, label), node("div", {class: bodyClass}, content));
}

function block(heading, content) {
    return node("section", {class: "block"}, node("h3", {}, heading), content);
}

function definitionList(entries, className = "structured") {
    const items = entries.filter(([, value]) => value !== "" && value !== null && value !== undefined);
    return node("dl", {class: className}, items.map(([label, value]) => {
        const pair = [node("dt", {}, label), node("dd", {}, value)];
        return className === "side-facts" ? node("div", {}, pair) : pair;
    }));
}

function link(url, label) {
    try {
        const target = new URL(url);
        if (target.protocol === "https:" && !target.username && !target.password) {
            return node("a", {href: target.href, target: "_blank", rel: "noopener noreferrer"}, label);
        }
    } catch {
        // A malformed provider link is shown as plain text.
    }
    return node("span", {}, label);
}

function header(data, state) {
    const turn = data.selected;
    const suggestion = turn?.mode === data.mode ? turn?.report?.suggestion : null;
    const mode = node("select", {id: "research-mode", "aria-label": "Research mode", disabled: Boolean(state.pending)},
        node("option", {value: "live", selected: data.mode === "live"}, "Live"),
        node("option", {value: "replay", selected: data.mode === "replay"}, "Fixture replay"));
    const form = node("form", {id: "research-form", class: "prompt-form"},
        node("label", {for: "research-question", class: "visually-hidden"}, "Research question"),
        node("textarea", {id: "research-question", class: "question", required: true, maxlength: 750, rows: 1,
            placeholder: "Ask about a financial claim or company event", "data-focus": "question"}, state.draft),
        node("button", {type: "submit", class: "primary", disabled: Boolean(state.pending)},
            "Run investigation", node("span", {"aria-hidden": "true"}, "→")));
    return node("header", {class: "masthead"}, node("div", {class: "shell"},
        node("div", {class: "brandbar"},
            node("div", {class: "brand"}, node("span", {class: "mark", "aria-hidden": "true"},
                node("i"), node("i"), node("i")), "Financial Research Agent"),
            node("div", {class: "mode-control"}, mode)),
        node("div", {class: "hero"}, node("h1", {}, "Investigate a financial claim"),
            node("p", {class: "intro"}, "Check financial claims against filings, economic data, and independent coverage."),
            form, data.error && node("p", {class: "prompt-error", role: "alert"}, data.error),
            node("div", {class: "examples", "aria-label": "Example questions"}, node("span", {}, "Try an example"),
                data.examples.map(item => button(item.label, "fill", item.question, {class: "example"}))),
            suggestion && node("div", {class: "follow-up"}, node("span", {}, "Continue this report"),
                button(suggestion, "fill", suggestion, {class: "example"})),
            data.mode === "live" && data.messages.length > 0 && disclosure(
                state, "provider-setup", "Provider setup", node("ul", {}, data.messages.map(message => node("li", {}, message))),
                "provider-setup", "setup-body"))));
}

function sidebar(data, state) {
    const active = data.selection;
    const running = data.turns.some(item => ["Queued", "Running"].includes(item.status));
    const confirming = state.deleteSession === active.session_id;
    const sessionTitle = data.sessions.find(item => item.id === active.session_id)?.title;
    return node("aside", {class: "session-sidebar", "aria-label": "Session history", "data-expanded": String(state.sessions)},
        node("div", {class: "sidebar-bar"}, node("h2", {}, "Research sessions"),
            button("Sessions", "sessions", null, {class: "secondary mobile-sessions",
                "aria-controls": "session-menu", "aria-expanded": String(state.sessions)})),
        node("div", {class: "sidebar-menu", id: "session-menu"},
            button("", "new", null, {class: "secondary new-session", "aria-label": "New session"},
                node("span", {"aria-hidden": "true"}, "+"), "New session"),
            node("nav", {class: "session-list", "aria-label": "Research sessions"},
                data.sessions.map(item => {
                    const selected = active.session_id === item.id;
                    return node("div", {class: "session-card"},
                        button(item.title === "New session" ? "Untitled session" : item.title, "session", item.id,
                            {class: "session-title", "aria-current": String(selected)}),
                        selected && button("×", "confirm_delete", item.id,
                            {class: "session-delete-icon", "aria-label": "Delete session",
                                "aria-expanded": String(confirming), disabled: running || Boolean(state.pending),
                                title: running ? "Wait for the active investigation to finish" : "Delete session"}));
                })),
            confirming && node("div", {class: "delete-confirmation", role: "group", "aria-label": "Confirm session deletion"},
                node("p", {}, `Delete “${sessionTitle === "New session" ? "Untitled session" : sessionTitle}” and all its investigations? This cannot be undone.`),
                node("div", {class: "delete-actions"},
                    button("Cancel", "cancel_delete", active.session_id, {class: "secondary"}),
                    button("Delete permanently", "delete_session", active.session_id,
                        {class: "secondary delete-session", disabled: running || Boolean(state.pending)}))),
            node("section", {"aria-labelledby": "session-questions"},
                node("h3", {class: "eyebrow sidebar-title", id: "session-questions"}, "Session questions"),
                data.turns.length === 0 && node("p", {class: "session-empty"}, "Investigations appear here."),
                node("nav", {class: "session-turns", "aria-label": "Session questions"},
                    data.turns.map(item => button(item.question, "turn", item.id,
                        {"aria-current": String(active.turn_id === item.id)},
                        node("span", {}, item.status + (item.mode === "replay" ? " · Replay" : "")))))),
            node("p", {class: "storage-note"}, "Saved on this computer")));
}

function heading(eyebrow, title, description, status) {
    return [
        node("div", {class: "panel-topline"}, node("p", {class: "eyebrow"}, eyebrow), status && badge(status)),
        node("h2", {id: "stage-title"}, title),
        description && node("p", {class: "lede"}, description),
    ];
}

function clarification(turn, state) {
    const request = turn.clarification;
    return [
        heading("Parse", "Clarify the claim", request.message, "Waiting for clarification"),
        node("form", {id: "clarify-form", class: "clarify"},
            request.fields.map(field => {
                const id = "clarify-" + field.name;
                const attributes = {id, name: field.name, required: true, "data-focus": id};
                const control = field.control === "select" ? node("select", attributes,
                    node("option", {value: ""}, "Choose " + field.label.toLowerCase()),
                    field.options.map(item => node("option", {value: item.value,
                        selected: state.answers[field.name] === item.value}, item.label)))
                    : node("input", {...attributes, type: "text", maxlength: 160, value: state.answers[field.name] || ""});
                return node("div", {class: "clarify-field"}, node("label", {for: id}, field.label), control);
            }),
            node("button", {type: "submit", class: "primary", disabled: Boolean(state.pending)}, "Resume investigation")),
    ];
}

function claimList(findings) {
    return node("div", {class: "result-list"}, findings.map(item => node("div", {class: "result-claim"},
        node("strong", {}, item.label), badge(item.verdict), node("p", {}, item.reason))));
}

function actionLabel(item) {
    return [operationNames[item.operation], item.entity, item.series, item.periods.map(period).join(", "),
        item.start && item.end ? item.start + " to " + item.end : ""].filter(Boolean).join(" · ");
}

function planPanel(turn, state, event) {
    const passes = turn.events.filter(item => item.stage === "Plan" && item.kind === "stage" && item.outcome === "complete");
    const completed = turn.events.find(item => item.stage === "Retrieve" && item.pass_no === event.pass_no
        && item.kind === "stage" && item.outcome === "complete");
    const labels = Object.fromEntries((turn.parsed?.claims || []).map(claim => [claim.id, claim.label]));
    return [
        heading("Investigation workflow", event.pass_no ? "Follow-up plan" : "Retrieval plan",
            event.pass_no ? event.data.gaps?.map(item => item.reason).join(" ") : null),
        passes.length > 1 && node("div", {class: "plan-toggle", role: "group", "aria-label": "Planner passes"},
            passes.map(item => button(item.pass_no ? "Follow-up plan" : "Initial plan", "history", item.sequence,
                {class: "secondary", "aria-pressed": String(item.sequence === event.sequence)}))),
        node("ul", {class: "plan-list"}, event.data.output.actions.map(item => {
            const result = completed?.data.lookups.find(entry => JSON.stringify(entry.action) === JSON.stringify(item));
            return node("li", {}, node("p", {class: "eyebrow"}, sourceNames[item.source]),
                node("p", {class: "request"}, actionLabel(item)), node("p", {}, item.purpose),
                node("p", {class: "plan-claim"}, "Claim · " + item.claim_ids.map(id => labels[id] || id).join(" / ")),
                item.query && node("p", {}, item.query),
                result?.reused && node("p", {class: "reuse-mark"}, "Reused from this session"),
                result && result.retrieval !== "cached" && node("p", {},
                    result.error || (turn.mode === "replay" ? "Fixture lookup" : "Live lookup")));
        })),
    ];
}

function evidenceBlocks(turn, state, ids, namespace) {
    const records = new Set(ids);
    const groups = new Map();
    for (const item of turn.evidence || []) {
        if (!records.has(item.id)) continue;
        const key = item.type === "fact" ? item.accession || item.id : item.id;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    }
    return node("div", {class: "sources"}, [...groups.entries()].map(([key, group]) => {
        const first = group[0];
        const reuse = (turn.report?.reused_sources || turn.events.filter(event => event.kind === "cache")
            .flatMap(event => event.data.sources)).includes(first.accession || first.url);
        const body = [
            reuse && node("p", {class: "reuse-mark source-reuse"}, "Reused from this session"),
            node("p", {}, [sourceNames[first.source], first.publisher, first.form, first.published, first.accession,
                first.vintage && "Vintage " + first.vintage].filter(Boolean).join(" · ")),
            group.map(item => item.type === "fact" ? node("p", {},
                [item.metric_label + " · " + item.display_value, item.start + " to " + item.end,
                    item.basis, item.scope, item.frequency, item.restatement, item.derivation].filter(Boolean).join(" · "))
                : item.passages.map(text => node("p", {}, text))),
            link(first.url, "Open original source"),
        ];
        return disclosure(state, namespace + "-" + key, first.title, body);
    }));
}

function reportPanel(turn, state) {
    const report = turn.report;
    const calculations = report.findings.flatMap(item => item.calculations);
    const referenceIds = [...report.evidence_ids, ...report.timeline.map(item => item.evidence_id)];
    const records = Object.fromEntries(turn.evidence.map(item => [item.id, item]));
    return [
        heading("Investigation report", "Conclusion", report.conclusion, report.overall),
        report.carried.length > 0 && node("p", {class: "lede"}, "Carried forward · " + report.carried.join(", ")),
        calculations.length > 0 && node("div", {class: "metrics"}, calculations.map(item =>
            node("div", {class: "metric"}, node("span", {}, item.label), node("strong", {}, item.display_value)))),
        claimList(report.findings),
        disclosure(state, "supporting-details", "Supporting details", [
            calculations.length > 0 && block("Calculations", node("div", {class: "sources"},
                calculations.map((item, index) => disclosure(state, "calculation-" + index,
                    item.label + " · " + item.display_value, [
                        node("p", {}, item.formula),
                        node("table", {class: "data-table"}, node("thead", {}, node("tr", {},
                            node("th", {scope: "col"}, "Operand"), node("th", {scope: "col"}, "Value"))),
                            node("tbody", {}, item.operands.map(operand => node("tr", {},
                                node("td", {}, operand.label), node("td", {}, operand.display_value))))),
                    ])))),
            referenceIds.length > 0 && block("Source evidence", evidenceBlocks(turn, state, referenceIds, "report")),
            report.timeline.length > 0 && block("Evidence timeline", node("ol", {class: "timeline"},
                report.timeline.map(item => node("li", {}, node("time", {datetime: item.date}, item.date),
                    link(records[item.evidence_id]?.url, item.title), node("p", {}, item.passage))))),
            block("Limitations", node("div", {},
                report.limitations.length ? report.limitations.map(item => node("p", {}, item))
                    : node("p", {}, "Claims were evaluated against the evidence available at the saved cutoff."),
                node("p", {}, "Evidence cutoff · " + report.cutoff))),
        ], "report-support", "report-support-body"),
    ];
}

function stagePanel(turn, state, stage, event, running) {
    if (!turn) return heading("Research workspace", "Start an investigation",
        "Ask a question above or choose an example to inspect a research workflow.");
    if (event && event.kind !== "stage") {
        return [heading(event.stage, event.data.tool || event.kind, event.outcome), node("pre", {class: "inspect"},
            JSON.stringify({sequence: event.sequence, time: event.timestamp, ...event.data}, null, 2))];
    }
    if (stage === "Parse" && turn.status === "Waiting for clarification") return clarification(turn, state);
    if (stage === "Report" && turn.report) return reportPanel(turn, state);
    if (running) return [
        heading("Investigation workflow", stage, null, turn.status),
        node("div", {class: "stage-progress"},
            node("span", {class: "stage-pulse", "aria-hidden": "true"}),
            node("span", {}, "Stage in progress"),
            node("span", {class: "stage-clock", role: "timer", "data-started-at": event.timestamp})),
    ];
    if (event?.outcome === "started") {
        return heading("Investigation workflow", stage,
            turn.status === "Interrupted" ? "The app closed while this stage was running." : "Stage started",
            turn.status);
    }
    if (event?.outcome === "error" || turn.error && !event) {
        return heading("Investigation workflow", stage, event?.data.message || turn.error, turn.status);
    }
    if (!event) return heading("Investigation workflow", stage, "This stage has no recorded output.");
    if (stage === "Plan") return planPanel(turn, state, event);
    if (stage === "Parse") {
        const parsed = event.data.output;
        return [heading("Investigation workflow", "Parsed question", parsed.out_of_scope || parsed.clarification?.message),
            definitionList([["Evidence cutoff", event.data.cutoff], ["Carried forward", parsed.carried.join(", ")]]),
            parsed.claims.length > 0 && block("Resolved claims", node("ul", {}, parsed.claims.map(claim =>
                node("li", {}, [claim.label, claim.entities.join(" / "), period(claim.period)].filter(Boolean).join(" · ")))) )];
    }
    if (stage === "Retrieve") return [
        heading("Investigation workflow", "Retrieved evidence"),
        node("ul", {class: "plan-list"}, event.data.lookups.map(result => node("li", {},
            node("p", {class: "request"}, sourceNames[result.action.source] + " · " + actionLabel(result.action)),
            result.reused && node("p", {class: "reuse-mark"}, "Reused from this session"),
            result.retrieval !== "cached" && node("p", {}, result.error || result.records + " evidence records")))),
        block("Source records", evidenceBlocks(turn, state, event.data.lookups.flatMap(item => item.evidence_ids), "retrieve")),
    ];
    if (stage === "Verify") return [
        heading("Investigation workflow", event.pass_no ? "Follow-up verification" : "Evidence verification"),
        claimList(event.data.findings),
        event.data.gaps.length > 0 && block(event.pass_no ? "Remaining evidence gaps" : "Evidence needed",
            node("ul", {}, event.data.gaps.map(gap => node("li", {}, gap.reason)))),
    ];
    return heading("Investigation workflow", stage, event.data.conclusion);
}

function history(turn, state) {
    const stageEvents = turn.events.filter(item => item.kind === "stage" && item.outcome !== "started");
    const entries = (events, compact) => node("ol", {class: "history"}, events.map(event => {
        const stage = (event.pass_no ? "Follow-up " : "") + event.stage;
        const label = compact ? stage : event.sequence + " · " + (event.data.tool || event.kind) + " · " + event.outcome;
        return node("li", {}, button(label, "history", event.sequence,
            {"aria-current": String(state.event === event.sequence)}),
            node("small", {}, compact ? event.data.gaps?.[0]?.reason || event.data.message || "" : stage));
    }));
    return disclosure(state, "investigation-history", "Investigation history", [
        entries(stageEvents, true),
        disclosure(state, "raw-events", "Model, lookup, and status events", entries(turn.events, false)),
    ]);
}

function runPanel(turn, state) {
    return node("aside", {class: "panel panel-side", "aria-labelledby": "run-heading"},
        node("h3", {id: "run-heading"}, "Run details"),
        node("p", {class: "side-question"}, turn ? turn.question : "Select a question to see its run details."),
        turn && definitionList([
            ["Sources used", turn.report?.sources.join(", ") || "—"],
            [turn.mode === "replay" ? "Fixture lookups" : "New provider lookups", turn.counts.lookups],
            ["Reused sources", turn.counts.reused], ["Cache hits", turn.counts.cache_hits],
            ["Evidence follow-ups", turn.counts.followups], ["Transport retries", turn.counts.retries],
            ["Model calls", turn.counts.models],
            ["Active time", turn.active_seconds === undefined ? undefined : turn.active_seconds.toFixed(2) + " s"],
        ], "side-facts"),
        turn && history(turn, state));
}

function render(root, data, state) {
    clearInterval(state.stageClock);
    const focused = root.getRootNode().activeElement;
    const focusKey = focused?.getAttribute("data-focus");
    const turn = data.selected;
    const events = turn?.events || [];
    const newest = events.findLast(item => item.kind === "stage");
    const selectedEvent = state.event && events.find(item => item.sequence === state.event);
    const stage = selectedEvent?.stage || state.stage || (turn?.report ? "Report" : newest?.stage) || "Parse";
    const event = selectedEvent || events.findLast(item => item.stage === stage && item.kind === "stage");
    const status = turn?.status || "Ready";
    const running = status === "Running" && newest?.outcome === "started" && event === newest;
    root.replaceChildren(header(data, state), node("main", {class: "shell workspace"},
        sidebar(data, state),
        node("div", {class: "workflow-main"},
            node("section", {class: "workflow", "aria-labelledby": "workflow-heading"},
                node("div", {class: "section-heading"},
                    node("h2", {id: "workflow-heading"}, "Investigation workflow"),
                    node("div", {role: "status"}, badge(status))),
                node("nav", {class: "steps", "aria-label": "Investigation steps"},
                    stages.map((item, index) => button("", "stage", item,
                        {class: "step " + (newest?.stage === item ? "current"
                            : events.some(entry => entry.stage === item) ? "done" : "pending"),
                        "aria-pressed": String(stage === item), "aria-label": item},
                        node("span", {class: "step-number", "aria-hidden": "true"}, String(index + 1)),
                        node("span", {}, item))))),
            node("div", {class: "layout"},
                node("section", {class: "panel panel-main", "aria-labelledby": "stage-title", tabindex: -1},
                    stagePanel(turn, state, stage, event, running)), runPanel(turn, state)))));
    const clock = root.querySelector(".stage-clock");
    if (clock) {
        const started = Date.parse(clock.dataset.startedAt);
        const tick = () => {
            if (!clock.isConnected) {
                clearInterval(state.stageClock);
                return;
            }
            const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
            const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} elapsed`;
            if (clock.textContent !== elapsed) clock.textContent = elapsed;
        };
        tick();
        state.stageClock = setInterval(tick, 250);
    }
    if (focusKey) {
        const replacement = root.querySelector("[data-focus=" + CSS.escape(focusKey) + "]");
        if (replacement && replacement !== document.activeElement) replacement.focus({preventScroll: true});
    }
}

export default function workspace({parentElement, data, setTriggerValue}) {
    const root = parentElement.querySelector(".research-app");
    if (!root || !data) return;
    if (!root.researchState) {
        root.researchState = {draft: "", sessions: false, open: new Set(), answers: {}};
        root.addEventListener("toggle", event => {
            if (event.target.nodeName !== "DETAILS") return;
            if (event.target.open) root.researchState.open.add(event.target.id);
            else root.researchState.open.delete(event.target.id);
        }, true);
    }
    const state = root.researchState;
    const key = JSON.stringify(data);
    if (state.key !== key) {
        state.key = key;
        if (state.pending === data.acknowledged) state.pending = null;
        if (state.deleteSession !== data.selection.session_id) state.deleteSession = null;
        if (state.turn !== data.selected?.id) {
            state.turn = data.selected?.id;
            state.stage = null;
            state.event = null;
            state.open.clear();
            state.answers = {};
        }
        render(root, data, state);
    }
    const send = (command, fields = {}) => {
        if (state.pending) return;
        const nonce = crypto.randomUUID();
        state.pending = nonce;
        render(root, data, state);
        setTriggerValue("action", {command, nonce, ...fields});
    };
    root.onclick = event => {
        const control = event.target.closest("button[data-action]");
        if (!control) return;
        const action = control.dataset.action;
        const value = control.dataset.value;
        if (action === "fill") {
            state.draft = value;
            const text = root.querySelector("#research-question");
            text.value = value;
            text.focus();
        } else if (action === "sessions") {
            state.sessions = !state.sessions;
            render(root, data, state);
        } else if (action === "confirm_delete" || action === "cancel_delete") {
            state.deleteSession = action === "confirm_delete" ? value : null;
            render(root, data, state);
            root.querySelector(`[data-action="${state.deleteSession ? "cancel_delete" : "confirm_delete"}"]`)
                ?.focus();
        } else if (action === "stage" || action === "history") {
            state.stage = action === "stage" ? value : null;
            state.event = action === "history" ? Number(value) : null;
            render(root, data, state);
        } else {
            if (action === "new") state.draft = "";
            if (action === "delete_session") state.deleteSession = null;
            send(action, {id: value});
        }
    };
    root.oninput = event => {
        if (event.target.id === "research-question") state.draft = event.target.value;
        if (event.target.form?.id === "clarify-form") state.answers[event.target.name] = event.target.value;
    };
    root.onchange = event => {
        if (event.target.id === "research-mode") send("mode", {mode: event.target.value});
    };
    root.onsubmit = event => {
        event.preventDefault();
        if (state.pending) return;
        if (event.target.id === "research-form") {
            state.stage = null;
            state.event = null;
            send("run", {question: state.draft});
        } else if (event.target.id === "clarify-form") {
            state.stage = null;
            state.event = null;
            send("clarify", {answers: Object.fromEntries(new FormData(event.target))});
        }
    };
}
