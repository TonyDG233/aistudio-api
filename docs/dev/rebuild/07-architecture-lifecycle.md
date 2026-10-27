# 07 当前实现架构与生命周期

- 性质：历史快照，基线 `rebuild` @ `e3a77c2`；仅只读参考，不表示当前事实源状态。当前台账/批次已在后续提交跟踪；以 `06-feature-ledger.md` 与 `06-decision-log.md` 为准。
- 范围：当前代码**实际装配与运行路径**，不是 05 的目标架构。仅只读代码与既有文档/证据，不重跑测试、不启动真实 CDP、不访问账号/IP/网络。
- 图例：实线=代码直接调用；虚线=问题标记；红色=已知问题节点；蓝色=控制面；绿色=请求实例面；`待验证`=缺少真实 CDP/集成证据。
- 证据口径：`file:line` 均指当前 `src/aistudio_api`；06 只作交叉索引，不作事实来源。

## 1. 当前运行架构

当前是**控制面 + 请求实例面**双结构：`BrowserSession` 负责控制面浏览器/默认 tab，`InstancePool` 以 `BrowserContext` 为 slot 承载请求；`InstanceManager` 以记录关联 slot，并把 create/close 转调 pool。lifespan 同时启动 warmup、实例 startup、模型发现、keepalive、空闲回收与 tier 校验（`api/app.py:54-267`），并在 `:270-290` 注册四协议 API 与 UI 路由。

```mermaid
flowchart TB
  subgraph BOOT[启动面]
    APP[FastAPI app]
    LIFE[lifespan]
    RT[RuntimeState]
    LOG[setup_logging]
  end
  subgraph CTRL[控制面 蓝色]
    CLI[AIStudioClient]
    BS[BrowserSession]
    CAP[Capture and Replay]
    STR[StreamingGateway]
  end
  subgraph INST[请求实例面 绿色]
    RP[RoutingPipeline]
    POOL[InstancePool]
    SLOT[InstanceSlot Context]
    PS[PooledSession chat Page]
    BSF[BuildSurface build Page]
  end
  subgraph SVC[应用服务]
    GS[GenerationService]
    KA[AccountKeepalive]
    IM[InstanceManager]
    ROT[AccountRotator]
    REF[AccountInfoRefresher]
    STORE[AccountStore]
  end
  subgraph EDGE[入口与观测]
    ROUTES[API routes]
    UI[Vue UI]
    BR[LogBroadcaster]
  end

  APP --> LIFE
  LIFE --> CLI
  LIFE --> RT
  LIFE --> STORE
  LIFE --> IM
  LIFE --> ROT
  LIFE --> REF
  LIFE --> KA
  LIFE --> LOG
  CLI --> BS
  CLI --> POOL
  CLI --> CAP
  CLI --> STR
  CLI --> BSF
  IM --> POOL
  KA --> IM
  KA --> ROT
  KA --> CLI
  ROUTES --> GS
  GS --> RP
  RP --> POOL
  POOL --> SLOT
  SLOT --> PS
  SLOT --> BSF
  PS --> CAP
  CAP --> STR
  GS --> CLI
  GS --> RT
  ROUTES --> RT
  LOG --> BR
  BR --> ROUTES
  UI --> ROUTES

  SLOT -. 同 Context 可挂两页 .-> BAD1[Context 双页]
  CLI -. build 用控制面 scope .-> BAD2[build 身份错位]
  RP -. retry_same 未置真 .-> BAD3[同号重试退化]
  KA -. auto switch 不看 pinned .-> BAD4[固定实例被改绑]
  LOG -. dump 写原文 .-> BAD5[脱敏绕过]
  RT -. attempt 与 client 双口径 .-> BAD6[统计口径混排]

  classDef bad fill:#ffe5e5,stroke:#cc0000,color:#990000
  classDef ctrl fill:#eef2ff,stroke:#4f46e5,color:#1e1b4b
  classDef inst fill:#f0fdf4,stroke:#16a34a,color:#14532d
  class BAD1,BAD2,BAD3,BAD4,BAD5,BAD6 bad
  class BS,CLI,CAP,STR ctrl
  class POOL,SLOT,PS,BSF,RP inst
```

- 控制面仍有独立默认 tab 与蓝绿切换（`session.py:221-310`），请求池则用隔离 Context（`instance_pool.py:963-1047`）；两者不是同一套 page 生命周期。
- `InstanceManager` 以 `slot_id` 关联用户记录与 pool，并把 create/close 调用转给 pool；`_max_instances()` 直接等于 `pool.max_contexts`（`instance_manager.py:809-810`）。
- `BuildSurface` 既可由 slot 惰性创建（`instance_pool.py:126-140`），也可由 client 控制面兜底创建（`client.py:219-224`）。
- `RoutingPipeline` 懒绑定当前 runtime client，候选在 `begin()` 冻结（`routing_pipeline.py:241-396`, `:592-614`）。

