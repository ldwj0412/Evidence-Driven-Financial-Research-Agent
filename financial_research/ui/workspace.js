const stages = ["Parse", "Plan", "Retrieve", "Verify", "Report"];
const sourcesDisclosure = "support-sources";
const activityDisclosure = "run-activity";
const questionsDisclosure = "session-questions";
const defaultOpenDisclosures = [sourcesDisclosure, activityDisclosure, questionsDisclosure];
const sourceNames = {sec: "SEC EDGAR", fred: "FRED", news: "News search"};
const missingSourceSetup = {
    "Set SEC_USER_AGENT or sec.user_agent in config.local.toml to your project name and contact email.": "SEC EDGAR",
    "Set FRED_API_KEY or fred.api_key in config.local.toml to retrieve macro observations.": "FRED",
    "Set TAVILY_API_KEY or tavily.api_key in config.local.toml to retrieve news coverage.": "Tavily",
};
const settingsSections = {
    model: {
        label: "Model settings", form: "model-settings-form", style: "model-settings",
        settings: "modelSettings", feedback: "modelSettingsFeedback",
        save: "save_model_settings", reset: "reset_model_settings",
    },
    data: {
        label: "Data source settings", form: "data-source-settings-form", style: "data-source-settings",
        settings: "dataSourceSettings", feedback: "dataSourceSettingsFeedback",
        save: "save_data_source_settings", reset: "reset_data_source_settings",
    },
};
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

function badge(t, label) {
    const color = ["Contradicted", "Failed", "Interrupted"].includes(label) ? "negative"
        : ["Unresolved", "Partially supported", "Waiting for clarification"].includes(label) ? "warning"
        : ["Supported", "Complete", "Running"].includes(label) ? "" : "neutral";
    return node("span", {class: "status " + color}, t(label));
}

function period(value) {
    if (!value) return "";
    return "FY" + value.year + (value.quarter ? " Q" + value.quarter : "");
}

