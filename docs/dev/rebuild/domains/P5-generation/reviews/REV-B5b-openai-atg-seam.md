# REV-B5b — independent read-only review

- Target worktree: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- HEAD: `629614f` (B5b changes are uncommitted)
- Review date: 2026-10-30
- Re-review date: 2026-10-30 (B-01/B-02 fixes, same uncommitted worktree)
- Review mode: read-only source/test/config inspection; no source/test/config edits, no staging, no commit, no B6 start, no network/real account/IP.
- Authority checked: `06-decision-log.md` P5-Q1/Q3/Q4/Q6/D7, user correction (OpenAI + `/v1/models` + tools + ATG + PG-07/PG-08 are B5b; Claude/Responses are C3), `domains/P5-generation/index.md`, `09-pg-toolcall-analysis.md`, `WIRE_SPECIFICATION.md`, and the `rebuild` main-branch reference.
- Only files created/updated by this review: the two copies of this report (target worktree and main workspace).

## 0. Verdict

**PASS (re-review 2026-10-30).**

The original B-01/B-02 blockers are **RESOLVED**. Native Gemini non-stream now calls `generation_service.generate(api_format="gemini")`; native Gemini stream consumes `generation_service.stream_generation(api_format="gemini")`; the duplicate acquire/retry loops are gone. ATG now applies to native Gemini `prefer`/`required` through the shared service, including injection, stream unwrapping, history purge, and PG-07 guard. No open blocker remains. All prior non-blocking N/Q items are preserved below; N-15/N-16 are added and Q-11 is marked resolved.

## 1. Independent gates

Commands were run in the target worktree.

| Gate | Result | Notes |
|---|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 720 passed, 1 skipped in 9.39s** | Re-review count matches the expectation. Both failures are the pre-existing Windows environment failures documented in earlier reviews: `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` (CloakBrowser not installed) and `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly` (`bun.CMD` reports `\n was unexpected at this time`). The skip is the POSIX-only watchdog test. Initial review count was 713 passed; the 7 new tests are the native Gemini/ATG and generation-service spy tests. |
| `uv run ruff check src tests` | **All checks passed** (exit 0) | Matches worker claim. |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations** (exit 0) | Matches worker claim. |
| `git diff --check` | **exit 0** | Only Git LF/CRLF informational warnings; no whitespace errors. |
| Targeted re-review: `test_api_service_gemini`, `test_gemini_atg_native`, `test_api_responses`, `test_generation_routing`, `test_openai_pg08_e2e`, `test_atg_integration` | **45 passed in 0.55s** | Verifies spy calls, native ATG, stream pre-read/retry, and release. |
| Targeted re-review: `test_format_openai`, `test_toolshape_and_signature`, `test_signature_cache`, `test_pg08_lenient_history`, `test_token_count_routes`, `test_token_counter` | **59 passed in 0.75s** | Verifies OpenAI/PG-08/signature/toolshape/token paths remain green. |
| `git status --short` | 12 tracked modified files + 24 untracked paths (7 source entries, 16 tests, 1 report) | All entries are B5b-related; no UI/frontend/static/build files. No staged changes. |
| Root `config.yaml` | `git ls-files config.yaml` is empty; `/config.yaml` is ignored | Root config remains untracked. `defaults/config.yaml` is tracked. |

## 2. Blockers

### B-01 — Native Gemini generation bypasses the protocol-neutral generation service [RESOLVED 2026-10-30]

**Original finding (historical):** the Gemini handler had independent non-stream/stream retry loops and did not consume `generation_service`.

**Resolution evidence:**

- `src/aistudio_api/application/api_service_gemini.py:24-25` imports `generation_service` / `StreamEvent`; the file no longer imports `RouteContext` or performs `pipeline.begin` / `pipeline.acquire`.
- Non-stream: `handle_gemini_generate_content` calls `generation_service.generate(..., api_format="gemini")` at `src/aistudio_api/application/api_service_gemini.py:143-148`.
- Stream: `_build_gemini_streaming_response` consumes `generation_service.stream_generation(..., api_format="gemini")` at `src/aistudio_api/application/api_service_gemini.py:309-314`.
- A file-wide search for `pipeline.acquire` / `pipeline.begin` / `handle_error` / `record_success` in `api_service_gemini.py` returns no matches; the only `while True` is the regex loop in `clean_upstream_error_message`, not a retry loop.
- `generation_service.generate` / `stream_generation` own acquisition, retry/switch, empty retries, ATG, and release: `src/aistudio_api/application/generation_service.py:145-279`, `:292-500`.
- `rebuild` main-reference expectation is now met (`api_service_gemini.py:21-26,198-203,236-241` in the main workspace).
- Tests spy the service calls: `tests/unit/test_api_service_gemini.py:212-283` asserts `model_path`, `api_format="gemini"`, and the same pipeline object for both non-stream and stream; `tests/unit/test_gemini_atg_native.py` drives the native handler through the shared service.