## 2. 请求生命周期

非流式 `generate` 与流式 `stream_generation` 共用 `_begin_route → next_candidate → acquire → generate/stream → release`；流式另有 `_LeaseHolder` 在生成器取消时兜底释放（`generation_service.py:79-91`, `:851-852`）。

```mermaid
sequenceDiagram
  autonumber
  participant R as Route
  participant G as GenerationService
  participant P as RoutingPipeline
  participant Q as InstancePool
  participant S as Slot 与 Pages
  participant C as Client
  participant U as Upstream

  R->>G: generate 或 stream_generation
  G->>P: begin model 与 api_format
  P->>P: eligible_accounts 与 candidates
  P-->>G: RouteContext
  G->>P: next_candidate
  P-->>G: Candidate
  G->>P: acquire
  P->>Q: acquire account channel surface
  alt 同账号 slot 已存在
    Q->>S: 复用 slot 并按需补页
  else Context 预算未满
    Q->>S: create_browser_context 与引导页
  else 有空闲 slot
    Q->>S: rebind 到目标账号
  end
  Q-->>P: InstanceLease
  P-->>G: lease
  Note over C: build 分支用控制面 session.request_scope
  G->>C: generate 或 stream 带 lease
  C->>U: chat capture/replay 或 build 9998
  U-->>C: response 或 SSE frames
  C-->>G: output 或 events

  alt 首帧前错误
    G->>P: handle_attempt_exception
    P->>P: record_switch 或 record_retry
    Note over G,P: 204 404 hang 走 retry 分支 但 retry_same 未置真
    G->>P: release lease
    G->>P: next_candidate
  else 已出内容后错误
    Note over G,R: 200 已出 只能把错误写进 SSE body
  end

  G->>P: record_success
  G->>P: release lease
  P->>Q: release
  Q->>S: reservations 减一
```

- 候选选择由 `begin()` 一次冻结；`acquire` 失败分两类：build 的 `surface_instance_busy` 只做 1s defer，quota 类才走 `next_candidate`（`generation_service.py:193-251`）。
- `retry_same` 只在 `record_success` 置回 False，`record_retry` 从不置 True（`routing_pipeline.py:514-522`, `:557-559`）；下一轮因此进入 `next_candidate` 换号，且没有 `record_switch` 记账（`generation_service.py:303-315`; `routing_safety.py:58-60`）。
- 流式错误分类传入 `has_yielded_data`；任一内容事件已 yield 后，429/auth 不再切号（`upstream_errors.py:195`, `:230`, `:261`）。
- `thought_signature` 与 `response_id` 只缓冲，不计入 `has_yielded_data`（`generation_service.py:660-663`），因此元数据阶段仍允许换号。
- Gemini 在路由前就返回 `StreamingResponse`，异常只能由生成器写成 SSE 错误块（`api_service_gemini.py:228-263`）；Claude 会先取首个事件，首事件前还能返回真实 HTTP 状态（`routes_claude.py:115-144`）。

## 3. 实例/Context/Page 生命周期

用户可见的“实例”不是 Context 数，也不是 Page 数：manager 记录按 `account+channel` 增长，`startup` 可为同账号不同渠道建两条记录（`instance_manager.py:117-246`），而 pool 同账号第二渠道复用同一 Context 只加页（`instance_pool.py:546-588`）。这与 05 的“一 Context 一 Page”目标直接冲突。

```mermaid
flowchart TD
  ST0[manager.startup] --> ST1[按启用渠道计算启动槽]
  ST1 --> ST2[逐渠道选合格账号]
  ST2 --> ST3[建记录 STARTING]
  ST3 --> CR{同账号已有 slot}
  CR -->|是| EN[ensure_channel 补页]
  CR -->|否| CT[create_browser_context]
  CT --> BP[create_page_in_context 引导页]
  BP --> RL{channel_role}
  RL -->|chat| CH[chat_counted 置真]
  RL -->|build| CB[关闭引导 chat 页]
  CB --> BU[BuildSurface 建 build 页]
  EN --> CH
  EN --> BU
  CH --> AC[记录 ACTIVE slot READY]
  BU --> AC
  AC --> LQ[请求 acquire 置 BUSY]
  LQ --> RS[release 只减 reservations]
  RS --> AC
  AC --> CL[manager.close 或换渠道]
  CL --> CK{in_flight 为 0}
  CK -->|否| BE[InstanceBusyError 或 pool 拒绝]
  BE --> AC
  CK -->|是| RC[后台 reclaim 或 close_channel]
  RC --> PG[关闭对应 page]
  PG --> DP[dispose_browser_context]
  DP --> DR[移除记录与 realm 服务]

  EN -. 同账号第二页 .-> P1[同 Context 双页]
  ST1 -. 实例数 不等于 Context 数 不等于 Page 数 .-> P2[计数口径混用]

  classDef bad fill:#ffe5e5,stroke:#cc0000,color:#990000
  class P1,P2 bad
```