function cutoffLabel(locale, value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : node("time", {datetime: value, title: value},
        locale.cutoffTime.format(date));
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

function dataSourceSetupButton(state, focusKey) {
    return button(state.locale.text("Configure data sources"), "open_connection_settings", "data", {
        class: "secondary data-source-setup", disabled: Boolean(state.pending), "aria-haspopup": "dialog",
        "data-focus": focusKey,
    });
}

function header(data, state) {
    const {code, text: t} = state.locale;
    const turn = data.selected;
    const suggestion = turn?.mode === data.mode ? turn?.report?.suggestion : null;
    const mode = node("select", {id: "research-mode", "aria-label": t("Research mode"),
        disabled: Boolean(state.pending)},
        node("option", {value: "live", selected: data.mode === "live"}, t("Live")),
        node("option", {value: "replay", selected: data.mode === "replay"}, t("Fixture replay")));
    const language = node("select", {id: "interface-language", "aria-label": t("Interface language"),
        title: t("Interface language"), "data-focus": "interface-language"},
        node("option", {value: "en", lang: "en", selected: code === "en"}, "English"),
        node("option", {value: "zh", lang: "zh-CN", selected: code === "zh"}, "简体中文"));
    const settings = button(t("Connection settings"), "open_connection_settings", "model", {
        class: "connection-settings-trigger", "aria-haspopup": "dialog", disabled: Boolean(state.pending),
    });
    const feedback = Object.values(settingsSections).map(({feedback, style}) => {
        const notice = data[feedback];
        return notice && !notice.error && node("p", {class: "connection-feedback " + style + "-feedback", role: "status"},
            localizeDiagnostic(notice.message, t));
    });
    const form = node("form", {id: "research-form", class: "prompt-form"},
        node("label", {for: "research-question", class: "visually-hidden"}, t("Research question")),
        node("span", {class: "prompt-icon", "aria-hidden": "true"}),
        node("textarea", {id: "research-question", class: "question", required: true, maxlength: 750, rows: 1,
            placeholder: t("Ask about a financial claim or company event"), "data-focus": "question"}, state.draft),
        node("button", {type: "submit", class: "primary", "aria-label": t("Run investigation"), disabled: Boolean(state.pending)},
            node("span", {}, t("Run"), node("span", {class: "run-long"}, t(" investigation"))),
            node("span", {"aria-hidden": "true"}, "→")));
    return node("header", {class: "masthead"}, node("div", {class: "shell"},
        node("div", {class: "brandbar"},
            node("div", {class: "brand"}, node("span", {class: "mark", "aria-hidden": "true"},
                node("i"), node("i"), node("i")), t("Financial Research Agent")),
            node("div", {class: "mode-control"}, settings, language, mode)),
        feedback,
        node("div", {class: "hero"}, node("h1", {}, t("Investigate a financial claim")),
            node("p", {class: "intro"}, t("Check filings, compare figures, and see the evidence behind each answer.")),
            form, data.error && node("p", {class: "prompt-error", role: "alert"}, localizeDiagnostic(data.error, t)),
            node("div", {class: "examples", "aria-label": t("Example questions")}, node("span", {}, t("Try an example")),
                data.examples.map(item => button(t(item.label), "fill", item.questions[code], {class: "example"}))),
            suggestion && node("div", {class: "follow-up"}, node("span", {}, t("Continue this report")),
                button(suggestion, "fill", suggestion, {class: "example"})),
            data.mode === "live" && data.messages.length > 0 && disclosure(
                state, "provider-setup", t("Provider setup"), [node("ul", {},
                    data.messages.map(message => node("li", {}, localizeDiagnostic(message, t)))),
                    dataSourceSetupButton(state, "provider-setup-data-sources")],
                "provider-setup", "setup-body"))));
}

function modelField(label, control, ...notes) {
    return node("div", {class: "model-field"}, node("label", {for: control.id}, label), control, notes);
}

function modelProviderFields(t, settings, provider) {
    const values = settings.providers[provider];
    const compatible = provider === "openai_compatible";
    return [
        compatible && modelField(t("Base URL"), node("input", {
            id: "model-base-url", name: "base_url", type: "url", required: true, value: values.base_url,
            spellcheck: "false", placeholder: "https://api.example.com/v1", "aria-describedby": "model-url-help",
        }), node("p", {id: "model-url-help"},
            t("Include the API version prefix if needed (for example, /v1). The app appends /chat/completions."))),
        modelField(t("Model ID"), node("input", {
            id: "model-name", name: "model", type: "text", required: true, value: values.model,
            spellcheck: "false", "aria-describedby": compatible ? "model-name-help" : null,
        }), compatible && node("p", {id: "model-name-help"},
            t("The endpoint and model must support Chat Completions with strict JSON schema responses."))),
        modelField(t("API key"), node("input", {
            id: "model-api-key", name: "api_key", type: "password", autocomplete: "off",
            "aria-describedby": "model-key-help", "data-sensitive": true,
        }), node("p", {id: "model-key-help"}, [
            t(values.has_key ? "A key is configured for the saved endpoint." : "No key is configured for the saved endpoint."),
            t("Leave blank to keep the configured key for this provider and endpoint."),
            compatible && t("Loopback endpoints can omit the API key."),
        ].filter(Boolean).join(" "))),
        compatible && modelField(t("Token limit parameter"), node("select", {
            id: "model-token-limit-parameter", name: "token_limit_parameter",
        }, ["max_completion_tokens", "max_tokens"].map(value => node("option", {
            value, selected: value === values.token_limit_parameter,
        }, value)))),
    ].filter(Boolean);
}

function modelSettingsFields(t, settings) {
    const provider = node("select", {
        id: "model-provider", name: "provider", "aria-describedby": "model-provider-help",
    }, node("option", {value: "openrouter", selected: settings.provider === "openrouter"}, "OpenRouter"),
    node("option", {value: "openai_compatible", selected: settings.provider === "openai_compatible"}, t("OpenAI-compatible")));
    const fields = node("div", {class: "model-fields"}, modelProviderFields(t, settings, provider.value));
    provider.addEventListener("change", () => {
        clearSensitiveInputs(fields);
        fields.replaceChildren(...modelProviderFields(t, settings, provider.value));
    });
    return [modelField(t("Model provider"), provider, node("p", {id: "model-provider-help"},
        t("Choose the provider that issued your API key."))), fields];
}

function sourceSettingsGroup(t, name, title, description, configured, ...fields) {
    return node("section", {class: "data-source-group", "data-provider": name, "aria-labelledby": name + "-settings-heading"},
        node("div", {class: "data-source-heading"},
            node("h3", {id: name + "-settings-heading"}, title),
            node("span", {class: "data-source-status status" + (configured ? "" : " neutral")},
                t(configured ? "Configured" : "Not configured"))),
        node("p", {class: "data-source-description"}, t(description)), fields);
}

function dataSourceFields(t, settings) {
    const {sec, fred, tavily} = settings.providers;
    const securedInput = {autocomplete: "off", spellcheck: "false", "data-sensitive": true};
    return [
        node("p", {class: "data-source-description"}, t("Leave fields blank to keep existing values.")),
        sourceSettingsGroup(t, "sec", "SEC EDGAR",
            "Company filings and reported financials. SEC requires a project name and contact email. No API key is required.",
            sec.configured, node("div", {class: "sec-contact-fields"},
                modelField(t("Project name (English)"), node("input", {
                    ...securedInput, id: "sec-project", name: "sec_project", type: "text",
                })),
                modelField(t("Contact email"), node("input", {
                    ...securedInput, id: "sec-email", name: "sec_email", type: "text", inputmode: "email",
                })))),
        sourceSettingsGroup(t, "fred", "FRED", "Macro data such as interest rates and inflation.", fred.configured,
            modelField(t("FRED API key"), node("input", {
                ...securedInput, id: "fred-api-key", name: "fred_api_key", type: "password",
            }))),
        sourceSettingsGroup(t, "tavily", "Tavily", "News search for company events and context.", tavily.configured,
            modelField(t("Tavily API key"), node("input", {
                ...securedInput, id: "tavily-api-key", name: "tavily_api_key", type: "password",
            }))),
    ];
}

function connectionSettingsPanel(t, data, key) {
    const section = settingsSections[key];
    const settings = data[section.settings];
    const fields = key === "model" ? modelSettingsFields(t, settings) : dataSourceFields(t, settings);
    return node("div", {id: `connection-${key}-panel`, role: "tabpanel", "aria-labelledby": `connection-${key}-tab`},
        node("p", {class: `connection-settings-error ${section.style}-error`, role: "alert", tabindex: -1, hidden: true}),
        node("form", {id: section.form, method: "post", autocomplete: "off"}, node("fieldset", {},
            fields, node("div", {class: "connection-settings-actions"},
                button(t("Restore defaults"), section.reset, null,
                    {class: "secondary connection-reset", disabled: !settings.overridden}),
                button(t("Cancel"), "close_connection_settings", null, {class: "secondary"}),
                node("button", {type: "submit", class: "primary"}, t("Save settings"))))));
}

function connectionSettingsDialog(data, state, initial) {
    const t = state.locale.text;
    const sections = Object.keys(settingsSections);
    const panels = sections.map(key => connectionSettingsPanel(t, data, key));
    const tabs = sections.map(key => node("button", {
        id: `connection-${key}-tab`, type: "button", role: "tab", "aria-controls": `connection-${key}-panel`,
    }, t(settingsSections[key].label)));
    function select(index) {
        tabs.forEach((tab, i) => {
            const selected = i === index;
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (!selected && !panels[i].hidden) clearSensitiveInputs(panels[i]);
            panels[i].hidden = !selected;
        });
    }
    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => select(index));
        tab.addEventListener("keydown", event => {
            const target = {
                ArrowLeft: (index + tabs.length - 1) % tabs.length,
                ArrowRight: (index + 1) % tabs.length,
                Home: 0, End: tabs.length - 1,
            }[event.key];
            if (target === undefined || state.pending) return;
            event.preventDefault();
            select(target);
            tabs[target].focus();
        });
    });
    select(sections.indexOf(initial));
    const dialog = node("dialog", {id: "connection-settings-dialog", class: "connection-settings-dialog",
        "aria-labelledby": "connection-settings-heading", "aria-describedby": "connection-settings-scope"},
        node("h2", {id: "connection-settings-heading"}, t("Connection settings")),
        node("p", {id: "connection-settings-scope"},
            t("Changes apply to new or resumed investigations in this app session. Restore defaults to use the file and environment settings.")),
        node("div", {class: "connection-tabs", role: "tablist", "aria-label": t("Connection settings")}, tabs), panels);
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        dialog.querySelector('[role="tabpanel"]:not([hidden]) [data-action="close_connection_settings"]').click();
    });
    return dialog;
}

