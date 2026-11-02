# REV-C1c — SSE keepalive / 首窗 524 修复 独立只读离线审计

- Audit date: 2026-11-02 (UTC), fully offline.
- Target worktree: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`.
- Target branch / HEAD: `rebuild-upstream` / `3b40f2273bbe2f619717e494985d58f5c4eadd49`（工作区未提交改动，见 §2）。
- Main repo: `D:\LLM\self_programming\aistudio-api`，branch `rebuild` / HEAD `d34c548485bf3311f7a1093eddfdedd17977b997`。
- Audited delta: 2 tracked modified files + 2 new untracked files：
  - `M src/aistudio_api/api/gemini_service.py`
  - `M src/aistudio_api/api/routes_openai.py`
  - `?? src/aistudio_api/api/sse_keepalive.py`（194 行 / 6,463 bytes）
  - `?? tests/unit/test_sse_keepalive.py`（504 行 / 15,896 bytes）
- Method: read-only diff/source/test review；离线 `pytest`、`ruff check`、`pyright`、`git diff --check`、`git diff --cached --check`、config 跟踪状态检查。**未启动服务/浏览器，未发起任何网络请求，未修改任何源码/测试/配置，未 stage，未 commit，未 push。** 本报告仅写入目标树与主仓两份副本。

## 0. Verdict

**PASS — 0 个 P0，0 个 P1。**

C1c 要求的 SSE keepalive 与 PG-02 首窗行为均已落实：首事件使用 `create_task + asyncio.wait({task}, timeout)` 的有界等待，超时不取消 pending `__anext__` 且复用同一 task；首窗 deadline 为绝对时间，不被 `routing_wait` 重置；Gemini 与 OpenAI 两个流式面均保留 pre-header 真实非 200、deadline 后 200 + 首帧 `: keepalive\n\n`、body 周期心跳、post-header 错误以 SSE error 收尾、finally 释放 active request。全量门禁与期望完全一致（990/0/0、ruff 0、pyright 0、diff 干净、无 staged/commit）。

P2/Q/N 均为非阻塞，见 §9；按指令未修改任何源代码。

## 1. 审查重点逐项结论

| 审查项 | 结论 | 证据 |
|---|---|---|
| `api/sse_keepalive.py` 无 `asyncio.wait_for` | 通过 | 全文件仅 `asyncio.create_task` + `asyncio.wait`；`wait_for` 0 命中（`sse_keepalive.py:64-93`） |
| 使用 create_task + `asyncio.wait({task}, timeout)` | 通过 | `sse_keepalive.py:84-88` |
| timeout 不取消 pending task | 通过 | `asyncio.wait` 超时只返回空 done 集；`not done` 直接返回 sentinel，不 `cancel()`（`:88-90`）；测试断言 `not pending[0].done()`（`test_sse_keepalive.py:166-168`） |
| 同一 pending task 跨调用复用 | 通过 | `if pending: task = pending[0]`（`:81-82`）；超时保留、完成时 `pending.clear()`（`:91-92`）；测试断言第二次取回原事件（`test_sse_keepalive.py:170-172`） |
| 绝对首窗 deadline，不被 routing_wait 重置 | 通过 | deadline 循环外只算一次：Gemini `gemini_service.py:239-240`、OpenAI `routes_openai.py:140-141`；每轮重算 `remaining = deadline - loop.time()`（`gemini_service.py:244-246`、`routes_openai.py:145-147`）；OpenAI 对 routing_wait 显式 `continue`（`:152-153`），Gemini 经 `_render_stream_event` 返回 None 继续（`gemini_service.py:199-201, 242-250`） |
| env 默认 15，非法值回退 | 通过 | `DEFAULT_KEEPALIVE_INTERVAL_SEC = 15.0`（`sse_keepalive.py:28`）；`:45-61` 处理未设置/空白/非数字/NaN/inf/≤0；8 组参数化测试（`test_sse_keepalive.py:135-155`） |
| close/cancel/aclose 释放 task | 通过 | CancelledError → `await self.aclose()` 后 re-raise（`:144-146`）；`aclose()` 关闭幂等、取消并 await pending、再关闭底层 iterator（`:155-165`）；`_cancel_pending` 取走 task 并吞掉 Cancelled/StopAsyncIteration/异常（`:167-182`）；测试（`test_sse_keepalive.py:195-211`） |
| 无并发 anext | 通过 | 唯一在飞 task 保存在 `_pending` 单槽；Phase A 与 body 串行消费；任意时刻最多一个 `__anext__`（`:99-103, 124-147`） |
| Gemini pre-header 400/403/502 仍为真实非 200 | 通过 | 400/403 由 RequestError 等直接抛出，502 由 StopAsyncIteration/BrowserTransportError/AtgError 显式映射（`gemini_service.py:251-265`）；HTTP 测试断言 400/403/502 且非 event-stream（`test_sse_keepalive.py:394-416`） |
| Gemini 窗内空流非 200 | 通过 | `StopAsyncIteration` → `RequestError(502, ...)`（`gemini_service.py:251-256`）；测试断言 502（`test_sse_keepalive.py:442-458`） |
| Gemini deadline 后 200 + 首帧 keepalive | 通过 | 返回 `StreamingResponse` 后 body 首帧 `yield KEEPALIVE_FRAME`（`gemini_service.py:267-275`）；测试断言首帧恰为 `KEEPALIVE_FRAME` 且 elapsed < 0.1（`test_sse_keepalive.py:350-365`） |
| Gemini body 周期 keepalive | 通过 | `async for item in events` 中 str 直接 yield（`gemini_service.py:276-279`），由 `KeepaliveIterator.__anext__` 在超时时产生 `KEEPALIVE_FRAME`（`sse_keepalive.py:149-153`） |
| Gemini functionCall/tool 数据不丢 | 通过 | pending task 保留原 `__anext__`，首个可渲染事件不丢弃；测试断言 functionCall name/args、finishReason、usage（`test_sse_keepalive.py:254-300`） |
| Gemini post-header 异常/空 => SSE error | 通过 | 异常 `yield format_sse_error`（`gemini_service.py:288-289`）；loop 结束后 `not rendered_any` 也发 502 SSE error（`:284-287`）；空流有测试（`test_sse_keepalive.py:473-489`） |
| Gemini finally 释放 active_requests | 通过 | body `finally: await events.aclose()`（`gemini_service.py:290-291`）→ 关闭 `stream_generation` → 其 finally `pipeline.release`（`generation_service.py:487-489`）；测试断言 0（`test_sse_keepalive.py:300, 365`） |
| OpenAI PG-02 同款两阶段 | 通过 | 构造、Phase A、body 与 Gemini 对称（`routes_openai.py:127-208`） |
| OpenAI 首个可渲染事件跳过 routing_wait | 通过 | `candidate.kind == "routing_wait"` 时 `continue`，`first_event` 仍为 None（`:152-153`） |
| OpenAI pre-header 错误 JSON 非 200 | 通过 | `format_error_response(exc, "openai")`；空流返回 HTTPException 500 JSON（`:155-169`）；测试断言 400/403/502 且非 event-stream（`test_sse_keepalive.py:419-439`、`:460-470`） |
| OpenAI deadline 200 + keepalive | 通过 | Phase A 超时 break 后返回 StreamingResponse，body 首帧 keepalive（`:181-182`）；测试（`:368-388`） |
| OpenAI tool_calls/usage/tail close 保留 | 通过 | 正常迭代后 `renderer.close()` 产出终止帧（`:191-194`）；测试断言 tool_calls/finish_reason/usage/`data: [DONE]` 结尾（`:303-347`） |
| OpenAI post-header 错误 => openai_sse_error | 通过 | `except Exception as exc: yield openai_sse_error(exc)`（`:199-200`）；空流也走同款 502 错误（`:195-198`） |
| OpenAI cleanup | 通过 | body `finally: await events.aclose()`（`:201-202`）；测试断言 active_requests 0（`:347, 388`） |
| 测试 env 0.02 与真实延迟 ≥2 个间隔 | 通过 | `FAST_INTERVAL="0.02"` 且 autouse fixture（`test_sse_keepalive.py:42-47`）；真实延迟流 0.06s（3 个间隔）与 0.3s（15 个间隔）（`:93-99, 254-300, 350-388`） |
| 无真实 15s sleep | 通过 | 全量 pytest 11.91s；测试仅有被取消的 `sleep(10)` 与 0.06/0.3 的短延迟 |
| PG-02 两端口均有覆盖 | 通过 | pre-header 错误 3×2、窗内空流 2、deadline 后空流 2、慢首事件 header 2（`test_sse_keepalive.py:394-504, 350-388`） |

## 2. 工作树状态、范围与洁净

审计开始时 `git status --porcelain=v1`（目标树）：

```text
 M src/aistudio_api/api/gemini_service.py
 M src/aistudio_api/api/routes_openai.py
