/**
 * File: src/aistudio_api/infrastructure/browser/js/build_signer.js
 * Description: WP4a — Build 宿主页「直连签名器」浏览器侧脚本。
 *
 * 由 Page.addScriptToEvaluateOnNewDocument 在 Build 宿主页
 * (https://aistudio.google.com/apps/<id>) 的主框架顶层文档上安装（iso 帧不需要）。
 *
 * 溯源：本文件移植 Ark_AIStudioToAPI 的 src/core/DirectSignerService.js 浏览器侧
 * installDirectSigner()（源文件 116-910 行）与 src/core/MakerSuiteCodec.js 的 codecFactory。
 * 传输由 Ark 的 WebSocket(9998) 换为本仓的 CDP Binding 推送架构
 * （window.__aistudio_stream_push__，对齐 infrastructure/gateway/transport.py 的事件 demux）。
 *
 * ─────────────────────────────── 冻结契约（WP4a，勿擅改） ───────────────────────────────
 * 全局：window.__buildSigner = {
 *   state: 'installing' | 'harvesting' | 'warmup-captured' | 'ready' | 'failed',
 *   ready(): boolean,                       // 是否可执行直连（tb + 收割头 x-goog-api-key 齐备）
 *   lastError(): string,                    // 最近一次失败/降级原因（供 Python 探测，禁止静默）
 *   execute(taskId: string, model: string, jsonBody: string): Promise<void>,
 *   harvest(): { headers: object, tbPresent: boolean },
 * }
 * 绑定推送：window.__aistudio_stream_push__(JSON.stringify({
 *   rid: taskId, type: 'status'|'chunk'|'done'|'error',
 *   status?: number, text?: string, message?: string,
 * }))
 *   - status  = HTTP 状态码
 *   - chunk   = scanArrayElements 切出的原始叶子字符串（Holder 拆壳留给 Python）
 *   - done    = 流正常结束
 *   - error   = 异常（message 具体化）
 * 收割头 key 来源（E8）：一律取收割头 x-goog-api-key，禁用 tb.mb.nK / tb.mb.jK 属性。
 * 防重复安装：if (window.__buildSigner) return；安装异常时 state='failed' 且 lastError 记录。
 *
 * 有意偏离 Ark：
 *  1) WS(9998) → 本仓 CDP Binding（window.__aistudio_stream_push__）。
 *  2) codecFactory：Ark 在 Node 侧运行时序列化 MakerSuiteCodec.js；本仓直接内联其源码为静态工厂函数。
 *  3) 帧推送：Ark 推送 `data: <json>\n\n`（SSE 形态，已在页面内 convChunk 转译）；本仓推送原始叶子，
 *     转换（Holder 拆壳 + REST→wire）全部留给 Python。
 *  4) RPC 端点：优先 tb.mb.F；缺省回退实测主机 alkalimakersuite-pa.clients6.google.com（spikeP 证据）。
 */

