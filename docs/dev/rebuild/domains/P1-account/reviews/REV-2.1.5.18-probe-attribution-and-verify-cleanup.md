# REV-2.1.5.18 探针出口归因与假 verify 清理审查

- 审查对象：工作区未提交改动（复审第二轮，Worker 已修 P1-1 + 6 个 P2）
- 版本口径：`2.1.5.18`（第四位审查标号，第三位 `2.1.5` 不变）
- 审查日期：2026-11-23（第二轮复审 14:02Z）
- 审查方式：只读；`uv run`；临时目录/临时 store；未启动真实服务/浏览器，未访问 VPS/外网/真实账号
- **结论：PASS（0 P0 / 0 P1 / 0 P2；保留 Q/N 见 §6/§7）**

## 0. 复审范围与 diff 规模（更新）

`git status`：11 个 tracked 文件修改 + 3 个新增文件（未提交）。

| 文件 | 变化 |
|---|---|
| `src/aistudio_api/api/routes_accounts.py` | 34 行（探针/导入统一谓词） |
| `src/aistudio_api/application/account_keepalive.py` | 50 行（探针出口 + 空绑定） |
| `src/aistudio_api/application/exit_arbitration.py` | 25 行（`probe_exit_id` 门） |
| `src/aistudio_api/infrastructure/account/account_info_refresher.py` | 247 行（谓词/会话/归因） |
| `src/aistudio_api/infrastructure/account/account_store.py` | 10 行（`accounts_dir` 只读属性） |
| `src/aistudio_api/infrastructure/account/cookie_parser.py` | 15 行（`_resolve_probe_proxy` 降级） |
| `src/aistudio_api/infrastructure/exit_pool/__init__.py` | 4 行（导出） |
| `src/aistudio_api/infrastructure/exit_pool/resolver.py` | 26 行（共享谓词） |
| `tests/unit/test_credential_exit_routing.py` | 418 行（11 个新增用例） |
| `tests/unit/test_exit_arbitration.py` | 62 行（3 个新增用例） |
| `tests/unit/test_account_keepalive.py` | 9 行（double 签名） |
| `scripts/clear_verify_required.py`（新） | 455 行 |
| `scripts/clear_verify_required.sh`（新） | 24 行 |
| `tests/unit/test_clear_verify_required.py`（新） | 384 行（12 用例） |

`git diff --shortstat`：`11 files changed, 844 insertions(+), 56 deletions(-)`；另 3 个 untracked 共 863 行。无 UI 变更，无需截图。

## 1. P1-1 修复核对（独立验证，不只信描述）

**原问题（历史，已修复）**：应急阀 `AISTUDIO_EXIT_DIRECT_FALLBACK=1` 下，`_bound_socks=""` 的 direct-fallback browserless 槽位会话把 tier 探针从宿主机出口发出 → region 403 → `_probe_exit_id` 回落当前绑定 → 健康出口 canary 200 → `account_bad` → 写 `verify_required`。

### 1.1 共享谓词 —— 已修复

- `resolver.py:146 probe_egress_allowed()`：`external && action == "direct_fallback"` → `False`；`decision is None` → `False`；off 模式（`proxy,None,external=False`）→ `True`。
- `resolver.py:163 probe_skip_reason()`：external direct_fallback → `no_pooled_exit`，其余 `reason or "no_exit"`。
- `exit_pool/__init__.py:44-45` 导出，供各调用点复用。
- 独立实测：

```
off_proxy_none:      allowed=True  reason=no_exit
external_proxy:      allowed=True  reason=no_exit
external_fallback:   allowed=False reason=no_pooled_exit
external_skip:       allowed=False reason=no_exit
none:                False
```

### 1.2 refresher 会话/归因 —— 已修复

- `account_info_refresher.py:679 _probe_allows_bare` 全部走共享谓词；`:695 _session_probe_has_exit` 对 external + browserless + 空 `_bound_socks` 返回 `False`。
- `refresh_account:1057-1065`、`refresh_tier:1365`：空绑定会话**直接 `_skipped_result`，不发请求**；`_skipped_result`（`:637-671`）只打 skip/defer 日志，不调用仲裁。
- `:715 _probe_exit_id`：空 `_bound_socks` → `""`；会话出口与绑定不一致 → `""`；裸租约路径仍返回 `decision.exit_id`。
- `_post_tier_request`（`:1646-1659`）、`_fetch_app_html`（`:1739-1741`）均加 external + `proxy is None` → 0 裸请求兜底。
- 独立复跑上一轮 P1 复现脚本（应急阀=1、空 `_bound_socks`、假 canary 固定 account_bad）：

