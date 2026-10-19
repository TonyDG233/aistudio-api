# WP5 v3 Phase 5 evidence

Status: code and unit-test cleanup complete; an independent audit was performed,
follow-up fixes were applied, and user-approved commit is still required. This is
not a claim that Phase 5 is fully verified.

## Baseline commits

Phase 5 builds on:

```text
b6a0340 docs(plan): WP5 v3 implementation plan
8b6b52e feat(routing): WP5 v3 config contract and eligibility graph
1201a37 feat(gateway): WP5 v3 unified Context pool
cd569bc feat(routing): WP5 v3 pre-dispatch instance switching
dd5e359 feat(gateway): WP5 v3 instance switching and budgets
6305377 feat(api): WP5 v3 instance snapshot and model list
```

The Phase 5 fixes and tests are currently uncommitted.

## What Phase 5 was meant to verify

Phase 5 was an integration-check phase, not a feature phase. The intended
contracts were:

- One page can serve multiple concurrent requests.
- `max_pages` limits surface pages, not request concurrency.
- A build Context's hidden BotGuard chat page is not a counted chat surface
  until it is actually leased for chat.
- A quota 429 enters the account-switch path; it is not returned to the caller
  while usable candidates remain.
- An idle slot is reused before failing on page budget. When spare Context
  budget exists, a new Context may be created instead.
- Page budgets cover global, channel, and group limits, including implicit
  groups.
- Rotation fairness must not permanently favor a warm account or an account
  with a large lifetime request count.

## Fixes in this cleanup

### Resource lifecycle

- Non-stream and stream paths release the lease when
  `handle_attempt_exception()` raises.
- Non-stream cancellation (`BaseException`, including `asyncio.CancelledError`)
  also releases the lease.
- A slot whose build surface creation fails after Context load is fully closed:
  pages, Context, retirement callback, and pool entry.
- `rebind()` rejects `LOADING`/`RETIRING` slots.
- `rebind()` checks page budgets and creates the build surface when rebinding to
  `build`.
- Idle rebind selection skips idle slots whose replacement would violate a
  page budget.

### Page accounting

- `pending_pages` no longer double-counts a build page that is already marked
  `build_counted`.
- Counted-page flags are cleared when a slot enters rebind/load, so old pages
  are not charged to the new group together with the pending page.
- Hidden bootstrap chat pages are checked against chat/global/group budgets when
  first leased.
- Borrowed uncounted pages are budget-checked and marked.
- Closed idle pages release their counted-page flags.
- Snapshot rows expose `chat_counted`, `build_counted`, `pending_pages`, and
  per-slot `pages`.

### Routing and switch

- `InstanceSwitcher` excludes non-idle slots and passes group `max_pages`,
  including implicit groups, into `rebind()`.
- After a 429 switch, a candidate whose pool acquisition fails with
  `surface_quota_exceeded` is skipped and the next candidate is tried.
- Switched candidates use a bounded acquisition wait instead of an immediate
  zero-timeout failure (30s first attempt, 15s after a switch).
- Page-budget exhaustion now waits for an in-flight slot to become ready or
  idle instead of returning 429 immediately; slot load completion notifies
  waiters.
- Freshly imported accounts without rotator statistics are treated as available
  instead of being excluded from routing.
- Fairness score no longer includes `account_id`; a warm slot can break an exact
  fairness tie.
- `get_configured_groups` import restored after `ruff` found a latent
  `NameError` in `_resolve_surface()`.

## Regression tests

Added or updated:

- `tests/unit/test_instance_pool.py`
  - concurrent build creation counts one page
  - hidden bootstrap chat page obeys chat budget
  - new slot is closed when build surface creation fails
  - rebind to build creates a build surface
  - idle closed page releases budget
  - pending build page counts once
  - concurrent chat waits for a loading slot and reuses its page
- `tests/unit/test_generation_routing.py`
  - switch-budget exhaustion releases the lease
  - non-stream cancellation releases the lease
  - a capacity-denied candidate is skipped and the next candidate succeeds