?? src/aistudio_api/api/sse_keepalive.py
?? tests/unit/test_sse_keepalive.py
```

- 2 个已跟踪修改 + 2 个未跟踪新增，与任务给出的 “2 modified + 2 untracked” 完全一致；报告落盘后目标树会额外多出本报告 1 个未跟踪文件。
- `git diff --stat`：`2 files changed, 108 insertions(+), 29 deletions(-)`（`gemini_service.py` 59 行变动、`routes_openai.py` 78 行变动；新增文件另计）。
- `git diff --cached --stat` 为空，`git diff --cached --check` 退出码 0：**没有任何 staged 内容**。
- 目标树 HEAD 仍为 `3b40f22`；主仓 HEAD 仍为 `d34c548`；**未提交、未 push**（目标分支 `rebuild-upstream...origin/rebuild-upstream` 无 ahead 记录；主仓 `rebuild` 的 `[ahead 41]` 为审计前既有状态）。
- `git diff --check` 退出码 0（仅 `routes_openai.py` 的 LF→CRLF advisory warning）。
- Root `config.yaml` 在两个仓库均未跟踪且被忽略：
  - 目标树：`git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`；
  - 主仓：`git check-ignore -v config.yaml` → `.gitignore:62:/config.yaml`；
  - 两仓库 `git ls-files config.yaml` 均为空，唯一 tracked 配置模板为 `defaults/config.yaml`。
- 审计期间未新增/修改任何配置、文档、测试或源码；仅写入本报告两份副本。

## 3. `sse_keepalive.py` 核心实现审计

### 3.1 超时语义

`next_with_timeout`（`sse_keepalive.py:64-93`）：

1. 无 pending 时以 `asyncio.create_task(iterator.__anext__())` 创建 task，并在提供 pending 槽时立即登记（`:83-87`）。
2. `done, _ = await asyncio.wait({task}, timeout=timeout)`（`:88`）；`asyncio.wait` 超时**不会**取消传入的 task。
3. `not done` → 返回 `KEEPALIVE_TIMEOUT`，task 原样留在 `pending`（`:89-90`）。
4. done → `pending.clear()` 后 `task.result()`，异常（含 `StopAsyncIteration`）原样抛出（`:91-93`）。

因此跨调用复用是“同一个 Task 对象被再次 `asyncio.wait` 观察”，不是重新调用 `__anext__`；不会重复消费上游事件，也不会丢失慢事件。

### 3.2 绝对 deadline

Gemini：`deadline = loop.time() + events.interval`（`gemini_service.py:239-240`）在循环外计算；每轮 `remaining = deadline - loop.time()`（`:244-246`）。`routing_wait` 由 `_render_stream_event` 返回 None（`:199-201`），循环继续但 deadline 不变。

OpenAI：同样在循环外计算 deadline（`routes_openai.py:140-141`），`routing_wait` 显式 `continue`（`:152-153`），每轮重算 remaining（`:145-147`）。长串 `routing_wait` 不会把首窗延长。

### 3.3 环境变量

`keepalive_interval_sec()`（`sse_keepalive.py:45-61`）：

- 未设置/空白 → 15.0；
- `float()` 失败 → 15.0；
- `NaN`/`inf`/`-inf`/`<= 0` → 15.0；
- 合法正有限值原样返回（如 `0.02`）。

### 3.4 关闭/取消

- `KeepaliveIterator.next_event` 对 `asyncio.CancelledError` 执行 `await self.aclose()` 后 re-raise（`:144-146`），避免取消路径遗留 task。
- `aclose()` 幂等（`_closed` 短路，`:157-159`），先 `_cancel_pending()` 再调用底层 iterator 的 `aclose()`（`:160-165`）。
- `_cancel_pending()` `pop()` 唯一 task，未完成则 `cancel()`，随后 `await task` 并吞掉 Cancelled/StopAsyncIteration/普通异常（`:167-182`），因此 pending 结果/异常总会被取回，不会产生 “Task exception was never retrieved”。
- 当前唯一生产消费方是 `KeepaliveIterator`（`gemini_service.py:225-233`、`routes_openai.py:127-135`），无并发消费者。

## 4. Gemini 流式面审计

文件：`gemini_service.py:219-297`。

- 构造：`KeepaliveIterator(generation_service.stream_generation(...), interval=keepalive_interval_sec())`（`:225-233`）。
- Phase A（`:239-265`）：
  - 只等待“可渲染 chunk”；`routing_wait`/空渲染事件不阻塞首窗计时；
  - 400/403 等异常直接向上抛出，由路由/异常处理器映射为真实非 200；
  - `StopAsyncIteration`、`BrowserTransportError`、`AtgError` 分别映射为 502（`:251-262`）；
  - 其他异常 `await events.aclose()` 后原样 re-raise（`:263-265`），保证泄漏窗口关闭。
- 200 分支（`:267-297`）：
  - 已有首 chunk 时先 yield 它；首窗超时则以 `KEEPALIVE_FRAME` 作为首帧（`:270-275`）；
  - `async for` 中 str（keepalive）直接透传，`StreamEvent` 经 `_render_stream_event` 渲染（`:276-283`）；
  - 流结束仍无任何可渲染事件 → `format_sse_error(RequestError(502, ...))`（`:284-287`）；
  - 迭代异常 → `format_sse_error(exc)`（`:288-289`）；
  - `finally: await events.aclose()`（`:290-291`）释放 pending task 并关闭 `stream_generation`，其 `finally` 再 `pipeline.release`（`generation_service.py:487-489`）。
- `handle_gemini_generate_content` 对 `stream=True` 直接返回该 `StreamingResponse`（`gemini_service.py:51-54`）；非流式路径未改。

## 5. OpenAI PG-02 流式面审计

文件：`routes_openai.py:112-210`。

- 非流式分支未改（`:118-125`）。
- 流式构造 `KeepaliveIterator(stream_generation(...), interval=keepalive_interval_sec())`（`:127-135`）。
- Phase A（`:140-169`）：
  - 绝对 deadline；`routing_wait` 跳过（`:152-153`），因此首个真正可渲染事件之前不会把控制事件当作首事件；
  - `StopAsyncIteration` → 关闭 iterator 并返回 HTTPException 500 JSON（`:155-166`）；
  - 其他异常 → 关闭 iterator 并 `format_error_response(exc, "openai")`（`:167-169`），400/403/502 等均为真实非 200。
- 200 body（`:173-208`）：
  - 有 `first_event` 时先渲染；否则首帧 `KEEPALIVE_FRAME`（`:176-182`）；
  - keepalive str 直接透传，其余交给 `OpenAIStreamRenderer.render`（`:183-190`）；
  - `rendered_any` 为真时 `renderer.close()` 补齐终止帧/`[DONE]`（`:191-194`）；为假时发 502 `openai_sse_error`（`:195-198`）；
  - 迭代异常 → `openai_sse_error(exc)`（`:199-200`）；
  - `finally: await events.aclose()`（`:201-202`）。
- `X-Accel-Buffering: no` 与 `Cache-Control: no-cache` 保留（`:204-208`）。

## 6. 测试审计

文件：`tests/unit/test_sse_keepalive.py`（25 个用例，全部离线）。

- 环境变量：8 组参数化（未设置、空、0、-1、nan、inf、非数字、0.02）→ 15/15/15/15/15/15/15/0.02（`:135-155`）。
- task 保留：0.01 超时后 `pending[0]` 未完成；第二次以 0.5 超时取回 "value" 且 `pending == []`（`:158-173`）。
- keepalive 迭代器：0.02 间隔 + 0.06 延迟源，先得到 `KEEPALIVE_FRAME`，随后取回数据，耗尽后 `StopAsyncIteration`（`:175-193`）。
- 关闭释放：pending 被取消、底层 generator 的 finally 被触发（`closed.is_set()`）（`:195-211`）。
- 跟踪管线：真实 `ScriptedPipeline` 子类计数 acquire/release（`:69-90`），不是空 fake。
- 真实延迟流：0.06s（3 个 0.02 间隔）与 0.3s（15 个间隔），无 15s 真实等待。
- Gemini：200 + keepalive + functionCall + finishReason + usage + active_requests==0（`:254-300`）。
- OpenAI：200 + keepalive + tool_calls + finish_reason + usage + `data: [DONE]` 结尾 + active_requests==0（`:303-347`）。
- 两端口 “慢首事件前先出 header”：直接驱动 `body_iterator`，断言首帧 keepalive、elapsed < 0.1、关闭后 active_requests==0（`:350-388`）。
- PG-02 pre-header 400/403/502：Gemini 与 OpenAI 各 3 例，断言状态码、非 event-stream、错误体（`:394-439`）。
- 窗内空流：Gemini 502（`:442-458`）、OpenAI 500（`:460-470`），均为非 200。
- deadline 后空流：两端口均 200 + keepalive + 末尾 SSE error（Gemini code 502、OpenAI type api_error）（`:473-504`）。
- 计数：C1b 基线 965 + 本文件 25 = 990，与全量结果一致。

## 7. 边界/竞态/取消分析

| 场景 | 分析结论 |
|---|---|
| deadline 恰好与事件完成竞争 | `asyncio.wait` 若已返回 done 则消费结果；若返回空 done 而 task 随后立即完成，sentinel 返回后 body 的 `async for` 复用同一已 done task，`asyncio.wait` 立即返回并消费结果，不丢数据、不重复消费 |
| routing_wait 反复出现 | 每轮重算 absolute remaining；不会重置首窗，也不会忙等（每次 next_event 都真实 await） |
| 首窗超时后 upstream 才报错 | body 迭代时异常 → SSE error；Gemini/OpenAI 均有对应 except |
| 首窗超时后 upstream 空结束 | body 结束 `rendered_any == False` → SSE error（两端口均有测试） |
| 客户端在 Phase A 取消 | `next_event` 捕获 CancelledError → `aclose()` → 取消 pending → 关闭底层 generator → re-raise；不会遗留 task |
| 客户端在 body 中途断开 | body 的 `finally` 调用 `aclose()`；显式 `body_iterator.aclose()` 路径已由测试证明释放 active_requests（`:350-388`） |
| pending task 在 keepalive yield 期间完成/抛错 | `aclose()`/下一次 `next_event` 都会取回结果或异常；异常不会静默丢失 |
| 重复 aclose | `_closed` 短路，幂等 |
| 并发 anext | 无：单消费者 + 单 `_pending` 槽 |

## 8. 门禁与实跑

全部离线，在目标工作树 `.venv` 下执行：

```text
$ .\.venv\Scripts\python.exe -m pytest -q
=> 990 passed in 11.91s            (期望 990/0/0)