function clearSensitiveInputs(container) {
    for (const input of container.querySelectorAll("[data-sensitive]")) input.value = "";
}

function closeConnectionSettings(root, state) {
    const dialog = root.querySelector("#connection-settings-dialog");
    if (!dialog) return;
    clearSensitiveInputs(dialog);
    dialog.close();
    dialog.remove();
    const opener = root.querySelector(`[data-focus="${CSS.escape(state.connectionOpener)}"]`)
        || root.querySelector(".connection-settings-trigger");
    opener?.focus({preventScroll: true});
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
    const {text: t, shortTime, cutoffTime} = state.locale;
    const active = data.selection;
    const running = sessionRunning(data);
    return node("aside", {class: "session-sidebar", "aria-label": t("Session history"), "data-expanded": String(state.sessions)},
        node("div", {class: "sidebar-bar"}, node("h2", {}, t("Research sessions")),
            button(t(state.sessions ? "Hide sessions" : "Show sessions"), "sessions", null, {
                class: "secondary mobile-sessions", "aria-controls": "session-menu",
                "aria-expanded": String(state.sessions)})),
        node("div", {class: "sidebar-menu", id: "session-menu"},
            button("", "new", null, {class: "secondary new-session", "aria-label": t("New session")},
                node("span", {"aria-hidden": "true"}, "+"), t("New session")),
            node("nav", {class: "session-list", "aria-label": t("Research sessions")},
                data.sessions.map(item => {
                    const selected = active.session_id === item.id;
                    return node("div", {class: "session-card"},
                        button(item.title === "New session" ? t("Untitled session") : item.title, "session", item.id,
                            {class: "session-title", "aria-current": String(selected)}),
                        selected && button("×", "confirm_delete", item.id,
                            {class: "session-delete-icon", "aria-label": t("Delete session"),
                                "aria-haspopup": "dialog", disabled: running || Boolean(state.pending),
                                title: t(running ? "Wait for the active investigation to finish" : "Delete session")}));
                })),
            disclosure(state, questionsDisclosure, node("span", {class: "eyebrow"}, t("Session questions")), [
                data.turns.length === 0 && node("p", {class: "session-empty"}, t("Investigations appear here.")),
                node("nav", {class: "session-turns", "aria-label": t("Session questions")},
                    [...data.turns].reverse().map(item => button(item.question, "turn", item.id,
                        {"aria-current": String(active.turn_id === item.id)},
                        node("span", {class: "session-turn-meta"},
                            item.is_follow_up ? node("span", {class: "follow-up-label"}, t("Follow-up")) : null,
                            t(item.status) + (item.mode === "replay" ? " · " + t("Replay") : ""),
                            item.created_at && node("time", {datetime: item.created_at,
                                title: cutoffTime.format(new Date(item.created_at))},
                                shortTime.format(new Date(item.created_at)))))))
            ], "disclosure session-questions"),
            node("p", {class: "storage-note"}, t("Saved on this computer"))));
}