- slot 关闭路径：`close_channel` 先拒绝在途，再只销毁该渠道页（`instance_pool.py:689-719`）；`close_instance` 弹出 slot 后 `close_pages + dispose context`（`:739-750`, `:1214-1231`）。
- build-only 在 `_load_slot` 末尾主动关闭引导 chat 页并设 `slot.session=None`（`instance_pool.py:1028-1036`）；装载期仍真实创建过该页，随后才建 build 页，存在一次额外渲染成本（`:1028-1051`）。
- manager 的在途检查与 pool 的实际关闭不是原子操作：manager 先读 `in_flight` 再调度 reclaim（`instance_manager.py:303-311`），pool 在后续锁内才再次拒绝（`instance_pool.py:745-746`）。
- `_state_from_slot` 把 pool 的 `READY/COOLDOWN` 等一律投影为 `ACTIVE`，而 `COOLDOWN` 在 pool 内无赋值点（`instance_manager.py:990-998`; `instance_pool.py:36-42`）。

## 4. Build Surface 生命周期

Build 面按 slot 惰性创建并绑定该 slot 的 `context_id`（`instance_pool.py:126-140`），但 CDP client 来自控制面 `self._session.ensure_browser_cdp_client()`（`client.py:172-177`）。就绪判定已改为 **native 9998 优先、signer 备用**：9998 连接即 `ready`，signer 后台探测失败不阻塞（`build_surface.py:1156-1187`）。

```mermaid
flowchart TD
  B0[ensure_ready] --> B1{in_flight 大于 0 且非同一就绪账号}
  B1 -->|是| E1[BUILD_BUSY]
  B1 -->|否| B2{页面存活 ready 且 last_error 空}
  B2 -->|是且 9998 可用或原地恢复成功| W0[启动 watchdog]
  B2 -->|否| B3[建页 在 lease context 或控制面]
  B3 --> B4[注入 cookies responder signer]
  B4 --> B5[导航 Build app 与过门重试]
  B5 --> B6{9998 app 已连接}
  B6 -->|否| E2[APP_NOT_CONNECTED]
  B6 -->|是| B7[native_ready 置真]
  B7 --> B8[__warmup__ 与 signer 后台探测]
  B8 --> W0
  W0 --> B9[ready]
  B9 --> S0[stream_generate]
  S0 --> S1{transport}
  S1 -->|native| S2[下发 spec 到 9998]
  S1 -->|signer| S3[page.execute signer]
  S2 --> S4[pump native 事件]
  S3 --> S5[binding 回推 chunk]
  S4 --> S6[Holder 解码]
  S5 --> S6
  S6 --> S7{error 或 timeout}
  S7 -->|是且未发帧| S8[retry native 或 fallback signer]
  S8 --> S1
  S7 -->|是且已发帧| E3[TIMEOUT 或错误]
  S7 -->|否| S9{done}
  S9 -->|否| S6
  S9 -->|是| S10[finally 清 queue 与 pending]
  E3 --> S10

  B3 -. 页面用 lease context 但 CDP 取控制面 .-> D1[lease 身份与控制面不一致]
  W0 -. watchdog 仅 realm 启用 .-> D2[非 realm 无 watchdog]
  S10 -. abort_stream 无 src 调用方 .-> D3[无主动中止入口]
  B8 -. 空 realm 回退全局 9998 表 .-> D4[连接串扰待验证]

  classDef bad fill:#ffe5e5,stroke:#cc0000,color:#990000
  class D1,D2,D3,D4 bad
```

