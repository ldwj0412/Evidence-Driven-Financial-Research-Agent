const stages = ["Parse", "Plan", "Retrieve", "Verify", "Report"];
const sourcesDisclosure = "support-sources";
const activityDisclosure = "run-activity";
const questionsDisclosure = "session-questions";
const defaultOpenDisclosures = [sourcesDisclosure, activityDisclosure, questionsDisclosure];
const shortTime = new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});
const cutoffTime = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
});
const planDate = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
});
const planList = new Intl.ListFormat("en", {style: "long", type: "conjunction"});
const sourceNames = {sec: "SEC EDGAR", fred: "FRED", news: "News search"};
const operationNames = {
    financials: "Consolidated financial facts", release: "Earnings release", filing: "Filing passages",
    observations: "Macro observations", articles: "Article text",
};
const planTitles = {
    financials: "Get reported financial figures", release: "Read the earnings release",
    filing: "Check company filings", observations: "Look up economic data", articles: "Search news coverage",
};
const seriesNames = {FEDFUNDS: "Federal funds rate", CPIAUCSL: "Consumer Price Index", UNRATE: "Unemployment rate"};

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

function cutoffLabel(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : node("time", {datetime: value, title: value}, cutoffTime.format(date));
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

function sourceUrl(url) {
    try {
        const target = new URL(url);
        if (target.protocol === "https:" && !target.username && !target.password) return target.href;
    } catch {
        // A malformed provider link is shown as plain text.
    }
    return null;
}

function link(url, label) {
    const href = sourceUrl(url);
    return href ? node("a", {href, target: "_blank", rel: "noopener noreferrer"}, label) : node("span", {}, label);
}

function header(data, state) {
    const turn = data.selected;
    const suggestion = turn?.mode === data.mode ? turn?.report?.suggestion : null;
    const mode = node("select", {id: "research-mode", "aria-label": "Research mode", disabled: Boolean(state.pending)},
        node("option", {value: "live", selected: data.mode === "live"}, "Live"),
        node("option", {value: "replay", selected: data.mode === "replay"}, "Fixture replay"));
    const form = node("form", {id: "research-form", class: "prompt-form"},
        node("label", {for: "research-question", class: "visually-hidden"}, "Research question"),
        node("span", {class: "prompt-icon", "aria-hidden": "true"}),
        node("textarea", {id: "research-question", class: "question", required: true, maxlength: 750, rows: 1,
            placeholder: "Ask about a financial claim or company event", "data-focus": "question"}, state.draft),
        node("button", {type: "submit", class: "primary", "aria-label": "Run investigation", disabled: Boolean(state.pending)},
            node("span", {}, "Run", node("span", {class: "run-long"}, " investigation")),
            node("span", {"aria-hidden": "true"}, "→")));
    return node("header", {class: "masthead"}, node("div", {class: "shell"},
        node("div", {class: "brandbar"},
            node("div", {class: "brand"}, node("span", {class: "mark", "aria-hidden": "true"},
                node("i"), node("i"), node("i")), "Financial Research Agent"),
            node("div", {class: "mode-control"}, mode)),
        node("div", {class: "hero"}, node("h1", {}, "Investigate a financial claim"),
            node("p", {class: "intro"}, "Check filings, compare figures, and see the evidence behind each answer."),
            form, data.error && node("p", {class: "prompt-error", role: "alert"}, data.error),
            node("div", {class: "examples", "aria-label": "Example questions"}, node("span", {}, "Try an example"),
                data.examples.map(item => button(item.label, "fill", item.question, {class: "example"}))),
            suggestion && node("div", {class: "follow-up"}, node("span", {}, "Continue this report"),
                button(suggestion, "fill", suggestion, {class: "example"})),
            data.mode === "live" && data.messages.length > 0 && disclosure(
                state, "provider-setup", "Provider setup", node("ul", {}, data.messages.map(message => node("li", {}, message))),
                "provider-setup", "setup-body"))));
}

function marketTexture() {
    const canvas = node("canvas", {class: "market-texture", "aria-hidden": "true"});
    const context = canvas.getContext("2d");
    if (!context) return canvas;
    new ResizeObserver(([{contentRect: {width, height}}]) => {
        if (width === 0 || height === 0) return;
        const scale = Math.min(devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        context.scale(scale, scale);
        // Synthetic candlesticks are decorative and independent of the report data.
        const draw = (x, y, count, spacing, amplitude, phase) => {
            let previous = y;
            for (let index = 0; index < count; index++) {
                const column = x + index * spacing;
                const displacement = Math.sin(index * .55 + phase) * 15 + Math.cos(index * .21) * 22;
                const next = y + (displacement - index * .8) * amplitude;
                const top = Math.min(previous, next);
                const bottom = Math.max(previous, next);
                context.strokeStyle = context.fillStyle = next > previous ? "#a55d49" : "#286f65";
                context.globalAlpha = .085 + .12 * Math.sin(Math.PI * index / count);
                context.beginPath();
                context.moveTo(column, top - (3 + index % 4) * amplitude);
                context.lineTo(column, bottom + (4 + index % 3) * amplitude);
                context.stroke();
                context.fillRect(column - 2, top, 4, Math.max(bottom - top, 1));
                previous = next;
            }
        };
        const spacing = Math.max(6, width * .012);
        draw(width * .64, 102, 33, spacing, 1.35, 1.6);
        draw(-12, Math.min(240, height * .28), 25, spacing, 1.15, 2.7);
        draw(-12, height * .74, 40, spacing, 1.65, .1);
        draw(width * .78, height * .61, 26, spacing, 1.35, 3.3);
    }).observe(canvas);
    return canvas;
}

function sidebar(data, state) {
    const active = data.selection;
    const running = sessionRunning(data);
    return node("aside", {class: "session-sidebar", "aria-label": "Session history", "data-expanded": String(state.sessions)},
        node("div", {class: "sidebar-bar"}, node("h2", {}, "Research sessions"),
            button(state.sessions ? "Hide sessions" : "Show sessions", "sessions", null, {
                class: "secondary mobile-sessions", "aria-controls": "session-menu",
                "aria-expanded": String(state.sessions)})),
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
                                "aria-haspopup": "dialog", disabled: running || Boolean(state.pending),
                                title: running ? "Wait for the active investigation to finish" : "Delete session"}));
                })),
            disclosure(state, questionsDisclosure, node("span", {class: "eyebrow"}, "Session questions"), [
                data.turns.length === 0 && node("p", {class: "session-empty"}, "Investigations appear here."),
                node("nav", {class: "session-turns", "aria-label": "Session questions"},
                    [...data.turns].reverse().map(item => button(item.question, "turn", item.id,
                        {"aria-current": String(active.turn_id === item.id)},
                        node("span", {}, item.status + (item.mode === "replay" ? " · Replay" : ""),
                            item.created_at && node("time", {datetime: item.created_at,
                                title: cutoffTime.format(new Date(item.created_at))},
                                shortTime.format(new Date(item.created_at)))))))
            ], "disclosure session-questions"),
            node("p", {class: "storage-note"}, "Saved on this computer")));
}