function sessionRunning(data) {
    return data.turns.some(item => ["Queued", "Running"].includes(item.status));
}

function deleteDialog(data, state) {
    const t = state.locale.text;
    const session = data.sessions.find(item => item.id === state.deleteSession);
    if (!session || session.id !== data.selection.session_id) return null;
    const cancel = button(t("Cancel"), "cancel_delete", session.id, {class: "secondary", autofocus: true});
    const title = session.title === "New session" ? t("Untitled session") : session.title;
    const dialog = node("dialog", {class: "delete-dialog", "aria-labelledby": "delete-heading",
        "aria-describedby": "delete-description"},
        node("h2", {id: "delete-heading"}, t("Delete session?")),
        node("p", {id: "delete-description"}, t("Delete “{title}” and all its investigations? This cannot be undone.", {title})),
        node("div", {class: "delete-actions"}, cancel,
            button(t("Delete permanently"), "delete_session", session.id,
                {class: "secondary delete-session", disabled: sessionRunning(data) || Boolean(state.pending)})));
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        cancel.click();
    });
    return dialog;
}

function heading(t, eyebrow, title, description, status, divider = false) {
    return [
        node("div", {class: "panel-topline"}, node("p", {class: "eyebrow"},
            typeof eyebrow === "string" ? t(eyebrow) : eyebrow), status && badge(t, status)),
        node("h2", {id: "stage-title"}, t(title)),
        divider && node("hr", {class: "conclusion-divider"}),
        description && node("p", {class: "lede"}, description),
    ];
}

function clarification(turn, state) {
    const t = state.locale.text;
    const request = turn.clarification;
    return [
        heading(t, "Parse", "Clarify the claim",
            node("span", {lang: turn.response_language}, request.message), "Waiting for clarification"),
        node("form", {id: "clarify-form", class: "clarify"},
            request.fields.map(field => {
                const id = "clarify-" + field.name;
                const attributes = {id, name: field.name, required: true, "data-focus": id};
                const control = field.control === "select" ? node("select", attributes,
                    node("option", {value: ""}, t("Choose {label}", {label: field.label.toLowerCase()})),
                    field.options.map(item => node("option", {value: item.value,
                        selected: state.answers[field.name] === item.value}, item.label)))
                    : node("input", {...attributes, type: "text", maxlength: 160, value: state.answers[field.name] || ""});
                return node("div", {class: "clarify-field"}, node("label", {for: id}, field.label), control);
            }),
            node("button", {type: "submit", class: "primary", disabled: Boolean(state.pending)}, t("Resume investigation"))),
    ];
}

function claimList(t, findings, {showReasons = true, details, canJump, isTargeted} = {}) {
    return node("div", {class: "result-list"}, findings.map(item => {
        const label = node("strong", {}, item.label);
        const status = badge(t, item.verdict);
        const reason = showReasons && node("span", {class: "claim-reason"}, item.reason);
        if (canJump?.(item)) {
            return button("", "verify_claim", item.claim_id,
                {class: "result-claim claim-link", "aria-label": t("Show verification for {label}", {label: item.label})},
                label, node("span", {class: "claim-link-status"}, status,
                    node("span", {"aria-hidden": "true"}, "→")), reason);
        }
        return node("div", {class: "result-claim" + (isTargeted?.(item) ? " is-targeted" : "")},
            label, status, reason, details?.(item));
    }));
}

function actionLabel(t, item) {
    return [t(operationNames[item.operation]), item.entity, item.series, item.periods.map(period).join(", "),
        item.start && item.end ? t("{start} to {end}", {start: item.start, end: item.end}) : ""].filter(Boolean).join(" · ");
}

function lookupError(state, message, className, focusKey) {
    const t = state.locale.text;
    const provider = Object.hasOwn(missingSourceSetup, message) ? missingSourceSetup[message] : null;
    if (!provider) return node("p", {class: className}, message);
    return [node("p", {class: className}, t("{provider} was not configured when this lookup ran.", {provider})),
        dataSourceSetupButton(state, focusKey)];
}