$ .\.venv\Scripts\python.exe -m pytest -q tests/unit/test_sse_keepalive.py
=> 25 passed in 1.16s

$ .\.venv\Scripts\python.exe -m ruff check .
=> exit 0                          (0 error)

$ .\.venv\Scripts\python.exe -m pyright
=> 0 errors, 0 warnings, 0 informations

$ .\.venv\Scripts\python.exe -m pyright src tests
=> 0 errors, 0 warnings, 0 informations

$ git diff --check
=> exit 0                          (仅 LF→CRLF advisory warning)

$ git diff --cached --check
=> exit 0

$ git diff --cached --stat
=> empty                           (0 staged)
```

补充说明：

- `ruff format --check` 全仓不是 clean（92 个文件会重排）；其中 `gemini_service.py:192` 的重排建议在 HEAD `3b40f22` 的同一文件副本中已存在（用 `git show HEAD:...` 复核），**不是 C1c 引入**。C1c 新增的 `sse_keepalive.py`、`test_sse_keepalive.py` 与 `routes_openai.py` 均通过 format 检查。项目既有门禁口径是 `ruff check`，本次为 0。
- `pyright` 默认配置 `include = ["src"]`；额外显式跑 `src tests` 仍为 0。
- 未启动 `main.py server`、浏览器或任何网络探测；ASGI 测试使用进程内 `httpx.ASGITransport`。

## 9. 问题清单（P0/P1/P2/Q/N 全覆盖）

### 9.1 P0（阻塞）

无。

### 9.2 P1（提交前必须修复）

无。

### 9.3 P2（应修复，非阻塞）

- **P2-1 — `next_with_timeout` 在未传 `pending` 时超时会遗留 task。**
  - 位置：`sse_keepalive.py:81-93`。`pending is None` 时创建 task 但不登记；超时直接返回 sentinel，task 无引用、未取消、异常未取回。
  - 影响：当前生产路径全部经 `KeepaliveIterator` 传入 `pending`（`:138-140`），无现网影响；但该函数是导出 API（`__all__`），未来直接调用会在超时后泄漏一个仍在运行的 `__anext__` Task（可能伴随 “Task was destroyed but it is pending” / 异常未取回）。
  - 建议：将 `pending` 改为必填，或在 `pending is None` 且超时时 `task.cancel()` 并 await 回收。

- **P2-2 — 首窗 deadline 直接复用可配置的 keepalive 间隔。**
  - 位置：`gemini_service.py:240`、`routes_openai.py:141` 使用 `events.interval`；`keepalive_interval_sec()` 接受任意正有限值（`sse_keepalive.py:45-61`）。
  - 影响：默认 15s 安全；若运维把 `AISTUDIO_SSE_KEEPALIVE_SEC` 调到 >100s，Phase A 会迟迟不返回 header，重新落回 Cloudflare 524 窗口；若调到极小正数，则心跳过密、CPU/带宽浪费。
  - 建议：首窗使用独立常量上限（如 `min(interval, 15s)`），或对环境值增加合理上下界校验。

### 9.4 Q（需用户/维护者裁决）

- **Q-1 — OpenAI 窗内空流为 500，Gemini 为 502。** OpenAI `routes_openai.py:155-166` 返回 HTTPException(500)，Gemini `gemini_service.py:251-256` 返回 RequestError(502)。两者都满足“非 200”，且 500 是 C1c 之前的既有口径（新测试 `test_sse_keepalive.py:460-470` 将其固化）。请确认是否保持 500，还是统一为表示上游异常的 502。
- **Q-2 — `AISTUDIO_SSE_KEEPALIVE_SEC` 未在文档/默认配置/`.env.example` 中出现。** 代码读取该 env（`sse_keepalive.py:29, 45-61`），但全仓文档 0 命中。请确认是否需要作为正式运维开关补齐文档，以及是否应改为 `config.yaml` 配置项。
- **Q-3 — 无整体生成超时。** 首窗 524 已修复，但上游若永久挂起，body 会无限发送 keepalive，请求永不终止（连接/实例长期被占用）。请确认是否需要额外的整体 idle/生成超时；若需要，应作为后续独立改动。

### 9.5 N（非阻塞观察 / 残余风险）

- **N-1 — post-header 异常路径无专门测试。** 空流 post-header 已覆盖两端口（`test_sse_keepalive.py:473-504`），但“已产出数据后上游抛错 → SSE error”仅由代码审查确认（`gemini_service.py:288-289`、`routes_openai.py:199-200`）。该 except 为既有模式，C1c 未引入回归。
- **N-2 — “routing_wait 不重置 deadline” 无直接测试。** 代码每轮重算 `remaining`（`gemini_service.py:244-246`、`routes_openai.py:145-147`），逻辑正确；测试未构造长串 routing_wait 断言总耗时。
- **N-3 — 无 ASGI `http.disconnect` 级测试。** 清理契约通过显式 `body_iterator.aclose()`（`test_sse_keepalive.py:350-388`）与 pre-header 错误路径验证。Starlette 1.10 在断连时取消 `stream_response`，会展开 body 的 `finally`，但真实断连集成测试仍是更强证据。
- **N-4 — “routing_wait 后紧接 pre-header 错误” 仍无直接测试。** 现有 pre-header 测试在首个事件前就抛错（`test_sse_keepalive.py:394-439`）；该场景在 Phase A 中会先跳过 routing_wait、再映射错误，逻辑成立但未断言。
- **N-5 — `ruff format --check` 全仓不 clean。** 92 个文件（含 `gemini_service.py:192` 的既有格式偏差，HEAD 版本同样存在）；C1c 新增/改动的 4 个文件中仅该既有行不满足 format。`ruff check` 为 0。
- **N-6 — keepalive 以 `str` 作为哨兵。** `KeepaliveIterator.__anext__` 返回 `T | str`（`sse_keepalive.py:149-153`），依赖 `T` 不是 `str`；当前 `T = StreamEvent`，安全。若未来复用该泛型于字符串流，需改为专用 sentinel。
- **N-7 — Phase A 的 `await events.aclose()` 可能掩盖原始异常。** 若底层 iterator 的 `aclose()` 自身抛错，Gemini `:252/258/261/264` 与 OpenAI `:156/168` 的清理会覆盖原始错误。概率低；可包 `try/except` 保原始异常优先。
- **N-8 — `aclose()` 无超时地 await pending task。** 先 `cancel()` 再 await，符合标准取消语义；若底层在取消后仍不合作，关闭可能被拖慢（非挂死，除非底层忽略取消）。
- **N-9 — routing_wait-only 流的双端语义分叉。** OpenAI 把 `routing_wait` 渲染为 `": resolving-instance\n\n"`（`openai_response.py:163-164`），因此会被计入 `rendered_any`，最终以正常 stop + `[DONE]` 收尾；Gemini 将其视为不可渲染，最终会发 502 SSE error。真实 `stream_generation` 在 routing_wait 后必产出 usage 或抛错，该分叉基本不可达，仅作记录。
- **N-10 — 幂等/关闭后行为无显式单测。** 重复 `aclose()` 与关闭后 `next_event`（`sse_keepalive.py:134-135, 157-159`）实现简单、审查通过，但无专门用例。
- **N-11 — 计数一致性。** C1b 基线 965 + `test_sse_keepalive.py` 25 = 990，与期望 990/0/0 精确吻合。
- **N-12 — tracked diff 哈希对换行敏感。** 本报告记录的 8,993 bytes / SHA256 为本次 Windows 工作区 `git diff` 原样字节；若 `core.autocrlf` 不同，哈希会变化。

## 10. 只读声明、提交状态与 SHA256

- 本次审计**只读**：未修改任何源代码、测试、配置、文档或 git 索引；仅写入本报告的目标树副本与主仓副本。未启动服务/浏览器，未联网，未 stage，未 commit，未 push。
- **未提交、未 push**：目标树 HEAD 仍为 `3b40f2273bbe2f619717e494985d58f5c4eadd49`；主仓 HEAD 仍为 `d34c548485bf3311f7a1093eddfdedd17977b997`。目标树工作区为 2 modified + 2 untracked（外加本报告），主仓除本报告外干净。
- SHA256（本次审查的 tracked diff，2 files）：
  - bytes: `8,993`
  - SHA256: `277D2C07261342D4F612B98DB14AAA99B26F676F7EF0527044E6F17F944EEA26`
- SHA256（C1c 产物文件）：
  - `src/aistudio_api/api/sse_keepalive.py`: `66323055F591781C57D63242C043659841E443C718D9101DD5AB90D15BA01F6C`
  - `src/aistudio_api/api/gemini_service.py`: `5FA1B5E1A46B0A03B4293707BDA4759F964513821A6472D8735FCF33711EEA78`
  - `src/aistudio_api/api/routes_openai.py`: `FBB44A020B67D65A23C1A923AC646DC983CAFAF0373583B175EA525188A3B14D`
  - `tests/unit/test_sse_keepalive.py`: `2735F416847F8856D89C270855D1D5E37169B3E47FC4907D6D0929DE54490744`
- 本报告写入两份字节完全一致的副本：
  - `.kilo/worktrees/rebuild-upstream/docs/dev/rebuild/domains/P5-generation/reviews/REV-C1c-sse-keepalive-524-fix.md`
  - `docs/dev/rebuild/domains/P5-generation/reviews/REV-C1c-sse-keepalive-524-fix.md`
  - 两份副本的 SHA256 在审计回复中给出；不在文内嵌入自身哈希（嵌入会改变文件内容）。

## 11. Blockers

**None.** 未发现 P0/P1。按指令未修复任何源码。

## 12. C1c-2 复核闭环（2026-11-02 追加，只读离线）

- 复核对象：目标树 HEAD `3b7a8b8`（`3b7a8b881e5fccf15d0d80326550868fae6c913e`），工作区 6 个已跟踪修改；主仓 HEAD `86bee18`（`86bee1809f9511d9c8e2593c640fc79ea1b68ce4`）。
- 只读边界：本节追加前目标树 `git status --porcelain=v1` 恰为 6 个 `M`；追加后仅多出本报告 1 个 `M`，主仓也仅本报告 1 个 `M`。未修改任何源码/测试/配置，未启动服务/浏览器，未联网，未 stage、未 commit、未 push。
- 门禁（目标树 `.venv`，全程离线，逐字结果）：
  - `pytest -q` → **1012 passed in 12.75s**（期望 1012/0/0）
  - `pytest -q tests/unit/test_sse_keepalive.py` → **47 passed in 1.98s**
  - `ruff check .` → exit 0（All checks passed，0 error）
  - `pyright` → **0 errors, 0 warnings, 0 informations**
  - `pyright src tests` → **0 errors, 0 warnings, 0 informations**
  - `git diff --check` → exit 0（仅 LF→CRLF advisory warnings）
  - `git diff --cached --check` → exit 0；`git diff --cached --stat` 为空（0 staged）
- 分支状态：目标树 `rebuild-upstream...origin/rebuild-upstream [ahead 1]`；主仓 `rebuild...origin/rebuild [ahead 42]`。均为本地提交，**未 push**。

### 12.1 原问题逐项闭环

| 原项 | 闭环结论 | 证据（file:line） |
|---|---|---|
| P2-1 | **已闭环** | `next_with_timeout` 已整体删除（全仓 0 命中）；唯一创建 task 的 `_ensure_pending` 在 await 前同步写入单槽 `self._pending`（`sse_keepalive.py:133-139,154`），超时保留同一 task（`:160-163`），关闭时 `_cancel_pending` 取消并回收（`:194-211`）；`__all__` 不再导出该函数（`:214-226`）。测试 `test_next_event_preserves_pending_task`（`test_sse_keepalive.py:251-265`）断言超时后 task 仍存活、再次调用取回结果且槽位清空。 |
| N6 | **已闭环** | 哨兵改为唯一 `object()` 单例（`sse_keepalive.py:38-39`），`__anext__` 返回该单例（`:171-175`）；Gemini/OpenAI 均以 `item is KEEPALIVE_SENTINEL` 判定（`gemini_service.py:288-291`、`routes_openai.py:184-187`）。测试以 `is KEEPALIVE_SENTINEL` 断言（`test_sse_keepalive.py:275,300,318,352`）。 |
| N7 | **已闭环** | 两个流式面的 pre-header 清理全部改为 `with contextlib.suppress(Exception): await events.aclose()`：Gemini 4 条路径（`gemini_service.py:259-277`），OpenAI 2 条路径（`routes_openai.py:160-170`）；原始异常仍原样抛出/映射，`CancelledError`（BaseException）不被吞掉。 |
| Q1 / N9 | **已闭环** | OpenAI 窗内空流由 500 改为 `RequestError(502, "generation produced no stream events")`（`routes_openai.py:160-166`），502 映射为 `api_error`（`format_errors.py:30,221,247`）。`routing_wait` 在 Phase A 被显式跳过、不占首窗（`routes_openai.py:155-158`、`gemini_service.py:253-257`）；post-header 的 `routing_wait` 不计入 `rendered_any`，只有 routing_wait 的 200 流最终发 SSE error 且无 `[DONE]`（`routes_openai.py:190-203`）。测试：窗内空流 502（`test_sse_keepalive.py:611-626`）；routing_wait→pre-header 400/502（`:761-807`）；routing_wait-only 预算内 502（`:810-823`）；预算后 200 + keepalive + 末尾 api_error（`:826-841`）。 |
| P2-2 / Q2 / Q3 | **已闭环** | 新增独立 `AISTUDIO_SSE_PREREAD_WINDOW_SEC`（`sse_keepalive.py:31,79-90`），未设置/非法时跟随 keepalive（`:87-90`，keepalive 默认 15s：`:29,68-76`）；两端改用 `preread_window_sec()`（`gemini_service.py:243`、`routes_openai.py:144`）。8 组环境值解析 + 跟随/独立测试（`test_sse_keepalive.py:213-247`）。文档：`.env.example:50-55`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:423-425`，其中 `:425` 明确“没有整体生成/空闲总超时”。 |
| N1 | **已闭环** | post-header 已产出数据后上游抛错 → SSE error 且释放：Gemini `gemini_service.py:300-303` + 测试 `test_sse_keepalive.py:665-680`；OpenAI `routes_openai.py:204-207` + 测试 `:683-696`；两者均断言 `active_requests == 0`。 |
| N2 | **已闭环** | 连续 routing_wait 不重置绝对 deadline：Gemini `gemini_service.py:242-249`、OpenAI `routes_openai.py:143-150`；测试构造 20×10ms routing_wait + 0.05s 预读窗，断言 200、首帧 keepalive 且晚到内容仍渲染（`test_sse_keepalive.py:844-880`）。 |
| N3 | **已闭环（补强）** | 新增 ASGI `http.disconnect` 集成测试（`test_sse_keepalive.py:699-758`），断言底层 generator `finally` 触发且 `pipeline.active_requests == 0`。Gemini 侧复用同一 `KeepaliveIterator` 清理契约，未单独加断连用例（残留观察，非阻塞）。 |
| N4 | **已闭环** | routing_wait 后紧接 pre-header 错误：Gemini/OpenAI 各 2 例（400/502），断言真实非 200、非 event-stream、含 error body（`test_sse_keepalive.py:761-807`）；实现先跳 routing_wait 再映射错误（`gemini_service.py:253-277`、`routes_openai.py:154-170`）。 |
| N8 | **已闭环** | `aclose()` 对 pending 的 post-cancel await 增加 `CLOSE_AWAIT_TIMEOUT_SEC = 1.0` 上限（`sse_keepalive.py:33,194-211`），超时注册 `_consume_task_outcome` 后返回，避免关闭挂死；测试把上限降到 0.05s，断言 `aclose()` <0.5s 且释放后底层 `finally` 执行（`test_sse_keepalive.py:329-361`）。 |
| N10 | **已闭环** | `aclose()` 幂等 + 关闭后 `next_event`/`__anext__` 安全抛 `StopAsyncIteration`（`sse_keepalive.py:151-152,177-192`）；测试 `test_aclose_is_idempotent_and_next_event_after_close_is_safe`（`test_sse_keepalive.py:306-326`）。 |