```
deferred: False
region_outcome: None
canary_events: []
verify_required: False
VERDICT: SAFE                     # 上一轮为 FALSE_VERIFY_REACHABLE
```

- 新增用例实测（全部通过）：`test_emergency_direct_fallback_never_raw_probes_in_external_mode`、`..._skips_email_probe_with_zero_requests`、`..._skips_refresh_tier_with_zero_requests`、`test_pooled_session_without_bound_exit_skips_without_request`、`test_fetch_app_html_external_proxy_none_never_sends`；均断言 `proxies == []` / `session.calls == 0` / `arbiter.calls == []` / `verify_required is False`。

### 1.3 cookie/rotate 与 routes 谓词 —— 已修复（decision 分支）

- `cookie_parser.py:44-68 _resolve_probe_proxy`：external direct_fallback 降级为 `ExitDecision("skip", reason=probe_skip_reason(...), external=...)`，defer 原样保留。
- `routes_accounts.py:216/986/1183/1791/1860/1972/2276` 的探针/导入判定全部改为 `probe_egress_allowed()` / `probe_skip_reason()`；`detect_account_tier` 的 503 判定在调用裸探针之前（`:1183-1193`）。
- 新用例 `test_cookie_parser_probe_blocks_direct_fallback`、`test_rotate_cookies_blocks_direct_fallback` 均断言 `captured == []`（`httpx.AsyncClient` 从未构造）且 `rotate` 返回 `skipped=no_exit / reason=no_pooled_exit`，cookie 不被改写。

### 1.4 keepalive 与仲裁 —— 已修复

- `account_keepalive.py:319 _probe_exit_id`，`:353-356` 空 `session_socks` → `""`（inconclusive），`:374` 传入 `adjudicate`。
- `exit_arbitration.py:302-314`：`probe_exit_id is not None and assignment.exit_id != probe_exit_id` → `inconclusive`（不跑 canary、不报事件）。
- 独立实测（假 resolver/canary）：

```
default-None: outcome=account_bad canary_calls=1 events=1   # 旧行为不变
match:        outcome=account_bad canary_calls=1 events=1
empty:        outcome=inconclusive canary_calls=0 events=0
mismatch:     outcome=inconclusive canary_calls=0 events=0
```

- `test_keepalive_probe_exit_id_empty_bound_socks_is_inconclusive` 断言 `probe_exit == ""`；`test_probe_exit_empty_string_stays_inconclusive` 覆盖空串语义。

### 1.5 正/负例与生成路径回归 —— 已修复/未回归

- 正例：`test_pooled_session_probe_exit_match_writes_verify`（`_bound_socks=EXIT_ONE` == 绑定 exit-1 → `account_bad`、事件上报、`verify_required=True`）。
- 负例：`test_pooled_session_probe_exit_mismatch_never_writes_verify`、`test_region_403_probe_exit_mismatch_never_writes_verify`、`test_probe_exit_mismatch_stays_inconclusive`。
- 生成/路由 403 canary：`routing_pipeline.py:1266` 未改、不传 `probe_exit_id`（默认 None，行为不变）；`test_account_bad_keeps_an_existing_expiry`、`test_region_arbitration_uses_bound_build_surface_kind` 通过。

## 2. P2 修复核对（逐条）

| 原 P2 | 状态 | 位置与实测证据 |
|---|---|---|
| P2-1 备份/恢复目录与 store 同源 | **已修复** | `account_store.py:531 accounts_dir` 只读属性；`clear_verify_required.py:360 resolved_accounts_dir = store.accounts_dir`。实测：默认备份落在 `<accounts-dir>/backups`；单测 `test_cli_apply_prints_stop_hint_and_backs_up_same_dir` 把 `AISTUDIO_DATA_DIR` 指向别处，断言 `<other-data>/accounts/backups` 不存在。 |
| P2-2 `.sh` 可执行位 | **已记录（提交事项）** | 本机 `core.filemode=false`；主控 commit 时必须 `git update-index --add --chmod=+x scripts/clear_verify_required.sh`（仓库既有 `.sh` 均为 `100755`）。报告已注明，未改仓库。 |
| P2-3 `--apply` 需 `--yes` + 停服提示；`--list` 互斥 | **已修复** | 脚本 `:368-369`（`--list` 配 `--ids/--apply` → `parser.error`，rc 2）；`:403-409`（缺 `--yes` → rc 2 且不写）；`:417-420` 打印 NOTE。实测 rc=2 / 输出 `error: --apply requires --yes ...`、`error: --list cannot be combined with --ids/--apply`。 |
| P2-4 `--force` 保护 `last_refresh_error=null` | **已修复** | `_has_no_exit_fingerprint`（`:74-82`）、`plan.force_required`（`:120-124`）、`run:410-416`、`apply_clear:203-209` 双重校验。实测：`--ids acc_86bbfa31 --apply --yes` → `WARNING ... require --force`、rc=2、零写入；5 假（`skipped_no_exit: no_exit`）无需 force 即可清。 |
| P2-5 `_fetch_app_html` 兜底 / `_probe_skip_reason` | **已修复** | `account_info_refresher.py:1739-1741` external+proxy=None 抛 `RuntimeError`；`refresh_tier` 的 `response is None` 分支改用 `_probe_skip_reason`（`:1409-1411`）。`test_fetch_app_html_external_proxy_none_never_sends` 断言 0 请求。 |
| P2-6 pooled 正例覆盖 | **已修复** | `test_pooled_session_probe_exit_match_writes_verify`（新增，实测通过）。 |