function planPanel(turn, state, event) {
    const {text: t, planDate, planList} = state.locale;
    const passes = turn.events.filter(item => item.stage === "Plan" && item.kind === "stage" && item.outcome === "complete");
    const completed = turn.events.find(item => item.stage === "Retrieve" && item.pass_no === event.pass_no
        && item.kind === "stage" && item.outcome === "complete");
    const labels = Object.fromEntries((turn.parsed?.claims || []).map(claim => [claim.id, claim.label]));
    return [
        heading(t, "Investigation workflow", event.pass_no ? "Follow-up plan" : "Retrieval plan",
            event.pass_no ? event.data.gaps?.map(item => item.reason).join(" ") : null),
        passes.length > 1 && node("div", {class: "plan-toggle", role: "group", "aria-label": t("Planner passes")},
            passes.map(item => button(t(item.pass_no ? "Follow-up plan" : "Initial plan"), "history", item.sequence,
                {class: "secondary", "aria-pressed": String(item.sequence === event.sequence)}))),
        node("ol", {class: "plan-list plan-actions"}, event.data.output.actions.map((item, index) => {
            const result = completed?.data.lookups.find(entry => JSON.stringify(entry.action) === JSON.stringify(item));
            const company = turn.company_names?.[item.entity] || item.entity;
            const window = item.start && item.end
                ? t("{start} to {end}", {start: planDate.format(new Date(item.start)), end: planDate.format(new Date(item.end))})
                : null;
            const target = [company, seriesNames[item.series] ? t(seriesNames[item.series]) : item.series,
                item.periods.length > 0 && planList.format(item.periods.map(period)), window].filter(Boolean);
            const claims = item.claim_ids.map(id => labels[id]).filter(Boolean);
            const lookup = result?.retrieval === "cached" ? "Used saved evidence"
                : result ? (turn.mode === "replay" ? "Used replay data" : "Looked up for this run") : null;
            return node("li", {},
                node("h3", {}, t(planTitles[item.operation] || operationNames[item.operation])),
                target.length > 0 && node("p", {class: "plan-target"}, target.join(" · ")),
                node("p", {class: "plan-purpose"}, item.purpose),
                node("p", {class: "plan-source"}, t(sourceNames[item.source]),
                    result?.reused && " · " + t("Reused evidence from this session"),
                    lookup && !result?.error && (result.retrieval !== "cached" || !result.reused) && " · " + t(lookup)),
                result?.error && lookupError(state, result.error, "plan-error", `plan-source-settings-${index}`),
                (claims.length > 0 || item.query) && disclosure(state, "plan-action-" + event.sequence + "-" + index,
                    t("Lookup details"), definitionList([
                        [t("Question checked"), claims.length > 0 && claims.join(" / ")],
                        [t("Search terms"), item.query],
                    ].filter(([, value]) => Boolean(value)))));
        })),
    ];
}

function evidenceBlocks(turn, state, ids, namespace) {
    const t = state.locale.text;
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
            evidenceMetadata(t, turn, group[0]), group.map(item => evidenceRecord(t, item)),
            link(group[0].url, t("Open original source")),
        ]);
    }));
}

function evidenceMetadata(t, turn, record) {
    const reuse = (turn.report?.reused_sources || turn.events.filter(event => event.kind === "cache")
        .flatMap(event => event.data.sources)).includes(record.accession || record.url);
    return [
        reuse && node("p", {class: "reuse-mark source-reuse"}, t("Reused from this session")),
        node("p", {}, [t(sourceNames[record.source]), record.publisher, record.form, record.published, record.accession,
            record.vintage && t("Vintage {date}", {date: record.vintage})].filter(Boolean).join(" · ")),
    ];
}

function evidenceRecord(t, item) {
    if (item.type !== "fact") return item.passages.map(text => node("p", {}, text));
    return node("p", {}, [t(item.metric_label) + " · " + item.display_value,
        t("{start} to {end}", {start: item.start, end: item.end}),
        item.basis, item.scope, item.frequency, item.restatement, item.derivation,
        item.vintage && t("Vintage {date}", {date: item.vintage})].filter(Boolean).join(" · "));
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

function reportCitations(t, ids, citations) {
    const groups = new Set(ids.map(id => citations.byRecord.get(id)).filter(Boolean));
    return groups.size > 0 && node("span", {class: "report-citations"},
        [...groups].map(group => button("[" + group.number + "]", "citation", group.number, {
            class: "report-citation",
            "aria-label": t("View source {number}, {title}", {number: group.number, title: group.title}),
            "aria-controls": "report-source-" + group.number,
        })));
}

function citedText(t, text, ids, citations) {
    const references = reportCitations(t, ids, citations);
    const ending = /\s(\S{1,24})$/.exec(text);
    if (!references || references.childElementCount > 2 || !ending) return [text, references];
    // Keep the citation with the last word when a paragraph wraps.
    return [text.slice(0, ending.index + 1), node("span", {class: "citation-tail"}, ending[1], references)];
}

function disclosureQuotes(t, items, records, markExcerpt, citations = null) {
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
        return node("li", {}, node("p", {}, citations ? citedText(t, text, [item.evidence_id], citations) : text));
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

function reportPanel(t, turn, view) {
    const report = turn.report;
    const lang = turn.response_language;
    const {citations, hasHighlights, highlights, quotes, records, showQuotes, summaries, synthesized} = view;
    return [
        heading(t, [t("Investigation report"),
            turn.parent_id && report.carried.length > 0 && node("span", {class: "follow-up-label"}, t("Follow-up"))],
            "Conclusion",
            hasHighlights ? null : node("span", {lang}, citedText(t, report.conclusion, report.evidence_ids, citations)),
            report.overall, true),
        hasHighlights && report.answers.length > 0 && node("div", {class: "report-answers"},
            report.answers.map(item => node("div", {class: "report-answer", "data-verdict": item.verdict},
                node("p", {lang}, citedText(t, item.text, item.evidence_ids, citations))))),
        highlights.length > 0 && node("div", {class: "metrics" + (hasHighlights ? " report-highlights" : "")},
            highlights.map(item => node("div", {class: "metric"},
                node("span", {class: "metric-label"}, item.label),
                node("div", {class: "metric-value"}, node("strong", {}, item.calculation.display_value),
                    reportCitations(t, item.calculation.evidence_ids, citations)),
                item.period_end && node("p", {class: "metric-period"}, item.period && t("{period} ended ", {period: item.period}),
                    node("time", {datetime: item.period_end}, item.period_end))))),
        hasHighlights && quotes.length > 0 && block(t("What the company disclosed"), [
            summaries?.length > 0 && node("ul", {class: "report-explanations"}, summaries.map(item => {
                const paragraphs = [item.text, ...(item.details || [])];
                return node("li", {}, paragraphs.map((text, index) => node("p", {lang},
                    index === paragraphs.length - 1 ? citedText(t, text, item.evidence_ids, citations) : text)));
            })),
            showQuotes && disclosureQuotes(t, quotes, records, synthesized, citations),
            !showQuotes && summaries.length === 0 && node("p", {},
                t("The verified excerpts are under Supporting details.")),
        ]),
        hasHighlights && report.explanation_note && node("p", {class: "report-note", lang}, report.explanation_note),
        report.carried.length > 0 && node("p", {class: "lede"}, t("Carried forward") + " · " + report.carried.map(item => t(item)).join(", ")),
        claimList(t, report.findings, {showReasons: !hasHighlights, canJump: item => verificationEvent(turn, item.claim_id)}),
    ];
}

function reportSource(turn, state, view, group) {
    const t = state.locale.text;
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
        originalUrl && link(originalUrl, t("Open original source")),
        [...related].map(([url, title]) => node("p", {}, link(url, title))),
        disclosure(state, id + "-details", t("Source details"), [
            evidenceMetadata(t, turn, first),
            items.filter(item => item.type === "fact").map(item => evidenceRecord(t, item)),
        ]),
        quotes.length > 0 && disclosure(state, id + "-excerpts", t("Source excerpts"),
            disclosureQuotes(t, quotes, view.records, true)),
    ], "disclosure report-source");
    if (state.citation === id) details.classList.add("is-cited");
    return details;
}

