# 12 Gate 0.5 Inputs（P9-pre 前置）

用途：把 P9-pre 的输入冻结成可执行契约。Gate 0.5 未通过，不得进入 P9-pre 编码。事实源仍是 `06-decision-log.md`（P2/P3 行为权威）与 `06-feature-index.csv`；本页只收敛接口、状态、schema 与验收模板。

## 1. 目标 seam / 五层接口

请求实例固定：`1 账号 + 1 Context + 1 Page + 1 surface`。控制面只做登录/保活，不占请求容量。

```python
class AccountService(Protocol):
    async def resolve(self, account_id: str) -> Account: ...
    async def eligible(self, account_id: str, surface: Surface, tier: Tier) -> bool: ...
    async def cookie_for(self, account_id: str) -> CookieState: ...

class InstanceFactory(Protocol):
    async def create(self, account_id: str, surface: Surface, min_tier: Tier) -> Instance: ...
    async def rebuild(self, instance: Instance) -> Instance: ...  # 仅显式关闭旧实例后

class SurfaceResolver(Protocol):
    def parse(self, model: str, api_format: ApiFormat) -> Surface: ...  # 只认 chat/build
    def tier(self, model: str) -> Tier: ...  # ModelCatalog 单一所有者

class Executor(Protocol):
    async def acquire(self, surface: Surface, tier: Tier) -> ActiveHandle: ...
    async def release(self, handle: ActiveHandle) -> None: ...  # finally

class EventSink(Protocol):
    def request(self, event: RequestEvent) -> None: ...
    def attempt(self, event: AttemptEvent) -> None: ...
    def usage(self, event: UsageEvent) -> None: ...
```

接口红线：

- `ActiveHandle.active_requests` 是唯一并发账本；无 lease/reservation/30s/15s 预算。
- 关闭/改绑前检查 `active_requests`；大于 0 拒绝。成功、失败、取消、断连、流关闭都在 `finally` 释放。
- 账号有活动实例时 Cookie 回写走实例页；否则才允许独立控制页。
- 同账号 chat/build 不并存；切换 surface 必须显式关闭旧实例再重建。
- 后台自动 rebind/rebalance 不实现；手动显式关闭旧实例再重建允许。
- 路由候选包含 SLEEPING/UNSTARTED；消息到达先唤醒合格实例。

## 2. 实例状态表

| 状态 | 含义 | 允许进入 | 允许离开 |
|---|---|---|---|
| `UNSTARTED` | 有账号配置，未建 browser/context/page | 启动预检、消息唤醒 | `STARTING` |
| `SLEEPING` | 实例已释放页面，可被消息唤醒 | idle（默认不自动关）、消息唤醒 | `STARTING` |
| `STARTING` | 正在建 context/page/surface | 消息唤醒、手动重启 | `READY` / `CLOSED` |
| `READY` | 可接请求，`active_requests==0` | 初始化完成 | `BUSY` / `RETIRING` |
| `BUSY` | 有在途请求，`active_requests>0` | acquire | `READY`（全部释放）；`active_requests=0` 后才允许 `RETIRING` |
| `RETIRING` | 正在关闭，不再接新请求 | 关闭/手动 rebind | `CLOSED` |
| `CLOSED` | 已释放；需重建才能再用 | 显式重建 | `STARTING`（仅手动） |

事件与转换：

- 消息到达：`SLEEPING` / `UNSTARTED` 合格实例 → `STARTING` → `READY` → `BUSY`。
- 请求结束：`BUSY` → `READY`（`active_requests` 归零）；关闭/改绑在 `BUSY` 时拒绝并提示等待，`active_requests==0` 后才允许 `RETIRING`。
- idle browser 自动回收默认关闭；开启时也只把无 `active_requests` 的实例转 `SLEEPING`，不自动关账号。
- 失败/stale 才手动重启；禁止自动 stale 复活。
- 手动 rebind：`READY`（`active_requests==0`）→ `RETIRING` → `CLOSED` → `STARTING`；后台自动 rebind 不存在。
- 429：同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 `503`；已出正文不切号。

