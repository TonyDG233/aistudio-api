# 出口池最终方案 v2（按调用预算 + 维护优先）

- 仓库：D:\LLM\self_programming\aistudio-api
- 基线：`242fd36`（管理器 v2 与四份 REV 之后的本地 HEAD；未 push）
- 日期：2026-11-23
- 性质：v2 定稿文档，单一版本口径；**不施工**。本文不修改代码、测试、配置、defaults、web、data/tools；不执行 git add/commit；不访问 VPS、账号文件、真实 Google 或外网。
- 依据：`AGENT.md` §4；`docs/dev/rebuild/domains/P1-account/research/exit-pool/EVALUATION.md`；同目录 `CONTRACT.md`；`REV-2.1.5.12`（fanout 机器接口）、`REV-2.1.5.13`（租约与 defer）、`REV-2.1.5.14`（管理器 v2 预算/调度/烧毁）、`REV-2.1.5.15`（verify 事件上报）；`data/tools/exit-manager/` v2 实现；app 提交 `224fd1e`、`25003bd`、`d09f591`、`a19d81d`、`242fd36`。
- 证据口径：代码与审查结论优先于旧文档。本文与 `data/tools/exit-manager/` v2 及 app 现有实现逐项对齐；旧版本文的“按账号容量”“503 作为无容量”“2 WARP + 3 fanout”“lease granted/category/ttl 120”等口径全部作废（见 §附录 C）。

---

## 0. 结论（v2 单一版本）

1. **池子**：fanout 总上限 4 条 = paid 3 条（2 条主力 + 1 条热备）+ free_overflow 1 条；WARP 目标 3 条、上限 6 条。`paid` 只落 fanout，`free` 只落 WARP，`free_overflow` 仅在全部 WARP 不可用且维护队列积压时对 free 应急开放；paid 永不落 WARP，paid 永不使用 overflow。直连应急阀默认关闭，设计口径仅允许 maintenance，generation 永不直连。
2. **预算**：窗口 1800s；paid 软 30 / 硬 45 / 日 400；free 软 50 / 硬 75 / 日 2400；每出口维护预留 20、探针预留 6；日上限只约束 generation；租约 TTL 30~60s。维护/探针严格吃各自预留，生成不得占用。
3. **接口**：`POST /v1/lease` 为主入口（`account/lane/class/calls/request_id`；成功返回 `exit_id/socks_url/generation/ttl_s/budget_left`；无容量 **429 `no_capacity` + `retry_after_s`**）；`GET /v1/assign` 仅兼容；`POST /v1/events` 接收 `region_403 | verify_account | other_403` + `canary`；`GET /v1/status` 只返回聚合白名单；`request_id` 幂等 600s 且持久化。
4. **烧毁**：同一健康出口 30 分钟内 ≥2 个不同账号 verify → `cooling` 120 分钟，到期先 L0 探活；单账号 verify 不烧出口；`disabled` 为配置硬开关，任何事件/探针/refresh 都不覆盖；`quarantined` 只走人工 `recover`。
5. **维护与补充**：健康循环驱动 L0/L1 与 `replenish_tick`；paid/free/free_overflow 都覆盖；target/cap、5 分钟冷却、每小时 3 次上限；候选最终必须经健康探针才可分配；未配置补充命令不视为错误（tick 跳过，显式调用只告警）。

---

## 1. 最终运行态全景

### 1.1 特殊模式（`AISTUDIO_EXIT_MODE=external`）

```text
aistudio-api 进程（browserless；external 下 browser 引擎启动即 fail-fast）
  RoutingPipeline / GenerationService
    ├─ 生成请求（Generate/stream/countTokens 经槽位）
    │    └─ _LeasedBrowserlessSession.request_scope
    │         ├─ 每次请求 lease 1 次 generation（冷启动首个请求复用建会话租约）
    │         ├─ 200 → 使用账号绑定出口的 socks_url → signer → 上游
    │         └─ 429 no_capacity → ExitBudgetWaitError（HTTP 429 + Retry-After）
    │              → 槽位保持 UNSTARTED，不换候选、不切号、不中途换出口
    ├─ region 403 → ExitArbitration（现有 canary）
    │    ├─ exit_bad → POST /v1/events → 管理器隔离/迁移 → 槽位按 D-05 重建
    │    ├─ account_bad / inconclusive → 记录；不烧出口
    │    └─ verify_account → POST /v1/events（canary=inconclusive，fire-and-forget）
    └─ verify-account 403 → 现有扫码验证（独立 Chromium，不进出口池预算）
  AccountInfoRefresher / Keepalive / RotateCookies / 导入探测
    └─ 每次带凭据调用 → resolve_credential_exit(category=maintenance)
         ├─ 200 → 使用账号绑定出口，完成调用
         ├─ 429 no_capacity → defer(retry_after_s)，保持账号 due，绝不推进 24h
         └─ 管理器不可达/载荷非法 → fail-closed，维护按 ≥300s 短周期重试
  VerifyAccountService（独立 Chromium，默认直连 AISTUDIO_VERIFY_PROXY）
    └─ 不属于出口池预算；不做软件层硬限流
  UI SettingsView『外部出口池（只读）』
    └─ 管理器在线/健康数/隔离/冷却/绑定数/窗口与日余量/维护队列/最近切换
        │ 127.0.0.1:8788 或 172.17.0.1:8788（Bearer + allowed_client_cidrs）
        ▼
 宿主 exit-manager（systemd；Python 标准库；单写者）
  POST /v1/lease    account/lane/class/calls/request_id
      200 {exit_id,socks_url,generation,ttl_s,budget_left}
      429 {error:no_capacity,retry_after_s,lane,class} + Retry-After
      400/401/403/404/405 参数/鉴权/来源/路由错误
  GET  /v1/assign   account/kind/lane（兼容入口，内部等价 lease(gen,1)）
  POST /v1/events   region_403 / verify_account / other_403 + canary
  GET  /v1/status   聚合只读状态（无 socks/token/exit id/账号）
  POST /v1/admin/recover  人工恢复 quarantine（disabled 返回 409）
  出口注册表：滑动窗口调用数 + 每车道日计数 + cooling/quarantine + generation
  账号绑定表：HMAC(account) -> exit_id + generation + lane + kind + last_confirmed_at
  健康循环：refresh_fanout → replenish_tick → L0/L1；cooling 到期先 L0
  烧毁/隔离：
    ├─ canary exit_bad / L1 region / 连续 L0/L1 失败 → quarantined（人工 recover）
    ├─ 同出口 30min ≥2 个不同账号 verify → cooling 7200s（仅 healthy 出口）
    ├─ WARP 3 条（free，目标 3/上限 6；wireproxy 独立 key/端口）
    ├─ fanout 最多 4 条（paid 3 = 2 主力 + 1 热备；free_overflow 1）
    └─ 直连应急阀（默认关；仅 maintenance；不进自动池）
```