function calculationBody(t, item) {
    return [
        node("p", {}, item.formula),
        node("table", {class: "data-table"}, node("thead", {}, node("tr", {},
            node("th", {scope: "col"}, t("Operand")), node("th", {scope: "col"}, t("Value")))),
            node("tbody", {}, item.operands.map(operand => node("tr", {},
                node("td", {}, operand.label), node("td", {}, operand.display_value))))),
    ];
}

function supportingPanel(turn, state, view) {
    const {text: t} = state.locale;
    const {calculations, citations, records, timeline} = view;
    const report = turn.report;
    const count = citations.groups.length;
    return node("section", {class: "panel panel-side supporting-panel", "aria-labelledby": "support-heading"},
        node("h3", {id: "support-heading"}, t("Supporting details")),
        count > 0 && disclosure(state, sourcesDisclosure,
            [t("Sources"), node("span", {class: "source-count"}, String(count))],
            citations.groups.map(group => reportSource(turn, state, view, group)),
            "disclosure audit-disclosure source-disclosure", "sources"),
        calculations.length > 0 && disclosure(state, "support-calculations", t("Calculations"), node("div", {class: "sources"},
                calculations.map((item, index) => disclosure(state, "calculation-" + index,
                    item.label + " · " + item.display_value, calculationBody(t, item)))), "disclosure audit-disclosure"),
        timeline.length > 0 && disclosure(state, "support-timeline", t("Evidence timeline"), node("ol", {class: "timeline"},
                timeline.map(item => node("li", {}, node("time", {datetime: item.date}, item.date),
                    link(records[item.evidence_id]?.url, item.title), node("p", {}, item.passage)))),
            "disclosure audit-disclosure"),
        disclosure(state, "support-limitations", t("Limitations"), node("div", {},
                report.limitations.length ? report.limitations.map(item => node("p", {}, item))
                    : node("p", {}, t("Claims were evaluated against the evidence available at the saved cutoff.")),
                node("p", {}, t("Evidence cutoff") + " · ", cutoffLabel(state.locale, report.cutoff))), "disclosure audit-disclosure"));
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
    const t = state.locale.text;
    const records = Object.fromEntries(turn.evidence.map(item => [item.id, item]));
    const details = item => {
        const checks = item.checks || [];
        const cited = new Set(checks.flatMap(check => check.evidence_ids));
        const sources = verificationLinks(item.evidence_ids.filter(id => !cited.has(id)), records);
        return disclosure(state, verificationAuditId(event, item.claim_id), t("How this was verified"), [
            checks.length > 0
                ? node("ol", {class: "verify-checks"}, checks.map(check => node("li", {},
                    node("strong", {}, check.label), node("p", {}, check.detail),
                    check.excerpt && node("blockquote", {}, check.excerpt),
                    verificationLinks(check.evidence_ids, records))))
                : node("p", {}, t("This saved run recorded the verdict and available supporting evidence.")),
            item.calculations.length > 0 && block(t("Calculations"), item.calculations.map(calculation =>
                node("div", {class: "verify-calculation"},
                    node("strong", {}, calculation.label + " · ", node("span", {}, calculation.display_value)),
                    calculationBody(t, calculation)))),
            sources && block(t("Evidence and context"), sources),
        ], "disclosure verify-audit");
    };
    return [
        heading(t, "Investigation workflow", event.pass_no ? "Follow-up verification" : "Evidence verification"),
        claimList(t, event.data.findings, {
            details, isTargeted: item => state.verifyTarget === verificationAuditId(event, item.claim_id),
        }),
        event.data.gaps.length > 0 && block(t(event.pass_no ? "Remaining evidence gaps" : "Evidence needed"),
            node("ul", {}, event.data.gaps.map(gap => node("li", {}, gap.reason)))),
    ];
}