### B-02 — ATG is silently absent on native Gemini requests [RESOLVED 2026-10-30]

**Original finding (historical):** ATG was wired only in `generation_service` and therefore only applied to OpenAI.

**Resolution evidence:**

- Native Gemini passes `api_format="gemini"` into the shared service, so `_create_atg` resolves `features.atg.mode_by_format.gemini` / global mode (`generation_service.py:76-84`; `atg/config.py:43-58`).
- ATG wrapping happens once before normalization: `generation_service.py:158-161`, `:308-312`; injection/unwrap uses the same `AtgProcessor` for Gemini and OpenAI.
- Native non-stream ATG `prefer` injects the synthetic tool, returns body text, preserves `responseId`, and releases the attempt: `tests/unit/test_gemini_atg_native.py:90-133`.
- Native non-stream history purge removes stale synthetic `functionCall`/`functionResponse` pairs and keeps the real user turn: `tests/unit/test_gemini_atg_native.py:135-203`.
- Native stream ATG `prefer` injects, side-buffers the preamble, unwraps the synthetic call to body content, maps finish to STOP, and releases: `tests/unit/test_gemini_atg_native.py:206-248`.
- Native `required` missing synthetic call raises the reference `SyntheticRequiredError` non-stream and surfaces the expected SSE error after text: `tests/unit/test_gemini_atg_native.py:251-313`.
- No double wrapping: one `_wrap_atg_request` call per generation; client synthetic guard remains in `atg/client_patterns.py` and `generation_service.py:102-109`.
- No global timeout/watchdog was added to ATG.
- `test_atg_config.py` covers `mode_by_format` resolution, including Gemini.

## 3. Scope evidence

### 3.1 Generation chain — PASS (B-01 resolved)

- `generation_service.py` acquires `RouteAttempt` handles from `RoutingPipelineProtocol` and releases in `finally` on success, failure, switch, and cancellation: `src/aistudio_api/application/generation_service.py:136-143`, `:271-273`, `:483-485`.
- Tests cover same-attempt retry, switch release, failure release, normal stream termination, and cancellation release: `tests/unit/test_generation_routing.py:40-233`.
- Native Gemini non-stream/stream both route through the shared service: `api_service_gemini.py:143-148`, `:309-314`.
- Native Gemini stream pre-reads until the first renderable event, skipping `routing_wait` and metadata (`response_id`, `finish_reason`, `thought_signature`, non-terminal usage are buffered by `stream_generation`): `api_service_gemini.py:315-339`; `generation_service.py:379-417`, `:494-500`.
- Pre-first failures become real HTTP/JSON: the pre-reader re-raises domain errors (`api_service_gemini.py:332-337`), and app handlers render 400/401/403/429/502/503 via `error_payload` / `classify_gemini_error_payload` (`api/app.py:247-315`, `api/error_mapping.py:56-132`). Generic non-domain failures such as pre-first `SyntheticRequiredError` are not formatted by the Gemini pre-reader; see N-16.
- Post-first failures become SSE errors: `api_service_gemini.py:341-351`.
- Existing PG-02 metadata-only retry and pre-stream 400 tests still pass: `tests/unit/test_api_service_gemini.py:167-208`, `tests/unit/test_api_responses.py:239-293`, `tests/unit/test_routes_gemini.py:157-193`.

### 3.2 OpenAI surface — PASS

- Routes: `POST /v1/chat/completions`, `GET /v1/models`, `GET /v1/models/{model_id:path}`: `src/aistudio_api/api/routes_openai.py:78-194`.
- API-key auth: the router is included with `Depends(require_api_key)` at `src/aistudio_api/api/app.py:234`; the empty-key/deny behavior remains in `src/aistudio_api/api/dependencies.py:73-135`.
- `features.formats.openai` gate: `require_openai` -> `require_format_enabled("openai")` at `src/aistudio_api/api/routes_openai.py:46-48`, `src/aistudio_api/api/format_errors.py:70-115`.
- Bare model IDs: `strip_models_prefix` at `src/aistudio_api/application/format_converters/common.py:30-31`; `/v1/models` projects bare IDs via `project_model_ids` at `src/aistudio_api/api/routes_openai.py:160-175`.
- Non-stream response: role/content/reasoning_content/tool_calls/finish/usage/images are rendered at `src/aistudio_api/application/format_converters/openai_response.py:64-99`.
- SSE: role/content/reasoning_content/tool_calls/finish/usage/[DONE] at `src/aistudio_api/application/format_converters/openai_response.py:102-218`.
- Pre-first-event real HTTP status: `routes_openai.py:116-133`.
- Protocol-aware OpenAI error shape: `src/aistudio_api/api/format_errors.py:127-220`, plus app-level handlers at `src/aistudio_api/api/app.py:241-315`.
- OpenAI/PG-08/signature/toolshape/token targeted tests: 59 passed in the re-review.
- Caveat: the OpenAI `id` is a locally generated `chatcmpl-*`; `GenerationOutcome.response_id` is not exposed (N-13/Q-06).