工具其余契约（默认 dry-run、仅指定 ID、备份/原子/失败恢复、meta/rotator 同步、未知 ID 中止、不自动按 fingerprint）均保持：`test_write_failure_restores_backup`、`test_unknown_id_aborts_without_writes`、`test_apply_clears_only_selected_and_backs_up` 等 12 用例全过。

## 3. 清理工具 8 → 5/3 实测（真实 CLI + 临时 store）

临时目录种子：5 假（`verify_required=true` + `skipped_no_exit: no_exit`）+ 3 真（`verify_required=true` + `last_refresh_error=null`），rotator 全 `true`。

| 步骤 | 命令（略去临时路径） | 结果 |
|---|---|---|
| 列 | `--list` | 恰好 8 行；5 假带 no_exit 指纹，3 真 `last_refresh_error=None`；邮箱 `u***N@example.com` |
| dry-run | `--ids <5 假>` | rc=0，`dry-run: pass --apply --yes ...`，registry 字节不变、无 backups |
| 无 yes | `--ids <5 假> --apply` | rc=2，`--apply requires --yes`，零写入 |
| 互斥 | `--list --apply` | rc=2，`--list cannot be combined with --ids/--apply` |
| 真号无 force | `--ids acc_86bbfa31 --apply --yes` | rc=2，`WARNING: ... require --force`，零写入 |
| 落盘 | `--ids <5 假> --apply --yes` | rc=0，打印停服 NOTE；`after ... verify_required=False`；备份 `<accounts-dir>/backups/clear-verify-<ts>` |

落盘后直接读盘核对：

- registry：5 假 `False`，3 真 `True`；
- meta：5 假 `False`，3 真 `True`；
- rotator：5 假 `False`，3 真 `True`；
- 备份：`registry.json` + `rotator_state.json` + `meta/<5 假>.json`。

生产执行顺序（主控在停服/重启流程执行；本轮未执行）：

```bash
docker compose stop aistudio-api
docker compose run --rm --entrypoint sh aistudio-api scripts/clear_verify_required.sh --list
docker compose run --rm --entrypoint sh aistudio-api scripts/clear_verify_required.sh \
  --ids acc_a5a0296a,acc_b477bef7,acc_2d684b14,acc_79cbb018,acc_fbb16921
docker compose run --rm --entrypoint sh aistudio-api scripts/clear_verify_required.sh \
  --ids acc_a5a0296a,acc_b477bef7,acc_2d684b14,acc_79cbb018,acc_fbb16921 --apply --yes
docker compose up -d aistudio-api   # 复查 verify_required 应为 3
```

## 4. 门禁实跑（本轮独立执行）

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **2035 passed**（63.00s，与期望一致） |
| `uv run pytest tests/unit/test_credential_exit_routing.py -q` | **77 passed**（含 4 CI + 11 新增） |
| `uv run pytest tests/unit/test_clear_verify_required.py -q` | **12 passed** |
| 11 个关键 P1 用例定向 | **11 passed** |
| `uv run ruff check .` | **All checks passed**（rc=0） |
| `uv run pyright src tests scripts/clear_verify_required.py` | **0 errors, 0 warnings** |
| `git diff --check` | **rc=0** |
| `uv run ruff format --check`（13 个改动文件） | **13 files already formatted** |
| `uv run ruff format --check .` | 57 files would be reformatted（**既有基线**：`docs/**` 代码块与 `docs/dev/evidence/**`，本轮未触碰；改动文件全绿） |

