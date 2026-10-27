# WP5 二期（P4.5）执行看板

> 目的：在上下文压缩/交接后仍能恢复完整决策与进度。任何实现偏离本文件必须先改本文件并说明原因。
>
> 基线：分支 `p1-ark-merge`，起始提交 `4cbb266`，工作树干净。
> 质量门基线（2026-10-19 实跑）：ruff check 通过；ruff format 155 files；pyright 0 errors；pytest 571 passed。

## 1. 用户已确认的决定（不可自行改写）

| ID | 决定 |
|---|---|
| U1 | `max_switches=2` 与 `MAX_RETRIES=5` **并存**：同账号重试走重试次数；只有“明确换账号/换实例”的动作才消耗切换预算。两层上限同时生效，禁止无限切号。 |
| U2 | **必须支持跨账号并发**（级别 B Realm）。`realm_mode` 默认 `true`，`max_realms` 默认 `2`，惰性创建；单账号场景不额外开 context。 |
| U3 | 账号级路由覆盖存入 `AccountMeta.routing`，随 `registry.json` + `meta.json` 持久化，**不新增 routing.json**。 |
| U4 | 测试断言允许按新契约更新（groups 的 `enabled`/`weight`、routing 字段等）。 |
| U5 | 纯后端；不改 `web/` 与静态产物；UI 全部 WP7。 |
| U6 | 不浪费 pro 账号 `gemini-4-argon` 额度（每天 50 次）；真机优先 free/flash，argon 仅做必要验证（目标 ≤2 次）。 |
| U7 | 每个提交前需用户批准；不自行 push；完成后以单测 + 真机结果汇报，并给 `git diff --numstat`/commit stats。 |
| U8 | 文档必须同步；本看板持续更新，防上下文压缩丢细节。 |

## 2. 边界

- **WP5 v1**：保留 `resolve_surface` / `match_group` / `check_instance_quota` / `SurfaceDecision` / `SurfacePolicyError` 的既有导出与输出；旧单测除 groups 形状外不得回归。
- **WP10**：`switch_to_account` / `try_switch_account` / 双保活回写语义不变；仅把在途门槛扩展到全部 Realm。`auto_switch` 在 Realm 模式只改新会话排序，不拆除在役 Realm、不打断在途流。
- **WP8**：格式层不感知路由；只新增 `routing_wait` SSE 注释帧渲染与错误形状映射。
- **WP9**：官网渠道仅注册 `enabled=false, implemented=false`，不实现。

## 3. 配置契约

```yaml
channels:
  chat:     { enabled: true,  max_instances: 2 }   # pg/playground
  build:    { enabled: false, max_instances: 2 }
  official: { enabled: false, max_instances: 0 }

routing:
  max_switches: 2
  global_max_instances: 2
  max_realms: 2
  realm_mode: true
  borrow: false
  prewarm: false
  circuit_breaker:
    window_seconds: 600
    failure_threshold: 5
    cooldown_seconds: 300

slots: []   # [{account_id, channel, enabled, note}]

groups:
  - name: pro
    enabled: true
    weight: 1.0
    match_tier: [pro, ultra, plus]
    allowed_surfaces: [chat]
    max_page_instances: 2
```

- `features.build_surface` 是 legacy alias：仅当 YAML 未显式给出 `channels.build` 时映射到 `channels.build.enabled`；输出 `build_surface` 镜像 `channels.build.enabled`。
- 默认值必须复现 v1 的单账号行为；跨账号只在需要第二个账号同时在线时才创建第二个 Realm。
- 四层预算取最严：全局实例、渠道实例、组 `max_page_instances`、Realm 环境数。
- 账号级覆盖：
  ```json
  "routing": {
    "surfaces": ["chat", "build"],
    "weight": 1.0,
    "model_surfaces": {"gemini-3.8-flash": "build"}
  }
  ```
  只能收窄，不能放宽能力；缺省=跟随组。

## 4. 决策管线

`①能力解析 → ②候选池 → ③组约束 → ④排序 → ⑤实例落地 → ⑥失败链`

- 渠道在 ① 固定，链内不换面。
- ④ 排序 = 单请求内候选账号/实例的尝试顺序：黏性所有权 → 冷却健康 → 轮转/权重（分数低者优先）→ account_id 确定性兜底。
- ⑤ 热实例直用 → 预算内惰性建页/Realm → 不可用则顺延。
- ⑥ 仅首字节前：borrow（若开）→ 换实例（`max_switches`）→ 熔断 → 显式错误；已出首字节绝不切号。
- 错误码：`surface_group_disabled` 403、`surface_channel_disabled` 403、`surface_model_guard` 403、`surface_no_capable_account` 403、`surface_quota_exceeded` 429、`surface_switch_exhausted` 503、`surface_instance_not_ready` 503。

## 5. 文件所有权与阶段

### Phase 1：配置 + 纯策略（可并行）
| 文件 | 负责内容 | 并行独占 |
|---|---|---|
| `application/channel_registry.py` | 渠道注册表、前缀解析、multi-mode | Agent A |
| `application/account_routing.py` | 账号覆盖归一/收窄 | Agent B |
| `infrastructure/account/account_store.py` | `AccountMeta.routing` + CRUD | Agent B |
| `infrastructure/gateway/model_defaults.py` | channels/routing/slots/group enabled+weight、legacy alias | 主 agent |
| `application/surface_orchestrator.py` | GroupPolicy/select_group/capability/错误码 | 主 agent |
| `defaults/config.yaml` + 根 `config.yaml` | 配置双写 | 主 agent |
| 测试 | `test_channel_registry.py`、`test_account_routing.py`、`test_features_routing_config.py`、更新 `test_features_groups.py` | 各自 |

