# WP5 收尾 UI 设计与接线转交提示词

你是接续本仓库 UI/WP7 工作的新 agent。仓库：

```text
D:\LLM\self_programming\aistudio-api
分支：p1-ark-merge
```

## 当前基线

WP5 v3 后端已提交：

```text
b59d6f3 fix(wp5v3): 完成实例池并发与预算收尾
0ced898 docs: 同步 WP5 v3 架构与实例池说明
8ad9a63 fix(accounts): 兼容旧项目 auth JSON 导入
```

本阶段只做 **WP5 的 UI 收尾与接线**，不重写后端。

## 必读

```text
docs/dev/UI-CHANNEL-DESIGN-20261018.md
docs/dev/WP5-V3-DESIGN-20261019.md
docs/dev/WP5-V3-IMPLEMENTATION-PLAN-20261019.md
docs/dev/PLAN-ark-merge-20261017.md §4 WP5 / §4 WP7
docs/dev/evidence/wp5v3-20261019/README.md
AGENT.md §5 / §6
```

UI 设计与后端语义冲突时，以 `WP5-V3-DESIGN-20261019.md` 为准；不要在 UI 里重新定义“一个页面只能一个请求”或“max_pages 是并发上限”。

## 已存在的后端接口

不要另造数据源，先接这些：

- `GET /surface/instances`
  - `context_count`
  - `live_instances`
  - `planned_instances`
  - `desired_instances`
  - `pages`
  - `budgets`
  - `can_add_context`
  - `blocked_reason`
  - `active_account`
  - `contexts[]`：`slot_id`、`account_id`、`channel_role`、`state`、`chat_ready`、`build_ready`、`chat_counted`、`build_counted`、`pending_pages`、`pages`、`last_used`
- `GET /accounts`
- `POST /accounts/import-bundle`
  - 现在支持单个旧项目 auth JSON、`accounts` bundle、以及目录路径
- `POST /accounts/refresh`
- `GET /config`
- `PUT /config/features`
- `PUT /config/yaml`
- `GET /v1/models` / `GET /v1beta/models`
- `GET /logs/stream`（若已实现）

## 页面与信息架构

遵循 UI 稿：

- P1 控制面板 `/`
  - 当前活跃账号/实例列表
  - 可用账号 x/y
  - 实例数（当前/计划）
  - 实时实例明细入口（点击展开 `/surface/instances` 数据）
  - 总请求数 + 429 子指标
- P2 账号管理 `/accounts`
  - 账号行分模式渲染
  - 渠道矩阵、挂载徽章、资格/挂载/命中三态
  - Cookie 凭证组与子账号
  - 旧项目 auth JSON 导入入口
- P3 系统与模型配置 `/settings`
  - 基础区 + “显示深度功能”
  - 渠道注册表、策略组、槽位表、路由安全、分组编排
  - 不新增 P4 实例页
- P5 日志 `/logs`（若不在本阶段，可保留入口）

## 必须接线的语义

1. **Context 与 Page 分开**
   - Context = 独立 cookie jar/账号隔离，`max_contexts` 计数。
   - Page = Context 内 surface 页面，`max_pages` 计数。
   - 同一页面可同时承载多个请求；页面忙不是 429 理由。
2. **页面预算**
   - 展示 `pages` 与 `pending_pages`，不要用 `live_instances` 冒充并发数。
   - build Context 的隐藏 BotGuard chat 页不是已计数 chat surface，UI 不要把它显示成用户 chat 页。
3. **换号**
   - 429 应显示“已切换账号/正在切换”，不是直接显示用户 429。
   - 只有候选/切换预算耗尽时才显示 `surface_switch_exhausted` / 本地容量错误。
   - 切号方向是实例切换，不是 API 请求方向。
4. **组与额度池**
   - 组是账号级约束层，不是请求级单组。
   - QuotaPool 是账号背后模型限额资格，不是模型清单。
5. **模型列表前缀**
   - 单模式裸 ID；多模式只列 `pg/`、`build/`。
   - `chat/`、`playground/` 只是输入别名；`official/` 不列。
6. **旧项目导入**
   - 支持直接选择 `auth-*.json` 或整个 `configs/auth` 目录。
   - 导入后必须能显示 `expired=false/true` 与 tier；不要把地域 Cookie 覆盖问题重新引入 UI 层。
7. **错误文案**
   - `surface_no_capable_account` 403
   - `surface_switch_exhausted` 503
   - `surface_quota_exceeded` 429
   - 带具体账号/组/预算/处置建议，不使用泛化“请求失败”。

## UI 验收

- 页面只使用真实 `/surface/instances` 与 `/accounts` 数据，不硬编码实例数。
- 4 个账号轮转时，P1/P2 能看到活跃账号变化，context 数不异常增长。
- 同页 2/4 并发时，实例面板显示 1 个 chat page，不显示为 4 个并发页。
- 429 换号时，UI 显示切换过程，不直接把 429 抛给用户。
- 旧项目 auth JSON 可从 P2 直接导入；导入后刷新 tier/expired 正常。
- `max_contexts`、`max_pages`、`max_switches`、`quota_pool`、`borrow`、`auto_switch` 的文案符合 UI 稿，不写“max_pages=并发数”。
- 不修改 `web/` 之外的后端语义；发现后端字段缺失，单独提交并说明，不在 UI 里伪造。

## 质量门

```text
cd web
bun run lint
bun run type-check
bun run build
```

后端如未改动，至少跑：

```text
uv run pytest tests/unit -q
uv run ruff check .
```

## 禁止事项

- 不重写 WP5 实例池。
- 不把“同页并发”显示成多实例。
- 不把旧项目地域 Cookie 原样发回后端。
- 不在 UI 层做 429 静默重试或假数据。
- 未经用户批准不 commit，不 push。