### 1.2 默认模式（`AISTUDIO_EXIT_MODE` 缺失/非 external）

```text
aistudio-api → Settings.proxy_url → 现有全局代理链路，完全不变
不构造 ExitResolver、不发任何 /v1/*、不改 settings.proxy_url
UI 仍显示『已配置代理/直连』
出口管理器即使存在也不被访问
```

硬边界：默认模式零管理器请求、零 resolver、零 UI 新字段；`AISTUDIO_EXIT_*` 只走环境变量，不进 `config.yaml` / `config.schema.json`，不进 UI 设置项。

---

## 2. 出口数据模型

### 2.1 ExitState v2

| 字段 | 语义 |
|---|---|
| `id` / `kind` | 出口 id；`warp` / `fanout` |
| `lane` | `paid` / `free` / `free_overflow`；WARP 默认 free，fanout 默认 paid，应急 fanout 必须显式 `free_overflow` |
| `socks_url` | `socks5://` / `socks5h://`；fanout 必带 socks 用户名+密码；只进 `/v1/lease` 响应，永不进 `/v1/status`/UI/日志 |
| `country` / `region` | JP/KR/...；迁移优先同区域 |
| `generation` | WARP 轮换/fanout 换节点时 +1；app 缓存失效依据 |
| `status` | `healthy` / `probing` / `cooling` / `quarantined` / `disabled` |
| `budget.window_s` | 滑动窗口，默认 1800s |
| `budget.events` | `[ts, class, count]`，启动时按窗口裁剪 |
| `budget.day_key` / `daily_total` | UTC 日计数；**只累加 generation** |
| `budget.soft_limit` / `hard_limit` / `daily_limit` | 按 lane 取 paid 30/45/400 或 free 50/75/2400 |
| `budget.maint_reserve` / `probe_reserve` | 20 / 6；生成不得占用 |
| `cooldown_until` / `burn_reason` / `burn_count` / `last_burn_at` | verify 连坐烧毁与观测 |
| `verify_events` | `[ts, account_hmac]`；30min 窗口内去重账号数达到阈值才烧 |
| `quarantined_at` / `last_error` / `last_error_at` / `fail_count` | 隔离与探活观测 |
| `last_checked_at` / `last_exit_ip` / `last_rotated_at` | 观测 |

与旧模型差异：旧 `account_soft_cap` / `paid_reserved_accounts` / `per_account_window_cap` / `per_account_daily_cap` **全部删除**；速率与容量完全由每出口预算窗口决定，绑定数只影响新绑定时的排序，不构成硬容量。旧的分级冷却（`min(上次×2, 14400s)`）不再使用，verify 连坐固定 `burn_cooldown_s=7200`。

### 2.2 Binding v2

| 字段 | 语义 |
|---|---|
| `account` | `HMAC-SHA256(hmac_key, account_id)`；管理器无法反推 |
| `exit_id` / `generation` | 绑定出口与出口身份代数 |
| `bound_at` / `last_confirmed_at` | 首次绑定 / 最近一次成功 lease 确认 |
| `kind` | `chat` / `build`；首次绑定确定，之后不改写 |
| `lane` | `paid` / `free`；由 app 按 tier 传入，不由管理器猜 |

绑定位置：管理器侧（权威事实源）。app 只做 30~60s 租约缓存 + `_last_known`，不持久化出口映射，避免双写。管理器的绑定没有“失败计数”字段；迁移由出口状态（隔离/烧毁/未知）与人工动作触发，不按负载迁移。

### 2.3 预算类别（按调用计）

一次调用 = 一个带账号凭据或匿名探针的上游 HTTP/RPC 请求经过某出口。流式无论多久只计 1 次；重试/重新签名按实际尝试分别计次。

| 类别 | 包含 | 预留 |
|---|---|---|
| `generation` | GenerateContent / streamGenerateContent / countTokens（经槽位） | 无独立预留；新绑定用 `soft - 26`，已绑定用 `hard - 26`；不得占用维护/探针预留 |
| `maintenance` | tier/email/RotateCookies/keepalive/导入/手动刷新 | 20/出口/窗口；只能吃自己的预留 |
| `probe` | 管理器 L1 匿名探测、app canary ListModels | 6/出口/窗口；只能吃自己的预留 |
| `L0` | 只连本地 socks 端口，不发 Google 请求 | 不计入任何预算 |

口径：按出口统计；滑动窗口日志（非固定窗口）；日上限只约束 generation，维护/探针不写 `daily_total`，因此日上限耗尽后维护/探针仍可在窗口预留内前进；维护/探针是预留+延迟，不是永久跳过。

---

## 3. 参数默认值（与管理器 v2 一致）

