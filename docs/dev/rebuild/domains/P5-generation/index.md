# P5 Generation / Protocol / Wire

状态：B5a/B5b/B6/C1c/C3 均已完成（PASS），当前处于单工作区 Stage 2.1 演进阶段。本文件是 P5 唯一域索引；PG/toolcall 历史基线分析详见 `09-pg-toolcall-analysis.md`。

## 1. 域职责边界与核心代码模块

- **四协议统一接入与转换**：原生 Gemini（`routes_gemini.py`/`gemini_service.py`）、OpenAI Chat（`routes_openai.py`）、Claude Messages（`routes_claude.py`）、OpenAI Responses（`routes_responses.py`），经 `application/contracts/` 与 `application/format_converters/` 归一为协议中立 `NormalizedGeminiRequest` 进入 `generation_service.py`。
- **WIRE / 工具调用硬门槛（PG-01..PG-08）**：`signature_cache.py` + `DUMMY_THOUGHT_SIGNATURE`、`sse_keepalive.py` 首窗预读与 15s keepalive、2s 工具流空闲 STOP、`wire_types.py`/`wire_codec.py` Struct 强制、`[toolshape]` 脱敏摘要（DEBUG 级别）、PG-08 宽松工具历史。
- **ATG（防截断合成工具网关）**：`application/atg/`（`off`/`prefer`/`required`、`mode_by_format`、五信号 guard、history purge、stream unwrapper），`text_model_pattern`/`non_text_model_pattern` 过滤。
- **模型发现与 `countTokens`**：`model_discovery.py`（pool-aware、`live_page`→`live_http` 依次重试、脏快照防护）、`token_counter.py`。

## 2. 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B5a PG-01..PG-04 工具调用硬门槛** | 签名缓存/dummy 兜底、首事件前真实 HTTP 状态、finish-only/2s 工具流 STOP、Struct 强制与 `[toolshape]` | 已完成（PASS） | `reviews/REV-B5a-toolcall.md` |
| **B5b OpenAI / 原生 Gemini / ATG 闭环** | 协议中立 `generation_service`、OpenAI Chat、ATG 全模块、`countTokens`、`signature_cache` 生产者 | 已完成（PASS） | `reviews/REV-B5b-openai-atg-seam.md` |
| **B6 模型发现加固与实机验收** | pool-aware `discovery_sessions`、动态 Paid 真值、防启动双预热、5/5 实机生成验收与 429/选择器修复 | 已完成（PASS） | `reviews/REV-B6-discovery-hardening.md`、`REV-B6-live-verification.md`、`REV-B6-stream-and-429-fix.md` |
| **C1c SSE Keepalive / 首窗 524 修复** | `KeepaliveIterator`（`create_task + asyncio.wait`、绝对首窗 deadline、15s 心跳、断连释放） | 已完成（PASS） | `reviews/REV-C1c-sse-keepalive-524-fix.md` |
| **C3 Claude / Responses 协议适配** | `/v1/messages` 与 `/v1/responses` 全量转换、PG-08/PG-01/PG-04 对齐、405 `Allow`/401 `WWW-Authenticate` | 已完成（PASS） | `reviews/REV-C3.md` |
| **Stage 2.1 Schema 归一化与 ATG 输入双轨演进** | ① 覆盖四协议的工具 Schema 归一化（`format_converters/common.py` + `wire_codec.py`：array `items` 缺失/None/`{}`/bool 补 `{"type":"string"}`、tuple 取首个非空、联合类型/`anyOf` 补 `type`、剔除 `required` 中不在 `properties` 的字段，防御 Gemini 400）；② 思维链截断单次 `WARNING` 告警（不盲目重试耗尽 Pro 配额）；③ ATG 输入双轨演进（保留 `off/prefer/required` 与工具历史净化，适配双引擎及多协议长工具流边界） | 进行中 / 持续强化 | — |

## 3. `reviews/` 审查报告索引

- [`reviews/REV-B5a-toolcall.md`](reviews/REV-B5a-toolcall.md) — B5a PG-01..04 工具调用复现审查（2026-10-30，PASS）
- [`reviews/REV-B5b-openai-atg-seam.md`](reviews/REV-B5b-openai-atg-seam.md) — B5b OpenAI/原生 Gemini/ATG 主链审查（2026-10-30，PASS）
- [`reviews/REV-B6-live-verification.md`](reviews/REV-B6-live-verification.md) — B6 本地实机综合验收与 B4 脱敏复核（2026-10-31/11-01，PASS）
- [`reviews/REV-B6-discovery-hardening.md`](reviews/REV-B6-discovery-hardening.md) — B6 模型发现/动态 Paid/防双预热审查（2026-10-31，PASS）
- [`reviews/REV-B6-stream-and-429-fix.md`](reviews/REV-B6-stream-and-429-fix.md) — B6 流式与 429/选择器五项微调审查（2026-10-31，PASS）
- [`reviews/REV-C1c-sse-keepalive-524-fix.md`](reviews/REV-C1c-sse-keepalive-524-fix.md) — C1c SSE Keepalive/首窗 524 修复审查（2026-11-02，PASS）
- [`reviews/REV-C3.md`](reviews/REV-C3.md) — C3 Claude Messages / OpenAI Responses 协议适配终审（2026-11-08，PASS）

## 4. 早期约束与文档归属留档

### B5a/B5b 早期约束（历史验收基线）

- 复现“酒馆预设把发送文本放进 `functionResponse`、回答放进 `functionCall.args`”的 PG/toolcall/preset 链路。
- PG-01..PG-04 必须有离线复现/修复/回归；PG-05..PG-13 逐项 triage，标阻塞或后置；1 账号 + 1 Context + 1 Page 无泄露且 `active_requests` 归零。

### 文档归属

- 步骤/退出条件：`05 §4.1`
- 决策门槛：`06-decision-log.md` D7
- 技术详情：`09-pg-toolcall-analysis.md`
- 交接指针：`10-handoff-prompt.md`
- 证据：`docs/dev/evidence/P5/`
- 不创建 `B5a.md`；不把 PG-01..PG-13 复制到 05/06/10。
