# REV-2.1.4.3 — `auth_update` 防刷屏、跨通道合并与指数退避只读审查报告

- **任务目标**：对 `src/aistudio_api/application/account_keepalive.py` 与 `tests/unit/test_account_keepalive.py` 的修复（根治 `auth update partial` 每 76 秒死循环刷屏并连带重刷正常账号的 Bug）进行独立只读代码审计与门禁实跑
- **所属域**：`P6-keepalive`（保活与冷却调度域）
- **审查基准**：`AGENT.md §3.3.9`、`§7.1`；`docs/dev/rebuild/domains/INDEX.md`
- **审查方式**：100% 独立只读审查；未修改任何业务代码或测试文件，未启动真实服务，未连接真实 Google 账号，未执行 `git commit` / `git push`
- **审查日期**：2026-11-18

---

## 1. 独立审查结论

**PASS**（经收口返工后复审通过；初审结论为 `CONDITIONAL`）

核心 Bug 修复与收口整改全部成立：
1. **跨通道结果合并准确**：当槽位 `save_current_cookies()` 因无内存脏数据返回 `False`、而随后的 `rotate_account_cookies()` 返回 `{"ok": True}` 时，账号会进入 `saved_accounts`，对应 `slot_id` 也会从 `failed_slots` 中彻底移除；`browserless` 干净槽位遇 `missing_seed` 时亦可通过 `browserless_disk_ok` 正确豁免。
2. **24h 按账号过滤与指数退避生效**：`trigger == "schedule"` 遇 `partial` 后按 `300s → 600s → 1200s` 指数退避，重试时已成功账号被 `_is_account_auth_due` 100% 跳过；`trigger == "api"` / `"manual"` 不受 `_auth_saved_at` 限制且不消耗定时熔断计数。
3. **初审发现全部闭环**：已删除冗余 `_save_slot` 死包装实现原子切换（`P2-01`）；第 3 次失败耗尽周期后直接返回 `cycle_exhausted=True` 并立即结算下一 24h 周期与清零 `_auth_partial_failures`（`P2-02`）；指数幂次封顶至 `min(failure_count, 10)`（`Q-01`）；仅 `trigger == "schedule"` 累加 `_account_auth_failures`（`Q-02`）；补充 3 条定向单测验证手动触发、失败隔离与指数封顶（`N-02`）。

---

## 2. 审查发现清单（`P0 / P1 / P2 / Q / N`）

### P0（阻塞性功能/安全缺陷）
- 无。

### P1（高优先级逻辑回归）
- 无。

### P2（规范违例与状态收敛瑕疵）

1. **`P2-01`（`AGENT.md §3.3.9` 零 Shim 违例）：残留无调用方的 `_save_slot` 包装方法**
   - **位置**：`src/aistudio_api/application/account_keepalive.py:449-474`
   - **证据**：新增 `async def _save_slot_outcome(self, slot: Any) -> tuple[str | None, str, bool, bool]`（`:449-470`）后，`_auth_update`（`:584`）已直接改调 `_save_slot_outcome`，但 `:472-474` 仍保留了 `async def _save_slot(self, slot: Any) -> tuple[str | None, str, bool]` 转调 `_save_slot_outcome` 并丢弃第 4 位 `raised`。全库 `src/` 与 `tests/` 检索无任何 `_save_slot(` 调用方。
   - **要求**：按 `AGENT.md §3.3.9` 实施原子切换，直接将 `_save_slot_outcome` 命名为 `_save_slot`（或直接删除 `_save_slot` 死包装），不保留向后兼容存根。