| 参数 | 默认 | 说明 |
|---|---|---|
| `budget.window_s` | 1800s | 30 分钟滑动窗口 |
| `budget.paid.soft/hard/daily` | 30 / 45 / 400 | paid fanout 每出口 |
| `budget.free.soft/hard/daily` | 50 / 75 / 2400 | free WARP 每出口 |
| `budget.maint_reserve` | 20 | 每出口每窗口维护预留；生成不得占用 |
| `budget.probe_reserve` | 6 | 每出口每窗口探针预留；生成不得占用 |
| `budget.burn_cooldown_s` | 7200 | verify 连坐烧毁冷却 120 分钟 |
| `budget.verify_cluster_window_s` | 1800 | verify 连坐窗口 30 分钟 |
| `budget.verify_cluster_threshold` | 2 | 不同账号数阈值 |
| `budget.lease_min_ttl_s` / `max_ttl_s` | 30 / 60 | 单次租约 TTL |
| `budget.max_calls` | 10 | 单次 lease 的 `calls` 上限 |
| `health.interval_s` | 120 | 健康循环间隔 |
| `health.timeout_s` | 8 | L0/L1 超时 |
| `health.fail_threshold` | 2 | 连续失败进入 quarantine |
| `replenish.paid_target` / `paid_cap` | 3 / 3 | paid fanout：2 主力 + 1 热备 |
| `replenish.free_target` / `free_cap` | 3 / 6 | WARP 目标 3、上限 6 |
| `replenish.overflow_target` / `overflow_cap` | 1 / 1 | free_overflow 应急 1 条 |
| `replenish.cooldown_s` | 300 | 补充冷却 5 分钟 |
| `replenish.hourly_limit` | 3 | 每 lane 每小时最多 3 次 |
| `replenish.interval_s` | 60 | 健康循环内的补充节流下限；实际节奏受 `health.interval_s=120s` 约束 |
| `MAX_FANOUT_CAP` | 4 | `paid_cap + overflow_cap > 4` 加载期直接拒绝 |
| `ttl_s`（兼容字段） | 60（示例）/ 300（默认） | 仅 `/v1/assign` 兼容与历史配置；租约 TTL 以 `lease_*_ttl_s` 为准 |
| `watchdog` / 定时轮换 | 无 | 不做 12MB/35 分钟定时，不做请求数阈值，不做后台自动 rebalance |
| `security.allowed_client_cidrs` | `127.0.0.0/8`、`::1/128`、`172.17.0.0/16` | 管理器来源限制 |
| `listen.host` | `127.0.0.1` / `::1` / `localhost` / `172.17.0.1` | 拒绝公网绑定 |

**生成额度关键口径**：`soft/hard` 是“出口窗口总量阈值”，包含维护/探针预留。新绑定 generation 上限 = `soft - (maint_reserve + probe_reserve)`；已绑定 = `hard - 26`。因此：

- paid：新绑定 generation 上限 4/窗口，已绑定 19/窗口；
- free：新绑定 generation 上限 24/窗口，已绑定 49/窗口；
- `/v1/status` 的 `window_remaining = soft - total` 只是“距软阈值的剩余总量”，不是“还能生成的次数”，不要按它估算新账号生成额度。

**当前样例与目标的关系**：`config.example.json` 当前静态配置为 3 条 WARP（free）+ 2 条 paid fanout + 1 条 free_overflow fanout。`replenish.paid_target=3` 表示**配置补充命令后**把 paid 补到第 3 条（热备）；未配置命令时保持 2 条 paid 运行，不视为配置错误。`MAX_FANOUT_CAP=4` 目前只校验 `replenish.paid_cap + overflow_cap`，静态 `exits` 数量不在加载期单独计数（`REV-2.1.5.14` N-5），部署时仍需人工保证静态 fanout ≤4。

**容量公式**（不再使用旧的按账号容量）：

```text
N_lane ≈ max(
  ceil(peak_generation_calls_window_lane / (soft_limit_lane - maint_reserve - probe_reserve)),
  ceil(daily_generation_calls_lane / daily_limit_lane),
  min_healthy_lane + 1
)
```

维护容量按“每出口 20/窗口”单独核算，不与生成混算。

---

## 4. 调度算法

### 4.1 车道隔离与账号粘性

```python
def pick_exit(lane, exclude=set(), cls, calls, is_bound):
    overflow_ok = (no healthy free exit) and (free maintenance backlog > 0)
    for e in exits sorted by (bound_count, order):
        if e.id in exclude or e.status != healthy: continue
        if not serves_lane(e.lane, lane, overflow_ok): continue
        if budget_ok(e, cls, calls, is_bound): return e
    return None

def lease(account, lane, cls, calls, request_id):
    if request_id and idempotency_hit(account, lane, cls, calls, request_id):
        return cached_result                      # 不重复扣，跨重启保留 600s
    binding = bindings.get(account)
    if binding is not None and binding.lane == lane:
        state = exits[binding.exit_id]
        if state.status == healthy and serves_lane(state.lane, lane, overflow_ok):
            target = state; is_bound = True   # 粘性：健康且车道一致，原样返回
        else:
            target = pick_exit(lane, {binding.exit_id}, cls, calls, is_bound=False)
            if target is None: return 429 no_capacity
            bind(account, target, binding.kind, lane)   # 整组迁移
    else:
        target = pick_exit(lane, set(), cls, calls, is_bound=False)
        if target is None: return 429 no_capacity
        bind(account, target, kind, lane)
    if not budget_ok(target, cls, calls, is_bound):
        if cls == maintenance: maint_backlog[lane] += 1
        return 429 no_capacity
    target.budget.add(now, cls, calls)
    if cls == generation: target.budget.daily_total += calls
    binding.last_confirmed_at = now
    return {exit_id, socks_url, generation, ttl_s(30~60), budget_left}
```