- `tests/unit/test_routing_pipeline.py`
  - fairness regressions retained
  - equal fairness scores prefer the warm slot
- `tests/unit/test_rotator_surface_keys.py`
  - a fresh account without statistics is available
- `tests/unit/test_testing_hooks.py`
  - hook is off by default and matches one account/count

## Quality gates

```text
uv run pytest tests/unit -q
774 passed

uv run ruff check .
All checks passed!

uv run ruff format --check .
180 files already formatted

bun x pyright src tests
0 errors, 0 warnings, 0 informations
```

## Environment state

- Root `config.yaml` restored from
  `%TEMP%/wp5v3-config-before.yaml`.
- Restored configuration has no v3 `max_contexts` or `global_max_pages`
  markers and has `build_surface: false`.
- Only the original accounts remain:
  - `acc_46223636` (pro)
  - `acc_b1a36ccf` (free)
- All temporary accounts imported for this round were deleted, including:
  - `acc_93c6ead1`, `acc_f5837f2d`, `acc_4cd8be58`, `acc_a78228ce`
  - the earlier expired imports and probe accounts
- No cookie/token/secret matches were found in this evidence directory.
- The server is stopped.

## Live verification (2026-10-20)

Four random credentials from `D:\LLM\Ark_AIStudioToAPI\configs\auth` were
imported after cropping cookies to `.google.com` and `accounts.google.com`.
All four were valid free accounts:

```text
auth-32 -> acc_93c6ead1
auth-8  -> acc_f5837f2d
auth-38 -> acc_4cd8be58
auth-49 -> acc_a78228ce
```

Sequential rotation (4 requests):

```text
round 1  200  10.9s  acc_4cd8be58
round 2  200  12.2s  acc_8808817d
round 3  200  27.6s  acc_93c6ead1
round 4  200  11.4s  acc_a78228ce

distinct_accounts=4
final_context_count=1
```

Forced-429 switch (test hook, no real quota on the failed attempt):

```text
acc_f5837f2d -> injected 429
switch -> acc_b1a36ccf -> 200
elapsed 20.4s
context_count=1
switches=1
```

Same-page concurrency:

```text
2 concurrent chat requests: 200 + 200, 9.8s / 10.1s, context=1, chat pages=1
4 concurrent chat requests: 200 x 4, 11.0-12.2s, context=1, chat pages=1
```

An earlier concurrent pair returned 200 + 429 because the second request did not
wait for the first slot to finish loading. That defect was fixed by waiting for
page budget/slot readiness and verified by the 2- and 4-request runs above.

## Earlier raw evidence (pre-cleanup)

The older `raw/` files still contain earlier Phase 5 runs:

- `/v1beta/models` and `/v1/models` returned 64 projected entries; only `pg/`
  and `build/` appeared; `argon` appeared only under `pg/`.
- Three free-account rotation produced one cold-start 500 followed by three 200
  responses across three distinct accounts with `context_count=1`.
- Warm pro chat and free build requests returned 200.
- A concurrent pro-chat + free-build pair returned 200 + 429 before the hidden
  page accounting fix.

## Not verified

- `auto_switch`, `borrow`, and pro-only argon paths have not been re-run live.
- Cold-start bootstrap currently makes up to three attempts (initial plus two
  retries); the successful live loads did not exercise all retry branches.
- The test hook is enabled only with `AISTUDIO_TEST_HOOKS=1`; it is off by
  default.

## Test hook

Environment variables:

```text
AISTUDIO_TEST_HOOKS=1
AISTUDIO_TEST_FORCE_429_ACCOUNT
AISTUDIO_TEST_FORCE_429_MODEL
AISTUDIO_TEST_FORCE_429_SURFACE
AISTUDIO_TEST_FORCE_429_COUNT
```

The hook raises `UsageLimitExceeded` before the upstream call so the normal
429/switch path is exercised without real quota.

## Next gate

1. Review the final diff and commit Phase 5.
2. Push after user authorization.
3. Trigger a fresh independent audit only after three consecutive live-test
   failures.