### 12.2 仍未闭环但非阻塞的 N（原文保留）

- **N-5**（原文）：`ruff format --check` 全仓不 clean。92 个文件（含 `gemini_service.py:192` 的既有格式偏差，HEAD 版本同样存在）；C1c 新增/改动的 4 个文件中仅该既有行不满足 format。`ruff check` 为 0。
  - 复核：现该行因新增 import 位移到 `gemini_service.py:195`；`git show HEAD:src/aistudio_api/api/gemini_service.py | ruff format --check -` 仍 exit 1，确认非本次改动引入。**非阻塞。**
- **N-11**（更新）：C1b 基线 965 + `test_sse_keepalive.py` 47 = 1012，与全量 1012 精确吻合。**非阻塞。**
- **N-12**（原文）：tracked diff 哈希对换行敏感。本报告记录的 8,993 bytes / SHA256 为本次 Windows 工作区 `git diff` 原样字节；若 `core.autocrlf` 不同，哈希会变化。
  - 复核：本次 6 文件 diff 为 36,684 bytes（见 §13）；仍对换行敏感。**非阻塞。**

### 12.3 复核结论

**PASS — 0 个未闭环 P0/P1/P2/Q。** N-1/N-2/N-3/N-4/N-6/N-7/N-8/N-9/N-10 已闭环或补强；N-5/N-11/N-12 为非阻塞观察。按指令未修复任何源码。