粘性与迁移规则：

1. 首次带凭据调用时绑定；之后一直沿用。
2. 只在以下情况迁移：出口 `quarantined` / `cooling` / `disabled` / unknown；出口 generation 变化；人工显式迁移/恢复。**不因负载、窗口用量、新账号加入而迁移旧账号**。
3. 迁移优先同 country/region（JP→JP、KR→KR），减少地理跳变。
4. 迁移是管理器内存+原子写；旧出口上的 app 会话不会立刻消失，下一次请求按 D-05 重建（release → `active_requests==0` → close → restart → 一次重试），绝不中途热切换。
5. 迁移失败（无同 lane 健康目标）时保留旧绑定并返回 429 `no_capacity`，不把账号错误地送到别的 lane。

### 4.2 维护类调用：预留优先、defer 不放弃

- 已有绑定 → 绑定出口；无绑定 → 选绑定数最少、order 最小的健康出口（同 lane）。
- 绑定出口维护预留耗尽 → 429 + `retry_after_s`；管理器记录 `maint_backlog`；**不切到别的出口**（保持 IP 一致）。
- 维护/探针严格受 `maint_reserve=20` / `probe_reserve=6` 约束，不能借用未用生成空间；这是 v2 与旧 PLAN“维护可借用未用生成空间”的差异，以 `DEPLOY.md` 与实现为准。
- `no_capacity` 一律 defer：`account_keepalive._retry_delay = max(300s, retry_after_s)`；保持账号 due，不写 `tier_checked_at`/成功；手动刷新返回 `deferred=true`；导入继续入库，不整批 503。
- 生产口径：预算耗尽/无出口是**排队重试**，不是永久放弃；只有账号禁用、verify 锁、无 Cookie、出口永久 disabled 才是真正的不可调用状态。

**Free 维护容量示例（不编造峰值）**：当前约 41 个 free 账号，按每账号 1~3 次维护调用约 41~123 次；3 条 WARP、每出口每窗口维护预留 20，严格预留下需要约 1~3 个窗口完成，而不是旧版“两波 20~30 分钟”。不要在单窗口内打满 41+ 次；需要更快时优先补 WARP/出口，而不是提高单出口软阈值。

### 4.3 生成类调用：429 与 503 的严格区分

- **429 `no_capacity`**：管理器无预算/无同 lane 健康出口。app 抛 `ExitBudgetWaitError`（HTTP 429 + `Retry-After`），槽位保持 `UNSTARTED`，不换候选、不切号、不降级 free、不消耗账号 429 冷却；客户端按 `Retry-After` 退避。
- **503 `exit_unavailable`**：只用于管理器不可达/响应非法/账号无绑定等 fail-closed 场景；**不得把无容量 429 映射成 503**。实例层对 `exit_unavailable` 显式 re-raise，不改写为 409，也不把槽位置 `INVALID`；恢复后下一次请求可直接重建。
- **冷启动计数**：`create_session` 的建会话租约作为 `cold_start_lease` 信用，首个 `request_scope` 消费该信用，不再重复租 generation；第二个请求起每次租 1 次（`d09f591`）。
- **绑定变化/出口烧毁**：请求尚未发出 → 走现有 `_rebuild_for_new_exit`（D-05 单槽重建）；已有在途流 → 让当前流自然结束/失败，不切出口；`has_yielded_data` 时绝不重建。
- **已出正文**：继续，不仲裁、不切号、不切出口；本请求预算在开始时已计。
- **重试**：每次实际尝试分别计租约；同一请求的候选循环统一管理预算，不允许无限重试绕过预算。

### 4.4 软/硬阈值、烧毁、冷却与恢复（与实现一致）

```text
healthy --(维护/探针预留耗尽或窗口软/硬阈值)--> healthy(拒绝新生成/维护 defer；不换出口)
healthy --(canary exit_bad / L1 region / 连续 L0/L1 失败)--> quarantined
healthy --(同出口 30min ≥2 个不同账号 verify)--> cooling(120min)
cooling --(到期 + L0 通过)--> healthy
cooling --(到期 + L0 失败)--> quarantined(人工 recover)
quarantined --(POST /v1/admin/recover + 探活通过)--> healthy
disabled --(配置改回)--> healthy
disabled --(任何事件/探针/refresh)--> 仍 disabled（硬开关）
```

| 触发 | 级别 | 结果 | 是否换身份 |
|---|---|---|---|
| `canary=exit_bad`（真实 region 403） | 出口级 | `quarantined`，人工 recover | WARP 按 `rotate_on_recover` 在 recover 时轮换；默认 `rotate_on_isolation=false` |
| L1 匿名 region | 出口级 | `quarantined`，人工 recover | 同上 |
| 同出口 30min ≥2 个不同账号 verify | 出口级（连坐） | `cooling` 7200s；到期 L0 | 不自动轮换 |
| 同一账号 verify | 账号级 | 不烧出口；走现有扫码 | 不换出口 |
| `region_403 + canary=account_bad/inconclusive` | 账号级/记录 | 只记录，不隔离 | 不换出口 |
| 连续 L0/L1 失败达到 `fail_threshold` | 健康级 | `quarantined`，人工 recover | 不换身份 |
| `other_403` | 记录 | 不动 | 不动 |
| `disabled` + 任意事件 | 配置级 | 只记录摘要；状态保持 `disabled`；`recover` 返回 409 | 不换 |

关键原则：