function sessionRunning(data) {
    return data.turns.some(item => ["Queued", "Running"].includes(item.status));
}

function deleteDialog(data, state) {
    const session = data.sessions.find(item => item.id === state.deleteSession);
    if (!session || session.id !== data.selection.session_id) return null;
    const cancel = button("Cancel", "cancel_delete", session.id, {class: "secondary", autofocus: true});
    const title = session.title === "New session" ? "Untitled session" : session.title;
    const dialog = node("dialog", {class: "delete-dialog", "aria-labelledby": "delete-heading",
        "aria-describedby": "delete-description"},
        node("h2", {id: "delete-heading"}, "Delete session?"),
        node("p", {id: "delete-description"}, "Delete “" + title + "” and all its investigations? This cannot be undone."),
        node("div", {class: "delete-actions"}, cancel,
            button("Delete permanently", "delete_session", session.id,
                {class: "secondary delete-session", disabled: sessionRunning(data) || Boolean(state.pending)})));
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        cancel.click();
    });
    return dialog;
}

function heading(eyebrow, title, description, status, divider = false) {
    return [
        node("div", {class: "panel-topline"}, node("p", {class: "eyebrow"}, eyebrow), status && badge(status)),
        node("h2", {id: "stage-title"}, title),
        divider && node("hr", {class: "conclusion-divider"}),
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

function claimList(findings, {showReasons = true, details, canJump, isTargeted} = {}) {
    return node("div", {class: "result-list"}, findings.map(item => {
        const label = node("strong", {}, item.label);
        const status = badge(item.verdict);
        const reason = showReasons && node("span", {class: "claim-reason"}, item.reason);
        if (canJump?.(item)) {
            return button("", "verify_claim", item.claim_id,
                {class: "result-claim claim-link", "aria-label": "Show verification for " + item.label},
                label, node("span", {class: "claim-link-status"}, status,
                    node("span", {"aria-hidden": "true"}, "→")), reason);
        }
        return node("div", {class: "result-claim" + (isTargeted?.(item) ? " is-targeted" : "")},
            label, status, reason, details?.(item));
    }));
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
        node("ol", {class: "plan-list plan-actions"}, event.data.output.actions.map((item, index) => {
            const result = completed?.data.lookups.find(entry => JSON.stringify(entry.action) === JSON.stringify(item));
            const company = turn.company_names?.[item.entity] || item.entity;
            const window = item.start && item.end
                ? planDate.format(new Date(item.start)) + " to " + planDate.format(new Date(item.end))
                : null;
            const target = [company, seriesNames[item.series] || item.series,
                item.periods.length > 0 && planList.format(item.periods.map(period)), window].filter(Boolean);
            const claims = item.claim_ids.map(id => labels[id]).filter(Boolean);
            const lookup = result?.retrieval === "cached" ? "Used saved evidence"
                : result ? (turn.mode === "replay" ? "Used replay data" : "Looked up for this run") : null;
            return node("li", {},
                node("h3", {}, planTitles[item.operation] || operationNames[item.operation]),
                target.length > 0 && node("p", {class: "plan-target"}, target.join(" · ")),
                node("p", {class: "plan-purpose"}, item.purpose),
                node("p", {class: "plan-source"}, sourceNames[item.source],
                    result?.reused && " · Reused evidence from this session",
                    lookup && !result?.error && (result.retrieval !== "cached" || !result.reused) && " · " + lookup),
                result?.error && node("p", {class: "plan-error"}, result.error),
                (claims.length > 0 || item.query) && disclosure(state, "plan-action-" + event.sequence + "-" + index,
                    "Lookup details", definitionList([
                        ["Question checked", claims.length > 0 && claims.join(" / ")],
                        ["Search terms", item.query],
                    ].filter(([, value]) => Boolean(value)))));
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
        return disclosure(state, namespace + "-" + key, group[0].title, [
            evidenceMetadata(turn, group[0]), group.map(evidenceRecord), link(group[0].url, "Open original source"),
        ]);
    }));
}

function evidenceMetadata(turn, record) {
    const reuse = (turn.report?.reused_sources || turn.events.filter(event => event.kind === "cache")
        .flatMap(event => event.data.sources)).includes(record.accession || record.url);
    return [
        reuse && node("p", {class: "reuse-mark source-reuse"}, "Reused from this session"),
        node("p", {}, [sourceNames[record.source], record.publisher, record.form, record.published, record.accession,
            record.vintage && "Vintage " + record.vintage].filter(Boolean).join(" · ")),
    ];
}

function evidenceRecord(item) {
    if (item.type !== "fact") return item.passages.map(text => node("p", {}, text));
    return node("p", {}, [item.metric_label + " · " + item.display_value, item.start + " to " + item.end,
        item.basis, item.scope, item.frequency, item.restatement, item.derivation,
        item.vintage && "Vintage " + item.vintage].filter(Boolean).join(" · "));
}

function citationIndex(records, ids) {
    const byRecord = new Map();
    const groups = new Map();
    for (const id of new Set(ids)) {
        const record = records[id];
        if (!record) continue;
        const filing = record.source === "sec" && record.accession;
        const key = filing ? "sec:" + filing : sourceUrl(record.url) || id;
        if (!groups.has(key)) {
            groups.set(key, {number: groups.size + 1, title: record.title, records: []});
        }
        const group = groups.get(key);
        group.records.push(record);
        byRecord.set(id, group);
    }
    return {byRecord, groups: [...groups.values()]};
}

function reportCitations(ids, citations) {
    const groups = new Set(ids.map(id => citations.byRecord.get(id)).filter(Boolean));
    return groups.size > 0 && node("span", {class: "report-citations"},
        [...groups].map(group => button("[" + group.number + "]", "citation", group.number, {
            class: "report-citation",
            "aria-label": "View source " + group.number + ", " + group.title,
            "aria-controls": "report-source-" + group.number,
        })));
}

function citedText(text, ids, citations) {
    const references = reportCitations(ids, citations);
    const ending = /\s(\S{1,24})$/.exec(text);
    if (!references || references.childElementCount > 2 || !ending) return [text, references];
    // Keep the citation with the last word when a paragraph wraps.
    return [text.slice(0, ending.index + 1), node("span", {class: "citation-tail"}, ending[1], references)];
}

function disclosureQuotes(items, records, markExcerpt, citations = null) {
    return node("ul", {class: "report-explanations"}, items.map(item => {
        const quote = item.quote.replace(/\s+/g, " ").trim();
        const isClipped = markExcerpt && !/[.!?…][”"']?$/.test(quote)
            && (records[item.evidence_id]?.passages || []).some(passage => {
                const text = passage.replace(/\s+/g, " ");
                const start = text.indexOf(quote);
                if (start < 0) return false;
                const tail = text.slice(start + quote.length).trimStart();
                return tail && !/^[.!?](?:\s|$)/.test(tail);
            });
        const text = "“" + item.quote + (isClipped ? "…" : "") + "”";
        return node("li", {}, node("p", {}, citations ? citedText(text, [item.evidence_id], citations) : text));
    }));
}

function reportView(turn) {
    const report = turn.report;
    const hasHighlights = Array.isArray(report.highlights);
    const calculations = hasHighlights
        ? [...report.highlights.map(item => item.calculation), ...report.comparison_calculations]
        : report.findings.flatMap(item => item.calculations);
    const records = Object.fromEntries(turn.evidence.map(item => [item.id, item]));
    const summaries = report.disclosure_summary;
    const synthesized = Array.isArray(summaries);
    const quotes = report.explanations || [];
    const showQuotes = !synthesized || report.show_disclosure_quotes;
    const quoteKeys = new Set(quotes.map(item => JSON.stringify([item.evidence_id, item.quote])));
    const timeline = synthesized ? report.timeline.filter(item =>
        item.role !== "explanation" || !quoteKeys.has(JSON.stringify([item.evidence_id, item.passage]))) : report.timeline;
    const highlights = hasHighlights ? report.highlights.filter(item => !item.in_answer) : calculations.map(calculation =>
        ({label: calculation.label, calculation}));
    const citations = citationIndex(records, [
        ...(hasHighlights ? report.answers.flatMap(item => item.evidence_ids) : report.evidence_ids),
        ...highlights.flatMap(item => item.calculation.evidence_ids),
        ...(summaries || []).flatMap(item => item.evidence_ids),
        ...quotes.map(item => item.evidence_id),
        ...report.evidence_ids,
        ...calculations.flatMap(item => item.evidence_ids),
        ...report.timeline.map(item => item.evidence_id),
    ]);
    return {calculations, citations, hasHighlights, highlights, quotes, records, showQuotes, summaries, synthesized, timeline};
}

function reportPanel(turn, view) {
    const report = turn.report;
    const {citations, hasHighlights, highlights, quotes, records, showQuotes, summaries, synthesized} = view;
    return [
        heading("Investigation report", "Conclusion",
            hasHighlights ? null : citedText(report.conclusion, report.evidence_ids, citations), report.overall, true),
        hasHighlights && report.answers.length > 0 && node("div", {class: "report-answers"},
            report.answers.map(item => node("div", {class: "report-answer", "data-verdict": item.verdict},
                node("p", {}, citedText(item.text, item.evidence_ids, citations))))),
        highlights.length > 0 && node("div", {class: "metrics" + (hasHighlights ? " report-highlights" : "")},
            highlights.map(item => node("div", {class: "metric"},
                node("span", {class: "metric-label"}, item.label),
                node("div", {class: "metric-value"}, node("strong", {}, item.calculation.display_value),
                    reportCitations(item.calculation.evidence_ids, citations)),
                item.period_end && node("p", {class: "metric-period"}, item.period && item.period + " ended ",
                    node("time", {datetime: item.period_end}, item.period_end))))),
        hasHighlights && quotes.length > 0 && block("What the company disclosed", [
            summaries?.length > 0 && node("ul", {class: "report-explanations"}, summaries.map(item => {
                const paragraphs = [item.text, ...(item.details || [])];
                return node("li", {}, paragraphs.map((text, index) => node("p", {},
                    index === paragraphs.length - 1 ? citedText(text, item.evidence_ids, citations) : text)));
            })),
            showQuotes && disclosureQuotes(quotes, records, synthesized, citations),
            !showQuotes && summaries.length === 0 && node("p", {},
                "The verified excerpts are under Supporting details."),
        ]),
        hasHighlights && report.explanation_note && node("p", {class: "report-note"}, report.explanation_note),
        report.carried.length > 0 && node("p", {class: "lede"}, "Carried forward · " + report.carried.join(", ")),
        claimList(report.findings, {showReasons: !hasHighlights, canJump: item => verificationEvent(turn, item.claim_id)}),
    ];
}

function reportSource(turn, state, view, group) {
    const {number, records: items, title} = group;
    const first = items[0];
    const origin = items.find(item => item.type !== "fact" && sourceUrl(item.url))
        || items.find(item => sourceUrl(item.url));
    const originalUrl = sourceUrl(origin?.url);
    const related = new Map();
    for (const item of items) {
        const url = sourceUrl(item.url);
        if (url && url !== originalUrl && !related.has(url)) related.set(url, item.title);
    }
    const id = "report-source-" + number;
    const quotes = view.quotes.filter(item => view.citations.byRecord.get(item.evidence_id) === group);
    const details = disclosure(state, id, [
        node("span", {class: "source-number", "aria-hidden": "true"}, "[" + number + "]"),
        node("span", {}, title, node("span", {class: "source-meta"}, [
            originalUrl && new URL(originalUrl).hostname, first.published,
        ].filter(Boolean).join(" · "))),
    ], [
        originalUrl && link(originalUrl, "Open original source"),
        [...related].map(([url, title]) => node("p", {}, link(url, title))),
        disclosure(state, id + "-details", "Source details", [
            evidenceMetadata(turn, first),
            items.filter(item => item.type === "fact").map(evidenceRecord),
        ]),
        quotes.length > 0 && disclosure(state, id + "-excerpts", "Source excerpts",
            disclosureQuotes(quotes, view.records, true)),
    ], "disclosure report-source");
    if (state.citation === id) details.classList.add("is-cited");
    return details;
}

function calculationBody(item) {
    return [
        node("p", {}, item.formula),
        node("table", {class: "data-table"}, node("thead", {}, node("tr", {},
            node("th", {scope: "col"}, "Operand"), node("th", {scope: "col"}, "Value"))),
            node("tbody", {}, item.operands.map(operand => node("tr", {},
                node("td", {}, operand.label), node("td", {}, operand.display_value))))),
    ];
}

function supportingPanel(turn, state, view) {
    const {calculations, citations, records, timeline} = view;
    const report = turn.report;
    const count = citations.groups.length;
    return node("section", {class: "panel panel-side supporting-panel", "aria-labelledby": "support-heading"},
        node("h3", {id: "support-heading"}, "Supporting details"),
        count > 0 && disclosure(state, sourcesDisclosure,
            ["Sources", node("span", {class: "source-count"}, String(count))],
            citations.groups.map(group => reportSource(turn, state, view, group)),
            "disclosure audit-disclosure source-disclosure", "sources"),
        calculations.length > 0 && disclosure(state, "support-calculations", "Calculations", node("div", {class: "sources"},
                calculations.map((item, index) => disclosure(state, "calculation-" + index,
                    item.label + " · " + item.display_value, calculationBody(item)))), "disclosure audit-disclosure"),
        timeline.length > 0 && disclosure(state, "support-timeline", "Evidence timeline", node("ol", {class: "timeline"},
                timeline.map(item => node("li", {}, node("time", {datetime: item.date}, item.date),
                    link(records[item.evidence_id]?.url, item.title), node("p", {}, item.passage)))),
            "disclosure audit-disclosure"),
        disclosure(state, "support-limitations", "Limitations", node("div", {},
                report.limitations.length ? report.limitations.map(item => node("p", {}, item))
                    : node("p", {}, "Claims were evaluated against the evidence available at the saved cutoff."),
                node("p", {}, "Evidence cutoff · ", cutoffLabel(report.cutoff))), "disclosure audit-disclosure"));
}

function verificationLinks(ids, records) {
    const seen = new Set();
    const sources = [];
    for (const id of ids) {
        const record = records[id];
        if (!record) continue;
        const key = sourceUrl(record.url) || id;
        if (seen.has(key)) continue;
        seen.add(key);
        sources.push(node("li", {}, link(record.url, record.title),
            record.published && node("time", {datetime: record.published}, " · " + record.published)));
    }
    return sources.length > 0 && node("ul", {class: "verify-sources"}, sources);
}

function verificationEvent(turn, claimId) {
    return turn?.events.findLast(event => event.stage === "Verify" && event.kind === "stage"
        && event.outcome === "complete" && event.data.findings?.some(item => item.claim_id === claimId));
}

function verificationAuditId(event, claimId) {
    return "verify-" + event.sequence + "-" + claimId;
}

function verificationPanel(turn, state, event) {
    const records = Object.fromEntries(turn.evidence.map(item => [item.id, item]));
    const details = item => {
        const checks = item.checks || [];
        const cited = new Set(checks.flatMap(check => check.evidence_ids));
        const sources = verificationLinks(item.evidence_ids.filter(id => !cited.has(id)), records);
        return disclosure(state, verificationAuditId(event, item.claim_id), "How this was verified", [
            checks.length > 0
                ? node("ol", {class: "verify-checks"}, checks.map(check => node("li", {},
                    node("strong", {}, check.label), node("p", {}, check.detail),
                    check.excerpt && node("blockquote", {}, check.excerpt),
                    verificationLinks(check.evidence_ids, records))))
                : node("p", {}, "This saved run recorded the verdict and available supporting evidence."),
            item.calculations.length > 0 && block("Calculations", item.calculations.map(calculation =>
                node("div", {class: "verify-calculation"},
                    node("strong", {}, calculation.label + " · ", node("span", {}, calculation.display_value)),
                    calculationBody(calculation)))),
            sources && block("Evidence and context", sources),
        ], "disclosure verify-audit");
    };
    return [
        heading("Investigation workflow", event.pass_no ? "Follow-up verification" : "Evidence verification"),
        claimList(event.data.findings, {
            details, isTargeted: item => state.verifyTarget === verificationAuditId(event, item.claim_id),
        }),
        event.data.gaps.length > 0 && block(event.pass_no ? "Remaining evidence gaps" : "Evidence needed",
            node("ul", {}, event.data.gaps.map(gap => node("li", {}, gap.reason)))),
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
    if (running) return [
        heading("Investigation workflow", stage, null, turn.status),
        node("div", {class: "stage-progress"},
            node("span", {class: "stage-pulse", "aria-hidden": "true"}),
            node("span", {}, "Stage in progress"),
            node("span", {class: "stage-clock", role: "timer", "data-live-stage-clock": "pass"})),
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
            definitionList([["Evidence cutoff", cutoffLabel(event.data.cutoff)], ["Carried forward", parsed.carried.join(", ")]]),
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
    if (stage === "Verify") return verificationPanel(turn, state, event);
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
        turn && node("p", {class: "side-question-label"}, "Question asked"),
        node("p", {class: "side-question"}, turn ? turn.question : "Select a question to see its run details."),
        turn && disclosure(state, activityDisclosure, "Run activity", definitionList([
            ["Sources used", turn.report?.sources.join(", ") || "—"],
            [turn.mode === "replay" ? "Fixture lookups" : "New provider lookups", turn.counts.lookups],
            ["Reused sources", turn.counts.reused], ["Cache hits", turn.counts.cache_hits],
            ["Evidence follow-ups", turn.counts.followups], ["Transport retries", turn.counts.retries],
            ["Model calls", turn.counts.models],
            ["Active time", turn.active_seconds === undefined ? undefined : turn.active_seconds.toFixed(2) + " s"],
        ], "side-facts"), "disclosure run-activity"),
        turn && history(turn, state));
}

function finishedStages(events, status) {
    const latest = new Map(events.filter(event => event.kind === "stage").map(event => [event.stage, event]));
    const finished = new Set();
    // Parse can repeat until planning starts. Report is beyond the verification follow-up loop.
    if (latest.has("Plan") && latest.get("Parse")?.outcome === "complete") finished.add("Parse");
    if (latest.has("Report")) {
        for (const name of ["Plan", "Retrieve", "Verify"]) {
            if (latest.get(name)?.outcome === "complete") finished.add(name);
        }
    }
    if (status === "Complete" && latest.get("Report")?.outcome === "complete") finished.add("Report");
    return finished;
}

function stageDurations(events) {
    const starts = new Map();
    const totals = new Map();
    for (const event of events) {
        if (event.kind !== "stage") continue;
        const key = event.stage + "-" + event.pass_no;
        if (event.outcome === "started") {
            starts.set(key, Date.parse(event.timestamp));
        } else if (event.outcome === "complete" || event.outcome === "error") {
            const saved = event.data?.elapsed_ms;
            const elapsed = Number.isFinite(saved) ? saved : Date.parse(event.timestamp) - starts.get(key);
            starts.delete(key);
            if (Number.isFinite(elapsed) && elapsed >= 0) {
                totals.set(event.stage, (totals.get(event.stage) || 0) + elapsed);
            }
        }
    }
    return totals;
}

function stageTime(milliseconds) {
    return (milliseconds / 1000).toFixed(2) + " s";
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
    const finished = finishedStages(events, status);
    const durations = stageDurations(events);
    const active = status === "Running" && newest?.outcome === "started" ? newest : null;
    const running = active !== null && event === active;
    const report = turn?.report && stage === "Report" && (!event || event.kind === "stage");
    const view = report ? reportView(turn) : null;
    const dialog = deleteDialog(data, state);
    state.marketTexture ??= marketTexture();
    root.replaceChildren(state.marketTexture, header(data, state), node("main", {class: "shell workspace"},
        sidebar(data, state),
        node("div", {class: "workflow-main"},
            node("section", {class: "workflow", "aria-labelledby": "workflow-heading"},
                node("div", {class: "section-heading"},
                    node("h2", {id: "workflow-heading"}, "Investigation workflow"),
                    node("div", {role: "status"}, badge(status))),
                node("nav", {class: "steps", "aria-label": "Investigation steps"},
                    stages.map((item, index) => {
                        const live = active?.stage === item;
                        const elapsed = durations.get(item);
                        const description = [live ? "Running" : finished.has(item) ? "Finished" : null,
                            !live && elapsed !== undefined && stageTime(elapsed) + " elapsed"].filter(Boolean).join(". ");
                        return button("", "stage", item,
                            {class: "step " + (newest?.stage === item ? "current" : finished.has(item) ? "done" : "pending"),
                            "aria-pressed": String(stage === item), "aria-label": item,
                            "aria-description": description || null},
                            node("span", {class: "step-number", "aria-hidden": "true"}, finished.has(item) ? "✓" : String(index + 1)),
                            node("span", {class: "step-label"}, item,
                                (live || elapsed !== undefined) && node("span", {class: "step-time", "aria-hidden": "true",
                                    "data-live-stage-clock": live ? "total" : null}, stageTime(elapsed || 0))));
                    }))),
            node("div", {class: "layout"},
                node("section", {class: "panel panel-main", "aria-labelledby": "stage-title", tabindex: -1},
                    view ? reportPanel(turn, view) : stagePanel(turn, state, stage, event, running)),
                node("div", {class: "detail-rail"}, runPanel(turn, state),
                    view && supportingPanel(turn, state, view))))));
    if (dialog) {
        root.append(dialog);
        dialog.showModal();
    }
    const clocks = [...root.querySelectorAll("[data-live-stage-clock]")];
    if (active && clocks.length) {
        const started = Date.parse(active.timestamp);
        const previous = durations.get(active.stage) || 0;
        const tick = () => {
            if (!clocks[0].isConnected) {
                clearInterval(state.stageClock);
                return;
            }
            const elapsed = Number.isFinite(started) ? Math.max(0, Date.now() - started) : 0;
            for (const clock of clocks) {
                const total = clock.dataset.liveStageClock === "total";
                const text = stageTime(elapsed + (total ? previous : 0)) + (total ? "" : " elapsed");
                if (clock.textContent !== text) clock.textContent = text;
            }
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
        root.researchState = {draft: "", sessions: false, open: new Set(defaultOpenDisclosures), answers: {}};
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
            state.open = new Set(defaultOpenDisclosures.filter(id => id !== questionsDisclosure || state.open.has(id)));
            state.citation = null;
            state.verifyTarget = null;
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
            if (!state.deleteSession) root.querySelector('[data-action="confirm_delete"]')?.focus({preventScroll: true});
        } else if (action === "citation") {
            const source = root.querySelector("#report-source-" + CSS.escape(value));
            if (!source) return;
            const group = source.closest("#" + sourcesDisclosure);
            if (group) {
                state.open.add(group.id);
                group.open = true;
            }
            root.querySelector(".report-source.is-cited")?.classList.remove("is-cited");
            state.citation = source.id;
            state.open.add(source.id);
            source.open = true;
            source.classList.add("is-cited");
            source.querySelector("summary").focus({preventScroll: true});
            source.scrollIntoView({block: "nearest"});
        } else if (action === "verify_claim") {
            const check = verificationEvent(data.selected, value);
            if (!check) return;
            state.stage = "Verify";
            state.event = check.sequence;
            state.verifyTarget = verificationAuditId(check, value);
            state.open.add(state.verifyTarget);
            render(root, data, state);
            const audit = root.querySelector("#" + CSS.escape(state.verifyTarget));
            audit?.querySelector("summary").focus({preventScroll: true});
            audit?.closest(".result-claim").scrollIntoView({block: "nearest"});
        } else if (action === "stage" || action === "history") {
            state.verifyTarget = null;
            state.stage = action === "stage" ? value : null;
            state.event = action === "history" ? Number(value) : null;
            render(root, data, state);
        } else {
            if (action === "new") state.draft = "";
            if (action === "delete_session") {
                if (state.deleteSession !== value || data.selection.session_id !== value || sessionRunning(data)) return;
                state.deleteSession = null;
            }
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