### 3.3 PG-08 / PG-01 / PG-04 — PASS (with an upstream-verification condition)

- PG-08 lenient history is implemented in `src/aistudio_api/application/format_converters/openai_request.py:29-61`, `:217-323`:
  - missing/duplicate tool-call ids are synthesized (`:249-262`);
  - missing/unknown ids match pending calls by declared name or last pending (`:272-287`);
  - isolated/extra tool outputs become legal `functionResponse` parts (`:290-323`);
  - malformed `arguments` text becomes `{"content": ...}` (`:64-86`);
  - non-object tool output becomes `{"result": ...}` (`:89-128`).
- PG-01 signature fallback is produced in the converter (`openai_request.py:259-268`) and in native normalization (`src/aistudio_api/application/chat_service.py:720-734`, `:790-796`).
- PG-04 Struct coercion is applied in `chat_service.py:800-820` (`coerce_function_response`, `coerce_function_call_args`).
- Dedicated tests cover missing/duplicate ids, unknown ids, isolated outputs, extra outputs, prompt-in-tool-content, and sanitized warnings: `tests/unit/test_pg08_lenient_history.py:49-210`.
- The dedicated end-to-end test crosses request -> converter -> PG-08/PG-01/PG-04 -> `generation_service` -> ATG/native branch -> SSE: `tests/unit/test_openai_pg08_e2e.py:152-223`. It asserts the fabricated prompt and tool output survive, the dummy signature is attached, the tool response is a Struct-shaped dict, and SSE ends with `[DONE]`.
- Caveat: this E2E uses `ScriptedPipeline`/`FakeHandle`, not the real `RoutingPipeline`/`InstancePool`; see N-01/Q-09. Real-upstream acceptance of mismatched/undeclared tool ids is not proven offline; see Q-02.
- B5a PG-01..PG-04 behavior is unchanged by the B-01/B-02 fix; `test_api_responses.py` edits are signature adaptation only, and all B5a tests remain green.

### 3.4 ATG — PASS (B-02 resolved)

- Full module port exists: `config.py`, `injector.py`, `extractor.py`, `stream_extractor.py`, `stream_decoder.py`, `repair.py`, `client_patterns.py`, `history_purge.py`, `model_filter.py` under `src/aistudio_api/application/atg/`.
- The port is functionally identical to the main-branch reference except `client_patterns.py` adds `parametersJsonSchema` / alias support for PG-07.
- `off/prefer/required`: `atg/config.py:30-58`, `atg/__init__.py:41-90`; native Gemini resolves through `mode_by_format.gemini` (`atg/config.py:43-58`; `defaults/config.yaml:69-74`).
- Five-signal guard: `atg/client_patterns.py:120-227`; tests cover all five at `tests/unit/test_atg_client_patterns.py`.
- History purge: `atg/history_purge.py:15-51`, wired at `atg/__init__.py:61-63`; native Gemini history purge is now covered by `tests/unit/test_gemini_atg_native.py:135-203` (N-02 is superseded for native coverage, but the module lacks a dedicated unit test).
- Injector, model filter, extractor, stream decoder/unwrapper, repair, empty retries: implemented and covered by `test_atg_injector.py`, `test_atg_model_filter` cases in `test_atg_config.py`, `test_atg_extractor.py`, `test_atg_stream.py`, `test_atg_repair.py`.
- Required mode: explicit `SyntheticRequiredError` at `atg/extractor.py:188-195` and `atg/stream_extractor.py:158-165`; native Gemini non-stream/stream required paths are covered by `test_gemini_atg_native.py:251-313`.
- No double wrapping: client synthetic guard at `atg/client_patterns.py:136-190` and one `_wrap_atg_request` call at `generation_service.py:158-161`, `:308-312`; tests at `test_atg_injector.py` and `test_atg_integration.py:178-193`.
- No global deadline: no deadline/timeout/watchdog code in `application/atg/`.
- E5 ANY + `allowedFunctionNames`: injector merges the synthetic name at `atg/injector.py:173-187`; tests at `test_atg_injector.py` and `test_atg_integration.py:197-246`, `:351-369`.
- PG-07 `parametersJsonSchema`: guard reads it at `atg/client_patterns.py:91-117`; test at `test_atg_client_patterns.py`.
- OpenAI integration: `test_atg_integration.py` and `test_openai_pg08_e2e.py:170-188` pass.
- Native Gemini integration: `test_gemini_atg_native.py` passes for `prefer`/`required`, non-stream/stream, injection, unwrap, history purge, and release.
- Required errors after stream text are SSE, not pre-HTTP: `generation_service.py:441-447`, `:453-469`; `test_openai_pg08_e2e.py:192-205` and `test_gemini_atg_native.py:281-313` document 200 + partial + error. This does not violate the “pre-first-event” rule (the error occurs after data), but it remains a client-visible condition (Q-01).