2. **`P2-02`（第 3 次失败熔断当轮仍多排一轮 `1200s` 空醒）**
   - **位置**：`src/aistudio_api/application/account_keepalive.py:297-300, 547-562, 704-732`；`tests/unit/test_account_keepalive.py:996-1015`
   - **证据**：当本周期内最后一个失败账号在第 3 次失败轮次（如 `t=30_900`）触发 `failures >= MAX_AUTH_CONSECUTIVE_FAILURES`（`:706`）时，`:707` 已写入 `self._auth_saved_at[account_id] = now` 并弹出计数，意味着本周期内所有 `candidate_ids` 均已不再 `due`。但 `:717-732` 仍返回 `{"ok": False, "reason": "partial", ...}`，使 `_retry_delay`（`:297-300`）递增 `_auth_partial_failures` 并调度 `1200s` 后的第 4 轮重试（`t=32_100`）；第 4 轮进入 `:547-562` 后发现全员非 `due` 才零请求返回 `{"ok": True, "reason": "saved", "count": 0, "accounts": []}` 并推进 `+86_400s`。
   - **影响**：不会造成死循环或任何网络/槽位重刷（第 4 轮为纯内存早退），但会额外产生 1 次延迟 20 分钟的空醒、`last_results["auth_update"]` 在第 4 轮被 `count: 0, ok: True` 覆盖（掩盖第 3 轮的 `failed_accounts` 可观测性），并使下一 24h 周期额外后移 `1200s`。若在 `:717` 前或 `_retry_delay` 判定已无剩余 `due` 账号（或保留当轮 `partial` 结果但直接结算 24h 周期），可免去该空醒轮。

### Q（代码质量与设计可维护性观察）

1. **`Q-01`：`_retry_delay` 内含状态写副作用与未封顶幂次**
   - **位置**：`src/aistudio_api/application/account_keepalive.py:282-301, 329-331`
   - **证据**：`_retry_delay` 在计算返回延迟的同时直接修改 `self._auth_partial_failures`（`:287, 295, 297-298`）。全库检索确认目前仅 `_run_due`（`:329`）唯一一处调用 `_retry_delay`，暂不产生重复递增问题；但带副作用的查询函数若被外部或后续测试调用两次会跳过指数阶梯。此外 `:299` `2**failure_count` 未限制指数上限，建议将计数器递增显式上移至 `_run_due` 并对指数做上限保护（如 `min(failure_count, 10)`）。

2. **`Q-02`：手动触发（`trigger="api"` / `"manual"`）共享累加 `_account_auth_failures`**
   - **位置**：`src/aistudio_api/application/account_keepalive.py:484-485, 704-715`
   - **证据**：`_is_account_auth_due` 在 `trigger != "schedule"` 时恒返回 `True`（`:484-485`），保证了手动触发不受 `_auth_saved_at` 24h 缓存限制且全量执行，也不走 `_retry_delay` 故不污染 `_auth_partial_failures`。但 `:704-715` 在手动触发失败时同样会递增 `_account_auth_failures`，若在调度重试窗口期手动排查失败会消耗该账号的定时熔断次数。

### N（非阻塞行为与测试覆盖观察）

1. **`N-01`：多通道合并与同账号多槽位边界**
   - **位置**：`src/aistudio_api/application/account_keepalive.py:594-605, 660-666, 681-697`
   - **证据**：单槽位 `BrowserlessSession` 无脏 Cookie（`save_current_cookies()` 返回 `False`、`raised=False`）且 `rotate_account_cookies()` 返回 `{"ok": True}` 时：`slot_results[acc]` 为 `[False]`、`rotate_results[acc]` 为 `[True]`，`any(channel_outcomes)` 为 `True` → 进入 `saved_accounts`，`failed_set` 不含该账号，因此 `failed_slots`（`:695-697`）也不含其 `slot_id`。但若同账号存在某槽位抛异常（`slot_errored`）同时 `/RotateCookies` 返回 `{"ok": True}`，跨通道 `any()` 仍会将其判为 `saved_accounts`（磁盘滚动凭据已更新，但槽位内存脏 Cookie 可能未落盘）。
2. **`N-02`：测试覆盖缺口**
   - **位置**：`tests/unit/test_account_keepalive.py:817-1020`
   - **证据**：新增 4 个单测充分覆盖了「槽位无脏数据但 rotate 成功」「browserless 干净槽位 missing_seed 豁免」「partial 300s 退避且跳过已成功账号」「连续 3 次失败后熔断至下一 24h 周期」；但尚未补充显式断言 `trigger="api"`/`"manual"` 在 `_auth_saved_at` 24h 窗口内仍全量执行，以及同账号「1 clean browserless + 1 errored slot」组合行为的测试用例。

---

## 3. 门禁命令实跑结果