- 单账号 verify 不烧出口：verify 是账号级风控；只有同出口多账号连坐才升级为出口级。
- verify 连坐**只对 `healthy` 出口生效**；`quarantined` / `cooling` / `disabled` 只记录事件，不改变状态、不覆盖隔离原因、不绕过人工 recover（`REV-2.1.5.14` 二/三轮已修复）。
- 冷却到期不直接归位：先做一次 L0 TCP 探活；通过才 `healthy`，失败保持 `quarantined` 等人工 recover。
- `quarantine` 不自动恢复；只有 `cooling` 到期自动走 L0。`disabled` 是配置硬开关，不被 `canary=exit_bad`、verify 连坐、健康探针或 `_refresh` 覆盖。
- 不做定时/流量/请求数主动轮换；WARP 轮换只在人工 recover（或显式开启 `rotate_on_isolation`）时发生；多线程轮换由管理器 `rotation_sync`/后台线程串行收纳，不风暴。

---

## 5. 健康循环与自动补充

- `HealthLoop` 每 `health.interval_s=120s` 执行一次 `run_once()`：`refresh_fanout()` → `_maybe_replenish()` → 对全部 healthy 出口做 L0/L1 探针并应用结果。
- `_maybe_replenish()` 受 `replenish.interval_s=60s` 节流，因此实际补充节奏为 `max(health.interval_s, replenish.interval_s) = 120s`（`REV-2.1.5.14` N-3）。
- `replenish_tick` 遍历 **paid / free / free_overflow 三条 lane**：healthy < target、且配置了该 lane 的命令时才发起；`paid_cap + overflow_cap ≤ 4` 在加载期校验。
- 补充限制：5 分钟冷却（`cooldown_s=300`）、每小时最多 3 次（`hourly_limit=3`）；命令通过 `{slot}` / `{region}` 占位符展开，`subprocess.run(argv)` 不走 shell；单次超时 120s。
- 候选入池必须经过验证：`Replenisher.admit` 只在注入的 verifier 通过后才 `add`；当前 `replenish_tick` 负责触发命令，新出口只有经 `refresh_fanout` / L0/L1 探针达到 `healthy` 后才可被分配。不要把“命令返回 0”等同于“出口已可分配”。
- 未配置命令不视为错误：tick 对无命令 lane 直接跳过，不执行、不阻断健康循环；显式调用 `Replenisher.attempt` 时只记 warning（`no_command`），不抛错。
- 已知限制：补充命令在健康循环内同步执行，慢命令最长可阻塞约 3×120s；接入真实 provisioning 时应改用独立线程/队列或更短超时（`REV-2.1.5.14` N-2）。

---

## 6. 不阻断凭证更新的保证、管理器故障降级

### 6.1 保证

1. **维护预留**：每出口每窗口保留 20 次给 maintenance、6 次给 probe，生成不得占用；维护到预留耗尽才等待。
2. **日上限只约束 generation**：维护/探针不计入 `daily_total`，日上限打满后仍可在窗口预留内前进，不会等到 UTC 午夜。
3. **排队与重试**：`429 no_capacity` 一律 defer，按管理器给的 `retry_after_s`（60~600s，daily 耗尽时仍可能指向窗口重置）退避；保持账号 due，不写成功，不推进 24h。
4. **自动补充**：paid/free/overflow 均由健康循环驱动；命令未配置只告警/跳过。WARP 仍可人工补。
5. **告警**：日志 WARNING（按账号/出口限频）+ `/v1/status` 的 `maintenance_queue`/`lanes` + UI 只读卡片。
6. **真正的不可调用**只用于禁用账号、verify 锁、无 Cookie、配置 `disabled`；预算/无出口永远走 429/defer 或 fail-closed 503，不伪装成永久放弃。

### 6.2 管理器故障

- app lease 超时 2s；管理器 429 → defer；管理器不可达/非 JSON/非法载荷 → fail-closed：generation 503 `exit_unavailable`，维护 ≥300s 重试；不 fallback 全局 `AISTUDIO_PROXY`。
- 已有会话仍持有自己的 `socks_url`，可继续用到租约/会话结束；管理器故障不立即杀死在途会话。
- 管理器恢复后自动重新 lease；不需要 app 重启；status 15s 负缓存、assignment 30~60s 缓存。
- 状态目录必须在持久盘（非 tmpfs），`exits_state.json` / `bindings.json` / `idempotency.json` 原子写；建议定期备份。状态丢失会导致重新绑定/可能 IP 变化，这是单点风险。
- 单写者：同一 state 目录只允许一个管理器进程；systemd 单实例 + `Restart=on-failure`。
- `request_id` 幂等结果持久化 600s，键含 `account+lane+class+calls+request_id`；跨重启有效、不同账号互不串用；过期后重放会重新计数。**app 目前不发送 `request_id`**；启用前调用方应按 `ttl_s` 重新租约，不要把 600s 当租约有效期。

---

## 7. 控制面接口（摘要，详见 `CONTRACT.md`）

| 接口 | 作用 | 关键语义 |
|---|---|---|
| `POST /v1/lease` | 主入口 | `account` HMAC、`lane` paid/free、`class` gen/maint/probe、`calls` 1..10、可选 `request_id`；成功 `exit_id/socks_url/generation/ttl_s/budget_left`；无容量 429 `no_capacity` + `retry_after_s`；TTL 30~60s |
| `GET /v1/assign` | 兼容入口 | `account/kind/lane`；内部等价 `lease(class=gen, calls=1)`；无 request_id；无容量同样 429 |
| `POST /v1/events` | 出口/账号事件 | `kind ∈ region_403/verify_account/other_403`；`canary ∈ exit_bad/account_bad/inconclusive`；`202 accepted|duplicate`；摘要持久化 |
| `GET /v1/status` | 只读聚合 | `manager_ok`、`exits{total,healthy,quarantined,disabled,cooling}`、`cooldown_count`、`lanes{paid,free,free_overflow}`、`bound_accounts`、`last_switch_at`、`last_replenish_at`、`maintenance_queue`；不含 socks/token/exit id/账号 |
| `POST /v1/admin/recover` | 人工恢复 | `{exit_id}`；`quarantined → probing → 探活`；`disabled` 返回 409；`healthy` 返回 already healthy |