### 3.5 Token count — PASS (offline shape condition)

- Gemini route `POST /v1beta/{model_path:path}:countTokens`: `src/aistudio_api/api/routes_gemini.py:48-61`.
- `token_counter.py` packs system/tools/conversation into one synthetic prompt: `src/aistudio_api/application/token_counter.py:54-135`.
- Upstream `CountTokens` call reuses the live instance context and `active_requests` scope: `src/aistudio_api/infrastructure/gateway/instance_client.py:173-221`; route release in `token_counter.py:198-206`.
- No official API key is generated, echoed, or logged; logs contain format/model/contents count/total tokens only: `token_counter.py:160-180`.
- Tests: `tests/unit/test_token_counter.py:31-174`, `tests/unit/test_token_count_routes.py:29-83`; targeted re-review passed.
- Condition: the real upstream CountTokens JSPB response shape is inferred from a fixture; see Q-03/Q-10.

### 3.6 Signature producer and `[toolshape]` — PASS

- Startup configures `data/signature_cache.json`: `src/aistudio_api/api/app.py:96`.
- `put()` ignores empty id/signature and persists bounded entries: `src/aistudio_api/application/signature_cache.py:56-67`; defaults 30 days / 4096 entries at `:29`, `:34`.
- Non-stream/stream producers call `record_function_call_signatures` only for non-empty id+signature: `src/aistudio_api/application/signature_cache.py:161-180`, called from `generation_service.py:238`, `:395`.
- No signature or tool content is logged by the producer.
- `[toolshape]` is emitted only on the first normalize and only when the request has tools/tool history: `src/aistudio_api/application/chat_service.py:686-696`, `:1033-1041`; retry suppression via `toolshape_emitted` in `generation_service.py:165-180`, `:316-347`.
- Redaction: `_toolshape_summary` logs counts/hashes only (`chat_service.py:650-685`); tests assert secrets are absent and pure text emits nothing: `tests/unit/test_toolshape_and_signature.py:42-101`.
- Signature tests: `tests/unit/test_toolshape_and_signature.py:104-178`, `tests/unit/test_signature_cache.py`; targeted re-review passed.
- Non-blocking gaps: N-06/N-07.

### 3.7 Config / scopes — PASS

- `features.formats` and `features.atg` are in tracked defaults: `defaults/config.yaml:59-89`; mirrored in `src/aistudio_api/infrastructure/gateway/model_defaults.py:764-789`; loaders at `:1304-1322`.
- Root `config.yaml` remains untracked (`git ls-files config.yaml` empty; `.gitignore:15`).
- No UI/frontend/static/build changes in `git status`.
- No D10 statistics page changes.
- No B6 work started.
- No rental/lease/reservation/pinned/blue-green machinery added; `generation_service.py` explicitly uses only `RouteAttempt` handles.
- No Claude/Responses routes or converters added. Dormant Claude/Responses error/usage helpers exist in `format_errors.py` / `format_converters/common.py` (N-12); they are not exposed as adapters.

### 3.8 Tests — PASS on counts and no weakening

- Exact full-suite count matches the re-review expectation: 720 passed / 2 documented Windows failures / 1 skip.
- Native Gemini refactor tests: `tests/unit/test_gemini_atg_native.py` (new) and `tests/unit/test_api_service_gemini.py:212-283` (service spies).
- `tests/unit/test_api_responses.py` changes only adapt the private helper’s call signature (`pipeline`, `model_path`); the retry/pre-read assertions remain.
- `tests/unit/test_routes_models.py:51-62` remains the legitimate OpenAI-shape update (adds unauthorized 401; no weakening).
- No relevant test was deleted. New B5b tests are additive (16 test files).
- Test-quality caveats: N-01 (E2E uses a scripted pipeline; also applies to native ATG tests), N-02 (history purge lacks a dedicated module test), N-06 (signature cache defaults/empty coverage), N-15 (pre-first 401/429/502 not directly route-tested), N-16 (pre-first generic ATG errors not JSON-shaped).

## 4. Non-blocking findings (N)