function stagePanel(turn, state, stage, event, running) {
    const {text: t} = state.locale;
    if (!turn) return heading(t, "Research workspace", "Start an investigation",
        t("Ask a question above or choose an example to inspect a research workflow."));
    if (event && event.kind !== "stage") {
        return [heading(t, event.stage, event.data.tool || event.kind, t(event.outcome)), node("pre", {class: "inspect"},
            JSON.stringify({sequence: event.sequence, time: event.timestamp, ...event.data}, null, 2))];
    }
    if (stage === "Parse" && turn.status === "Waiting for clarification") return clarification(turn, state);
    if (running) return [
        heading(t, "Investigation workflow", stage, null, turn.status),
        node("div", {class: "stage-progress"},
            node("span", {class: "stage-pulse", "aria-hidden": "true"}),
            node("span", {}, t("Stage in progress")),
            node("span", {class: "stage-clock", role: "timer", "data-live-stage-clock": "pass"})),
    ];
    if (event?.outcome === "started") {
        return heading(t, "Investigation workflow", stage,
            t(turn.status === "Interrupted" ? "The app closed while this stage was running." : "Stage started"),
            turn.status);
    }
    if (event?.outcome === "error" || turn.error && !event) {
        return heading(t, "Investigation workflow", stage,
            localizeDiagnostic(event?.data.message || turn.error || "", t), turn.status);
    }
    if (!event) return heading(t, "Investigation workflow", stage, t("This stage has no recorded output."));
    if (stage === "Plan") return planPanel(turn, state, event);
    if (stage === "Parse") {
        const parsed = event.data.output;
        const description = parsed.out_of_scope || parsed.clarification?.message;
        return [heading(t, "Investigation workflow", "Parsed question",
            description && node("span", {lang: turn.response_language}, description)),
            definitionList([[t("Evidence cutoff"), cutoffLabel(state.locale, event.data.cutoff)],
                [t("Carried forward"), parsed.carried.map(item => t(item)).join(", ")]]),
            parsed.claims.length > 0 && block(t("Resolved claims"), node("ul", {}, parsed.claims.map(claim =>
                node("li", {}, [claim.label, claim.entities.join(" / "), period(claim.period)].filter(Boolean).join(" · ")))) )];
    }
    if (stage === "Retrieve") return [
        heading(t, "Investigation workflow", "Retrieved evidence"),
        node("ul", {class: "plan-list"}, event.data.lookups.map((result, index) => {
            const summary = result.error
                ? lookupError(state, result.error, "plan-error", `retrieval-source-settings-${index}`)
                : node("p", {}, t("{count} evidence records", {count: result.records}));
            return node("li", {},
                node("p", {class: "request"}, t(sourceNames[result.action.source]) + " · " + actionLabel(t, result.action)),
                result.reused && node("p", {class: "reuse-mark"}, t("Reused from this session")),
                result.retrieval !== "cached" && summary);
        })),
        block(t("Source records"), evidenceBlocks(turn, state, event.data.lookups.flatMap(item => item.evidence_ids), "retrieve")),
    ];
    if (stage === "Verify") return verificationPanel(turn, state, event);
    return heading(t, "Investigation workflow", stage, event.data.conclusion);
}

function history(turn, state) {
    const t = state.locale.text;
    const stageEvents = turn.events.filter(item => item.kind === "stage" && item.outcome !== "started");
    const entries = (events, compact) => node("ol", {class: "history"}, events.map(event => {
        const stage = event.pass_no ? t("Follow-up {stage}", {stage: t(event.stage)}) : t(event.stage);
        const label = compact ? stage : [event.sequence, t(event.data.tool || event.kind), t(event.outcome)].join(" · ");
        return node("li", {}, button(label, "history", event.sequence,
            {"aria-current": String(state.event === event.sequence)}),
            node("small", {}, compact ? event.data.gaps?.[0]?.reason || event.data.message || "" : stage));
    }));
    return disclosure(state, "investigation-history", t("Investigation history"), [
        entries(stageEvents, true),
        disclosure(state, "raw-events", t("Model, lookup, and status events"), entries(turn.events, false)),
    ]);
}