鉴权与边界：所有 `/v1/*` 需 `Authorization: Bearer <token>`（常量时间比较）；只接受 `security.allowed_client_cidrs` 内来源；监听仅 loopback/bridge；`/healthz` 免 Bearer 但同样受来源限制；只读 `GET`、事件 `POST`；远端部署必须 `https://`。

---

## 8. 实施状态（已完成 / 当前 / 下一步）

| 阶段 | 状态 | 证据 |
|---|---|---|
| 管理器 v2（车道、预算、预留、429、烧毁、幂等、补充） | **已完成** | `REV-2.1.5.14` 三轮 PASS（0 P0/P1/P2）；157 个 unittest；`--check-config` 输出 paid 30/45/400、free 50/75/2400、fanout cap 4、lease 30~60 |
| app 租约客户端 / 车道 / 429 defer / 维护不跳过 | **已完成** | `REV-2.1.5.13` 二轮 PASS；1966 passed（含 429 四协议、冷启动单计、per-task scope、defer 不推进 due） |
| verify-account 事件上报与 Q-10/Q-11 | **已完成** | `REV-2.1.5.15` PASS；1974 passed；`242fd36` 补 `record_attempts` 门控（countTokens 不上报）与无绑定 `exit_id` 跳过；fire-and-forget 有界、off 模式零网络 |
| fanout 机器接口（`FANOUT_MACHINE_TOKEN`） | **已完成（编译级）/待 Linux 运行时** | `REV-2.1.5.12` 二轮 PASS；20 个用例 Linux 目标编译通过；本机无 Linux runtime，未执行测试二进制 |
| 预算调度 v2 定稿（PLAN/CONTRACT 同步） | **已完成** | 本文 + `CONTRACT.md` 与 `data/tools/exit-manager/` v2 对齐；docs-only |
| 保活分批与 `no_capacity` 降噪 | **已完成** | `e9081d2`；首轮 43 账号分批执行，逐账号 INFO、每轮汇总 WARNING，连续 3 个 no_capacity 停止本轮 |
| 元数据/Cookie region 403 接 canary 出口仲裁 | **已完成** | `4493ac0`；元数据路径 region 403 触发 canary 出口仲裁，不再只记录 |
| 分批饥饿 / 仲裁 kind / 汇总口径修复 | **已完成** | `cf96f03`；审查 `REV-2.1.5.16` |
| 扫码验证 start/status 修复 | **已完成（本地待提交）/待部署** | `REV-2.1.5.17` PASS（P2 处理后复审）；单次 start（单 watcher + `openedFor` guard）、starting 2.5s 轮询、TTL 300s、出码后每 10s 自动查询、手动 `status?force=true`、上游 2/3 均视为通过（3 清锁）、1/None 显示「仍在等待扫码」、STARTING 复用等待在途启动、关闭弹窗只 cancel 一次；全量 2010 passed |
| VPS 部署（2.1.5） | **已完成** | 8 条健康出口（free WARP 4 / paid fanout 3 / overflow 1）、43 账号全部绑定、`verify_required` 5 个待扫码（用户确认；数量与清单以 `registry.json` 为准）；部署后最近 2 分钟 no_capacity/region 均为 0 |
| 后续批次观察（43 账号轮换完成度） | **当前** | 观察分批保活能否把 43 账号全部轮换完成，无连续 no_capacity/region 堆积 |
| 扫码验证修复部署与待验证账号 | **下一步** | 部署 `REV-2.1.5.17` 修复后按 `registry.json` 的 `verify_required` 逐个扫码（当前 5 个，数量以 registry 为准）；扫码独立直连 Chromium、不进出口池预算、不做软件层硬限流 |
| 无响应体 403 标签取舍 | **下一步** | 空体 403 目前只能按出口嫌疑（Q2）；需定取舍规则，避免误烧出口或反复切号 |
| fanout 节点掉线/热备 | **下一步** | paid 3 = 2 主力 + 1 热备；掉线检测与热备切换尚未补齐 |
| 1 核容量评估 | **下一步** | `free -m`/`top` 实测 WARP 4 + fanout 4 + app/signer 的 RSS 与负载 |
| Linux 运行时回归（fanout 20 用例 + 管理器 157 用例） | **下一步** | VPS 已实机部署；测试套件仍应在 Linux/Termux 或 CI 执行 `GOOS=linux go test ./...` 与 `python -m unittest discover -s data/tools/exit-manager/tests -v` |
| 直连应急阀收窄为 maintenance-only | **下一步（防御性）** | 当前由 `AISTUDIO_EXIT_DIRECT_FALLBACK` 统一门控，未按 class 收窄；默认关闭，generation fail-closed |

---

## 9. 风险与未知项（不编数字）

