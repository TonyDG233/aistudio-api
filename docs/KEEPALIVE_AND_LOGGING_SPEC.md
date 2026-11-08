# 保活与日志技术规格

- 状态：依据当前主仓源码整理，只读核对。
- 范围：24h Cookie 保活（`slot.lifecycle_lock`、按槽位成功记账）、24h 元数据刷新与 `tier_checked_at` 3 天跳过、单一全局 job 锁、B4 三 sink（5000 环形缓冲 / 7 天归档 / SSE）、`safe_*` dump、写入/导出双重脱敏、历史清理与上限。
- 非范围：实例池/路由调度、协议/工具调用。分别见 `INSTANCE_POOL_AND_ROUTING_SPEC.md` 与 `PROTOCOL_AND_TOOLCALL_SPEC.md`。
- 文中 `file:line` 均指本仓库路径。

---

## 目录

- [1. 组件与配置](#1-组件与配置)
  - [1.1 组件](#11-组件)
  - [1.2 默认配置](#12-默认配置)
- [2. 调度器：单循环 + 单一全局 job 锁](#2-调度器单循环--单一全局-job-锁)
  - [2.1 结构](#21-结构)
  - [2.2 启动与停止](#22-启动与停止)
  - [2.3 主循环](#23-主循环)
  - [2.4 到期判断](#24-到期判断)
  - [2.5 单一全局 job 锁](#25-单一全局-job-锁)
  - [2.6 状态查询](#26-状态查询)
- [3. 24h Cookie 保活](#3-24h-cookie-保活)
  - [3.1 Job 入口](#31-job-入口)
  - [3.2 槽位枚举](#32-槽位枚举)
  - [3.3 按槽位回写](#33-按槽位回写)
  - [3.4 控制会话回写](#34-控制会话回写)
  - [3.5 按槽位成功记账](#35-按槽位成功记账)
  - [3.6 可选的 active_activity](#36-可选的-active_activity)
  - [3.7 时序图：auth_update](#37-时序图auth_update)
- [4. 24h 元数据刷新与 3 天 tier 跳过](#4-24h-元数据刷新与-3-天-tier-跳过)
  - [4.1 调度层](#41-调度层)
  - [4.2 3 天 tier 新鲜度](#42-3-天-tier-新鲜度)
  - [4.3 启动时的两层协调](#43-启动时的两层协调)
  - [4.4 时序图：metadata_refresh](#44-时序图metadata_refresh)
- [5. B4 三 sink：环形缓冲、归档、SSE](#5-b4-三-sink环形缓冲归档sse)
  - [5.1 三 sink 装配](#51-三-sink-装配)
  - [5.2 环形缓冲（5000）](#52-环形缓冲5000)
  - [5.3 每日归档（7 天）](#53-每日归档7-天)
  - [5.4 SSE 流](#54-sse-流)
  - [5.5 导出](#55-导出)
  - [5.6 写入/导出双重脱敏](#56-写入导出双重脱敏)
  - [5.7 时序图：日志写入与 SSE](#57-时序图日志写入与-sse)
- [6. `safe_*` dump](#6-safe_-dump)
  - [6.1 开关](#61-开关)
  - [6.2 转储字段](#62-转储字段)
  - [6.3 文件 dump](#63-文件-dump)
  - [6.4 仍会出现的字段](#64-仍会出现的字段)
  - [6.5 时序图：dump](#65-时序图dump)
- [7. 历史清理与上限](#7-历史清理与上限)
  - [7.1 上限表](#71-上限表)
  - [7.2 环形缓冲淘汰](#72-环形缓冲淘汰)
  - [7.3 归档清理](#73-归档清理)
  - [7.4 导出范围](#74-导出范围)
  - [7.5 历史数据生命周期](#75-历史数据生命周期)
- [8. 残余限制](#8-残余限制)
- [9. 证据索引](#9-证据索引)
- [10. C1/C2c：/health 静默、401 去重与日志沙盒](#10-c1c2chealth-静默401-去重与日志沙盒)
- [11. C4：Client/Attempt 双口径与 90 天 daily/by_model](#11-c4clientattempt-双口径与-90-天-dailyby_model)

---

## 1. 组件与配置

### 1.1 组件

| 组件 | 职责 | 证据 |
|---|---|---|
| `AccountKeepaliveService` | 单循环调度 metadata/auth/activity 三个 job；共用一把 job 锁 | `src/aistudio_api/application/account_keepalive.py:112-547` |
| `AccountInfoRefresher` | 无浏览器直连探测 tier/登录态/邮箱；并发限流；进程内互斥 | `src/aistudio_api/infrastructure/account/account_info_refresher.py:191-398` |
| `AccountStore` | 持久化 `auth_saved_at`、`last_refresh_attempt_at`、`last_refresh_error`、`tier_checked_at` | `src/aistudio_api/infrastructure/account/account_store.py:718-778` |
| `PooledSession` | 提供 `save_current_cookies()` | `src/aistudio_api/infrastructure/gateway/pooled_session.py:321-322` |
| `InstanceManager.pool_slots()` | 暴露池槽位给保活回写 | `src/aistudio_api/application/instance_manager.py:676-680` |
| 日志三 sink | stderr、broadcast ring、daily archive | `src/aistudio_api/infrastructure/utils/logger.py:310-350` |
| `LogBroadcaster` | 5000 条环形缓冲 + asyncio 订阅者 | `src/aistudio_api/infrastructure/utils/log_broadcast.py:126-286` |
| `DailyLogFileHandler` | `data/logs/aistudio-YYYYMMDD.log`，7 天保留 | `log_broadcast.py:368-441` |
| 日志路由 | SSE `/logs/stream` 与 `/logs/export` | `src/aistudio_api/api/routes_logs.py:114-286` |

### 1.2 默认配置

`defaults/config.yaml:91-100`：

```yaml
features:
  keepalive:
    auth_update: true
    auth_update_hours: 24
    metadata_refresh: true
    metadata_refresh_hours: 24
    metadata_refresh_on_startup: true
    active_activity: false
    active_activity_seconds: 60
```

`defaults/config.yaml:54-64`：

```yaml
logging:
  level: INFO
  dump_requests: false
  dump_to_file: false
  dump_dir: dumps
```

`get_configured_keepalive_settings` 在缺失/非法时回退 24h 默认值（`model_defaults.py:1535-1555`）。`KeepaliveSettings.from_mapping` 对 bool/float 做类型检查（`account_keepalive.py:55-90`）。

---

## 2. 调度器：单循环 + 单一全局 job 锁

### 2.1 结构

`AccountKeepaliveService`（`account_keepalive.py:112-139`）：

```text
_task          单循环 asyncio.Task
_stop_event    停止事件
_job_lock      全局串行锁（所有 job 共用）
_idle_event    job 空闲事件
_running       当前 job 名或 None
_last_results  每个 job 最近一次结果
_next_due      每个 job 下次到期时间
```

`TASKS = ("metadata_refresh", "auth_update", "active_activity")`（`:33`）。

### 2.2 启动与停止

`start`（`:162-186`）：

```text
if task 已存在且未结束: return
now = clock()
next_due = {
  metadata_refresh: now if (metadata_refresh_on_startup and not startup_metadata_done)
                    else now + metadata_refresh_seconds,
  auth_update: now,
  active_activity: now + active_activity_seconds,
}
stop_event.clear()
task = create_task(_run(), name="account_keepalive")
```

应用启动时传 `startup_metadata_done=True`（`api/app.py:504-505`），因此：

- 调度器不会在启动瞬间再次跑 metadata；
- 应用层随后显式调用 `run_startup_metadata_refresh()`（`api/app.py:513-534`），确保启动元数据只跑一次并与后续 3 天跳过协调。

`stop` 设置 stop event，取消并等待 task（`account_keepalive.py:188-196`）。

### 2.3 主循环

`_run`（`:198-211`）：

```text
while not stop:
    try: await _run_due(settings)
    except CancelledError: raise
    except Exception: 记录 _last_error + warning
    await wait_for(stop_event.wait(), timeout=tick_seconds)
```

`tick_seconds` 默认 1.0，最小 0.01（`:123-130`）。循环每 tick 检查一次到期 job。

### 2.4 到期判断

`_run_due`（`:225-251`）：

```text
jobs = [
  ("metadata_refresh", metadata_refresh, metadata_refresh_seconds),
  ("auth_update", auth_update, auth_update_seconds),
  ("active_activity", active_activity, active_activity_seconds),
]
for task, enabled, interval in jobs:
    if not enabled: continue
    due = next_due.get(task)
    if due is not None and due > now: continue
    result = await run_now(task, trigger="schedule", settings)
    if result.reason == "busy": continue        # 不推进 next_due
    next_due[task] = clock() + _retry_delay(task, result, interval)
```

`_retry_delay`（`:212-223`）：

| 结果 | 延迟 |
|---|---|
| `ok=True` | 正常 interval（24h / 24h / 60s） |
| metadata `refresh_in_progress` | 60s |
| metadata 其他失败 | 300s |
| auth 失败 | 60s |
| activity 失败 | `min(60s, interval)` |

因此 busy 的 job 会保持到期状态，下一个 tick 继续尝试；失败 job 会短周期重试，而不是等到 24h。

### 2.5 单一全局 job 锁

`run_now`（`:253-288`）：

```text
if task not in TASKS: return unknown_task
if _job_lock.locked(): return {"ok":False, "reason":"busy"}   # 立即返回
async with _job_lock:
    _idle_event.clear()
    _running = task
    try: result = await _execute(task, settings, trigger)
    except Exception as exc: result = {ok:False, reason:"exception", error:str(exc)}
    finally: _running = None
    _last_results[task] = {**result, "at": iso(clock()), "trigger": trigger}
    _idle_event.set()
    return _last_results[task]
```

关键点：

- 三个 job 不能并行；后到的 job 直接 `busy`，不排队。
- `busy` 不写 `_last_results`，也不推进 `next_due`。
- 手动 API 触发同样受该锁约束（`routes_keepalive.py:38-60`）。

`run_startup_metadata_refresh`（`:290-296`）在 busy 时等待 `_idle_event` 后重试，保证启动刷新最终执行。

### 2.6 状态查询

`status()`（`:515-544`）返回：

- `enabled`：三个 job 的开关。
- `intervals`：auth/metadata 小时数、activity 秒数。
- `next_due`：启用 job 的 ISO 时间。
- `busy`：`_job_lock.locked()`。
- `running`：当前 job。
- `last_run` / `last_results` / `last_error`。
- `in_flight`：所有槽位 `active_requests` 之和。
- `pool_slots`：槽位数量。

HTTP：

- `GET /keepalive` -> `{available, ...status()}`（`routes_keepalive.py:24-35`）。
- `POST /keepalive/run` -> 校验 task，未知 400，服务未初始化 503，busy 409，exception 500，否则返回结果（`:38-60`）。

---

## 3. 24h Cookie 保活

### 3.1 Job 入口

`_execute`（`account_keepalive.py:298-305`）：

- `metadata_refresh` -> `_metadata_refresh`
- `auth_update` -> `_auth_update`
- 其他 -> `_active_activity`

### 3.2 槽位枚举

`_pool_slots`（`:148-160`）通过 `instance_manager.pool_slots()` 获取槽位；异常时 warning 并返回空元组。

`InstanceManager.pool_slots`（`instance_manager.py:676-680`）先 `_sync_records()`，再返回 `pool.slots()` 元组。

### 3.3 按槽位回写

`_save_slot`（`account_keepalive.py:357-378`）：

```text
slot_id = slot.slot_id
account_id = slot.account_id
session = slot.session
if not account_id or session is None: return (None, slot_id, False)
try:
    lifecycle_lock = slot.lifecycle_lock
    if lifecycle_lock:
        async with lifecycle_lock:
            saved = await session.save_current_cookies()
    else:
        saved = await session.save_current_cookies()
except Exception:
    warning; return (account_id, slot_id, False)
return (account_id, slot_id, saved)
```

要点：

- **`slot.lifecycle_lock` 是回写与 activate/close/retire 的互斥点**。保活在锁内保存 cookie，避免与“关闭旧 Page / 激活新账号”并发。
- 没有 `lifecycle_lock` 时才直接保存（兼容测试替身）。
- 保存失败不会抛出到 job 层，而是返回 `saved=False`，由上层记账。

`PooledSession.save_current_cookies`（`pooled_session.py:321-322`）委托 `save_browser_cookies(page, auth_file)`。

### 3.4 控制会话回写

`_save_control_session`（`account_keepalive.py:380-407`）：

- 取 `client.session`；没有或 Page 已关闭 -> `(None, False, False)`（未尝试）。
- `session.save_current_cookies()`；异常 -> `(None, False, True)`（尝试但失败）。
- 尝试从 session 的 `_auth_file`/`auth_file` 反查账号；找不到则回退 `account_store.get_active_account()`。
- 返回 `(account_id, saved, attempted)`。

这是池未启用或控制面仍有活跃 Page 时的兜底路径。

### 3.5 按槽位成功记账

`_auth_update`（`:409-503`）：

```text
attempts: dict[account_id, list[bool]] = {}
failed_slots: list[str] = []

for slot in pool_slots():
    account_id, slot_id, saved = await _save_slot(slot)
    if account_id is None: continue
    attempts[account_id].append(saved)
    if not saved and slot_id: failed_slots.append(slot_id)

control_account, control_saved, control_attempted = await _save_control_session()
if control_attempted and control_account:
    attempts[control_account].append(control_saved)

saved_accounts = [a for a, results in attempts.items() if results and all(results)]
failed_accounts = [a for a, results in attempts.items() if not results or not all(results)]

for account in saved_accounts:
    account_store.mark_auth_saved(account)
```

关键语义：

- **按账号聚合，而不是按槽位**：同一账号如果出现在多个槽位（当前架构下通常不会，因为同账号不能有两个实例），所有槽位都必须成功才算该账号成功。
- **`all(results)`**：任一槽位失败 -> 该账号进入 `failed_accounts`。
- **`saved_accounts` 才写 `auth_saved_at`**：`mark_auth_saved` 更新 `AccountStore.auth_saved_at`（`account_store.py:763-768`）。
- 控制会话的结果也加入同一账号的 results。

返回值分支（`:441-503`）：

| 条件 | 结果 |
|---|---|
| 无 saved 且控制会话未成功，且存在失败账号 + 至少一个槽位成功 | `ok=False, reason="partial"`，带 `failed_accounts/failed_slots` |
| 无 saved 且控制会话未成功，且没有“部分成功” | `ok=False, reason="no_active_page"` |
| 有 saved 但也有 failed | `ok=False, reason="partial"`，带 saved/failed |
| 只有控制会话成功 | `ok=True, reason="saved", count=1, control=True` |
| 全部成功 | `ok=True, reason="saved", count=len(saved_accounts)` |

`NO_ACTIVE_PAGE_RETRY_SECONDS = 60.0`（`:34`）用于 auth 失败后的短周期重试。

### 3.6 可选的 active_activity

`_active_activity`（`:505-513`）：

- 取控制会话 Page；没有或已关闭 -> `no_active_page`。
- `page.evaluate("() => { window.scrollBy(0, 2); return true; }")`。
- 成功 -> `{ok:True, reason:"activity"}`。

默认关闭（`defaults/config.yaml:99`）。没有 idle 回收/重建逻辑；它只是可选的控制页活动。

### 3.7 时序图：auth_update

```text
Scheduler tick
  -> _run_due: auth_update due
  -> run_now("auth_update") [job_lock]
     -> _auth_update
        for slot in pool_slots:
          -> _save_slot(slot)
             -> async with slot.lifecycle_lock:
                  -> session.save_current_cookies()
                     -> save_browser_cookies(page, auth_file)
             -> (account, slot, saved)
          -> attempts[account].append(saved)
        -> _save_control_session()  # 兜底
        -> saved = accounts where all(results)
        -> failed = accounts where not all(results)
        -> account_store.mark_auth_saved(saved)
        -> return saved/partial/no_active_page
     -> _last_results["auth_update"] = result
     -> next_due["auth_update"] = now + (24h 或 60s/300s 重试)
```

---

## 4. 24h 元数据刷新与 3 天 tier 跳过

### 4.1 调度层

`_metadata_refresh`（`account_keepalive.py:307-355`）：

```text
accounts = [a for a in list_accounts() if not a.expired]
now = clock()
due = [
  a for a in accounts
  if parse_ts(a.tier_checked_at) is None
     or now - checked >= metadata_refresh_seconds
]
if not due: return {ok:True, reason:"no_due_accounts", count:0}

try:
    results = await refresher.refresh_accounts(
        due, refresh_email=True, skip_fresh_free_tier=True
    )
except RefreshInProgressError:
    return {ok:False, reason:"refresh_in_progress", count:0}

checked_at = iso(now)
for result in results:
    account_store.mark_refresh_attempt(
        result.account_id,
        error=result.error,
        at=result.checked_at or checked_at,
    )

failed = count(not result.ok)
return {ok: failed==0, reason:"checked", count:len(results), failed, accounts:[...]}
```

要点：

- **24h 是调度节奏**：`tier_checked_at` 超过 `metadata_refresh_seconds`（默认 24h）或缺失，账号进入 `due`。
- **expired 账号不参与**：`not account.expired` 过滤。
- **`skip_fresh_free_tier=True`**：即使账号因 24h 到期进入 `due`，如果它是“3 天内已验证的 Free”，refresher 仍会跳过 tier 探测，只补邮箱。
- **`mark_refresh_attempt`**：即使 refresher 跳过 tier 探测，也会记录本次尝试时间与错误（`account_store.py:770-778`）。
- **`RefreshInProgressError`**：refresher 已有一次刷新在跑，本 job 返回 `refresh_in_progress`，调度器 60s 后重试。

### 4.2 3 天 tier 新鲜度

`is_verified_free_tier_fresh`（`account_info_refresher.py:149-158`）：

```text
if account.tier != "Free": return False
checked = parse(account.tier_checked_at)
if checked is None: return False
return now - checked < TIER_PROBE_FRESHNESS_SECONDS
```

`TIER_PROBE_FRESHNESS_SECONDS = 3 * 86400`（`:131`）。

`refresh_account` 的跳过分支（`:252-277`）：

- 读取 cookie；缺失 -> `ok=False`，错误提示重新导入。
- 若 `skip_fresh_free_tier and is_verified_free_tier_fresh(account)`：
  - 如需要，抓取邮箱。
  - 只更新邮箱（`update_account_refresh(email=...)`）。
  - 返回 `ok=True, tier=旧 tier, expired=False, checked_at=now`。
  - **不更新 `tier_checked_at`**；因此 3 天窗口从上次真实 tier 探测开始计算。

正常 200 分支（`:280-336`）：

- 解析 tier。
- 需要时抓取邮箱。
- `update_account_refresh(tier=..., tier_checked_at=checked_at, expired=False, email=...)`。
- 返回 `ok=True`。

401 分支（`:338-374`）：

- `update_account_refresh(expired=True)`。
- 返回 `ok=False, expired=True`。

其他非权威失败（`:374-378`）：

- 不改账号状态，返回 `ok=False`。
- `_metadata_refresh` 仍会 `mark_refresh_attempt` 记录错误。

### 4.3 启动时的两层协调

应用启动（`api/app.py:513-534`）：

1. 若 `metadata_refresh` 且 `metadata_refresh_on_startup`：
   - `keepalive.run_startup_metadata_refresh()`。
   - 若返回 `reason in ("checked","no_due_accounts")`，把 `accounts` 记为 `covered_ids`。
2. `_refresh_startup_account_metadata(account_store, covered_ids=covered_ids)`。

`_refresh_startup_account_metadata`（`api/app.py:194-301`）：

- 跳过已被 keepalive 覆盖的账号（`:216-225`）。
- 账号需要检查的条件：
  - 没有 email，或
  - `tier_checked_at` 缺失，或
  - `tier_checked_at` 超过 3 天。
- 有 cookie 才继续；没有 email 先探 email。
- 若 `is_verified_free_tier_fresh(account)` -> 跳过 tier 探测（`:252-258`）。
- 否则调用 `probe_account_benefit_tier`，`update_account_tier(..., checked_at=now)`（`:260-270`）。

因此启动阶段最多两层：

```text
keepalive metadata_refresh (24h due, 3d Free skip)
  -> covered_ids
app startup probe (only uncovered; 3d Free skip)
```

### 4.4 时序图：metadata_refresh

```text
Scheduler tick
  -> _run_due: metadata_refresh due
  -> run_now("metadata_refresh") [job_lock]
     -> _metadata_refresh
        accounts = non-expired
        due = tier_checked_at 缺失或 >= 24h
        if not due: no_due_accounts
        -> refresher.refresh_accounts(due, refresh_email=True, skip_fresh_free_tier=True)
           for each account (concurrency semaphore):
             if fresh Free (3d): 只补 email; 不写 tier_checked_at; ok=True
             else: probe tier
               200 -> tier/tier_checked_at/expired=false/email
               401 -> expired=true; ok=False
               其他 -> 状态不变; ok=False
        -> mark_refresh_attempt(account, error, at)
        -> ok = failed == 0
     -> _last_results["metadata_refresh"]
     -> next_due = now + (24h 或 60s/300s 重试)
```

---

## 5. B4 三 sink：环形缓冲、归档、SSE

### 5.1 三 sink 装配

`setup_logging`（`logger.py:310-350`）：

```text
formatter = UnifiedFormatter()
_root_handler = StreamHandler(sys.stderr)
_broadcast_handler = BroadcastHandler()
_file_handler = DailyLogFileHandler()

for handler in (root, broadcast, file):
    handler.addFilter(NoisyLoggerFilter())

root.handlers.clear()
root.addHandler(root)
root.addHandler(broadcast)
root.addHandler(file)
root.setLevel(DEBUG)

cleanup_old_logs()  # 启动清理超过 7 天
```

`set_log_level`（`:272-308`）：

- root 与 `aistudio` logger 固定 DEBUG（广播/归档需要完整日志）。
- stderr 使用配置级别。
- broadcast/file 固定 DEBUG。
- uvicorn 使用配置级别。
- 噪音 namespace（`websockets/httpx/httpcore/uvicorn.access/playwright/warp/tunnel/cdp/...`）固定 WARNING。
- `NoisyLoggerFilter` 在 sink 层再次丢弃噪音 logger 的低于 WARNING 记录（`:58-64`, `:324-328`）。

### 5.2 环形缓冲（5000）

`LogBroadcaster`（`log_broadcast.py:126-286`）：

- `DEFAULT_CAPACITY = 5000`（`:23`）。
- `DEFAULT_QUEUE_SIZE = 500`（`:24`）。
- `DEFAULT_REPLAY_LIMIT = 5000`（`:25`）。
- `DEFAULT_SUBSCRIBER_LIMIT = 8`（`:27`）。
- `_entries: deque[LogEntry]`、`_next_id` 单调递增、`threading.Lock` 保护（`:138-144`）。
- `publish`：
  - 校验 level 名称（`level_number`，未知抛 `ValueError`）（`:81-87`）。
  - 时间戳默认本地时区 ISO 秒（`:171`）。
  - `sanitize_log_message` 清洗（`:178`）。
  - 追加 entry；超过容量时优先删除最早的低级别（DEBUG/INFO）记录；若没有低级别则 `popleft()`（`:184-191`）。
  - 对每个订阅者 `loop.call_soon_threadsafe(_deliver, ...)`（`:192-201`）。
- `subscribe`：
  - 最多 8 个订阅者，超过抛 `SubscriptionLimitError`（`:203-222`）。
  - 每个订阅者有 `asyncio.Queue(maxsize=500)`。
- `unsubscribe`：标记 inactive 并移除（`:224-227`）。
- `replay`：
  - 按 level 过滤。
  - 若 `last_id < oldest-1`，返回 gap `{last_id, oldest_id, missed}`（`:241-248`）。
  - 返回 `entry.id > last_id` 的记录，取最后 `limit` 条（`:250-255`）。
- `_deliver`：
  - 队列满时 `dropped += 1`，丢弃最旧一条，再放入当前 entry（`:257-267`）。

### 5.3 每日归档（7 天）

`resolve_log_dir`（`:295-308`）：

- 显式参数 > `AISTUDIO_LOG_DIR` > `resolve_data_dir()/logs`。
- 目录不存在会创建。

`archive_file_name(day)` -> `aistudio-YYYYMMDD.log`（`:311-312`）。

`cleanup_old_logs`（`:319-347`）：

- `retention_days` 最小 1，默认 `LOG_RETENTION_DAYS=7`（`:28`）。
- `cutoff = today - (retention_days - 1)`。
- 只匹配 `^aistudio-(\d{8})\.log$`；早于 cutoff 的文件 `unlink`。
- 返回删除的文件列表。

`DailyLogFileHandler`（`:368-441`）：

- 每次 `emit` 取当天日期；跨天时 `_open_for_day`。
- `_open_for_day` 先关闭旧流，调用 `cleanup_old_logs`，再以追加模式打开 `aistudio-YYYYMMDD.log`（`:420-441`）。
- 写入格式：`YYYY-MM-DD HH:MM:SS LEVEL logger message`。
- 写入前 `sanitize_log_message(record.getMessage())`（`:393`）。
- 每次写入后 `flush()`（`:381`）。

启动时 `setup_logging` 也会调用一次 `cleanup_old_logs`（`logger.py:337-339`）。

### 5.4 SSE 流

路由 `GET /logs/stream`（`routes_logs.py:114-156`）：

- 支持 query `level`（默认 `DEBUG`）与 `last_id`，也支持 `Last-Event-ID` header（`:120-125`）。
- `prepare_stream` 先校验 level，再订阅，再 replay（`:35-47`）；这样不会丢失订阅与 replay 之间的日志。
- `level` 非法 -> 400；订阅者上限 -> 503（`:127-135`）。
- 返回 `StreamingResponse`，media type `text/event-stream; charset=utf-8`，禁用缓存/代理缓冲（`:140-154`）。

`stream_subscription`（`:72-110`）：

```text
last_sent = replay[-1].id or 0
if gap: yield gap event
for entry in replay: yield log event

while True:
    if disconnected: break
    dropped = subscription.take_dropped()
    if dropped: yield dropped event
    try:
        entry = await wait_for(queue.get(), timeout=15s)
    except TimeoutError:
        yield ": ping\n\n"
        continue
    if entry.id <= last_sent: continue
    last_sent = entry.id
    yield log event
finally:
    unsubscribe
```

事件格式（`:50-69`）：

```text
event: log
id: <entry.id>
data: {"id":...,"ts":...,"level":...,"logger":...,"message":...}

event: gap
data: {"last_id":...,"oldest_id":...,"missed":...}

event: dropped
data: {"dropped": N}
```

`HEARTBEAT_SECONDS = 15.0`（`:31`）。

### 5.5 导出

`GET /logs/export`（`routes_logs.py:257-286`）：

- 参数：`start`/`end` 或 `days`，二者不能混用（`:175-221`）。
- `days` 必须 1..7；单次范围最多 7 天；必须在最近 7 天内（`:190-221`）。
- aware 时间戳会转到服务器本地时区再取日期；naive 时间戳按本地日历处理（`:203-221`）。
- 单日：文件不存在 -> 404；存在 -> `.log` 下载（`:268-276`）。
- 多日：没有任何文件 -> 404；否则打包 ZIP（`:278-285`）。

### 5.6 写入/导出双重脱敏

第一层（写入）：

- `publish` 对 message 调 `sanitize_log_message`（`log_broadcast.py:178`）。
- `DailyLogFileHandler.emit` 对 message 再次调 `sanitize_log_message`（`:393`）。
- `sanitize_log_message` 覆盖：
  - Authorization header（Bearer 保留前缀，值打码）
  - Set-Cookie / Cookie header（整体打码）
  - `SAPISIDHASH/SAPISID/HSID/SSID/APISID/SID` 等 cookie token
  - `x-goog-api-key`/`api-key`/`access-token`
  - `/api-keys/<value>` 路径
  - `?key=` / `&key=`
  - Bearer token
  - `sk-/rk-/pk-` token
  - `AIza...` Google API key
  （`log_broadcast.py:61-78`）

第二层（导出）：

- `_iter_sanitized_lines` 在读取归档时再次 `sanitize_log_message(line)`（`routes_logs.py:224-229`）。
- 因此即使归档文件是被旧版本/外部写入的未脱敏内容，导出也会再清洗一次。

### 5.7 时序图：日志写入与 SSE

```text
logging.info(...)
  -> root handlers
     -> stderr handler (配置级别)
     -> BroadcastHandler.emit
          -> broadcaster.publish(level, logger, message, ts)
             -> sanitize_log_message
             -> append ring (cap 5000; 优先淘汰 DEBUG/INFO)
             -> for each subscriber: call_soon_threadsafe(_deliver)
     -> DailyLogFileHandler.emit
          -> sanitize_log_message
          -> append data/logs/aistudio-YYYYMMDD.log
          -> flush

Client GET /logs/stream
  -> prepare_stream: validate level -> subscribe (max 8) -> replay (max 5000)
  -> yield gap? -> replay entries -> live entries
  -> every 15s idle: ": ping"
  -> queue full: dropped event
  -> disconnect: unsubscribe
```

---

## 6. `safe_*` dump

### 6.1 开关

`logger.py`：

- `is_dump_requests_enabled`（`:227-255`）：运行时 override > DEBUG/AISTUDIO_DEBUG/AISTUDIO_DUMP_REQUESTS > `settings.dump_requests` > `config.logging.dump_requests`。
- `is_dump_to_file_enabled`（`:121-154`）：运行时 override > AISTUDIO_DUMP_FILE/AISTUDIO_DUMP_TO_FILE > `settings.dump_to_file` > `config.logging.dump_to_file`；若未显式禁用，`dump_requests` 开启时默认开启。
- `is_debug_env_active`（`:207-224`）：`DEBUG/AISTUDIO_DEBUG/AISTUDIO_DUMP_REQUESTS` 的 truthy 值。
- `get_dump_dir`（`:167-194`）：override > `AISTUDIO_DUMP_DIR` > `config.logging.dump_dir` > `data/dumps`；相对路径落在 `data/` 下。
- `set_dump_requests` / `set_dump_to_file` / `set_dump_dir` 提供运行时覆盖（`:157-164`, `:197-204`, `:258-264`）。

### 6.2 转储字段

`safe_json_payload`（`:431-441`）：

```text
safe_text = sanitize_log_message(text)
try: parsed = json.loads(safe_text)
except: return safe_text
return _sanitize_payload_value(parsed)
```

`_sanitize_payload_value`（`:406-429`）：

- dict：敏感键（`_is_sensitive_key`）值替换为 `***`，其余递归。
- list：逐项递归。
- str：`sanitize_log_message`。
- 其他原样。

敏感键判定（`:362-413`）：

- 正则 `authorization|api[_-]?key|cookie|set-cookie|token|secret|password|auth|credential`。
- 精确键 `key/api-key/apikey/x-goog-api-key`。

`_safe_pretty_payload`（`:443-458`）：

- 尝试 JSON 解析；失败则 `sanitize_log_message(text)`。
- 成功则 `json.dumps(_sanitize_payload_value(parsed), indent=2)`。
- 控制台输出超过 4000 字符截断并标注总长度。

### 6.3 文件 dump

`dump_request_exchange`（`:460-580`）：

- `safe_url = sanitize_log_message(url)`。
- `safe_headers = _mask_headers(headers)`。
- `safe_query_params = _mask_query_params(query_params)`。
- `pretty_body = _safe_pretty_payload(body_text)`，空则 `(空请求体)`。
- 流式响应不写完整 body，只写“流式传输中...首包耗时”。
- `pretty_resp = _safe_pretty_payload(response_text)` 或空响应。
- 控制台文本再次 `sanitize_log_message(pretty_body/pretty_resp)`（`:494-496`）。

写文件时（`:498-535`）：

```python
record_data = {
  "req_id", "timestamp", "iso_timestamp", "method", "url",
  "client", "model", "account_id", "auth_user",
  "safe_headers", "safe_query_params", "safe_body",
  "status_code", "elapsed_ms", "is_stream",
  "safe_response_headers", "safe_response_body",
  "token_usage", "finish_reason",
}
```

- 只写 `safe_*` 字段；不写 `headers` / `raw_body` / `response_body` 等原始键（`:507-528`）。
- 文件路径：`get_dump_dir()/<clean_req_id>.json`，`req_id` 只保留 `\w-.`（`:500-502`）。
- 同时覆盖 `latest_request.json`（`:547-548`）。
- 异常只 warning，不影响请求（`:534-535`）。

### 6.4 仍会出现的字段

`record_data` 仍包含：

- `model`
- `account_id`
- `auth_user`
- `token_usage`
- `finish_reason`
- `status_code`
- `client`
- `elapsed_ms`

这些是排障必要的元数据，不属于凭据；但 `account_id/auth_user` 属于账号标识，导出/分享 dump 时需注意。

### 6.5 时序图：dump

```text
dump_request_exchange(...)
  -> safe_url / safe_headers / safe_query_params
  -> _safe_pretty_payload(body)  -> JSON parse + key masking + sanitize
  -> _safe_pretty_payload(resp)  -> 同上（流式跳过）
  -> 控制台消息再 sanitize 一次
  -> if dump_to_file:
       record_data = 仅 safe_* + 元数据
       write <dumps>/<clean_req_id>.json
       write <dumps>/latest_request.json
  -> dump_logger.info(消息)
```

---

## 7. 历史清理与上限

### 7.1 上限表

| 项 | 值 | 证据 |
|---|---|---|
| 环形缓冲容量 | 5000 条 | `log_broadcast.py:23` |
| SSE replay 上限 | 5000 条 | `log_broadcast.py:25` |
| 每个订阅者队列 | 500 条 | `log_broadcast.py:24` |
| 最大订阅者 | 8 | `log_broadcast.py:27` |
| SSE 心跳 | 15 秒 | `routes_logs.py:31` |
| 归档保留 | 7 天 | `log_broadcast.py:28` |
| 单次导出范围 | 7 天 | `routes_logs.py:32`, `:190-221` |
| dump 控制台正文 | 4000 字符截断 | `logger.py:443-458` |
| dump 文件 | 每次请求一个 + `latest_request.json` | `logger.py:512-548` |
| 保留天数最小值 | 1 | `log_broadcast.py:326-327` |

### 7.2 环形缓冲淘汰

`publish`（`log_broadcast.py:184-191`）：

```text
while len(entries) > capacity:
    for index, item in enumerate(entries):
        if item.level in ("DEBUG","INFO"):
            del entries[index]
            break
    else:
        entries.popleft()
```

即：

- 优先淘汰最早的低级别记录，尽量保留 WARNING/ERROR/CRITICAL。
- 如果全部都是高级别，仍然淘汰最旧一条（否则无法维持上限）。

### 7.3 归档清理

`cleanup_old_logs`：

- 启动时调用一次（`logger.py:337-339`）。
- 每日切换文件时调用（`log_broadcast.py:389-398`）。
- `cutoff = today - (retention_days - 1)`，默认保留今天 + 前 6 天。
- 只删除匹配 `aistudio-YYYYMMDD.log` 的文件；其他文件名不动。

### 7.4 导出范围

`resolve_export_range`（`routes_logs.py:175-223`）：

- `days` 与 `start/end` 互斥。
- `days` 1..7。
- `start/end` 必须同时提供。
- aware/naive 必须一致。
- `start <= end`。
- 跨度 <= 7 天。
- 必须在最近 7 天内。
- 返回按日期升序的日期列表。

### 7.5 历史数据生命周期

```text
运行中:
  log record -> ring (5000, 内存)
             -> daily archive (data/logs/aistudio-YYYYMMDD.log)
             -> SSE 订阅者

跨天:
  DailyLogFileHandler._open_for_day:
    cleanup_old_logs(cutoff = today - 6)
    打开新一天文件

启动:
  setup_logging -> cleanup_old_logs

导出:
  只允许最近 7 天
  单日 .log / 多日 .zip
  导出时再次 sanitize
```

---

## 8. 残余限制

1. **环形缓冲是进程内内存**：重启即丢失；多进程/多 worker 各自独立，不共享（`log_broadcast.py:140-144`, `:409`）。
2. **环形缓冲可能丢高级别日志**：当缓冲全部是 WARNING/ERROR/CRITICAL 时，仍会淘汰最旧一条（`:184-191`）。
3. **订阅者上限 8**：第 9 个 SSE 连接直接 503（`log_broadcast.py:203-222`, `routes_logs.py:127-135`）。
4. **队列满会丢日志**：每个订阅者 500 条；满时丢最旧并增加 `dropped`，客户端只看到计数，不看到被丢内容（`log_broadcast.py:257-267`）。
5. **replay 上限 5000**：超过部分通过 `gap` 事件告知，不补发（`log_broadcast.py:229-255`）。
6. **归档清理只匹配标准文件名**：自定义命名或旧格式文件不会被自动清理（`log_broadcast.py:333-346`）。
7. **保留天数固定 7**：`LOG_RETENTION_DAYS` 是常量，没有配置项；`cleanup_old_logs` 支持参数，但 `DailyLogFileHandler` 默认使用常量（`log_broadcast.py:28`, `:350-360`）。
8. **归档按服务器本地日期分桶**：SSE 事件时间戳是本地时区 ISO；跨时区部署时“日期”语义依赖服务器本地时区（`log_broadcast.py:171`, `:372-376`）。
9. **脱敏是正则启发式**：未覆盖的自定义凭据格式可能漏网；Cookie header 整体打码是最强保护，但非 header 形式的自定义 token 不一定命中（`log_broadcast.py:61-78`）。
10. **dump 仍包含账号标识**：`account_id/auth_user/model/token_usage` 会落盘；共享 dump 前需自行评估（`logger.py:523-543`）。
11. **dump 文件无自动清理**：只有归档日志有 7 天清理；`data/dumps` 由外部管理，代码不删除历史 dump（`logger.py:167-194`, `:512-548`）。
12. **`latest_request.json` 覆盖写**：只保留最近一次请求（`logger.py:547-548`）。
13. **dump 控制台截断**：超过 4000 字符只显示前 4000 + 总长度；文件 dump 不截断（`logger.py:443-458`）。
14. **SSE 心跳是 15 秒固定值**：没有配置项（`routes_logs.py:31`）。
15. **导出 ZIP 使用临时文件**：多日导出会先在 `TemporaryFile` 生成完整 ZIP 再分块发送（`routes_logs.py:238-249`）；极大归档会占用临时磁盘。
16. **元数据刷新 24h 与 3 天跳过的双层语义**：Free 账号每 24h 仍会进入 due 并执行一次刷新过程，但 tier 探测被 3 天新鲜度跳过；这会产生“刷新尝试时间更新、tier_checked_at 不变”的现象（`account_keepalive.py:316-330`, `account_info_refresher.py:246-271`）。
17. **`mark_refresh_attempt` 会覆盖错误字段**：下一次成功刷新会把 `last_refresh_error` 置空；这是预期，但排障时需要看历史归档而不是只看当前值（`account_store.py:770-778`）。
18. **保活只覆盖“有槽位或有控制页”的账号**：没有绑定槽位且控制页不可用的账号不会在 auth_update 中被回写；它们仍可能被 metadata_refresh 覆盖（`account_keepalive.py:357-378`, `:380-407`, `:409-503`）。
19. **全局 job 锁不排队**：busy 的 job 直接跳过；如果 metadata 长时间运行，auth_update 可能被推迟到后续 tick（`account_keepalive.py:262-263`, `:247-248`）。
20. **`active_activity` 不是保活必需项**：默认关闭；它只滚动控制页，不重建实例、不回收 idle（`account_keepalive.py:505-513`）。

---

## 9. 证据索引

| 主题 | 主要证据 |
|---|---|
| 保活配置 | `defaults/config.yaml:91-100`; `model_defaults.py:1535-1555`; `account_keepalive.py:55-90` |
| 调度循环/锁 | `account_keepalive.py:112-139`, `:162-251`, `:253-296` |
| auth_update | `account_keepalive.py:409-503`; `pooled_session.py:321-322`; `instance_manager.py:676-680` |
| 成功记账 | `account_store.py:761-777` |
| metadata_refresh | `account_keepalive.py:307-355`; `account_info_refresher.py:131-158`, `:232-380`, `:382-502` |
| 启动协调 | `api/app.py:194-301`, `:504-534` |
| 三 sink | `logger.py:310-350`; `log_broadcast.py:288-311`, `:368-441` |
| 环形缓冲 | `log_broadcast.py:23-28`, `:126-286` |
| 归档清理 | `log_broadcast.py:337-366`, `:420-441` |
| SSE | `routes_logs.py:31-154`; `log_broadcast.py:209-274` |
| 导出 | `routes_logs.py:175-286` |
| 脱敏 | `log_broadcast.py:61-78`, `:178`, `:393`; `routes_logs.py:224-229` |
| dump | `logger.py:121-265`, `:406-458`, `:460-580` |

---

## 10. C1/C2c：/health 静默、401 去重与日志沙盒

### 10.1 `/health` 免密与静默

- `/health` 注册在 `system_public_router`（无 Web 认证依赖），返回 `{status, busy, ui_debug, ui_debug_scenario}`（`routes_system.py:50-58`、`:200-203`、`app.py:560-561`）。
- 访问日志中间件对 `path == "/health"` 且 `status_code == 200` 的成功探针完全不记录：不写 INFO、不写 DEBUG、不进入广播 ring（`app.py:840-851`）。
- 因此容器 HEALTHCHECK 可以高频轮询，不会制造日志洪水；失败探针仍按普通 HTTP 日志记录。

### 10.2 `/v1beta/models` 缺 Key 401 五分钟去重

- `_MISSING_KEY_WARN_WINDOW_SECONDS = 300.0`，按 `(client_ip, path)` 去重；状态表有 1024 上限并按窗口清理（`dependencies.py:20-27`、`:76-104`）。
- 首次缺 Key 401 打 WARNING，附带截断到 80 字符的 `User-Agent` 与去除 query/fragment 的 `Referer`，不记录密钥本身（`dependencies.py:112-143`）。
- 窗口内重复项降为 DEBUG，并累计抑制次数（`dependencies.py:123-134`）。
- 只读模型列表路由使用 `require_api_key_or_web_session` → `_validate_api_key(read_only=True)`；生成路由仍用严格 `require_api_key`（`dependencies.py:215-292`）。
- `x-api-key`、`x-goog-api-key`、`key` 查询参数与 `Authorization: Bearer` 统一解析（`dependencies.py:146-176`）。

### 10.3 C2c 日志沙盒注入

- `ui_debug` 启动时，`UiDebugIsolation.enter()` 在 `validate_config()` 与任何 config/log/API Key 路由之前执行（`app.py:330-346`）。
- 它把 `AISTUDIO_CONFIG_PATH`、`AISTUDIO_LOG_DIR`、数据目录、账号目录、统计文件、模型缓存、签名缓存与 dump 路径重定向到临时目录，并同步 `settings`（`ui_sandbox.py:175-219`）。
- `DailyLogFileHandler`、`resolve_log_dir`、`resolve_data_dir` 都读取重定向后的路径，因此 UI 调试产生的日志只落临时目录，关闭时随沙盒清理，不污染真实 `data/logs`。
- 关闭顺序为 `sandbox.close()` → 取消后台任务 → keepalive → instance_manager → client；随后 `isolation.close()` 恢复原始环境（`app.py:372-388`、`:303-328`）。

---

## 11. C4：Client/Attempt 双口径与 90 天 daily/by_model

### 11.1 两套台账

`api/state.py` 明确分离：

- **Attempt**：每次真实上游执行。`RuntimeState.record`（`state.py:844-854`）由 `RoutingPipeline.record_success` / `_record_error` 调用；字段 `attempts/success/rate_limited/errors`（`state.py:203-241`）。
- **Client**：每个客户端生成请求只记一次终态。`RuntimeState.record_client`（`state.py:856-880`）由 `RoutingPipeline.record_client`（`routing_pipeline.py:718-739`）调用；字段 `requests/success/errors/aborted/rate_limited/rescued_requests`（`state.py:142-200`）。
- 429 同号重试或切号成功后，Client 只记 1 次成功且 `rescued_requests += 1`；Attempt 记录失败尝试和最终成功两条。
- `countTokens` 独立进入 `count_tokens` 台账（`state.py:243-269`、`:882-898`），不进入生成成功率分母，也不计入生成 Token 总量。

### 11.2 持久化与 90 天 daily

- stats 文件为 v2 envelope：`{schema_version, models, daily, count_tokens}`（`state.py:40-66`、`:725-750`）。
- `load_stats` 只从 `models`/`daily` 读取；`finish_reasons` 等保留键被跳过，不会生成幽灵模型行（`state.py:626-709`）。
- 自然日按 `Asia/Shanghai`（UTC+8）切分，保留 90 天并自动裁剪（`state.py:41-43`、`:404-439`）。
- `build_stats_payload` 对 `days=None` 返回全时段 `totals/client/attempt` + 最近 90 个 daily 点；`days=7|30|90` 把汇总限定到窗口并返回恰好 N 个连续日期（`state.py:516-583`）。
- 每日条目包含 `client`、`attempt`、`tokens`、`by_model`；Token 分项为 `prompt/cached/completion/reasoning/total`（`state.py:474-513`、`:554-573`）。
- `GET /stats` 支持 `?days=7|30|90`（`routes_system.py:205-208`）；`ui_debug` 沙盒使用同一渲染路径并保持统计恒等（`routes_system.py:99-197`、`ui_sandbox.py:2001-2145`）。
