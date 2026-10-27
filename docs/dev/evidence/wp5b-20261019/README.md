# WP5 二期（P4.5）复杂策略组后端证据

- 日期：2026-10-19
- 分支：`p1-ark-merge`
- 相关提交：
  - `cdb9e15` 配置契约与纯策略模块
  - `ec35c8d` 唯一决策管线与级别 A
  - `28c981f` 跨账号 Realm 与实例 API
  - Phase 4 收尾提交：本文件所在提交
- 范围：纯后端；未改 `web/`、静态产物、WP10 保活、WP8 格式转换。

## 1. 质量门（最终）

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
171 files already formatted

bun x pyright src tests
0 errors, 0 warnings, 0 informations

uv run pytest tests/unit -q
732 passed in 21.92s
```

## 2. 真机环境

- 服务：`uv run python main.py server --port 8099 --browser-port 9333`
- 代理：`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:10809`
- 账号：
  - `acc_46223636`（pro，活跃）
  - `acc_b1a36ccf`（free）
- 模型：`gemini-flash-latest`
- **argon 调用次数：0**（未消耗每日 50 次额度）
- 运行配置为 git-ignored 根 `config.yaml`；证据配置模板见 `harness/phase-*.json`。

## 3. 真机结果

| 阶段 | 场景 | 结果 | 证据 |
|---|---|---|---|
| A | 零配置 chat（pro 活跃，单渠道） | 200；usage prompt 4 / completion 21 | `raw/phase-a-chat.json` |
| B | `official/` 前缀 | 403 `surface_channel_disabled`，0.0s，无上游调用 | `raw/phase-b-official.json` |
| B | 组约束：pro 组 bare chat | 200 | `raw/phase-b-group-bare.json` |
| B | 组约束：pro 组 `build/` | 403 `surface_group_disabled`（allowed=['chat']） | `raw/phase-b-group-build.json` |
| C | 预算超限（global=1） | 429 `surface_quota_exceeded`，顺延 1 次 | `raw/phase-c-quota.json` |
| E | 跨账号并发：pro `chat/` + free `build/` | 两者均 200；chat 3.2s，build 34.0s；free 独立 Realm | `raw/phase-e-chat-pro.json`、`raw/phase-e-build-free.json` |
| E | 实例/预算快照 | `live_instances=2`（默认 chat + free Realm）、`live_realms=1`、`planned_instances=2` | `raw/phase-e-instances.json` |

### 跨账号 Realm 日志摘录

```text
[surface] request=... channel=chat candidates=acc_46223636 ... multi_mode=True
[surface] request=... channel=build candidates=acc_b1a36ccf ... multi_mode=True
[cdp] 已创建 BrowserContext: 48D34C58FFB4D6AAD73D149E1AB8CC94
[build_surface] app 内嵌客户端已连接 9998（realm=realm-acc_b1a36ccf-1, 第 1 个）
[realm] Realm 创建: realm=realm-acc_b1a36ccf-1 account=acc_b1a36ccf live=1/2
[surface] request=... account=acc_b1a36ccf channel=build realm=realm-acc_b1a36ccf-1
```

两条请求时间窗口重叠，pro chat 在 free build 冷启动期间已完成并返回 200。

### 配置矩阵

- Phase A：`build_surface=false`、`route_by_tier=false`、`realm_mode=true`。
- Phase B：`build_surface=true`，`route_by_tier=true`，groups=`pro→[chat]`、`free→[build]`。
- Phase C：`build_surface=true`、`route_by_tier=false`、`realm_mode=false`、`global_max_instances=1`。
- Phase E：`build_surface=true`、`realm_mode=true`、`max_realms=2`；账号级覆盖 pro=`[chat]`、free=`[build]`；slots=pro/chat + free/build。

## 4. 覆盖说明

已由单测覆盖、真机未重复的场景：
- 裸名矩阵的组合分支（单渠道/多渠道无组/前缀/能力守卫）。
- 四层预算的 global/channel/group/realm 独立分支。
- 切换预算 `max_switches=2`、同账号重试不消耗切换预算。
- 熔断 5 次/10 分钟 → 503 `surface_switch_exhausted`。
- borrow：`test_borrow_uses_idle_realm_when_primary_budget_is_full` 验证首字节前借入且不切默认账号。
- 账号 routing 归一化与 `AccountMeta.routing` 刷新/保活后不丢。
- Realm 池复用、上限、LRU、在途不回收。
- Build WS `?realm=` 解引用与连接桶隔离。

## 5. 已知限制

- borrow 仅有单测验证；真机环境只有 2 个账号，未构造出“主槽不可用 + 另一账号就绪槽可借入”的稳定场景。
- 失败链的真机故障注入未做；由单测覆盖切换/熔断/错误码。
- 冷启动首次会话出现一次 `BotGuard service captured but wire template missing`，既有自动重试后成功，不属于 WP5b 回归。
- 首轮 phase-c-quota 在旧进程上返回 503；重启加载“候选顺延耗尽→429”修复后复测为 429，以 `raw/phase-c-quota.json` 为准。
- 官方渠道仅注册未实现，`official/` 前缀显式 403。

## 6. 安全

- 证据不包含 API Key、Cookie、账号密码。
- 账号 routing 覆盖只写 `AccountMeta.routing`（`registry.json` + `meta.json`），不触碰 `auth.json`。
- 真机结束后已恢复根 `config.yaml` 默认 features，并清除两个账号的 routing 覆盖。

## 7. 复现命令

```text
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
uv run pytest tests/unit -q
```

真机：

```text
$env:HTTPS_PROXY='http://127.0.0.1:10809'
$env:HTTP_PROXY='http://127.0.0.1:10809'
uv run python main.py server --port 8099 --browser-port 9333
uv run python docs/dev/evidence/wp5b-20261019/harness/wp5b_set_config.py <phase.json>
uv run python docs/dev/evidence/wp5b-20261019/harness/wp5b_call.py <tag> <model>
```