| 项 | 裁决 | 说明 |
|---|---|---|
| 车道隔离 | 可行 | paid/free/overflow 已实现；paid 只落 fanout、free 只落 WARP；overflow 仅全部 WARP 不可用 + 维护积压 |
| 预算与预留 | 可行 | 窗口/日/软/硬/预留已实现；日上限只约束 generation |
| 429/defer | 可行 | 管理器 429 + retry_after，app 端到端 429；槽位保持 UNSTARTED |
| fanout 公网节点/ToS | 风险高 | 志愿者节点质量/合规/元数据可见；仅 Free、TLS、不用于 Pro/verify |
| fanout 总上限 4 | 可行 | paid 3（2 主力 + 1 热备）+ overflow 1；`paid_cap+overflow_cap>4` 加载期拒绝，静态 exits 需人工保证 ≤4 |
| WARP 3/6 | 有条件可行 | 3 条示例已配置；WARP 新身份地区/健康需部署后 L1+canary 验证 |
| 高生成吞吐 | 风险中/高 | paid 新绑定仅 4/窗口、已绑定 19/窗口；free 新绑定 24/已绑定 49；高并发需加出口，不能靠提高预留/软阈值 |
| 1 核 1.9GB | 风险中 | manager 轻量；fanout/openvpn/signer/app 需实测 RSS；不要一次开 99 条 |
| 管理器单点 | 风险中 | fail-closed + 排队 + systemd + 持久 state；无 HA |
| socks 凭据安全 | 风险中 | state 0700/文件 0600、日志 `safe_url`、app allowed_hosts、UI 不暴露；机器接口的 `socks_url` 含凭据，仅 loopback+Bearer |
| 账号跨出口抖动 | 风险低/中 | 只在出口状态变化/人工动作时迁移；不按负载迁移；同区域优先 |
| verify 扫码 | 可行/不动 | 独立 Chromium，默认直连；不在预算内；不做软件层硬限流 |
| `disabled` 语义 | 已收敛 | 配置硬开关：不分配，事件/探针/refresh/recover 均不复活；`replenish_tick` 仍会按 healthy 缺口触发补充命令（Q-15），但不会直接复活 disabled 出口 |
| `request_id` 幂等 | 已实现 | 600s 持久化、五元组键；app 暂未发送；启用前按 TTL 重新租约 |
| 候选验证 | 部分接线 | `Replenisher.admit` 提供 verifier 闸门；当前 tick 触发命令，实际分配仍以健康探针为准 |

未知项：

- U1：历史“100~150 次”没有时间维度；v2 以 30 分钟窗口/每出口预算为准，不再沿用该数字。
- U2：free/paid 实际日均与峰值生成量未知；以 `/v1/status` 的 `lanes.*.window_remaining/daily_remaining` 与实际 429 为准。
- U3：WARP 新身份的真实出口地区/健康未知；需部署后 L1+canary 验证。
- U4：fanout socks 端口绑定地址（127.0.0.1 还是 172.17.0.1）未知；容器到宿主端口的可达性必须部署前验证。
- U5：fanout 只读 API 的 basepath / `readonly_token` / `FANOUT_MACHINE_TOKEN` 实际配置未知；管理器 `base_url/list_path` 必须对齐。
- U6：fanout 节点池中实际可用/地区干净的数量未知；不要按 99 计算容量，先 JP/KR。
- U7：1 核 1.9GB 下 WARP 3 + fanout 4 + app/signer 的 RSS/负载未知；需 `free -m`/`top` 实测。
- U8：Google 是否把匿名 L1 与账号 RPC 计入同一 IP 风控未知；v2 仍把 L1 计入 probe 预留，按保守处理。
- U9：verify 扫码是否应走专用出口未知；默认保持独立 Chromium/现有 `AISTUDIO_VERIFY_PROXY`，不进出口池预算。
- U10：管理器状态丢失后重新绑定会改变 IP；是否接受单点风险由用户决定（建议备份 state）。
- U11：fanout 机器接口 20 个用例仅完成 Linux 目标编译，未执行运行时；联调前必须在 Linux/CI 实跑。
- U12：app 目前不发送 `request_id`、`kind` 不参与管理器绑定（恒 `chat`）；`242fd36` 已把 verify 事件按 `record_attempts` 门控并在无绑定时跳过（`REV-2.1.5.15` P2-1/P2-2 已收敛）。

---

## 10. 与 AGENT.md §4 红线对照

- 不新增实例/Page/Context，不改变 Free/Pro lane、tier 选择器；出口池只影响 session 的 socks 端点（§4.1-4.2）。
- `active_requests` 仍是唯一并发原语；lease 预算只是出口调用配额，不参与请求准入，不是第二套并发账本（§4.3）。
- D-05：迁移/重建只发生在请求前，顺序 release → `active_requests==0` → close → restart → 一次重试；不 blue-green、不同账号双页、不中途热切换（§4.4）。
- 不做定时/流量主动轮换；WARP 只在人工 recover（或显式 `rotate_on_isolation`）时轮换；不恢复 12MB/35 分钟逻辑（§4.4-4.8）。
- 不做后台自动 rebind/rebalance；只有隔离/烧毁/人工动作触发迁移，且由请求或运维动作消费（§4.8）。
- fail-closed：无出口不回退全局 `AISTUDIO_PROXY`；唯一例外是显式 `AISTUDIO_EXIT_DIRECT_FALLBACK`（默认关，目标仅 maintenance）。
- 不读账号 Cookie/auth.json 到管理器；只传 HMAC、出口 id、事件分类；canary 在 app 侧（§4.6、EVALUATION §6.4）。
- 不新增 config schema/UI 设置项；`AISTUDIO_EXIT_*` 只走 env；UI 只读（D-12、EVALUATION §5）。
- verify 扫码不做软件层硬限流；不把 verify 纳入出口预算；不因出口池阻断扫码。
- 本文档提交未启动真实服务/浏览器，未访问 VPS/外网/账号。

---

## 11. 部署与回滚

### 11.1 部署顺序

1. 备份管理器 `state/`、`config.json`、fanout 配置；确认 `state/` 在持久盘。
2. 部署 manager v2：`python main.py --config /etc/warp-multi/config.json --check-config`；启动；验证 `/healthz` 与 `/v1/status`（只读，无 socks）。
3. 部署 app：设置 `AISTUDIO_EXIT_MODE=external`、endpoint/token/hmac 等 env；启动容器（一次冷启动，避免反复重启触发风控）。
4. 先验证 1 个账号的 lease + maintenance；再小批 5 个；再全量分波；观察 24h。
5. 观察项：`/v1/status` 的 `lanes.*.healthy/target/cap/window_remaining/daily_remaining`、`maintenance_queue`、`cooldown_count`；app 429/defer 与 503 日志；无 verify 连坐误烧。