/* eslint-env browser */
(() => {
    "use strict";

    if (window.__buildSigner) return; // 防重复安装

    let isTop = false;
    try {
        isTop = window === window.top;
    } catch (e) {
        isTop = false;
    }
    if (!isTop) return; // 仅主框架顶层文档

    // ------------------------------------------------------------------ 常量
    const RPC_RE = /MakerSuiteService\/Proxy(Unary|Streamed)Call(?:\?|$)/;
    const RPC_HEADER_ALLOWLIST = [
        "authorization",
        "content-type",
        "x-goog-api-key",
        "x-goog-authuser",
        "x-goog-ext-519733851-bin",
        "x-user-agent",
    ];
    const WARMUP_MARKER = "__warmup__";
    const READY_TIMEOUT_MS = 15000;
    const RPC_SUFFIX =
        "/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ProxyStreamedCall";
    const DEFAULT_RPC_BASE = "https://alkalimakersuite-pa.clients6.google.com"; // 实测主机（spikeP 证据）
    // E8：x-goog-api-key 优先取收割头；宿主未发请求时回退到本仓通用公网 web key
    // （spikeP 实证该 key 缺失亦可 200，此处补齐以对齐真实宿主请求头）。
    const DEFAULT_WEB_API_KEY = "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs";

    // ------------------------------------------------------- 服务对象（冻结形状）
    const service = {
        state: "installing",
        _error: "",
        _tb: null,
        _tbOrigin: "",
        _headers: null,
        _warmup: "",
        _hooked: false,
        _codecError: "",
        _selfTested: false,
        _selfTestAttempted: false,
        _selfTestAttempts: 0,
        _selfTestCandidateKeys: "",
        _selfTestError: "",
        _signer: null,
        _signerKey: "",
        _signerCandidates: [],
        _tokenCandidates: [],

        ready() {
            return this.state === "ready";
        },

        lastError() {
            return this._error || "";
        },

        harvest() {
            return {
                headers: Object.assign({}, this._headers || {}),
                tbPresent: !!this._tb,
            };
        },

        execute(taskId, model, jsonBody) {
            return runTask(taskId, model, jsonBody);
        },
    };

    // 先挂载，保证安装异常时 Python 仍能读到 state='failed' / lastError()（禁止静默）。
    window.__buildSigner = service;

    // 先捕获原生 fetch（Ark L139）：runTask 走原生，避免自我再入；收割 hook 仅观察。
    const nativeFetch = window.fetch.bind(window);

    // ------------------------------------------------------------------ 小工具
    function safeLog(message) {
        try {
            console.debug("[buildSigner] " + message);
        } catch (e) {
            /* 日志尽力而为 */
        }
    }

    function push(event) {
        try {
            if (typeof window.__aistudio_stream_push__ === "function") {
                window.__aistudio_stream_push__(JSON.stringify(event));
            }
        } catch (e) {
            /* binding 推送尽力而为 */
        }
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function hasHarvestedKey() {
        return !!(service._headers && service._headers["x-goog-api-key"]);
    }

    // 状态派生：failed 粘性，优先级 ready > warmup-captured > harvesting > installing。
    // 冻结契约：ready 仅在 tb 与收割头 x-goog-api-key 同时齐备时置位（防就绪竞态——
    // 收割头未落位即派发生成会被上游 403 "caller does not have permission" 拒绝，服务内实测）。
    function refreshState() {
        if (service.state === "failed") return;
        if (service._tb && service._selfTested && hasHarvestedKey()) {
            service.state = "ready";
            return;
        }
        if (service._warmup === "swallowed") {
            service.state = "warmup-captured";
            return;
        }
        if (service._hooked || service._tb) {
            service.state = "harvesting";
            return;
        }
    }

    // Bundle 更新会重命名 minified 符号；这里按源码结构发现，而不是记名字。
    function discoverSignerCandidates(M) {
        const candidates = [];
        const keys = Object.keys(M);
        for (const key of keys) {
            let fn;
            try {
                fn = M[key];
            } catch (e) {
                continue;
            }
            if (typeof fn !== "function" || fn.__buildSignerHook) continue;
            let source = "";
            try {
                source = Function.prototype.toString.call(fn);
            } catch (e) {
                continue;
            }
            if (source.indexOf("snapshot") === -1 || source.indexOf("content") === -1) continue;
            candidates.push({ key: key, fn: fn });
        }
        return candidates;
    }

    function discoverTokenCandidates(M) {
        const candidates = [];
        for (const key of Object.keys(M)) {
            let cls;
            try {
                cls = M[key];
            } catch (e) {
                continue;
            }
            if (typeof cls !== "function") continue;
            let source = "";
            try {
                source = Function.prototype.toString.call(cls);
            } catch (e) {
                continue;
            }
            if (source.indexOf("getMetadata") === -1) continue;
            if (source.indexOf("X-Goog-Api-Key") === -1 && source.indexOf("x-goog-api-key") === -1) continue;
            candidates.push({ key: key, cls: cls });
        }
        return candidates;
    }

    async function sha256Hex(text) {
        const data = new TextEncoder().encode(String(text));
        const digest = await crypto.subtle.digest("SHA-256", data);
        return Array.from(new Uint8Array(digest))
            .map(byte => byte.toString(16).padStart(2, "0"))
            .join("");
    }

    function tokenLike(value) {
        return !!(
            value &&
            (value.mb || value.A || typeof value.snapshot === "function" || typeof value.getMetadata === "function")
        );
    }

    async function selfTest() {
        if (service._selfTested) return true;
        if (!service._signerCandidates.length) return false;
        if (service._selfTestAttempted) return false;
        if (service._selfTestAttempts >= 3) return false;
        service._selfTestAttempted = true;
        service._selfTestAttempts += 1;
        if (service._tbOrigin.indexOf("hook") === 0) {
            acquireToken(true);
        }
        if (!service._tb) return false;
        const digest = await sha256Hex("build-signer-selftest");
        const failures = [];
        for (const candidate of service._signerCandidates) {
            try {
                const token = await candidate.fn.call(window.default_MakerSuite, service._tb, digest);
                if (typeof token === "string" && token.charAt(0) === "!" && token.length > 40) {
                    service._signer = candidate.fn;
                    service._signerKey = candidate.key;
                    service._selfTested = true;
                    service._selfTestError = "";
                    refreshState();
                    safeLog("signer resolved by structure: " + candidate.key + " tokenLen=" + token.length);
                    return true;
                }
                failures.push(
                    candidate.key + ":prefix=" + String(token).slice(0, 1) +
                    ",len=" + (typeof token === "string" ? token.length : -1)
                );
            } catch (e) {
                failures.push(candidate.key + ":" + String((e && e.message) || e).slice(0, 140));
            }
        }
        service._selfTestError = failures.join(" | ").slice(0, 500);
        safeLog("signer self-test failed candidates=" + service._signerCandidates.length + " tb=" + (service._tb ? "yes" : "no") + " error=" + service._selfTestError);
        return false;
    }

    // --------------------------------------------------- codecFactory（内联静态源码）
    // 移植 Ark MakerSuiteCodec.js（403 行）；Ark 在 Node 侧以 CODEC_FACTORY 运行时序列化，
    // 本仓按冻结契约改为静态内联工厂函数（module.exports → return）。
    // 请求/响应对照改编自 yunx738/ais2api 提交 79807728179482509cf1f8a4f5e3e175d9dd6702
    // （CC BY-NC 4.0，已获授权），经 Naoya666/AIStudioToAPI 提交 8a137ca 移植。
    function codecFactory() {
        const CAT = {
            HARM_CATEGORY_CIVIC_INTEGRITY: 11,
            HARM_CATEGORY_DANGEROUS_CONTENT: 10,
            HARM_CATEGORY_HARASSMENT: 7,
            HARM_CATEGORY_HATE_SPEECH: 8,
            HARM_CATEGORY_SEXUALLY_EXPLICIT: 9,
        };
        const THR = { BLOCK_LOW_AND_ABOVE: 1, BLOCK_MEDIUM_AND_ABOVE: 2, BLOCK_NONE: 4, BLOCK_ONLY_HIGH: 3, OFF: 5 };
        const FIN = { 1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER" };
        const TYP = { array: 5, boolean: 4, integer: 3, number: 2, object: 6, string: 1 };
        const fail = (message, status) => {
            throw Object.assign(new Error(message), { status: status === undefined ? 400 : status });
        };
        const fill = a => Array.from(a, v => (v === undefined ? null : v));
        function fields(value, allowed, path) {
            if (!value || typeof value !== "object" || Array.isArray(value)) fail(path + " must be an object");
            for (const key of Object.keys(value)) {
                if (value[key] === undefined || allowed.indexOf(key) >= 0) continue;
                let shown;
                try {
                    shown = JSON.stringify(value[key]);
                } catch (e) {
                    shown = String(value[key]);
                }
                if (shown && shown.length > 120) shown = shown.slice(0, 120) + "...";
                fail(
                    "Unsupported field \"" + path + "." + key + "\" (value: " + (shown == null ? "undefined" : shown) + ")" +
                        " - the direct channel cannot encode it; use the native channel for this request"
                );
            }
        }
        function encVal(v) {
            if (v == null) return [0];
            if (typeof v === "number" && Number.isFinite(v)) return [null, v];
            if (typeof v === "string") return [null, null, v];
            if (typeof v === "boolean") return [null, null, null, v];
            if (Array.isArray(v)) return [null, null, null, null, null, [v.map(encVal)]];
            if (typeof v === "object") return [null, null, null, null, encStruct(v)];
            return fail("Invalid function argument value (type: " + typeof v + ")");
        }
        function encStruct(o) {
            if (!o || typeof o !== "object" || Array.isArray(o))
                fail("Function call arguments must be a plain object (got " + (Array.isArray(o) ? "array" : typeof o) + ")");
            return [
                Object.entries(o)
                    .filter(([, v]) => v !== undefined)
                    .map(([k, v]) => [k, encVal(v)]),
            ];
        }
        function decVal(v) {
            if (!Array.isArray(v)) return fail("Malformed upstream value (direct channel)", 502);
            if (v[1] != null) return v[1];
            if (v[2] != null) return v[2];
            if (v[3] != null) return !!v[3];
            if (v[4] != null) return decStruct(v[4]);
            if (v[5] != null) return (v[5][0] || []).map(decVal);
            return null;
        }
        function decStruct(st) {
            if (!Array.isArray(st) || !Array.isArray(st[0]))
                return fail("Malformed upstream struct (direct channel)", 502);
            return Object.fromEntries(st[0].map(([k, v]) => [k, decVal(v)]));
        }
        function encSchema(sc) {
            fields(
                sc,
                ["type", "format", "description", "nullable", "enum", "items", "properties", "required"],
                "schema"
            );
            const type = TYP[String(sc.type || "object").toLowerCase()];
            if (!Number.isInteger(type)) fail("Unsupported JSON schema type \"" + sc.type + "\"");
            const a = [type];
            if (sc.format) a[1] = sc.format;
            if (sc.description) a[2] = sc.description;
            if (sc.nullable != null) a[3] = !!sc.nullable;
            if (sc.enum) a[4] = sc.enum.map(String);
            if (sc.items) a[5] = encSchema(sc.items);
            if (sc.properties) a[6] = Object.entries(sc.properties).map(([k, v]) => [k, encSchema(v)]);
            if (sc.required) a[7] = sc.required;
            return fill(a);
        }
        function encTools(tools) {
            tools = tools || [];
            const decls = [];
            for (const t of tools) {
                fields(t, ["functionDeclarations", "function_declarations"], "tools[]");
                for (const d of t.functionDeclarations || t.function_declarations || []) {
                    fields(
                        d,
                        ["name", "description", "parameters", "parametersJsonSchema"],
                        "tools[].functionDeclaration"
                    );
                    if (!d.name) fail("tools[].functionDeclaration.name is required");
                    const x = [d.name, d.description || null];
                    const ps = d.parameters || d.parametersJsonSchema;
                    if (ps) x[2] = encSchema(ps);
                    decls.push(x);
                }
            }
            return decls.length ? [[null, decls]] : null;
        }
        function toPart(p) {
            fields(
                p,
                [
                    "text",
                    "inlineData",
                    "inline_data",
                    "functionCall",
                    "function_call",
                    "functionResponse",
                    "function_response",
                    "thought",
                    "thoughtSignature",
                    "thought_signature",
                ],
                "contents[].parts[]"
            );
            const a = [],
                d = p.inlineData || p.inline_data,
                fc = p.functionCall || p.function_call,
                fr = p.functionResponse || p.function_response;
            if (typeof p.text === "string") a[1] = p.text;
            else if (d) {
                fields(d, ["mimeType", "mime_type", "data"], "inlineData");
                a[2] = [d.mimeType || d.mime_type, d.data];
            } else if (fc) {
                fields(fc, ["name", "args", "id"], "functionCall");
                a[10] = [fc.name, encStruct(fc.args || {}), fc.id || null];
            } else if (fr) {
                fields(fr, ["name", "response", "id"], "functionResponse");
                a[11] = [fr.name, encStruct(fr.response || {}), fr.id || null];
            } else if (!(p.thoughtSignature || p.thought_signature))
                return fail(
                    "Unsupported contents[].parts[] entry: no text/inlineData/functionCall/functionResponse/thoughtSignature"
                );
            if (p.thought != null) a[12] = !!p.thought;
            if (p.thoughtSignature || p.thought_signature) a[14] = p.thoughtSignature || p.thought_signature;
            return fill(a);
        }
        function toContent(c) {
            fields(c, ["role", "parts"], "contents[]");
            if (!Array.isArray(c.parts) || !c.parts.length) fail("contents[].parts must be a non-empty array");
            if (c.role && ["user", "model", "system"].indexOf(c.role) < 0)
                fail("Unsupported contents[].role \"" + c.role + "\" (expected \"user\", \"model\" or \"system\")");
            return [c.parts.map(toPart), c.role === "model" ? "model" : "user"];
        }
        function buildBody(model, g) {
            fields(
                g,
                [
                    "contents",
                    "generationConfig",
                    "generation_config",
                    "systemInstruction",
                    "system_instruction",
                    "safetySettings",
                    "safety_settings",
                    "tools",
                    "toolConfig",
                    "tool_config",
                    "model",
                ],
                "request"
            );
            const gc = g.generationConfig || g.generation_config || {},
                tc = gc.thinkingConfig || gc.thinking_config;
            fields(
                gc,
                [
                    "stopSequences",
                    "candidateCount",
                    "candidate_count",
                    "maxOutputTokens",
                    "temperature",
                    "topP",
                    "topK",
                    "responseMimeType",
                    "thinkingConfig",
                    "thinking_config",
                ],
                "generationConfig"
            );
            const candidateCount = gc.candidateCount != null ? gc.candidateCount : gc.candidate_count;
            if (candidateCount != null && String(candidateCount) !== "1")
                fail("generationConfig.candidateCount=" + candidateCount + " is not supported by the direct channel (only 1)");
            const cfg = [];
            if (Array.isArray(gc.stopSequences) && gc.stopSequences.length) cfg[1] = gc.stopSequences;
            cfg[3] = gc.maxOutputTokens != null ? gc.maxOutputTokens : 65536;
            cfg[4] = gc.temperature != null ? gc.temperature : 1;
            cfg[5] = gc.topP != null ? gc.topP : 0.95;
            cfg[6] = gc.topK != null ? gc.topK : 64;
            if (gc.responseMimeType) cfg[7] = gc.responseMimeType;
            cfg[13] = 1;
            if (tc) {
                fields(tc, ["includeThoughts", "thinkingBudget", "thinkingLevel"], "thinkingConfig");
                const th = [];
                if (tc.includeThoughts != null) th[0] = tc.includeThoughts ? 1 : 0;
                if (tc.thinkingBudget != null) th[1] = tc.thinkingBudget;
                if (tc.thinkingLevel != null) {
                    const level = { high: 2, low: 1 }[String(tc.thinkingLevel).toLowerCase()];
                    if (!Number.isInteger(level))
                        fail(
                            "generationConfig.thinkingConfig.thinkingLevel=\"" + tc.thinkingLevel +
                                "\" is not supported (only \"LOW\"/\"HIGH\"); use thinkingBudget instead"
                        );
                    th[3] = level;
                }
                cfg[16] = fill(th);
            }
            const toolConfig = g.toolConfig || g.tool_config;
            if (toolConfig) {
                fields(toolConfig, ["functionCallingConfig"], "toolConfig");
                const fc = toolConfig.functionCallingConfig;
                if (fc) {
                    fields(fc, ["mode"], "toolConfig.functionCallingConfig");
                    if (fc.mode && String(fc.mode).toUpperCase() !== "AUTO")
                        fail(
                            "Unsupported tool choice: toolConfig.functionCallingConfig.mode=\"" + fc.mode +
                                "\" - the direct channel can only encode \"AUTO\"."
                        );
                }
            }
            let safety = [7, 8, 9, 10].map(c => [null, null, c, 5]);
            const settings = g.safetySettings || g.safety_settings;
            if (settings)
                safety = settings.map(s => {
                    fields(s, ["category", "threshold"], "safetySettings[]");
                    if (!Number.isInteger(CAT[s.category]) || !Number.isInteger(THR[s.threshold]))
                        fail(
                            "Unsupported safetySettings entry: category=\"" + s.category + "\", threshold=\"" + s.threshold + "\""
                        );
                    return [null, null, CAT[s.category], THR[s.threshold]];
                });
            if (!Array.isArray(g.contents) || !g.contents.length) fail("request.contents must be a non-empty array");
            const contents = g.contents.map(toContent),
                si = g.systemInstruction || g.system_instruction;
            const sys = si ? toContent(si) : null;
            if (sys) sys[1] = "user";
            const body = [
                "models/" + model,
                contents,
                safety,
                fill(cfg),
                null,
                sys,
                encTools(g.tools),
                null,
                null,
                null,
                1,
                null,
                null,
                [[null, null, "UTC"]],
            ];
            const ht = contents
                .flatMap(c => c[0])
                .map(p => (p[1] != null ? p[1] : p[2] ? p[2][1] : ""))
                .filter(Boolean)
                .join(" ");
            return { body: body, ht: ht };
        }
        function buildHashText(g) {
            return (Array.isArray(g && g.contents) ? g.contents : [])
                .flatMap(c => (c && Array.isArray(c.parts) ? c.parts : []))
                .map(p => {
                    if (p && typeof p.text === "string") return p.text;
                    const inline = p && (p.inlineData || p.inline_data);
                    return inline && typeof inline.data === "string" ? inline.data : "";
                })
                .filter(Boolean)
                .join(" ");
        }
        function convChunk(m) {
            const out = {};
            if (Array.isArray(m[0]))
                out.candidates = m[0].map((c, i) => {
                    if (!Array.isArray(c && c[0] && c[0][0])) fail("Malformed upstream candidate (direct channel)", 502);
                    const parts = c[0][0].map(p => {
                        const o = {};
                        if (typeof p[1] === "string") o.text = p[1];
                        if (Array.isArray(p[2])) o.inlineData = { data: p[2][1], mimeType: p[2][0] };
                        if (Array.isArray(p[10])) {
                            o.functionCall = { args: decStruct(p[10][1]), name: p[10][0] };
                            if (p[10][2]) o.functionCall.id = p[10][2];
                        }
                        if (p[12] === 1 || p[12] === true) o.thought = true;
                        if (p[14]) o.thoughtSignature = p[14];
                        if (!Object.keys(o).length) fail("Unrecognized upstream response part (direct channel)", 502);
                        return o;
                    });
                    const cand = { content: { parts: parts, role: "model" }, index: i };
                    if (c[1]) cand.finishReason = Object.hasOwn(FIN, c[1]) ? FIN[c[1]] : "OTHER";
                    return cand;
                });
            if (Array.isArray(m[2])) {
                out.usageMetadata = {};
                for (const [key, idx] of Object.entries({
                    candidatesTokenCount: 1,
                    promptTokenCount: 0,
                    thoughtsTokenCount: 9,
                    totalTokenCount: 2,
                })) {
                    if (typeof m[2][idx] === "number") out.usageMetadata[key] = m[2][idx];
                }
            }
            if (typeof m[7] === "string") out.responseId = m[7];
            return out;
        }
        function merge(chunks) {
            const candidates = new Map();
            const out = { candidates: [] };
            for (const ch of chunks) {
                for (const c of ch.candidates || []) {
                    let dest = candidates.get(c.index);
                    if (!dest) {
                        dest = { content: { parts: [], role: "model" }, index: c.index };
                        candidates.set(c.index, dest);
                    }
                    if (c.finishReason) dest.finishReason = c.finishReason;
                    for (const p of c.content.parts) {
                        const last = dest.content.parts.at(-1);
                        if (p.text != null && last && last.text != null && !!last.thought === !!p.thought && !last.thoughtSignature) {
                            last.text += p.text;
                            if (p.thoughtSignature) last.thoughtSignature = p.thoughtSignature;
                        } else if (
                            (p.text === undefined || p.text === "") &&
                            p.thoughtSignature &&
                            Object.keys(p).every(key => ["text", "thought", "thoughtSignature"].indexOf(key) >= 0) &&
                            last
                        )
                            last.thoughtSignature = p.thoughtSignature;
                        else dest.content.parts.push(Object.assign({}, p));
                    }
                }
                if (ch.usageMetadata) out.usageMetadata = Object.assign({}, out.usageMetadata, ch.usageMetadata);
                if (ch.responseId) out.responseId = ch.responseId;
            }
            out.candidates = Array.from(candidates.values());
            if (!out.candidates.length || out.candidates.some(c => !c.finishReason))
                fail("Upstream response ended without a confirmed finish reason (direct channel)", 502);
            return out;
        }
        const RPC_STATUS = { 3: 400, 5: 404, 7: 403, 8: 429, 9: 400, 13: 500, 14: 503, 16: 401 };
        function wrapResponse(text, streamed) {
            let root;
            try {
                root = JSON.parse(text);
            } catch (e) {
                return fail("Upstream response is not valid JSON (direct channel)", 502);
            }
            const isChunk = x =>
                Array.isArray(x) &&
                ((Array.isArray(x[0]) &&
                    x[0].length > 0 &&
                    x[0].every(c => Array.isArray(c && c[0] && c[0][0]) && c[0][1] === "model")) ||
                    ((x[0] == null || (Array.isArray(x[0]) && x[0].length === 0)) && Array.isArray(x[2])));
            const arr = Array.isArray(root) && root.some(isChunk) ? root : root && root[0];
            if (!Array.isArray(arr)) fail("Unrecognized upstream response envelope (direct channel)", 502);
            for (const record of arr) {
                if (Array.isArray(record) && Number.isInteger(record[0]) && record[0] !== 0 && typeof record[1] === "string") {
                    throw Object.assign(new Error(record[1]), { rpcStatus: record, status: RPC_STATUS[record[0]] || 502 });
                }
            }
            const chunks = arr.filter(isChunk).map(convChunk);
            const merged = merge(chunks); // 即便返回旧流式 envelope 也校验完成态
            if (!streamed) return JSON.stringify([JSON.stringify(merged)]);
            const tail = arr.length && !isChunk(arr.at(-1)) ? arr.at(-1) : null;
            return JSON.stringify([chunks.map(c => [JSON.stringify(c)]).concat(tail ? [tail] : [])]);
        }
        function errorEnvelope(text, status) {
            let value;
            try {
                value = JSON.parse(text);
            } catch (e) {
                try {
                    value = JSON.parse(text.replace(/^\[(\s*),/, "[$1null,"));
                } catch (e2) {
                    value = null;
                }
            }
            if (Array.isArray(value) && typeof value[0] === "number") {
                return { body: JSON.stringify(value), status: status >= 400 ? status : RPC_STATUS[value[0]] || 502 };
            }
            if (Array.isArray(value) && Array.isArray(value[1]) && typeof value[1][0] === "number") {
                const record = value[1];
                return { body: JSON.stringify(record), status: status >= 400 ? status : RPC_STATUS[record[0]] || 502 };
            }
            const error = value && value.error;
            const message =
                error && typeof error.message === "string"
                    ? error.message
                    : "MakerSuite upstream request failed (HTTP " + status + ")";
            const code = Object.keys(RPC_STATUS).find(k => RPC_STATUS[k] === status) || 13;
            return {
                body: JSON.stringify([Number(code), message, (error && error.details) || []]),
                status: status >= 400 ? status : 502,
            };
        }
        return {
            buildBody: buildBody,
            buildHashText: buildHashText,
            convChunk: convChunk,
            decStruct: decStruct,
            encStruct: encStruct,
            errorEnvelope: errorEnvelope,
            merge: merge,
            wrapResponse: wrapResponse,
        };
    }

    // ------------------------------------------------- scanArrayElements（原样内联，Ark L47-110）
    // Incremental splitter for the outer JSON array of a streamed GenerateContent
    // body. Stateful across reads: { started, ended, pos }. Pure string scanning.
    function scanArrayElements(buffer, state) {
        const out = [];
        if (state.ended) return out;
        if (!state.started) {
            const i = buffer.indexOf("[");
            if (i < 0) return out;
            state.outerStart = i;
            state.pos = i + 1;
            state.started = true;
        }
        for (;;) {
            while (state.pos < buffer.length && (buffer[state.pos] === "," || /\s/.test(buffer[state.pos]))) state.pos++;
            if (state.pos >= buffer.length) break;
            if (buffer[state.pos] === "]") {
                state.ended = true;
                break;
            }
            const start = state.pos;
            let depth = 0,
                inStr = false,
                esc = false,
                q = start,
                end = -1,
                outerClosed = false;
            for (; q < buffer.length; q++) {
                const ch = buffer[q];
                if (inStr) {
                    if (esc) esc = false;
                    else if (ch === "\\") esc = true;
                    else if (ch === "\"") inStr = false;
                    continue;
                }
                if (ch === "\"") inStr = true;
                else if (ch === "[" || ch === "{") depth++;
                else if (ch === "]" || ch === "}") {
                    if (depth === 0) {
                        end = q;
                        outerClosed = true;
                        break;
                    }
                    depth--;
                    if (depth === 0) {
                        end = q + 1;
                        break;
                    }
                } else if (ch === "," && depth === 0) {
                    end = q;
                    break;
                }
            }
            if (end < 0) break;
            if (end > start) {
                if (state.firstElemStart === undefined) state.firstElemStart = start;
                out.push(buffer.slice(start, end));
            }
            if (outerClosed) {
                state.ended = true;
                state.pos = q + 1;
                break;
            }
            state.pos = end;
        }
        return out;
    }

    // ----------------------------------------------------------- 叶子帧识别（仅用于切帧/锁模）
    // 与 Ark emitChunk 的判据一致（不含推送）：字符串/单串 holder 逐层解一层，
    // REST 形态认 candidates/usageMetadata；位置数组形态用 codec.convChunk 试转。
    // 注意：识别只决定 scan 模式；推送的始终是原始叶子，Holder 拆壳留给 Python。
    function looksLikeLeaf(raw) {
        let value;
        try {
            value = JSON.parse(raw);
        } catch (e) {
            return false;
        }
        if (typeof value === "string") {
            try {
                value = JSON.parse(value);
            } catch (e) {
                return false;
            }
        }
        if (Array.isArray(value) && value.length === 1 && typeof value[0] === "string") {
            try {
                value = JSON.parse(value[0]);
            } catch (e) {
                return false;
            }
        }
        if (value && typeof value === "object" && (value.candidates || value.usageMetadata)) return true;
        if (!Array.isArray(value)) return false;
        try {
            const chunk = service.codec && service.codec.convChunk(value);
            return !!(chunk && (chunk.candidates || chunk.usageMetadata));
        } catch (e) {
            return false;
        }
    }

    // ------------------------------------------------------------------- 收割/装配
    function buildRequestHeaders() {
        const headers = {};
        const harvested = service._headers || {};
        headers["content-type"] = "application/json+protobuf";
        headers["x-user-agent"] = "grpc-web-javascript/0.1";
        try {
            const tb = service._tb;
            const mb = tb && tb.mb;
            if (mb && mb.Ml != null) headers["x-goog-authuser"] = String(mb.Ml);
            const metadata = tb && typeof tb.getMetadata === "function" ? tb.getMetadata() : null;
            if (metadata && metadata.Authorization) headers["authorization"] = metadata.Authorization;
        } catch (e) {
            /* metadata 读取尽力而为；收割头为权威来源 */
        }
        // 收割头覆盖：x-goog-api-key 一律取自收割头（E8：禁用 tb.mb.nK/jK）。
        for (const name of RPC_HEADER_ALLOWLIST) {
            if (typeof harvested[name] === "string" && harvested[name]) headers[name] = harvested[name];
        }
        if (!headers["x-goog-api-key"]) headers["x-goog-api-key"] = DEFAULT_WEB_API_KEY;
        return headers;
    }

    function rpcBase() {
        try {
            const tb = service._tb;
            const mb = tb && tb.mb;
            if (mb && typeof mb.F === "string" && mb.F) return mb.F;
        } catch (e) {
            /* 回退到实测缺省主机 */
        }
        return DEFAULT_RPC_BASE;
    }

    function normalizeError(text, status) {
        let normalized = { body: JSON.stringify([14, "unexpected upstream failure"]), status: status || 502 };
        try {
            if (service.codec && typeof service.codec.errorEnvelope === "function")
                normalized = service.codec.errorEnvelope(text, status);
        } catch (e) {
            /* 回退原始文本 */
        }
        let detail = "";
        try {
            const parsed = JSON.parse(String(normalized.body));
            if (Array.isArray(parsed) && typeof parsed[1] === "string") detail = parsed[1];
        } catch (e) {
            /* 保持原始回退 */
        }
        if (!detail) detail = String(normalized.body || text);
        return { status: normalized.status || status || 502, detail: String(detail).slice(0, 2000) };
    }

    // ------------------------------------------------------------------ 任务执行
    async function runTask(taskId, model, jsonBody) {
        const rid = String(taskId);
        if (service.state === "failed") {
            push({ rid: rid, type: "error", message: "[buildSigner] signer failed: " + (service.lastError() || "unknown") });
            return;
        }
        if (!service.codec) {
            push({
                rid: rid,
                type: "error",
                message: "[buildSigner] codec unavailable: " + (service._codecError || "unknown"),
            });
            return;
        }
        const M = window.default_MakerSuite;
        if (!M) {
            push({ rid: rid, type: "error", message: "[buildSigner] default_MakerSuite missing (state=" + service.state + ")" });
            return;
        }

        // 本地 warmup（零上游流量）：只做 token 捕获与一次零额度自检，不生成内容。
        if (model === WARMUP_MARKER) {
            try {
                acquireToken();
                await selfTest();
            } catch (e) {
                /* 自检失败由外层超时诊断兜底；不记录凭据 */
            }
            service._warmup = "swallowed";
            refreshState();
            safeLog("warmup task swallowed locally (zero upstream traffic)");
            push({ rid: rid, type: "status", status: 204 });
            push({ rid: rid, type: "done" });
            return;
        }

        const deadline = Date.now() + READY_TIMEOUT_MS;
        while (service.state !== "ready") {
            if (Date.now() >= deadline) {
                push({
                    rid: rid,
                    type: "error",
                    message:
                        "[buildSigner] token service not ready (state=" + service.state +
                        ", tb=" + (service._tb ? "yes" : "no") + ", key=" + (hasHarvestedKey() ? "yes" : "no") + ")",
                });
                return;
            }
            await sleep(25);
        }

        const tb = service._tb;
        const pathElement = "/v1beta/models/" + model + ":streamGenerateContent";
        const jsonText = typeof jsonBody === "string" && jsonBody ? jsonBody : String(jsonBody == null ? "" : jsonBody);

        let token;
        try {
            if (!service._signer) throw new Error("signer not resolved");
            token = await service._signer.call(M, tb, await sha256Hex(pathElement + " " + jsonText));
        } catch (e) {
            push({ rid: rid, type: "error", message: "[buildSigner] signing failed: " + String((e && e.message) || e).slice(0, 300) });
            return;
        }
        if (!token) {
            push({ rid: rid, type: "error", message: "[buildSigner] token empty" });
            return;
        }

        const headers = buildRequestHeaders();
        const payload = [pathElement, jsonText, token];
        const url = rpcBase() + RPC_SUFFIX;

        let resp;
        try {
            resp = await nativeFetch(url, {
                body: JSON.stringify(payload),
                credentials: "include",
                headers: headers,
                method: "POST",
            });
        } catch (e) {
            push({
                rid: rid,
                type: "error",
                message: "[buildSigner] browser transport failed: " + String((e && e.message) || e).slice(0, 300),
            });
            return;
        }

        push({ rid: rid, type: "status", status: resp.status });

        if (resp.status !== 200 || !resp.body) {
            let text = "";
            try {
                text = await resp.text();
            } catch (e) {
                /* 响应体读取失败，保留空样本 */
            }
            const normalized = normalizeError(text, resp.status);
            push({
                rid: rid,
                type: "error",
                status: normalized.status,
                message:
                    "[buildSigner] upstream (HTTP " + (resp.status || 502) + ") rejected the request: " + normalized.detail,
            });
            return;
        }

        // Progressive read：Ark L490-605 的 flat/inner 双状态锁模扫描，改为 binding 推原始叶子。
        const decoder = new TextDecoder();
        const flat = { ended: false, pos: 0, started: false };
        const inner = { ended: false, pos: 0, started: false };
        const reader = resp.body.getReader();
        let mode = 0;
        let buffer = "";
        let frames = 0;
        const emitLeaf = raw => {
            push({ rid: rid, type: "chunk", text: raw });
            frames++;
        };
        const consume = scanState => {
            let leafAny = false;
            for (const elem of scanArrayElements(buffer, scanState)) {
                if (looksLikeLeaf(elem)) {
                    leafAny = true;
                    emitLeaf(elem);
                }
            }
            return leafAny;
        };

        try {
            for (;;) {
                const readResult = await reader.read();
                if (readResult.done) break;
                buffer += decoder.decode(readResult.value, { stream: true });
                if (mode === 2) {
                    consume(inner);
                } else if (mode === 1) {
                    consume(flat);
                } else {
                    if (!inner.started && flat.outerStart !== undefined) {
                        const nested = buffer.indexOf("[", flat.outerStart + 1);
                        if (nested >= 0) {
                            inner.pos = nested + 1;
                            inner.started = true;
                        }
                    }
                    if (inner.started && consume(inner)) {
                        mode = 2;
                    } else if (consume(flat)) {
                        mode = 1;
                    }
                }
                if (mode === 2 && inner.ended) break;
                if (mode !== 2 && flat.ended) break;
            }
        } catch (e) {
            push({
                rid: rid,
                type: "error",
                message: "[buildSigner] stream read failed: " + String((e && e.message) || e).slice(0, 300),
            });
            return;
        }

        // 兜底（防静默截断，对齐 Ark 的 recovery 意图）：增量扫描未产出任何叶子时，
        // 将整段原文作为单帧推送，交给 Python 的 Holder 解码器处理；仍为空则显式报错。
        if (frames === 0) {
            if (buffer) {
                push({ rid: rid, type: "chunk", text: buffer });
                frames = 1;
            } else {
                push({ rid: rid, type: "error", message: "[buildSigner] upstream response was empty" });
                return;
            }
        }

        safeLog("task done rid=" + rid + " frames=" + frames);
        push({ rid: rid, type: "done" });
    }

    // ---------------------------------------------------------------- 安装流程
    function acquireToken(force) {
        if (service._tb && !force) return true;
        const M = window.default_MakerSuite;
        if (!M) return false;
        if (!service._tokenCandidates.length) {
            service._tokenCandidates = discoverTokenCandidates(M);
        }
        for (const candidate of service._tokenCandidates) {
            let inst = null;
            if (typeof M.n === "function") {
                const injectToken = candidate.cls.ta || candidate.cls;
                try {
                    inst = M.n(injectToken);
                } catch (e) {
                    /* Angular 注入上下文不可用（NG0203） */
                }
            }
            if (!tokenLike(inst)) {
                try {
                    inst = new candidate.cls();
                } catch (e) {
                    /* 构造需要注入上下文 */
                }
            }
            if (tokenLike(inst)) {
                service._tb = inst;
                service._tbOrigin = "resolver:" + candidate.key;
                return true;
            }
        }
        return false;
    }

    // 结构与行为发现：只按源码特征找候选，名字不参与判定。
    function hookMakerSuite() {
        const M = window.default_MakerSuite;
        if (!M) return "module_missing";
        if (!service._signerCandidates.length) {
            service._signerCandidates = discoverSignerCandidates(M);
        }
        if (!service._tokenCandidates.length) {
            service._tokenCandidates = discoverTokenCandidates(M);
        }
        if (!service._signerCandidates.length) return "module_missing";
        for (const candidate of service._signerCandidates) {
            const original = candidate.fn;
            if (original.__buildSignerHook) continue;
            const wrapped = function (a) {
                if (tokenLike(a)) {
                    const currentKeyed = !!(service._tb && service._tb.mb && (service._tb.mb.jK || service._tb.mb.nK));
                    const nextKeyed = !!(a.mb && (a.mb.jK || a.mb.nK));
                    if (!service._tb || nextKeyed || !currentKeyed) {
                        service._tb = a;
                        service._tbOrigin = "hook:" + candidate.key;
                    }
                }
                return original.apply(this, arguments);
            };
            wrapped.__buildSignerHook = true;
            M[candidate.key] = wrapped;
            candidate.fn = wrapped;
        }
        service._hooked = true;
        const candidateKeys = service._signerCandidates.map(candidate => candidate.key).join(",");
        if (candidateKeys && candidateKeys !== service._selfTestCandidateKeys) {
            service._selfTestCandidateKeys = candidateKeys;
            service._selfTestAttempted = false;
        }
        if (service._tb && !service._selfTested && !service._selfTestAttempted) {
            setTimeout(() => {
                void selfTest().catch(() => {});
            }, 0);
        }
        return "token_pending";
    }

    // 本地吞没 warmup（Ark L321-361 / L846-853）；绝不发包、零额度。
    function swallow(xhr, entry) {
        let readyState = 1,
            status = 0,
            statusText = "",
            responseText = "";
        const chunks = [];
        const own = (name, get) => {
            try {
                Object.defineProperty(xhr, name, { configurable: true, get: get });
            } catch (e) {
                /* 属性可能不可配置 */
            }
        };
        const fire = type => {
            try {
                xhr.dispatchEvent(new Event(type));
            } catch (e) {
                /* 页面事件派发尽力而为 */
            }
        };
        own("readyState", () => readyState);
        own("status", () => status);
        own("statusText", () => statusText);
        own("responseText", () => responseText);
        own("response", () => chunks);
        own("responseURL", () => entry.url || "");
        xhr.getAllResponseHeaders = () => "content-type: application/json+protobuf; charset=UTF-8\r\n";
        xhr.getResponseHeader = name =>
            /^content-type$/i.test(String(name)) ? "application/json+protobuf; charset=UTF-8" : null;
        fire("loadstart");
        setTimeout(() => {
            status = 400;
            statusText = "ERROR";
            responseText = '[3,"build-signer warmup swallowed"]';
            readyState = 4;
            fire("readystatechange");
            fire("progress");
            fire("load");
            fire("loadend");
        }, 0);
    }

    function installXhrHarvest() {
        const proto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
        if (!proto) return;
        const open = proto.open,
            setRequestHeader = proto.setRequestHeader,
            send = proto.send;
        const state = new WeakMap();
        proto.open = function (method, url) {
            let matches = false;
            try {
                matches = RPC_RE.test(String(url));
            } catch (e) {
                matches = false;
            }
            state.set(this, { headers: {}, matches: matches, url: String(url) });
            return open.apply(this, arguments);
        };
        proto.setRequestHeader = function (name, value) {
            const entry = state.get(this);
            if (entry && entry.matches) {
                try {
                    entry.headers[String(name).toLowerCase()] = String(value);
                } catch (e) {
                    /* header 记录尽力而为 */
                }
            }
            return setRequestHeader.apply(this, arguments);
        };
        proto.send = function () {
            const entry = state.get(this);
            if (entry && entry.matches) {
                const harvested = {};
                for (const name of RPC_HEADER_ALLOWLIST) {
                    if (typeof entry.headers[name] === "string" && entry.headers[name]) harvested[name] = entry.headers[name];
                }
                if (Object.keys(harvested).length) {
                    service._headers = Object.assign(service._headers || {}, harvested);
                    refreshState();
                }
                const rawBody = typeof arguments[0] === "string" ? arguments[0] : null;
                if (rawBody && rawBody.indexOf(WARMUP_MARKER) !== -1) {
                    service._warmup = "swallowed";
                    refreshState();
                    safeLog("warmup request swallowed (credentials captured)");
                    return swallow(this, entry);
                }
            }
            return send.apply(this, arguments);
        };
    }

    // 观察式 fetch hook（Ark L859-881）：部分 Studio bundle 经 fetch 发 RPC，
    // 仅收割白名单头（不吞没、不改写、不拦截真实流量）；
    // 实测宿主 egress 为 fetch（direct-run24 日志），此路径是收割头的主要来源。
    function installFetchHarvest() {
        window.fetch = function (input, init) {
            try {
                const url = typeof input === "string" || input instanceof URL ? String(input) : input.url;
                const isWarmup = url.indexOf(WARMUP_MARKER) !== -1;
                if (RPC_RE.test(url) || isWarmup) {
                    const headerBag = new Headers(
                        (init && init.headers) || (input instanceof Request ? input.headers : undefined)
                    );
                    const harvested = {};
                    for (const name of RPC_HEADER_ALLOWLIST) {
                        const value = headerBag.get(name);
                        if (value) harvested[name] = value;
                    }
                    if (Object.keys(harvested).length) {
                        service._headers = Object.assign(service._headers || {}, harvested);
                        refreshState();
                    }
                }
                if (isWarmup) {
                    // The Build client uses fetch (not XHR) for proxy requests. A real
                    // warmup must be swallowed locally so it never reaches upstream.
                    try {
                        acquireToken();
                        void selfTest();
                    } catch (e) {
                        /* tb 捕获尽力而为；后续由就绪探测给出诊断 */
                    }
                    service._warmup = "swallowed";
                    refreshState();
                    safeLog("warmup fetch swallowed locally (zero upstream traffic)");
                    return Promise.resolve(
                        new Response('[3,"build-signer warmup swallowed"]', {
                            status: 200,
                            headers: { "content-type": "application/json" },
                        })
                    );
                }
            } catch (e) {
                /* 收割尽力而为 */
            }
            return nativeFetch(input, init);
        };
    }

    function install() {
        if (codecFactory) {
            try {
                service.codec = codecFactory();
            } catch (e) {
                service._codecError = String((e && e.message) || e);
                service.state = "failed";
                service._error = "CODEC_EVAL_FAILED: " + service._codecError;
                return;
            }
        }

        installXhrHarvest();
        installFetchHarvest();
        hookMakerSuite();
        refreshState();

        // default_MakerSuite 晚到：轮询安装（Ark L883-887 的 timer 等价物）。
        const timer = setInterval(() => {
            try {
                const ready = hookMakerSuite() === "ready";
                refreshState();
                if (ready && service.state === "ready") clearInterval(timer);
            } catch (e) {
                /* 轮询尽力而为 */
            }
        }, 1000);
    }

    try {
        install();
    } catch (e) {
        service.state = "failed";
        service._error = "INSTALL_FAILED: " + String((e && e.message) || e);
    }
})();