- **N-01 — E2E does not exercise the real pool.** `tests/unit/test_openai_pg08_e2e.py:24`, `:155-157` and `tests/unit/test_gemini_atg_native.py:31-36` use `ScriptedPipeline` / `FakeHandle`; they prove the route/service seam but not `RoutingPipeline` + `InstancePool` + real `active_requests` accounting. Add a FakeManager/real-pipeline variant before claiming pooled E2E.
- **N-02 — history purge has no dedicated test.** `atg/history_purge.py` is wired at `atg/__init__.py:61-63`; native Gemini history purge is exercised indirectly by `test_gemini_atg_native.py`, but no test file directly references `purge_synthetic_history`.
- **N-03 — image-only responses look empty to the retry loop.** `generation_service.py:68-73` treats a candidate with no text/thinking/function_calls as empty even when `images` exist; `:181-226` may retry a valid image-only response once.
- **N-04 — token packing drops non-text system/tool parts.** `token_counter.py:54-73` only counts text/function-call/function-response parts; system/tool inline images or file parts are omitted.
- **N-05 — token counts are logged at INFO.** `token_counter.py:175-180` logs `totalTokens`; not secret, but user prompt size is observable in logs.
- **N-06 — signature-cache tests do not assert defaults/edge cases.** Constants are correct (30 days / 4096, `signature_cache.py:29,34`), but tests do not assert TTL expiry or the default 4096 bound; `record_function_call_signatures` empty cases are covered in `test_toolshape_and_signature.py:140-178`.
- **N-07 — `ThoughtSignatureCache.clear()` does not reset `_path_resolved` or persist deletion.** `signature_cache.py:48-54`, `:85-88`; after `clear()`, a later `put()` may skip file persistence until `configure_path` is called.
- **N-08 — format-gate config errors become 403.** `format_errors.py:70-82` swallows config load errors and returns `False`, so a broken config looks like “format disabled” rather than a server/config error.
- **N-09 — duplicated path classification.** `app.py:241-242` uses a narrower `_is_openai_path` than `format_errors.format_for_path` (`format_errors.py:58-67`); future OpenAI paths could silently receive Gemini error shapes.
- **N-10 — string `"false"` is truthy in the format gate.** `format_errors.py:82` uses `bool(...)`; YAML normally parses `false` as bool, but a string `"false"` would enable the format.
- **N-11 — history purge can leave zero contents.** `history_purge.py:46-47` drops empty contents; a request whose entire history is synthetic then reaches `normalize_gemini_request` with `contents=[]` and fails with “contents is required” (`chat_service.py:738-739`).
- **N-12 — dormant Claude/Responses helpers are in-scope code.** `format_errors.py:17,32-44,58-67,180-226` and `format_converters/common.py:56-62,102-115,130-142` contain `claude_*` / `/v1/messages` / `/v1/responses` error and usage helpers with no routes or converters. They are not active adapters, but they are dead scope creep.
- **N-13 — OpenAI response `id` ignores `outcome.response_id`.** `openai_response.py:64-99` always generates a local `chatcmpl-*`; the stream renderer also keeps a local id (`:105-113`). Upstream `responseId` is only carried in `GenerationOutcome.response_id` and never rendered.
- **N-14 — `/v1/models` test depends on discovery state.** `tests/unit/test_routes_models.py:51-62` uses the real app and real `model_discovery`; the test passed here, but it is environment/cache-sensitive.
- **N-15 — pre-first 401/429/502 route coverage is indirect.** `tests/unit/test_routes_gemini.py:157-193` covers pre-stream 400, `:63-101` covers 403/503, and 401 is covered at auth level (`test_api_auth.py`), but no route-level test raises 401/429/502 from the upstream before the first renderable event. The shared error path is general (`api_service_gemini.py:332-337`; `api/error_mapping.py:56-132`), but parameterized coverage would prove all six statuses.
- **N-16 — Gemini pre-first generic exceptions bypass protocol JSON.** `api_service_gemini.py:315-337` re-raises non-`BrowserTransportError` exceptions during first-event pre-read. A pre-first `SyntheticRequiredError` (ATG `required`, zero content) is not an `AistudioError`, so Starlette/FastAPI returns its default 500 plain-text response instead of a Gemini JSON error; OpenAI pre-read formats all exceptions (`routes_openai.py:116-122`). Catch and render generic pre-first failures with the protocol error mapper, or classify ATG errors explicitly.

## 5. Decision / condition findings (Q)