## 3. Config schema（最小字段）

```yaml
instances:
  - account_id: string          # 1 账号
    count: int                  # 实例数，>0
    surface: chat|build         # 1 实例 1 surface
    min_tier: free|pro          # 实例级准入
api_keys: [string]              # [] 默认拒绝；显式 "" 才开放
features:
  formats: [openai|gemini|claude|responses]
  build_surface: bool
keepalive:
  enabled: bool
  interval_hours: 24
logging:
  level: string
  daily_dir: string
  retention_days: 7
```

规则：配置单一 schema（YAML + env + 默认值）；`count/surface/min_tier` 只在实例级；全局只做总上限；关键配置 fail-fast，非关键回退默认；不保留 channel registry。

## 4. Event schema（P5 只扩展字段）

```json
{
  "request_id": "string",
  "client_request_id": "string",
  "account_id": "string",
  "model": "string",
  "surface": "chat|build",
  "paid": true,
  "api_format": "openai|gemini|claude|responses",
  "attempt": 1,
  "status": "success|error|cancelled",
  "error_class": "string|null",
  "latency_ms": 0,
  "usage": {"input": 0, "output": 0, "cache": 0, "count_tokens": 0},
  "ts": "ISO-8601"
}
```

规则：

- `EventSink.request` 记客户端结果；`EventSink.attempt` 记上游每次尝试；两者分母分离，不混算成功率。
- token 汇总 = generation + input + cache，不含 `countTokens`；`countTokens` 独立计数。
- raw dump 只落 `safe_*`，默认关闭；禁止明文 headers/raw_body；`[toolshape]` 只记 hash/类型/计数。
- P7 只消费事件做聚合；不另起一套账本。

## 5. Acceptance 模板

每个切片提交前填一行：

| 字段 | 内容 |
|---|---|
| Feature ID | `Dxx-NNN` |
| 测试项 | 一句话行为断言 |
| 预期结果 | HTTP/状态/事件/日志的确定结果 |
| 命令 | `pytest ...` 或 fake harness 命令 |
| 工件 | 脱敏日志/SSE/fixture 路径 |
| 生命周期插入点 | Account/Instance/Pool-Router/Executor/Event Sink |
| 证据等级 | 离线 fixture / fake CDP / 受控真实（后置） |

最低验收：

- P3/P2：无 lease 断言；`active_requests` finally 释放；候选含 SLEEPING/UNSTARTED；429 同号重试→切换→503；保存后切换重试。
- P5/PG：F-01 functionCall 缺 thoughtSignature 兜底；F-02 首事件前真实 HTTP 4xx/5xx；F-03 finish-only/工具流终止兜底；F-04 functionResponse/functionCall 强制 Struct；`[toolshape]` 脱敏。F-05/06/07 后置。
- P6/P7：24h fake clock；build-only 回写；日志 SSE/1/3/7 天；client/attempt 分母；token 汇总不含 countTokens。

### 5.1 目标路径（待创建，不阻塞文档，但阻塞 Gate0.5 勾选）

- `config/config.example.yaml`：本页 §3 的示例。
- `config/config.schema.json`：§3 的机器校验 schema。
- `tests/fixtures/events/event.sample.json`：§4 的事件样例。
- `tests/fixtures/pg/f01_missing_signature.json`
- `tests/fixtures/pg/f02_prestream_400.json`
- `tests/fixtures/pg/f03_finish_only.bin`
- `tests/fixtures/pg/f04_string_response.json`
- `docs/dev/rebuild/acceptance/README.md`：每片验收工件目录规范。

## 6. Gate 0.5 退出清单

- [ ] 本页五节均通过评审并绑定 feature ID。
- [ ] `06-test-disposition.csv` 覆盖所有 `tests/**/*.py`，且无 `pending`。
- [ ] 状态表能覆盖 sleep/wake/idle/rebind/429/503 的转换测试。
- [ ] config/event schema 有示例与离线 fixture 路径。
- [ ] 每片 acceptance 模板已用于 P9-pre 的首个切片。