### Phase 2：管线 + 级别 A
- `application/routing_safety.py`（预算/熔断）
- `application/routing_pipeline.py`（①–⑥ 编排）
- `infrastructure/gateway/instance_manager.py`（预算取最严 / 级别 A）
- `application/generation_service.py`（非流式走管线、routing_wait、失败链）
- `application/upstream_errors.py`（routing-aware switch）
- `application/account_orchestrator.py`（activate_candidate、account_id 记账）
- `infrastructure/gateway/client.py`（surface/lease、in_flight 聚合、snapshot）
- `api/routes_gemini.py`/`routes_openai.py`/`routes_claude.py`（SSE 注释帧）
- 测试：`test_routing_safety.py`、`test_routing_pipeline.py`、`test_generation_routing.py`、`test_instance_manager.py`

### Phase 3：Realm 级别 B + API
- `infrastructure/browser/cdp_client.py`：browser-level CDP、BrowserContext CRUD
- `infrastructure/gateway/realm_pool.py`：Realm 池、RealmSessionAdapter
- `infrastructure/gateway/build_surface.py`：WS 连接按 Realm 解复用、非流式聚合
- `application/streaming.py`/`capture.py`：收窄为协议依赖
- `api/routes_system.py`：`/surface/instances`、`/routing/state`
- `api/routes_accounts.py`：`GET/PUT/DELETE /api/accounts/{id}/routing`
- `api/state.py`/`app.py`：pipeline/instance/realm 生命周期
- 测试：`test_realm_pool.py`、`test_surface_instances_api.py`

### Phase 4：真机与证据
- 证据目录：`docs/dev/evidence/wp5b-20261019/`
- 脚本：`harness/wp5b_set_config.py`、`wp5b_call.py`、`wp5b_instances.py`
- 阶段：A 零配置回归 / B 裸名矩阵 / C 预算失败链 / D borrow / E 槽位 Realm 跨账号 / F 渠道与账号覆盖
- argon 预算：优先 free/flash；argon 仅验证守卫与 chat-only，目标 ≤2 次。

## 6. 关键实现细节

- `AccountMeta.to_dict()` 每次刷新/保活都会整体重写 `meta.json`，`routing` 必须进入 dataclass 的 `from_dict`/`to_dict`，否则被覆盖。
- Build WS sink 目前是全局 `_APP_WS_CONNS`（`build_surface.py:130`）；级别 B 必须按 Realm 解复用。首选注入 `WebSocket` 包装追加 `?realm=`；失败则降级串行 warmup 并记录。
- `active_account` 在 Realm 模式 = 默认账号/新会话黏性，不是唯一在线账号。
- `in_flight_count` 必须聚合 chat + build + 全部 Realm，WP10 切号门槛依赖它。
- 非流式 build 目前不存在；需用 `BuildSurface.stream_generate` 聚合成 `ModelOutput`。

## 7. 进度日志

| 时间 | 阶段 | 状态 | 证据/命令 |
|---|---|---|---|
| 2026-10-19 12:30 | 计划批准、看板建立 | 完成 | 本文件 |
| 2026-10-19 12:38 | Phase 1 配置/纯策略 | 完成 | 新增 channel_registry/account_routing/AccountMeta.routing；单测 680 passed；pyright 0；ruff 通过 |
| 2026-10-19 12:39 | Phase 2 管线/级别 A | 完成 | commit `ec35c8d`；721 passed；pyright 0；ruff/format 通过 |
| 2026-10-19 12:53 | Phase 3 跨账号 Realm | 完成 | commit `28c981f`；731 passed；pyright 0 |
| 2026-10-19 13:20 | Phase 4 真机（v2） | 完成 | chat/official/group/quota/cross-account；argon 0；证据 README |
| 2026-10-19 13:25 | v2 收尾 | 完成 | commit `4035d36`；全量 732 passed；ruff/format/pyright 通过 |
| 2026-10-19 14:35 | v3 设计重定向 | 完成 | `docs/dev/WP5-V3-DESIGN-20261019.md`；UI 稿与 PLAN 已更新；v2 实例层不视为最终交付 |

## 8. Phase 4 补充决策（v2 中间态，已被 v3 取代）

> 以下为 v2 实现的临时口径，保留作历史证据；**最终执行以 `docs/dev/WP5-V3-DESIGN-20261019.md` 为准**。

- v2 曾把“一个存活 Realm 计一个全局实例槽”；v3 改为页面数与环境数分开计数，取消该补丁。
- v2 曾让活跃账号使用默认会话、非活跃账号创建 Realm；v3 取消默认/Realm 分立，改为统一 Context 实例池。
- v2 的 borrow 复用“其它账号空闲且就绪的 Realm”；v3 保留 borrow 语义，但挂到统一实例池上。

## 9. v3 重定向（2026-10-19）

- **v2 功能审计不通过**：default/realm 分立、Realm 按账号硬绑定、`max_realms`/`global_max_instances` 重叠、pro-only 账号能力缺失、跨组选账号缺失、页面/环境计数不一致。
- **v3 目标**：统一 Context 实例池；请求期派发前切换/装载；实例切换方向（最受限槽位优先 + 渠道亲和）；额度池；页面/环境预算分开；模型列表规范前缀。
- **唯一执行口径**：`docs/dev/WP5-V3-DESIGN-20261019.md`。
- **v2 提交**：`cdb9e15`、`ec35c8d`、`28c981f`、`4035d36` 保留为中间态，未 push；v3 实现可复用渠道注册表、组约束、账号 routing、失败链、borrow、槽位计划数。
- **下一步**：按 v3 出逐文件实现计划 → 实现 → 单测/真机 → 提交（不 push）。