- 复用快路径要求 `same_auth + page 存活 + ready + last_error 空 + 9998 可达/可恢复`（`build_surface.py:1015-1032`）；新页未就绪会被关闭（`:1180-1186`）。
- `_connections()` 在无 realm 时返回模块级全局表，且 realm 连接也会追加进全局表（`build_surface.py:150-162`）；`D4` 的真实串扰未做 CDP 验证，标 `待验证`。
- watchdog 只在 `_realm_id` 存在时启动（`build_surface.py:1312-1321`），非 realm 仅靠请求路径的 `_recover_warmup` 触发。
- `abort_stream` 只向 task queue 塞 error（`build_surface.py:1844-1854`），src 内无调用方；客户端断开后的取消行为依赖 ASGI/生成器回收，未实测。

## 5. 账号保活与切号

keepalive 是单循环 + 单 job 锁，四类任务按 `next_due` 轮询：metadata、auth update、auto switch、active activity（`account_keepalive.py:175-217`）。调度间隔来自 features，默认值在 `KeepaliveSettings`（`:39-85`）。

```mermaid
flowchart TD
  K0[AccountKeepalive.start] --> K1[1s tick 检查 next_due]
  K1 --> K2{job lock 空闲}
  K2 -->|否| KB[busy 返回仍推进 next_due]
  KB --> K1
  K2 -->|是| K3{task}
  K3 -->|metadata_refresh| M1[刷新 due 账号 metadata]
  M1 --> M2[mark_refresh_attempt]
  M2 --> M3{失败}
  M3 -->|是| M4[等下一周期 默认 24h]
  M4 --> K1
  K3 -->|auth_update| A1[save_pool_cookies]
  A1 --> A2{存在 chat_ready slot}
  A2 -->|是| A3[逐账号 mark_auth_saved]
  A2 -->|否| A4[控制面 save_current_cookies]
  A4 --> A5[mark_auth_saved active]
  A3 --> K1
  A5 --> K1
  K3 -->|auto_switch| S1[InstanceSwitcher.rebalance]
  S1 --> S2[plan_rebalance 按 eligibility 选号]
  S2 --> S3[pool.rebind 空闲 slot]
  S3 --> K1
  K3 -->|active_activity| X1[控制面 page scroll]
  X1 --> K1

  A2 -. build-only 无 chat_ready .-> Q1[build-only 不回写]
  A3 -. 保存失败仍 mark .-> Q2[auth_saved_at 空转]
  S2 -. SlotView 无 pinned .-> Q3[pinned 实例被改绑]

  classDef bad fill:#ffe5e5,stroke:#cc0000,color:#990000
  class Q1,Q2,Q3 bad
```

- `save_pool_cookies` 只遍历 `slot.session` 且要求 `chat_ready()`（`client.py:187-197`）；build-only slot 的 `session=None`，因此永不被回写。
- `_auth_update` 先保存，再对所有 `chat_ready` slot 无条件 `mark_auth_saved`（`account_keepalive.py:314-325`），保存失败与标记成功脱钩。
- `plan_rebalance` 只看 in-flight/state，`SlotView` 不含 `pinned`（`instance_switch.py:51-68`, `:81-87`）；`pool.rebind` 也不检查 pinned（`instance_pool.py:785-812`）。
- `auto switch` 使用 `eligible_accounts`/groups 做资格筛选（`instance_switch.py:89-136`），与“按 paid/free tier 映射、拒绝策略组”的既有决策冲突（06 D08 已记）。

## 6. 观测链路

日志是 root DEBUG 三 sink：stderr、broadcast ring、daily file（`logger.py:245-297`）；HTTP middleware 负责 access log 与按需 dump（`app.py:376-480`）。统计是 `RuntimeState` 双入口：上游 attempt `record` 与客户端 outcome `record_client`（`state.py:426-470`）。

```mermaid
flowchart LR
  REQ[HTTP 请求] --> MW[logging middleware]
  MW --> LOG[setup_logging root DEBUG]
  LOG --> H1[stderr handler]
  LOG --> H2[BroadcastHandler]
  LOG --> H3[DailyLogFileHandler]
  H2 --> BR[LogBroadcaster ring 5000]
  BR --> SSE[Logs SSE]
  SSE --> LV[LogsView]
  MW -->|dump 开启| DUMP[dump_request_exchange]
  DUMP --> DF[req_id.json]
  GS[GenerationService] --> RS[record attempt]
  GS --> RC[record_client]
  TC[token_counter] --> RT2[record_token_count]
  RS --> ST[stats.json]
  RC --> ST
  RT2 --> ST
  ST --> API[Stats API]
  API --> SV[StatsView 与 StatsOverview]

  DUMP -. 同时写原始 headers 与 body .-> O1[dump 脱敏绕过]
  SSE -. gap 只有 last_id 与 oldest_id .-> O2[前端读 missed dropped gap]
  RS -. attempt 分母 .-> O3[与 client 口径混排]
  RC -. client 分母 .-> O3

  classDef bad fill:#ffe5e5,stroke:#cc0000,color:#990000
  class O1,O2,O3 bad
```