| 检查项 | 命令 | 实跑结果 |
|---|---|---|
| Ruff Lint | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check src/aistudio_api/application/account_keepalive.py tests/unit/test_account_keepalive.py` | `All checks passed!`（0 errors） |
| Ruff Format | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff format --check src/aistudio_api/application/account_keepalive.py tests/unit/test_account_keepalive.py` | `2 files already formatted` |
| Pyright | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` |
| Pytest 定向单测 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest tests/unit/test_account_keepalive.py -q` | `28 passed in 0.78s` |
| Diff 空白检查 | `git diff --check` | 退出码 `0`（无空白/冲突标记错误） |

---

## 4. 100% 只读声明

- 本报告由独立只读 Reviewer 输出：审查过程中未修改任何业务源码、测试文件或配置文件；仅写入本审查报告并同步更新 `docs/dev/rebuild/domains/P6-keepalive/index.md` 与 `docs/dev/rebuild/domains/INDEX.md`。
- 未启动任何服务进程，未连接任何真实 Google 账号，未执行 `git commit` 或 `git push`。

---

## 5. 收口复审核验记录（2026-11-18）

### 5.1 初审问题闭环核查

| 编号 | 原问题 | 复核证据（`file:line`） | 状态 |
|---|---|---|---|
| `P2-01` | 残留无调用方 `_save_slot` 死 shim（违反 `AGENT.md §3.3.9`） | `account_keepalive.py:452-473, 583`：直接由 `_save_slot` 返回 4 元组 `(account_id, slot_id, saved, raised)`，`_save_slot_outcome` 已彻底删除，无冗余包装 | **已闭环** |
| `P2-02` | 第 3 次失败熔断当轮仍排 `1200s` 空醒并覆盖 `last_results` | `account_keepalive.py:297-299, 703-732`；`test_account_keepalive.py:996-1021`：所有当轮失败账号均达 3 次熔断时设置 `cycle_exhausted=True`；`_retry_delay` 清零 `_auth_partial_failures` 并直接返回 `interval`（`+86_400s`），保留 `last_results["auth_update"]` 的 `partial`/`failed_accounts` 可观测性且无多余空醒 | **已闭环** |
| `Q-01` | `_retry_delay` 指数幂次未封顶与内部状态更新 | `account_keepalive.py:302`；`test_account_keepalive.py:1135-1143`：已添加 `min(failure_count, 10)` 幂次封顶防止极端情况下浮点溢出；`_retry_delay` 为 `_run_due`（`:332`）唯一内部调用的私有方法且单测直接断言其步进契约 | **已闭环** |
| `Q-02` | 手动触发共享累加 `_account_auth_failures` | `account_keepalive.py:703-717`；`test_account_keepalive.py:1089-1132`：仅 `trigger == "schedule"` 累加 `_account_auth_failures` 与触发 3 次熔断；`manual`/`api` 失败不消耗定时重试配额，成功仍清除失败计数并刷新 `_auth_saved_at` | **已闭环** |
| `N-02` | 缺少手动触发绕过 24h 与失败隔离等定向测试 | `test_account_keepalive.py:1025-1143`：新增 3 条单测覆盖 24h 窗口内 `manual` 全量执行、`manual`/`api` 失败不耗尽 `schedule` 计数以及指数封顶 | **已闭环** |

### 5.2 复审门禁实跑结果

| 检查项 | 命令 | 复审实跑结果 |
|---|---|---|
| Ruff Lint | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check src/aistudio_api/application/account_keepalive.py tests/unit/test_account_keepalive.py` | `All checks passed!`（0 errors） |
| Ruff Format | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff format --check src/aistudio_api/application/account_keepalive.py tests/unit/test_account_keepalive.py` | `2 files already formatted` |
| Pyright | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` |
| Pytest 定向单测 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest tests/unit/test_account_keepalive.py -q` | `31 passed in 0.87s`（较初审 +3 例） |
| Diff 空白检查 | `git diff --check` | 退出码 `0`（无输出） |

- **复审最终结论**：**PASS**（所有 `P2 / Q / N` 收口项均已验证闭环，无遗留阻塞项）。
- **复审只读声明**：复审全程保持 100% 只读，未修改任何业务代码、测试或配置文件，未启动真实服务或连接真实 Google 账号，未执行 `git commit` / `git push`。
