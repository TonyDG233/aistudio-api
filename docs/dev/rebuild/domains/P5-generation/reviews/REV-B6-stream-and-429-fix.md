# REV-B6-stream-and-429-fix — final independent read-only offline audit (5 micro-fixes)

- Audit date: 2026-10-31 (UTC), fully offline.
- Target worktree: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`.
- Branch / HEAD: `rebuild-upstream` / `da937b6 chore(rebuild): 合并上游 852de53 并吸收 403、流式与启动修复`.
- Audited delta: the **25 tracked uncommitted files** in the worktree plus this untracked report. This final round supersedes the earlier 790-pass version of this report; it adds the five adjudicated micro-fixes (Q3/N29, Q11/N25, Q5/N19, Q4/N20, N18/Q9/N22/N23).
- Method: read-only diff/source/test review, offline `pytest`, `ruff`, `pyright`, `git diff --check`, `git diff --cached --check`, YAML comparison, and line-anchor inspection. No `main.py server`, no Chromium/CloakBrowser, no network request, no source/test/config modification, no staging, no commit. The only files written are this report and its byte-identical main-repo copy.

## 0. Verdict

**PASS — no blocker inside this audit scope.**

All five micro-fixes are implemented in the real request/selection/config paths and are covered by tests that pass offline. Gate count is exactly the expected **813 passed, 2 failed (documented pre-existing Windows environment failures), 1 skipped**. The two failures are in files untouched by this delta.

Scope caveat unchanged: B6 live acceptance is still **FAIL/PARTIAL** in `docs/dev/evidence/B6/results.json:78`. This audit verifies the fixes offline and cannot replace the authorized live re-verification.

## 1. Worktree state and diff scope

`git status --porcelain=v1` at audit start (`??` = this report):

```text
 M defaults/config.yaml
 M docs/dev/evidence/B6/diagnostics.json
 M docs/dev/evidence/B6/results.json
 M docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md
 M src/aistudio_api/api/error_mapping.py
 M src/aistudio_api/api/format_errors.py
 M src/aistudio_api/api/routes_instances.py
 M src/aistudio_api/application/account_rotator.py
 M src/aistudio_api/application/api_service_gemini.py
 M src/aistudio_api/application/instance_manager.py
 M src/aistudio_api/application/model_access.py
 M src/aistudio_api/application/routing_pipeline.py
 M src/aistudio_api/domain/instances.py
 M src/aistudio_api/infrastructure/gateway/instance_pool.py
 M src/aistudio_api/infrastructure/gateway/model_defaults.py
 M tests/unit/routing_fakes.py
 M tests/unit/test_account_routing.py
 M tests/unit/test_features_config.py
 M tests/unit/test_format_error_routing.py
 M tests/unit/test_instance_manager.py
 M tests/unit/test_model_access.py
 M tests/unit/test_routes_gemini.py
 M tests/unit/test_routes_instances.py
 M tests/unit/test_routing_pipeline.py
 M tests/unit/test_token_count_routes.py
?? docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-stream-and-429-fix.md
```

- 25 modified tracked files; 1 untracked file (this report).
- `git diff --cached --stat` is empty: **nothing staged**.
- HEAD is still `da937b6`; no commit was created by this audit.
- `git diff --check` and `git diff --cached --check` exit 0 (only LF→CRLF advisory warnings).
- Root `config.yaml` is untracked and ignored in both repositories:
  - worktree: `git ls-files config.yaml` empty, `git check-ignore` → `.gitignore:15:/config.yaml`;
  - main repo: `git ls-files config.yaml` empty, `git check-ignore` → `.gitignore:62:/config.yaml`.
- Root `config.yaml` and `defaults/config.yaml` `instances` blocks are semantically identical (`yaml.safe_load`): `[{count:1,surface:chat,only_tier:Free},{count:1,surface:chat,min_tier:Pro}]`.
- Tracked working-tree diff (all 25 modified files, excluding this untracked report): 121,445 bytes, SHA256 `1EAD5953FCF4776F324A9D0F63FB9BB18C625074F3EB964C7A93357C05BAB872`.

## 2. Fix 1 — Q3/N29: `max_tier`-only paid lower bound is Pro

### 2.1 Implementation

`src/aistudio_api/application/model_access.py:157-165`:

- `max_tier` alone with a paid upper bound now returns `TierSelector("paid", None, "Pro", upper)` — the lower bound is **Pro**, not Plus.
- `max_tier: Plus` alone is rejected (`tier_rank(upper) < tier_rank("Pro")`, `:160-164`).
- A paid interval whose upper bound is below Pro is rejected (`:183-187`, e.g. `min_tier: Plus` + `max_tier: Plus`).
- `max_tier: Free` remains an exact Free selector (`:158-159`).

`_normalize_instance_specs` preserves the explicit upper bound and writes `min_tier: "Pro"` for this case (`model_defaults.py:362-375`), so the normalized spec is `{"count", "surface", "min_tier": "Pro", "max_tier": <upper>}`.

### 2.2 Tests

- `tests/unit/test_model_access.py:87-100` — `resolve_tier_selector(max_tier="Ultra")` has `min_tier == "Pro"`, `lane == "paid"`; `max_tier="Plus"` and `min_tier=Plus,max_tier=Plus` raise `TierSelectorError` with `Pro`.
- `tests/unit/test_features_config.py:232-245` — `max_tier: Ultra` normalizes to `min_tier: Pro, max_tier: Ultra`.
- `tests/unit/test_features_config.py:248-260` — `max_tier: Plus` alone is a `ConfigValidationError` matching `Pro`.
- `tests/unit/test_features_config.py:263-276` — `min_tier: Plus` + `max_tier: Plus` is rejected matching `Pro`.

### 2.3 Assessment

**PASS.** Q3/N29 resolved with tests; the behavior is a deliberate hard error rather than silent Plus.

## 3. Fix 2 — Q11/N25: per-slot one-rebind cap removed

### 3.1 Implementation

- The earlier `rebound_instances` duplicate guard is **gone**: 0 occurrences in `src/` or `tests/`.
- `RouteContext.attempted_accounts` (`routing_pipeline.py:193`) is populated on **every** acquire (`:560`).
- `_try_rebind` (`routing_pipeline.py:737-866`):
  - exclusions = `failed_accounts | attempted_accounts | {attempt.account_id}` plus accounts bound to other instances (`:771-790`), so an account is never selected twice;
  - release-before-reassign and the `active_requests == 0` check are preserved (`:792-801`);
  - bounded selector loop `range(len(exclusions) + 1)` (`:803`);
  - each successful rebind records the switch budget (`:853`) and refreshes the candidate (`:836-851`);
  - the docstring explicitly states there is **no per-slot one-rebind cap**: one slot may rebind A → B → C (`:761-764`).
- Loop/duplicate protection is now `attempted_accounts`/`failed_accounts` plus the per-request `max_switches` budget; `_switch_or_raise` only enters the rebind path while `total_attempts < max_retries and budget.can_switch()` (`routing_pipeline.py:891-901`).
- `reuse = context.next_candidate() is None` (`:899`) still distinguishes single-slot (re-take the healed slot) from multi-slot (keep the current request on another candidate).

### 3.2 Tests

- `tests/unit/test_routing_pipeline.py:373-501` — **single Free slot, successive 429s**: `free-a` → `free-b` → `free-c`; asserts `reassign_calls == [("inst-1","free-b"), ("inst-1","free-c")]`, unique rebind accounts, `budget.switches == 2`, `attempted_accounts == {"free-a","free-b","free-c"}`, and that exclusions grow `{"free-a"}` then `{"free-a","free-b"}`.
- Same test `:490-500` — a third 429 with no budget left does not loop or rebind; the following `handle_error` raises `SurfaceSwitchExhaustedError` (503).
- `tests/unit/test_routing_pipeline.py:268-369` — multi-slot immediate rebind preserved: `inst-1` is healed to `free-c` while the request continues on `inst-2`; no slot keeps `free-a`; active requests 0.
- `tests/unit/test_routes_gemini.py:463-511` and `:513-537` — full HTTP route single-slot rebind and no-alternate 503.
- Focused run: 11 micro-fix tests passed (includes these).

### 3.3 Assessment

**PASS.** Q11/N25 resolved: the cap is removed, A→B→C works within `max_switches=2`, duplicate accounts are impossible via the exclusion set, and exhaustion is still a 503 (no infinite loop).

## 4. Fix 3 — Q5/N19: non-string/unknown selectors are hard errors

### 4.1 Implementation

- `model_access._selector_label` (`model_access.py:96-116`) raises `TierSelectorError` (a `ValueError`) for:
  - any non-string value except `None` (`:105-108`, message names the field and concrete type);
  - any non-empty string whose canonical tier is unknown (`:111-115`, message names the field and value).
- `_normalize_instance_specs` validates **each** selector before resolution and raises `ConfigValidationError` for non-string and unknown values even when a valid sibling exists (`model_defaults.py:299-325`).
- Empty/whitespace strings remain "not provided"; the "at least one selector" rule still applies (`:326-336`).

### 4.2 Tests

- `tests/unit/test_model_access.py:115-127` — `only_tier=1`, `min_tier=[1]`, `max_tier=True`, `only_tier="Diamond"` raise; `only_tier="Pro", min_tier=1` raises for `min_tier` (valid sibling does not mask it).
- `tests/unit/test_features_config.py:279-310` — parametrized numeric / list / bool / typo (`Diamond`) for each of `only_tier`, `min_tier`, `max_tier`; all are `ConfigValidationError` naming the field.
- `tests/unit/test_features_config.py:313-326` — `only_tier: Pro` + `min_tier: 1` raises for `min_tier`.

### 4.3 Assessment

**PASS.** Q5/N19 resolved with the requested numeric/list/bool/typo and valid-sibling cases.

## 5. Fix 4 — Q4/N20: all Free selectors share one full selector and merge key

### 5.1 Implementation

`model_defaults._normalize_instance_specs` (`model_defaults.py:345-354`):

- every Free selector (`only_tier: Free`, legacy `min_tier: Free`, `max_tier: Free`, `min=max=Free`) normalizes to the same full spec `{"count", "surface", "only_tier": "Free", "min_tier": "Free", "max_tier": "Free"}`;
- the merge key is always `(surface, "Free", "Free", "Free")`, so counts across syntaxes merge into one slot spec.
- `InstanceSpec.from_mapping` accepts this full Free form (`instance_pool.py:72-96`); its `selector` property re-resolves to Free..Free (`:98-106`), and `activate` enforces it (`:394-399`).
- `defaults/config.yaml` and `_default_config` use `only_tier: Free` (`model_defaults.py:960-964`), while legacy `min_tier: Free` from older files still normalizes to the same key.

### 5.2 Tests

- `tests/unit/test_features_config.py:329-354` — `only_tier: Free` (count 1) + `min_tier: Free` (count 2) + `max_tier: Free` (count 1) produce exactly **one** spec with `count: 4` and `only_tier/min_tier/max_tier == "Free"`.
- `tests/unit/test_features_config.py:131-164` — legacy `min_tier: Free` still normalizes to the same full Free spec and merges duplicates.
- `tests/unit/test_features_config.py:167-190` — `only_tier: Free` normalization; `only_tier: Pro` stays exact.
- `tests/unit/test_model_access.py:103-112` — the normalized full Free selector is consistent with `only_tier: Free`.

### 5.3 Assessment

**PASS.** Q4/N20 resolved; the merge key is now identical for all three Free spellings.

## 6. Fix 5 — N18/Q9/N22/N23: rank property, API fields, docstrings

### 6.1 Implementation

- N18: `_order` (`routing_pipeline.py:395-440`) now sorts through `RouteCandidate.from_instance` and uses `candidate.account_tier_rank` (property at `:164-167`, key at `:426-435`). The key is `(in_flight, account_tier_rank, total_requests, model_requests, last_used, warm, sticky, number)`; `account_tier_rank` is the second component, exactly as specified.
- Q9/N22: `InstanceResponse` now carries `only_tier`, `min_tier`, `max_tier` (`routes_instances.py:20-31`, serialized at `:51-64`); `Instance` carries `only_tier`/`max_tier` (`domain/instances.py:29-34`); `InstanceManager._view` fills them from the normalized selector (`instance_manager.py:1031-1047`). `/api/instances` therefore exposes all three fields.
- N23: `get_configured_instance_specs` docstring now documents `only_tier`/`min_tier`/`max_tier`, the Pro default, Free normalization and hard errors (`model_defaults.py:1508-1521`); `_normalize_instance_specs` has the matching docstring (`:261-279`).

### 6.2 Tests

- `tests/unit/test_routing_pipeline.py:229-243` — `_order` returns `inst-pro` before `inst-ultra` using the candidate rank (Ultra has better LRU, so the rank must dominate).
- `tests/unit/test_routing_pipeline.py:209-227` — full `begin()` Pro-before-idle-Ultra.
- `tests/unit/test_routes_instances.py:18-32` — `only_tier`/`min_tier`/`max_tier` are in the expected key set.
- `tests/unit/test_routes_instances.py:144-160` — `/api/instances` returns `only_tier == "Free"`, `min_tier == "Free"`, `max_tier == "Free"` and no `channel`/`context`/`pages`.
- `tests/unit/test_features_config.py:357-381` — normalized specs are consumable by `InstanceSpec`.

### 6.3 Assessment

**PASS.** N18, Q9, N22 and N23 resolved; the rank property is now the live sort input, and the API/docstrings match the new schema.

## 7. Preservation of prior B6/routing fixes

- Routing balance: `_order` keeps `sticky` as the **seventh** tie-breaker (`routing_pipeline.py:425-435`); `test_two_idle_free_slots_distribute_sequential_requests` (`test_routing_pipeline.py:169-206`) still distributes 2/2, and `test_warm_ready_is_only_a_tie_breaker` (`:85-98`) still passes.
- Multi-slot immediate rebind: `test_429_dead_slot_is_rebound_while_request_continues_on_other_slot` (`:268-369`) passes.
- Pro-before-Ultra: `:209-227` and `:246-265` pass.
- 429 policy: same-account retry → switch → 503 (`:565-607`), zero budget (`:672-694`), no switch after streamed data (`:609-627`).
- Paid 403 / `quota_scope` / single page: `test_paid_tier_required_error_raises_without_switch` (`:797-825`), `test_account_rotation_concurrency.py:80-155`, `test_browser_session.py:360`, `test_session_lifecycle.py:156` all pass in the full suite.
- Native stream: `api_service_gemini.py:309-360`; empty upstream raises real 502 (`test_routes_gemini.py:278-303`), first-frame completion and empty-first-chunk skip pass (`:201-276`).
- countTokens (`test_token_count_routes.py`), OpenAI/Claude 403 field preservation (`test_format_error_routing.py`), PG-02 pre-stream status (`test_routes_gemini.py:306-342`), B1–B6 discovery/startup/single-page tests all pass in the full suite.
- Focused preserved-file run (`test_routes_gemini.py`, `test_token_count_routes.py`, `test_format_error_routing.py`, `test_api_service_gemini.py`, `test_instance_pool.py`, `test_botguard_bootstrap.py`): **47 passed**.

## 8. Scope and offline constraints

- No UI/frontend file changed (`git diff --stat -- web` empty; no `web/` entry in `git status`).
- No browser-infrastructure source changed (`src/aistudio_api/infrastructure/browser` absent from the delta).
- `routes_instances.py` change is additive API serialization only; no route/service behavior was added.
- No `main.py`/uvicorn process was started by this audit; ports 8080 and 9222 are **not** listening (`Test-NetConnection` false for both).
- No Chromium/CloakBrowser process was started by this audit.
- Root `config.yaml` remains untracked/ignored in both repositories (see §1).
- Nothing was staged or committed.

## 9. Exact gates and status

All commands were run offline from the target worktree with `uv`:

```text
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
=> 2 failed, 813 passed, 1 skipped in 11.33s   (matches expected 813/2/1)