- `dump_request_exchange` 同时写 `headers`/`raw_body` 和 `safe_headers`/`safe_query_params`（`logger.py:421-441`）；脱敏结果只进 `safe_*`，原始值照样落盘。
- 广播 ring 满时优先淘汰 DEBUG/INFO，保留 WARNING+（`log_broadcast.py:194-201`）；SSE 先订阅再 replay，gap 字段为 `last_id/oldest_id`（`routes_logs.py:34-46`, `:63-65`），前端读 `missed/dropped/gap`（`LogsView.vue:324-329`）。
- 统计写盘 2s debounce / 5s 强制（`state.py:393-418`）；`record` 与 `record_client` 都写入同一 `ModelStatsItem` 的不同字段（`state.py:426-470`），UI 把两套分母并列展示（`StatsOverview.vue:23-26`, `:46-108`）。

## 7. 问题点索引

| 问题 | 图 | 证据 file:line | 影响 |
|---|---|---|---|
| HTTP 200 已出后错误只能进 SSE body | 图2 | `api_service_gemini.py:245-257`, `:259-263` | Gemini 恒 200；客户端无法按状态码重试/换号 |
| `retry_same` 从未置真 | 图2 | `routing_pipeline.py:514-522`; `generation_service.py:305`, `:445`; `routing_safety.py:58-60` | 204/404/hang 的“同号重试”退化为换号，且绕过 `max_switches` 记账 |
| 元数据事件不计入 `has_yielded_data` | 图2 | `generation_service.py:660-663`; `upstream_errors.py:195`, `:261` | 内容事件前可换号、内容后不可；签名/响应 ID 阶段语义不一致 |
| 同账号一 Context 可挂 chat+build 两页 | 图1/图3 | `instance_pool.py:440-454`, `:546-558`, `:642-660`, `:1048-1051` | 偏离 05 单 Context 单 Page；页面与实例预算口径混淆 |
| `max_contexts` 同时是实例/Context/页预算 | 图3 | `instance_manager.py:809-810`; `instance_pool.py:202-211`, `:333-335`; `routing_safety.py:27` | 单实例下 chat/build 互斥，故障转移只剩 1s×2 defer |
| build 请求记控制面 scope，页面却在 lease Context | 图1/图4 | `client.py:589`, `:699`, `:172-177`; `instance_pool.py:131-135`; `session.py:146-160` | 账号身份与在途计数错位，keepalive busy 判定可能失真 |
| 空 realm 的 9998 回退全局连接表 | 图4 | `build_surface.py:150-154`, `:161-162` | 非 realm surface 可能认领其他 realm 的 9998 连接；真实串扰待验证 |
| watchdog 仅 realm 启用，`abort_stream` 无 src 调用方 | 图4 | `build_surface.py:1314-1321`, `:1844-1854` | 非 realm 断线只能等请求触发；无主动中止入口，断开后回收待验证 |
| build-only 不回写 cookie | 图5 | `client.py:187-197`; `account_keepalive.py:320-324`; `instance_pool.py:1028-1036` | build-only 账号 `auth_saved_at` 不前进，长期可能静默失效 |
| auth update 标记成功与保存结果脱钩 | 图5 | `account_keepalive.py:314-325` | 单 slot 保存失败仍标记已保存，掩盖凭据过期 |
| auto switch 不看 `pinned` | 图5 | `instance_switch.py:51-68`, `:81-87`; `instance_pool.py:785-812` | 后台 rebalance 可改绑用户固定实例 |
| dump 文件写原始 headers/body | 图6 | `logger.py:428`, `:433`, `:437`, `:440` | cookie/token 落盘，绕过总线脱敏 |
| 日志 gap 字段前后端不一致 | 图6 | `routes_logs.py:63-65`; `LogsView.vue:324-329` | 缺口提示可能显示空值，用户误以为日志完整 |
| attempt 与 client 双口径混排 | 图6 | `generation_service.py:401-407`, `:497-502`, `:850`; `state.py:426-470`; `StatsOverview.vue:23-26` | 429/错误率分母不一致，面板读数失真 |
| 实例关闭的在途检查非原子 | 图3 | `instance_manager.py:303-311`, `:630-633`; `instance_pool.py:698-699`, `:745-746` | 检查与关闭之间有竞态窗口；关闭失败会把记录回滚为 ACTIVE |