- **Q-01 — ATG `required` after stream text is SSE, not pre-HTTP.** When upstream emits text and then omits the synthetic call, `StreamUnwrapper.finalize()` raises `SyntheticRequiredError` after content has been yielded (`generation_service.py:441-447`, `:453-469`; `atg/stream_extractor.py:158-165`). The client sees HTTP 200 + partial content + SSE error (`test_openai_pg08_e2e.py:192-205`; native Gemini `test_gemini_atg_native.py:281-313`). Is partial-output-then-error acceptable for `required` mode?
- **Q-02 — Real-upstream PG-08 id/name acceptance is unverified.** The converter can emit a `functionResponse` whose id differs from the earlier `functionCall` id (unknown-id branch, `openai_request.py:272-287`) and a function name with no declaration in `tools`. Offline tests only prove conversion/wire Struct; a real sanitized upstream probe is needed to confirm no 400.
- **Q-03 — CountTokens RPC shape is fixture-based.** `instance_client.py:173-221` assumes `[model, contents]` request and `data[0]` integer response (`test_token_counter.py:139-165`). Confirm against the real upstream before accepting token counts.
- **Q-04 — ATG defaults.** `defaults/config.yaml:69-71` defaults `features.atg.mode` to `off`; `mode_by_format` inherits. Is OpenAI/Gemini ATG intended to stay off by default in B5b, or should the B5b acceptance enable `prefer`?
- **Q-05 — OpenAI format default.** `defaults/config.yaml:59-61` enables `formats.openai` by default and disables Claude/Responses. Confirm that an unconfigured deployment should expose OpenAI without an explicit local config.
- **Q-06 — `response_id` semantics.** If the requirement’s `response_id` means the upstream `responseId`, N-13 is a gap; if it means the OpenAI `id`, the local `chatcmpl-*` is acceptable. Decide before closing B5b.
- **Q-07 — `stream_options.include_usage`.** The terminal SSE chunk always includes `usage` (`openai_response.py:147-174`), regardless of `stream_options.include_usage` (`schemas_openai.py:19`). Confirm whether strict OpenAI compatibility requires honoring it.
- **Q-08 — `required` non-stream status.** `SyntheticRequiredError` is a plain `Exception`, so the OpenAI error classifier returns 500 (`format_errors.py:156-160`; `generation_service.py:222-226`); native Gemini now shares that contract (`test_gemini_atg_native.py:251-277`). Decide whether an explicit 4xx/502 is required.
- **Q-09 — Real-pool E2E requirement.** If B5b closure requires a true `RoutingPipeline` + `InstancePool` end-to-end, N-01 must be fixed before PASS. If the scripted seam is accepted, document that exception.
- **Q-10 — CountTokens packing scope.** `token_counter.py:76-130` packs system/tools/conversation but not `generationConfig`, safety settings, or toolConfig. Confirm the upstream `CountTokens` contract does not require them.
- **Q-11 — ATG scope for Gemini [RESOLVED 2026-10-30].** ATG now applies to native Gemini through `generation_service(api_format="gemini")`; `mode_by_format.gemini` is honored. The original OpenAI-only ambiguity no longer applies.
- **Q-12 — Dormant Claude/Responses helpers.** Remove `claude_*` / `/v1/messages` / `/v1/responses` helpers in `format_errors.py` and `common.py`, or explicitly reserve them for C3. They are not active adapters.
- **Q-13 — Empty-contents after history purge.** Decide whether a request that purges to zero contents should be rejected early with a clear client error or rebuilt with a synthetic user turn (N-11).
- **Q-14 — Empty `toolConfig` counts as tool activity.** `chat_service.py:686-696` treats any non-None `toolConfig` (including `{}`) as tool-related and emits `[toolshape]`; decide whether “tool-related” should require a non-empty config.
- **Q-15 — Signature-cache clear semantics.** Decide whether `clear()` should delete the persisted file and reset path resolution (N-07), or whether tests/reloads must always call `configure_path`.
- **Q-16 — Offline model discovery.** Decide whether `/v1/models` tests must stub `model_discovery` to avoid dependency on live/cached discovery (N-14).

## 6. Scope boundary statement

- No source, test, or config file was modified by this reviewer; only the two report copies were updated.
- No changes were staged or committed; B6 was not started.
- No UI/frontend/static/build changes exist in the worktree.
- No D10 statistics page changes exist.
- No Claude/Responses/OpenAI Responses adapter was activated; only dormant error/usage helpers exist (N-12).
- No lease/reservation/pinned/blue-green machinery was added.
- Root `config.yaml` remains untracked; `defaults/config.yaml` is tracked.
- No real account, IP, or network request was used.

## 7. Report integrity

Both copies of this report are byte-identical:

- Target: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream\docs\dev\rebuild\domains\P5-generation\reviews\REV-B5b-openai-atg-seam.md`
- Main: `D:\LLM\self_programming\aistudio-api\docs\dev\rebuild\domains\P5-generation\reviews\REV-B5b-openai-atg-seam.md`

## 13. B-01/B-02 复审（2026-10-30）

### 13.1 结论

**B-01 RESOLVED. B-02 RESOLVED. Verdict: PASS.**

本次复审在同一未提交 B5b 工作树（HEAD `629614f`）只读复核；未修改源码/测试/配置，未 stage/commit，未启动 B6，未访问真实账号/IP/网络。

### 13.2 代码证据

- **B-01（生成主链）**：`src/aistudio_api/application/api_service_gemini.py:24-25` 引入 `generation_service`；非流式 `:143-148` 调用 `generate(..., api_format="gemini")`；流式 `:309-314` 消费 `stream_generation(..., api_format="gemini")`。该文件不再出现 `pipeline.begin` / `pipeline.acquire` / 独立重试循环；`generation_service.py:145-279`、`:292-500` 独占 acquire/release/retry/empty-retry/ATG。
- **B-02（ATG 原生 Gemini）**：`generation_service.py:76-84` 按 `api_format` 解析 ATG 模式；`:158-161`、`:308-312` 每次只 wrap 一次；`:348-352`、`:453-469` 执行 stream unwrap/finalize；`atg/config.py:43-58` 支持 `mode_by_format.gemini`。原生 Gemini `prefer`/`required` 的非流式与流式均通过 `tests/unit/test_gemini_atg_native.py` 验证。
- **流首事件预读**：`api_service_gemini.py:315-339` 在返回 `StreamingResponse` 之前预读第一个可渲染事件；`routing_wait` 及 metadata（`response_id`/`finish_reason`/`thought_signature`/非终态 usage）由 `stream_generation` 缓冲，不会阻断真实 HTTP 错误。`test_api_service_gemini.py:167-208`（metadata-only switch）、`test_api_responses.py:239-293`（thought_signature 后 204 retry）、`test_routes_gemini.py:157-193`（pre-stream 400 真 HTTP/JSON）通过。
- **流字段保留**：`format_sse_event` / `format_sse_usage` 保留 content/tool_calls/thinking/finishReason/usageMetadata/responseId/thoughtSignature：`api_service_gemini.py:173-263`；`stream_generation` 的终止 usage 携带 `finish_reason`/`response_id`：`generation_service.py:494-500`；`test_generation_routing.py:140-174` 与 `test_gemini_atg_native.py:206-248` 验证。
- **无双重 wrap / 无全局超时**：`generation_service.py` 每次生成只调用一次 `_wrap_atg_request`；ATG 模块无 deadline/timeout/watchdog。
- **OpenAI / B5a 未回归**：`test_format_openai`、`test_toolshape_and_signature`、`test_signature_cache`、`test_pg08_lenient_history`、`test_token_count_routes`、`test_token_counter` 共 59 passed；B5a 的 `test_api_responses.py` 仅改为新 helper 签名，断言保留；`test_api_service_gemini.py` 保留原 retry/release/switch 测试并新增 2 个 service spy。
- **active_requests 释放**：`generation_service.py:271-273`、`:483-485` 在 finally 释放；`token_counter.py:198-206` 同样 finally；native Gemini 非流式/流式测试均断言 `FakeHandle.released is True`。

### 13.3 门禁（本次复审）

- `uv run pytest -q`：**2 failed, 720 passed, 1 skipped in 9.39s**
  - `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`（CloakBrowser 缺失）
  - `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 的 `\n was unexpected at this time`）
  - 二者为既有 Windows 环境失败；skip 为 POSIX-only watchdog。
- 指定复审子集：**45 passed**（Gemini service/native ATG/api responses/generation routing/OpenAI E2E/ATG integration）。
- 指定 B5b 子集：**59 passed**（OpenAI format/PG-08/signature/toolshape/token）。
- `uv run ruff check src tests`：All checks passed。
- `uv run pyright src tests`：0 errors, 0 warnings, 0 informations。
- `git diff --check`：exit 0（仅 LF/CRLF 提示）。
- `git status --short`：12 个 tracked modified + 24 个 untracked 路径（7 个 source 条目、16 个 tests、1 个 report）；无 UI/static/Claude/Responses/B6/D10 变更；根 `config.yaml` 仍未被跟踪（`git ls-files config.yaml` 为空）。

### 13.4 保留的 N/Q

所有原 N-01..N-14、Q-01..Q-16 均保留在 §4/§5；新增 **N-15**（pre-first 401/429/502 缺少直接路由测试）与 **N-16**（pre-first 通用 ATG 异常未走协议 JSON）；**Q-11 已标记 RESOLVED**。Q-01、Q-02、Q-03、Q-08、Q-09 等仍为需要用户/真实上游裁决的非阻断条件，不影响 B-01/B-02 的 RESOLVED 结论。

### 13.5 报告哈希

SHA256 (report body, excluding this hash line): 9467cd3709d8ff43e6dca5a1e883be5c144b4bb0761415e0564a319dbdaaa194

## 14. B5b 四项收尾（2026-10-30）

### 14.1 结论

**N-03、N-13/Q-06、N-16/Q-08、N-09/N-10 已完成；Verdict: PASS。**

本次收尾在同一未提交 B5b 工作树（HEAD `629614f`）进行；未 stage/commit，未启动 B6，未改动主工作区业务代码，未新增 UI/前端/static/Claude/Responses/D10 范围，未访问网络/真实账号，根 `config.yaml` 仍未被跟踪。