## 13. 复核产物 SHA256

- 6 文件 tracked diff（`.env.example`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md`、`gemini_service.py`、`routes_openai.py`、`sse_keepalive.py`、`test_sse_keepalive.py`）：`36,684` bytes；SHA256 `27A3FEF193F0443FC5752732FAECCDB56B18CAE21D24FA7126D4CCE752ED8852`。
- `src/aistudio_api/api/sse_keepalive.py`：`7,949` bytes；`A5B39BF1F7D25B03CEE3812B9E693DDE941F4A2CB0C2DE67BFC827AB317AEFFB`。
- `src/aistudio_api/api/gemini_service.py`：`11,194` bytes；`F9F6D229E7407B8CEF87A1A2B3F8246CB316995BEE46345AEDC936A548DEE23D`。
- `src/aistudio_api/api/routes_openai.py`：`9,266` bytes；`4059E98F3C14B8EA39A577BDEFCA0F2F999A82590857DD1164C12BAEDFC6F26F`。
- `tests/unit/test_sse_keepalive.py`：`27,779` bytes；`00D60C3E7448D50C9135AEC7AFAE69CDDA39508C92D6069F171141D53E0982FB`。
- `.env.example`：`2,765` bytes；`D9B15AD873215A7BE048B450B39B3B410DEC18C2A3E736FE5035644C9DE46788`。
- `docs/PROTOCOL_AND_TOOLCALL_SPEC.md`：`49,049` bytes；`E4C129219A585541FFCD625B3C71E137B706C896751C24CEBDC5C90DD3A4D18D`。
- 两份报告副本追加后应字节一致；其 SHA256 在最终复核回复中给出（不嵌入文内，避免自指）。

## 14. 复核后状态

- **未提交**：目标树仍为 6 个已跟踪修改 + 本报告 1 个修改；主仓仅本报告 1 个修改。
- **未 push**：目标树 `rebuild-upstream` ahead 1、主仓 `rebuild` ahead 42，均为本地既有提交。
- **未 stage**：`git diff --cached --stat` 为空，退出码 0。
- 本节仅追加审计结论，未修复源码，未改变任何门禁口径。