function runPanel(turn, state) {
    const t = state.locale.text;
    return node("aside", {class: "panel panel-side", "aria-labelledby": "run-heading"},
        node("h3", {id: "run-heading"}, t("Run details")),
        turn && node("p", {class: "side-question-label"}, t("Question asked")),
        node("p", {class: "side-question", lang: turn?.response_language},
            turn ? turn.question : t("Select a question to see its run details.")),
        turn && disclosure(state, activityDisclosure, t("Run activity"), definitionList([
            ["Sources used", turn.report?.sources.map(item => t(item)).join(", ") || "—"],
            [turn.mode === "replay" ? "Fixture lookups" : "New provider lookups", turn.counts.lookups],
            ["Reused sources", turn.counts.reused], ["Cache hits", turn.counts.cache_hits],
            ["Evidence follow-ups", turn.counts.followups], ["Transport retries", turn.counts.retries],
            ["Model calls", turn.counts.models],
            ["Active time", turn.active_seconds === undefined ? undefined
                : t("{seconds} s", {seconds: turn.active_seconds.toFixed(2)})],
        ].map(([label, value]) => [t(label), value]), "side-facts"), "disclosure run-activity"),
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

function stageTime(t, milliseconds) {
    return t("{seconds} s", {seconds: (milliseconds / 1000).toFixed(2)});
}

function render(root, data, state) {
    clearInterval(state.stageClock);
    const {tag, text: t} = state.locale;
    root.lang = tag;
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
    if (!state.content) {
        state.content = node("div", {class: "workspace-content"});
        root.append(marketTexture(), state.content);
    }
    state.content.replaceChildren(header(data, state), node("main", {class: "shell workspace"},
        sidebar(data, state),
        node("div", {class: "workflow-main"},
            node("section", {class: "workflow", "aria-labelledby": "workflow-heading"},
                node("div", {class: "section-heading"},
                    node("h2", {id: "workflow-heading"}, t("Investigation workflow")),
                    node("div", {role: "status"}, badge(t, status))),
                node("nav", {class: "steps", "aria-label": t("Investigation steps")},
                    stages.map((item, index) => {
                        const live = active?.stage === item;
                        const elapsed = durations.get(item);
                        const progress = live ? t("Running") : finished.has(item) ? t("Finished") : null;
                        const timing = !live && elapsed !== undefined && t("{time} elapsed", {time: stageTime(t, elapsed)});
                        const description = [progress, timing].filter(Boolean).join(". ");
                        return button("", "stage", item,
                            {class: "step " + (newest?.stage === item ? "current" : finished.has(item) ? "done" : "pending"),
                            "aria-pressed": String(stage === item), "aria-label": t(item),
                            "aria-description": description || null},
                            node("span", {class: "step-number", "aria-hidden": "true"}, finished.has(item) ? "✓" : String(index + 1)),
                            node("span", {class: "step-label"}, t(item),
                                (live || elapsed !== undefined) && node("span", {class: "step-time", "aria-hidden": "true",
                                    "data-live-stage-clock": live ? "total" : null}, stageTime(t, elapsed || 0))));
                    }))),
            node("div", {class: "layout"},
                node("section", {class: "panel panel-main", "aria-labelledby": "stage-title", tabindex: -1},
                    view ? reportPanel(t, turn, view) : stagePanel(turn, state, stage, event, running)),
                node("div", {class: "detail-rail"}, runPanel(turn, state),
                    view && supportingPanel(turn, state, view))))));
    if (dialog) {
        state.content.append(dialog);
        dialog.showModal();
    }
    const settings = root.querySelector("#connection-settings-dialog");
    if (settings) {
        settings.querySelectorAll("fieldset, [role=tab]").forEach(control => { control.disabled = Boolean(state.pending); });
        if (state.pending) settings.querySelectorAll(".connection-settings-error").forEach(alert => { alert.hidden = true; });
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
                const time = stageTime(t, elapsed + (total ? previous : 0));
                const text = total ? time : t("{time} elapsed", {time});
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
        root.researchState = {
            locale: savedInterfaceLocale(), draft: "", sessions: false, open: new Set(defaultOpenDisclosures), answers: {},
        };
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
        const completed = state.pending === data.acknowledged ? state.pendingCommand : null;
        if (completed) {
            state.pending = null;
            state.pendingCommand = null;
        }
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
        const connectionSection = Object.values(settingsSections).find(
            section => completed === section.save || completed === section.reset);
        if (connectionSection) {
            const feedback = data[connectionSection.feedback];
            if (feedback?.error) {
                const alert = root.querySelector(`.${connectionSection.style}-error`);
                if (alert) {
                    alert.textContent = localizeDiagnostic(feedback.message, state.locale.text);
                    alert.hidden = false;
                    alert.focus({preventScroll: true});
                }
            } else {
                closeConnectionSettings(root, state);
            }
        }
    }
    const send = (command, fields = {}) => {
        if (state.pending) return;
        const nonce = crypto.randomUUID();
        state.pending = nonce;
        state.pendingCommand = command;
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
        } else if (action === "open_connection_settings") {
            state.connectionOpener = control.getAttribute("data-focus");
            const dialog = connectionSettingsDialog(data, state, value);
            root.append(dialog);
            dialog.showModal();
            dialog.querySelector(value === "data" ? "#sec-project" : "#model-provider").focus({preventScroll: true});
        } else if (action === "close_connection_settings") {
            closeConnectionSettings(root, state);
        } else if (action === "reset_model_settings" || action === "reset_data_source_settings") {
            clearSensitiveInputs(control.form);
            send(action);
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
        if (event.target.id === "interface-language") {
            state.locale = interfaceLocales[event.target.value] || interfaceLocales.en;
            saveInterfaceLocale(state.locale);
            render(root, data, state);
        }
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
        } else {
            const form = event.target;
            const section = Object.values(settingsSections).find(section => section.form === form.id);
            if (section) {
                const fields = Object.fromEntries(new FormData(form));
                clearSensitiveInputs(form);
                send(section.save, fields);
            }
        }
    };
}