### 14.2 变更

- **N-03（image-only 空响应误重试）**：`generation_service._output_is_empty` 将 `images` / `reasoning_images` 视为非空内容；纯图片响应不再触发同账号空响应重试。测试：`tests/unit/test_generation_routing.py::test_generate_image_only_output_is_not_empty`（`images`、`reasoning_images` 两参数，断言只调用上游一次且 handle 释放）。
- **N-13/Q-06（OpenAI 响应 id）**：非流式 `openai_response` 优先使用 `outcome.response_id`，缺失时才回退本地 `chatcmpl-*`；流式 `OpenAIStreamRenderer` 在每个事件上优先采用流内 `response_id`，缺失事件保留当前 id（初始为本地 `chatcmpl-*`）。测试：`test_format_openai.py::test_non_stream_response_fields_finish_reason_and_usage` 改为断言 `resp-1`，新增 `test_non_stream_response_id_falls_back_to_local_id`、`test_stream_prefers_upstream_response_id_and_keeps_local_fallback`、`test_stream_local_id_survives_events_without_response_id`。
- **N-16/Q-08（ATG required 502）**：`classify_gemini_error_payload` 将 `AtgError`（含 `SyntheticRequiredError` 及等价 ATG required 失败）映射为 HTTP 502 / `UNAVAILABLE`；OpenAI 非流式与 pre-first-stream 经 `format_error_response` 返回标准 OpenAI JSON；原生 Gemini 非流式与 pre-first-stream 在 `api_service_gemini` 转换为 `RequestError(502)`，由 app 级 `AistudioError` handler 返回标准 Gemini JSON，不再落入 Starlette 默认 500 纯文本。`_GEMINI_STATUS_BY_HTTP` 补充 502=`UNAVAILABLE`，OpenAI 502 映射为 `api_error`。流式首事件之后抛出的 required 错误仍以 SSE JSON 呈现，分类码同步为 502（`test_gemini_atg_native.py` 已更新）。测试：`test_format_openai.py` 新增 OpenAI 非流式/pre-stream 502 JSON 两例；`test_routes_gemini.py` 新增 Gemini 非流式/pre-stream 502 JSON 两例；`test_gemini_atg_native.py` 非流式改断言 `RequestError.status == 502` 且 cause 为 `SyntheticRequiredError`，流式断言 SSE `\"code\": 502`。
- **N-09/N-10（路径与开关口径）**：`api/app.py::_is_openai_path` 改为直接调用 `format_errors.format_for_path(path) == \"openai\"`，与错误渲染共用同一分类器；`format_errors.format_enabled` 通过 `_config_flag` 将字符串 `\"false\"`、`\"0\"`（去空白、大小写不敏感）以及空串视为 False。测试：`test_format_openai.py::test_is_openai_path_uses_shared_format_classifier`（monkeypatch 共享分类器证明委托）、`test_format_enabled_treats_string_false_and_zero_as_disabled`（`\" FALSE \"`/`\"0\"` 为 False，`\" True \"` 为 True）。

### 14.3 测试与门禁

- 新增聚焦测试 11 个（N-03 2、N-13 3、N-16 4、N-09/N-10 2）；同步更新 3 个既有断言（OpenAI 非流式 id、Gemini required 非流式异常类型、Gemini required 流式 SSE 码）。
- `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q`：**2 failed, 731 passed, 1 skipped in 9.17s**
  - 既有 Windows 环境失败：`tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`（CloakBrowser 缺失）、`tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 报 `\n was unexpected at this time`）。
  - 既有 POSIX-only watchdog skip。
- `uv run ruff check src tests`：**All checks passed**。
- `uv run pyright src tests`：**0 errors, 0 warnings, 0 informations**。
- `git diff --check`：**exit 0**（仅 LF/CRLF 提示）。
- `git status --short`：13 个 tracked modified + 24 个 untracked 路径（7 个 source 条目、16 个 tests、1 个 report）；无 UI/static/Claude/Responses/B6/D10 变更；根 `config.yaml` 仍未被跟踪（`git ls-files config.yaml` 为空）。

### 14.4 范围与保留项

- PG-01..PG-04、ATG（off/prefer/required、注入/解包/历史清理/流式跨 chunk/空响应重试/E5/PG-07）、signature_cache、`[toolshape]`、`active_requests`、Gemini/OpenAI 既有行为保持不变。
- N-03、N-09、N-10、N-13 关闭；N-16/Q-08 关闭 502 口径。其余 N/Q（如真实上游 PG-08 接受度 Q-02、CountTokens 形状 Q-03、post-first required 的 Q-01、真实 pool E2E Q-09）作为非阻断条件保留。
- 本报告两份副本（目标工作树与主工作区）保持字节一致。