uv run ruff check src tests
=> All checks passed! (exit 0)

uv run pyright src tests
=> 0 errors, 0 warnings, 0 informations (exit 0)

git diff --check
=> exit 0 (LF→CRLF advisory warnings only)

git diff --cached --check
=> exit 0; git diff --cached --stat empty (0 staged)

Focused micro-fix tests (5 fixes)
=> 11 passed in 0.64s

Focused preserved B6 files
(test_routes_gemini.py, test_token_count_routes.py,
 test_format_error_routing.py, test_api_service_gemini.py,
 test_instance_pool.py, test_botguard_bootstrap.py)
=> 47 passed in 0.98s
```

The 2 pytest failures are the documented pre-existing Windows environment failures, in files untouched by this delta:

1. `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` — Windows `Path("/opt/cloakbrowser")` becomes a backslash path, so the POSIX mock is not hit.
2. `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly` — `bun.CMD -e` under `cmd.exe` reports `) was unexpected at this time`.

The 1 skip is the POSIX-only watchdog test. Without `PYTHONUTF8=1` the suite reports additional GBK-locale fixture failures; that is environmental, not a B6 regression.

## 10. Q items (decision ledger, with this round's status)

**Resolved in this round**

- **Q3 — `max_tier`-alone lower bound. RESOLVED.** A paid `max_tier`-only selector now defaults the lower bound to **Pro**; `max_tier: Plus` alone and any paid interval whose upper bound is below Pro are hard errors (`model_access.py:157-188`; tests `test_model_access.py:87-100`, `test_features_config.py:232-276`).
- **Q4 — Legacy/new Free normalization identity. RESOLVED.** All Free spellings normalize to the identical full selector and merge key, so mixed syntax merges counts instead of creating two slots (`model_defaults.py:345-354`; `test_features_config.py:329-354`).
- **Q5 — Non-string selector leniency. RESOLVED.** Non-string or unknown selector values are hard `TierSelectorError`/`ConfigValidationError`, even when a valid sibling is present (`model_access.py:96-116`, `model_defaults.py:299-325`; tests above).
- **Q11 — One rebind per slot per request. RESOLVED BY DESIGN CHANGE.** The per-slot cap is removed; successive rebinds A→B→C are allowed while the `max_switches` budget lasts, and exhaustion ends in 503 (`routing_pipeline.py:737-866, 891-917`; `test_routing_pipeline.py:373-501`).
- **Q9 — UI/API exposure of the new schema. RESOLVED AT API LEVEL.** `/api/instances` now returns `only_tier`, `min_tier`, `max_tier` (`routes_instances.py:20-31, 51-64`; `test_routes_instances.py:144-160`). The UI itself was not modified (out of scope).

**Remaining open (not blockers)**

- **Q1 — Live B6 re-verification authorization.** `docs/dev/evidence/B6/results.json:78` still records `FAIL/PARTIAL`; a single authorized live run is still needed for native Gemini stream, countTokens, native tools + `signature_cache`, PG-08 and ATG.
- **Q2 — Rebind scope beyond 429.** `_switch_or_raise` is shared by `session_expired`, second `auth_error`, `session_hang` and 429 (`routing_pipeline.py:638-663, 679-685, 722-724, 879-917`); confirm the intended scope.
- **Q6 — SSE newline format.** The server emits `data: ...\n\n` (LF). SSE permits LF/CRLF/CR; confirm LF is acceptable for target clients.
- **Q7 — OpenAI/Claude non-tier `RoutingError` protocol type.** Non-tier routing errors use `service_unavailable`/`invalid_request` (OpenAI) or `api_error` (Claude), preserving the original code/reason (`error_mapping.py:83-113`); confirm the contract.
- **Q8 — countTokens client-side closure.** Server shape is verified; the live client JSON-parse failure should be closed by the next live run or the acceptance script.
- **Q10 — Rebind tier preference.** `select_rebind_account` picks the smallest satisfying tier first (`account_rotator.py:648-711`); an Ultra 429 on a Pro-sufficient model can rebound to Pro. Confirm intended.
- **Q12 — Documentation artifacts.** `config/config.schema.json:20-44` still documents the old `min_tier` enum and required `account_id`; `AGENT.md:202,323` still says `count/surface/min_tier`. The runtime validator ignores these, but they contradict the new schema.
- **Q13 — Approval source for the defaults.** Root `config.yaml` and `defaults/config.yaml` are semantically identical (`only_tier: Free` + `min_tier: Pro`). If the approved YAML is a separate document, it is not referenced by the code.

## 11. N items (non-blocking notes / residual risks)

**Resolved in this round**

- **N18 — `account_tier_rank` unused by `_order`. RESOLVED.** `_order` now sorts through `RouteCandidate.account_tier_rank` (`routing_pipeline.py:411-435`; `test_routing_pipeline.py:229-243`).
- **N19 — Schema leniency for non-string/unknown selector values. RESOLVED.** Hard errors (`model_access.py:96-116`, `model_defaults.py:299-325`).
- **N20 — `only_tier Free` vs legacy `min_tier Free` not merged. RESOLVED.** Identical full Free selector and key (`model_defaults.py:345-354`).
- **N22 — API/UI hides the interval. RESOLVED AT API LEVEL.** `only_tier`/`min_tier`/`max_tier` are serialized (`routes_instances.py:20-31, 51-64`).
- **N23 — Docstring drift in `get_configured_instance_specs`. RESOLVED.** Docstring rewritten (`model_defaults.py:1508-1521`).
- **N25 — One rebind per slot per request leaves budget unused. RESOLVED.** Cap removed; the A→B→C test consumes both switches (`test_routing_pipeline.py:373-501`).
- **N29 — `max_tier`-alone paid lower bound is Plus. RESOLVED.** Lower bound is now Pro; Plus-above-Free alone is rejected (`model_access.py:157-165`).

**Remaining open (non-blocking)**

- **N1 — A stuck upstream can still hold the stream.** `_build_gemini_streaming_response` waits for the first renderable frame with no wall-clock timeout (`api_service_gemini.py:321-325`); the 502 fix covers only “generator ended with zero frames”.
- **N2 — Rebind latency.** `InstanceManager.reassign` closes/reactivates Page/Context synchronously inside the request (`instance_manager.py:389-425`); no background prewarm by design.
- **N3 — Release-before-reassign race.** `_try_rebind` releases the handle before `reassign` (`routing_pipeline.py:792-794`); a concurrent request can acquire the slot, causing the target to be marked failed and a retry/503. Bounded and safe.
- **N4 — Test fidelity for the multi-slot rebind.** The committed multi-slot test uses `RebindFakeManager` plus a canned selector (`test_routing_pipeline.py:268-369`); the A→B→C test also uses a canned selector (`:373-501`). The real `AccountRotator` is covered by `test_account_routing.py:85-134` and the single-slot route test (`test_routes_gemini.py:463-511`), but a real-rotator multi-slot committed test is still absent.
- **N5 — countTokens client-only issue.** The live B6 run recorded server 200 with `totalTokens=11` but a client JSON parse failure (`docs/dev/evidence/B6/results.json`, `diagnostics.json`).
- **N6 — No live re-verification.** B6 evidence still says `FAIL/PARTIAL` (native stream 0 events, countTokens client parse failure, OpenAI 403 missing fields).
- **N7 — PG-02 coverage gap.** The real 4xx test raises before the first frame (`test_routes_gemini.py:306-342`); no test covers `routing_wait` followed by a real 4xx/5xx.
- **N8 — SSE frame newline not asserted.** Tests parse `data: ` lines; they do not assert `\r\n\r\n`. See Q6.
- **N9 — Rebind vs zero switch budget not directly tested.** `test_switch_budget_zero_exhausts_immediately` (`test_routing_pipeline.py:672-694`) uses a `FakeManager` without `reassign`; it proves the normal budget path but not “budget 0 → no rebind”.
- **N10 — Reassign-failure branch not directly tested.** If `reassign` raises, `_try_rebind` marks the target failed and loops (`routing_pipeline.py:838-847`); no test forces this branch.
- **N11 — Multi-slot no-alternate leaves a cooled slot bound.** If no alternate exists, `_try_rebind` returns False and the failed slot remains bound to the cooled account; subsequent `begin` calls filter it by cooldown. Multi-slot no-alternate is not tested (single-slot 503 is: `test_routes_gemini.py:513-537`).
- **N12 — Selector ranking.** `select_rebind_account` ranks by tier rank, total requests, model requests and LRU (`account_rotator.py:700-711`); it does not consider `in_flight`. Bound instances are excluded, so this is minor.
- **N13 — Non-tier routing error route tests.** No route-level test asserts OpenAI/Claude `RoutingError` `error.type` preservation; the shared adapter is checked by code review/direct protocol calls.
- **N14 — Whole-repo `ruff check .` noise.** Pre-existing errors live in `docs/dev/evidence/wp5*/harness/*.py`, outside `src`/`tests`. The gate remains `ruff check src tests`.
- **N15 — B6 evidence vs this fix.** The worktree's B6 evidence describes the live failures that motivated these fixes; it is not post-fix acceptance evidence.
- **N16 — Usage-only stream can still look empty.** A terminal `usage` event renders a usage frame (`api_service_gemini.py:289-306`), so the route can return 200 with usage/finishReason but no text.
- **N17 — Earlier Q/N ledgers.** The B6 discovery audit `Q1-Q8 / N1-N18` and the upstream-merge audit `Q-01..Q-13 / N-01..N-44` remain as adjudicated in prior reviews; this report adds the ledger above. The prior N11 (bound-account exclusion missing UNSTARTED/RECLAIMING) is resolved by `instance_manager.py:697-710`.
- **N21 — Stale schema/documentation artifacts.** `config/config.schema.json:20-44` still describes the old `min_tier` enum and required `account_id`; `AGENT.md:202,323` still says `count/surface/min_tier`. Not used by the runtime validator, but contradictory. See Q12.
- **N24 — Template-clear timing.** `_try_rebind` clears the failed client's templates before the active-request check (`routing_pipeline.py:793-801`); harmless because the client is about to be released/retried.
- **N26 — New-client template state not asserted.** Tests assert the old client's `clear_calls >= 1` but not the newly acquired client's template state; the new client is constructed fresh.
- **N27 — Rebind adds latency before switching to another candidate.** In the multi-slot case the request waits for the failed slot's close/activate before continuing, even though the other candidate is ready. This is the cost of immediate healing (see N2).
- **N28 — `instance_active_requests` returns 0 for unknown records.** `instance_manager.py:712-718` returns 0 when the record/slot is missing; a stale record could look idle, then `reassign` rejects/retries. Bounded.
- **N30 — Worker count claim verified, but environment-dependent.** `uv run pytest -q` produced exactly `2 failed, 813 passed, 1 skipped`; the two failures are pre-existing Windows path/`bun.CMD` environment issues. POSIX/CI runs will also run the skipped watchdog test.
- **N31 — Root config can drift from `defaults`.** Root `config.yaml` is untracked by design; its `instances` block currently matches `defaults/config.yaml`, but future edits to one will not be caught by tests unless the local file is compared explicitly.

## 12. Blockers

**None.** No `file:line` blocker was found in the audited delta. Per instruction, nothing was fixed, staged, or committed.

## 13. Double-write and SHA256

This report is written byte-for-byte to:

- `.kilo/worktrees/rebuild-upstream/docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-stream-and-429-fix.md`
- `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-stream-and-429-fix.md`

The tracked working-tree diff (all 25 modified files, excluding this untracked report) is 121,445 bytes with SHA256 `1EAD5953FCF4776F324A9D0F63FB9BB18C625074F3EB964C7A93357C05BAB872`. Both report copies are byte-identical (SHA256 reported in the audit response; a self-referential hash is not embedded because embedding it would change the file). Root `config.yaml` remains untracked in both repositories.