CI 4 用例（`test_external_no_exit_skips_with_zero_requests`、`test_emergency_direct_fallback_is_opt_in_and_log_throttled`、`test_refresher_skip_log_is_throttled_per_account_and_reason`、`test_keepalive_skip_log_is_throttled_across_rounds`）在“父 logger=CRITICAL + root handler 清空”的模拟 Linux 环境下 **4 passed**；负对照（`caplog.at_level` 旧写法）仍表现为丢记录，证明不是放宽断言。断言语义未改：节流用例仍断言 `len(lines) == 1` 并过滤真实 `record.name`/消息。

## 5. 历史问题原文（保留，均已修复）

- P1-1：应急阀下 direct-fallback 空绑定会话可被归因为 account_bad → 写 verify —— **已修复**（见 §1）。
- P2-1 备份目录分叉 —— 已修复。
- P2-2 `.sh` 可执行位 —— 提交事项已注明。
- P2-3 `--apply` 无 `--yes`/停服提示、`--list` 不互斥 —— 已修复。
- P2-4 缺 fingerprint 强制保护 —— 已修复。
- P2-5 `_fetch_app_html` 兜底/跳过原因不一致 —— 已修复。
- P2-6 pooled 正例缺失 —— 已补。

## 6. Q（观察，非缺陷）

1. **文档滞后（保留，主控另行同步）**：`docs/dev/rebuild/domains/P1-account/research/exit-pool/HANDOVER.md:109,144,224`、`PLAN.md:346,348`、`CONTRACT.md:426` 仍写“`verify_required` 5 个待扫码”。用户最新口径是 **8 个 = 3 真（`last_refresh_error=null`）+ 5 假（`skipped_no_exit: no_exit`）**。本轮按要求**未改文档**。
2. **`ruff format --check .` 57 个既有基线**：全部为 `docs/**` 与 `docs/dev/evidence/**`，与本轮 diff 无关；本轮 13 个改动文件格式全绿。
3. **手动 `detect-account-tier` 的引擎分支未复用空绑定守卫（残留，不产生假 verify）**：`routes_accounts.py:1085-1140` 在 `session is not None` 时直接 `probe_tier_over_session(session, ...)`，未检查 `_bound_socks`。离线实测：应急阀=1 + 空绑定 browserless session → `session_calls: 1`、HTTPException 502、`verify_required: False`。该路径无仲裁器、无 canary、无 verify 写入，且属于“应急阀允许 generation 走宿主机出口”的同一 transport、仅手动/支持动作触发，故不构成 P1/P2；建议作为 N-1 加固。
4. **auth update 的 no-exit skip 仍按轮换失败计数（既有语义）**：`cookie_parser` 降级后 `rotate_google_cookies` 返回 `skipped=no_exit`，`account_keepalive` 的 auth 轮询把它计入 `failed_accounts` 并推进 `MAX_AUTH_CONSECUTIVE_FAILURES`（`keepalive:1336-1349`）；只影响本轮调度，不写 `verify_required`/`expired`，与修复前 external no-exit 行为一致。
5. `detect_account_tier` 的告警文案仍写“回退裸 httpx”（`routes_accounts.py:1194-1196`），external 下实际是“租约 httpx”，措辞略旧，可忽略。
6. `account_info_refresher.py` 工作区为 CRLF（`.gitattributes: * text=auto eol=lf`，提交归一为 LF）；`--stat` 仅 247 行，无整文件翻转。
7. `--apply --yes` 目标全部为“已非 verify_required”时输出 `backup: None`，属正常空操作，措辞可忽略。

## 7. N（建议，不阻塞）

1. 把手动 `detect-account-tier`/`session_extractor` 的引擎分支也接入“空 `_bound_socks` → 不发送”守卫（与 refresher `_session_probe_has_exit` 同型），彻底闭合“应急阀下探针 0 请求”的措辞。
2. 可考虑让 `probe_egress_allowed` 也接受 `session` 形态（或提供 `session_probe_allowed`），避免各调用点重复实现 `_bound_socks` 判定。
3. auth update 对 `skipped=no_exit` 可像 metadata 一样按“未探测、保持 due”而非失败计数，减少应急阀下的失败预算消耗。
4. 提交时同步 `.sh` 可执行位并核对 `git ls-files -s scripts/clear_verify_required.sh` 为 `100755`。

## 8. 只读声明

本次复审仅执行读取、`uv run pytest/ruff/pyright`、`git diff/status/check`、以及仓库外临时目录（`%TEMP%`）的临时 store/脚本验证；除本报告文件外，未修改任何代码/配置/文档/测试/static/生产数据，未提交，未启动真实服务或浏览器、未访问 VPS/外网/真实账号，未对生产 `registry.json`/`meta.json`/`rotator_state.json` 执行任何写入或 `--apply`。