### 11.2 回滚

1. `AISTUDIO_EXIT_MODE` 置空并重启 app → 回到 `settings.proxy_url`（行为与改造前一致）。
2. 停止 manager；恢复 `state/` 与 `config.json` 备份；不强制 WARP 轮换。
3. 只回滚 app：manager 可继续运行（无 app 请求），不影响默认模式。
4. 回滚不删除 fanout fork；机器接口 token 可置空，`/api/machine/*` 回到 404。

---

## 附录 A：关键实现事实索引（v2）

- 配置默认值/上限：`data/tools/exit-manager/exit_manager/config.py`（`MAX_FANOUT_CAP=4`、`BudgetConfig`、`ReplenishConfig`、`paid_cap+overflow_cap>4` 校验、`lease_min/max_ttl_s`）。
- 预算/状态模型：`exit_manager/state.py`（`ExitBudget`、`ExitState`、`Binding`、`LANE_*`、`CLASS_*`、`verify_events`）。
- 车道/预算/租约：`exit_manager/manager.py`（`_exit_serves_lane`、`_overflow_ok`、`_budget_ok`、`_no_capacity`、`lease`、`_lease_locked`、`_idem_*`）。
- 隔离/烧毁/恢复：`manager.py`（`quarantine`、`_burn`、`recover`、`_record_verify_burn`、`apply_probe_result`、`_refresh`）。
- 事件：`manager.py`（`record_event`、`EVENT_KINDS`、`CANARY_VERDICTS`、摘要持久化）。
- 补充/状态：`manager.py`（`replenish_tick`、`status_summary`）；`exit_manager/replenish.py`（`can_replenish`、`attempt`、`admit`）；`exit_manager/health.py`（`HealthLoop.run_once`、`_maybe_replenish`）。
- HTTP 契约：`exit_manager/server.py`（`/healthz`、`/v1/lease`、`/v1/assign`、`/v1/events`、`/v1/status`、`/v1/admin/recover`、Bearer/CIDR/429）。
- app 客户端：`src/aistudio_api/infrastructure/exit_pool/resolver.py`（`lease`、`_parse_lease`、`_parse_no_capacity`、`resolve_credential_exit`、`report_event_nowait`、`pool_status`）。
- 生成计数/D-05：`src/aistudio_api/infrastructure/waa/engine_runtime.py`（`_resolve_exit_proxy`、`request_scope`、`cold_start_lease`）。
- 维护 defer：`src/aistudio_api/infrastructure/account/account_info_refresher.py`、`src/aistudio_api/application/account_keepalive.py`。
- verify 事件：`src/aistudio_api/application/routing_pipeline.py`（`verification_required` 分支与 `_report_verify_account_event`）。
- fanout 机器接口：fanout fork `279fd9c`（`machine.go`、`machine_test.go`、`main.go`、`README.md`），以 `REV-2.1.5.12` 二轮记录为准。

## 附录 B：配置样例（与 `config.example.json` 对齐）

```json
{
  "ttl_s": 60,
  "budget": {
    "window_s": 1800,
    "maint_reserve": 20,
    "probe_reserve": 6,
    "paid": {"soft": 30, "hard": 45, "daily": 400},
    "free": {"soft": 50, "hard": 75, "daily": 2400},
    "burn_cooldown_s": 7200,
    "verify_cluster_window_s": 1800,
    "verify_cluster_threshold": 2,
    "lease_min_ttl_s": 30,
    "lease_max_ttl_s": 60,
    "max_calls": 10
  },
  "replenish": {
    "paid_target": 3,
    "paid_cap": 3,
    "free_target": 3,
    "free_cap": 6,
    "overflow_target": 1,
    "overflow_cap": 1,
    "cooldown_s": 300,
    "hourly_limit": 3,
    "interval_s": 60
  }
}
```

## 附录 C：已删除/改写的旧口径（禁止再引用）

- ~~“2 条 WARP + 3 条 fanout 共 5 条出口”~~ → v2：WARP 目标 3/上限 6 + fanout 上限 4（paid 3 + overflow 1）。
- ~~“按账号容量（Free 3~5/IP）”“account_soft_cap”“paid_reserved_accounts”“per_account_window_cap=12 / daily=400”~~ → v2：无按账号容量/配额；只按每出口调用预算。
- ~~“无容量返回 503 `exit_unavailable` / `no_healthy_exit`”~~ → v2：无容量 **429 `no_capacity` + `retry_after_s`**；503 仅管理器不可达/载荷非法等 fail-closed。
- ~~“无出口直接 skip”~~ → v2：预算/无容量一律 429/defer；只有禁用账号、verify 锁、无 Cookie、配置 `disabled` 才是不可调用状态。
- ~~“lease 返回 `granted` / `category` / `ttl_s=120`”~~ → v2：返回 `exit_id/socks_url/generation/ttl_s(30~60)/budget_left`；`calls` 是请求字段。
- ~~“全出口统一 soft=50/hard=75/daily=2400”~~ → v2：paid 30/45/400、free 50/75/2400。
- ~~“日上限约束所有类别”~~ → v2：日上限只约束 generation；维护/探针只受窗口预留与 hard_limit。
- ~~“维护可借用未用生成空间”~~ → v2：维护/探针严格吃各自预留。
- ~~“分级冷却 min(上次×2, 14400s)”~~ → v2：verify 连坐固定 7200s；无分级冷却。
- ~~“43 个账号两波 20~30 分钟完成维护”~~ → v2：按每出口维护预留 20/窗口重算，约 1~3 个窗口；不要用旧估算。
- ~~“WARP 定时 12MB/35 分钟轮换 / 请求数阈值 / 后台自动 rebalance”~~ → v2：仅人工 recover（或显式 `rotate_on_isolation`）轮换，无定时/阈值/自动 rebalance。
