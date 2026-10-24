# 05 重启地图（一页版）

- 目的：保住 free/QQ bot 可用，同时让 paid 模型走 Pro 账号；不再按反代途径路由。
- 约束：不访问真实网页、不发真实请求、不提交、不构建；先离线验证。
- 原则：一页地图，不是 WP；功能必须能指到图上的位置。

## 1. 五个对象

| 对象 | 责任 | 不负责 |
|---|---|---|
| Account | 一个 Google 登录状态（auth.json） | 页面、路由 |
| Context | 一个隔离的浏览器上下文 | 多 page |
| Page | 一个上下文里唯一的活动页面 | 与别的 page 共享 context |
| Instance | Account + Context + Page + Channel（chat/build） | 跨账号复用 |
| Router | 根据模型 `paid` 标记选池子 | 按反代类型分流 |

硬规则：

- 一个 Context 只能有一个 Page。
- 同账号需要 chat + build，就建两个 Context，不共享。
- Free 池和 Pro 池不互相借实例。
- QQ bot 固定绑定 Free 池。

## 2. 两条路由

```text
paid == true  -> Pro pool  -> 选一个 Pro 实例
paid == false -> Free pool -> 选一个 Free 实例
QQ bot        -> Free pool（固定，不受 paid 请求影响）
```

不做：

- 不按 chat/build 反代类型做第一层路由。
- 不在 Pro 额度耗尽时把 QQ bot 切到 Free 池。
- 不因为 paid 请求失败去占用 Free 实例。

## 3. 复用 / 改造 / 隔离

| 模块 | 处理 | 原因 |
|---|---|---|
| `cdp_client.py` | 保留 | 浏览器/Context/Page 底层 |
| `session_auth.py` | 保留 | Cookie 灌入/保存 |
| `wire_codec.py`、`wire_parser.py`、`wire_spec.py` | 保留 | 协议编码/解码 |
| `capture.py`、`transport.py`、`streaming.py`、`stream_parser.py` | 保留 | 请求模板、流式传输 |
| `build_surface.py` | 保留 native 路径 | 9998 已跑通；signer 可隔离 |
| `pooled_session.py` | 改造 | 去掉“隐藏 chat 页”绑定，改为单 context 单 page |
| `instance_pool.py` | 只保留 Context 创建/销毁 | 去掉租约/预算/rebind 语义 |
| `client.py` | 拆分 | 保留发送/解析，移出实例/账号选择 |
| `instance_manager.py` | 隔离 | 记录/编号/UI 不再做运行时权威 |
| `routing_pipeline.py` | 隔离 | 候选/配额/反代维度全部重写 |
| `surface_orchestrator.py` | 隔离 | 组策略/配额不再进新核心 |
| `instance_switch.py` | 隔离 | 自动重排不进入第一版 |
| `account_keepalive.py` | 暂缓 | 第一版只做手动/显式刷新 |
| `generation_service.py` | 重写编排 | 只保留 attempt/release，不保留候选代数 |

## 4. 已有测试和证据

> 重要：仓库 `tests/unit` 是伴随实现写的 TDD/回归测试，部分断言固化了当时实现（如 capacity 429、提前租约）。它们只能证明“代码仍符合当前预期”，不能单独证明原始行为。先行验证脚本、真机日志、SSE 记录、bundle 证据优先于这些测试。

### 先行验证脚本（`%TEMP%\kilo`）

这些是独立探针，不是按实现反推的单测；只复用其结论/输出，不默认重跑（多数依赖真实 CDP/账号/9222，当前 IP 风控期禁止）：

| 脚本/目录 | 证明什么 | 使用方式 |
|---|---|---|
| `probe_signer_live.py` | signer `tb` 的实际字段、`H/A`、headers、selfTestError | 只读其输出；不重跑 |
| `probe-current.cjs`、`bundles-current/` | 当前 AI Studio bundle 的函数/字段形态 | 离线分析 |
| `probe_bundle.py`、`probe_bundle2.py` | bundle 内 `An/OCb/$B` 等符号证据 | 离线分析 |
| `probe_frontend_paid.py`、`analyze_paid_fields.py`、`chunks/` | paid/free 模型元数据来源 | 直接支撑 paid 路由设计 |
| `capture_headers.py`、`compare_page_cookies.py`、`send_after_inject.py` | 请求头/Cookie/注入后发送的真实行为 | 只读输出；不重跑 |
| `test_accounts_refresh.py` | 账号刷新 RPC/authuser 行为 | 只读输出；不重跑 |
| `wp5_budget_race_probe.py`、`wp5_cancel_probe.py`、`wp5v3_parallel_chat.py` | 预算竞态、取消、并发真机行为 | 作为旧架构证据 |
| `wp10_real_auth.py`、`wp10_real_refresh.py` | 保活/Auth 回写的真实行为 | 作为旧架构证据 |
| `mt-audit/` | 上游合并时的 ours/theirs/base 快照 | 只读对比 |

### 先行验证输出（raw evidence，`%TEMP%\kilo`）

`docs/dev/evidence/` 是整理后的副本；完整原始输出在 `%TEMP%\kilo`。这些目录包含真实 SSE、服务日志、失败码和实验 JSON，优先于仓库 TDD：

| 目录/文件 | 内容 | 用途 |
|---|---|---|
| `p2-verify/` | ATG off/prefer/required 的 SSE 与服务日志 | ATG 真实行为 |
| `p3-build3/` | Build 验收 run1/run2、service log/err | Build 原始链路 |
| `p3-build/` | `build-stream.sse`、`auth-1.json` | Build 流式/Auth 现场 |
| `wp5-verify/` | build/quota/guard/chat 的 `.out` 与服务日志 | 渠道/配额/守卫真实行为 |
| `wp6-verify/` | reuse/gatefix/switch-inflight/xconn 的 SSE；`wp6-coexist.json`、`wp6-stats.json` | 并发/统计/coexist 真实行为 |
| `atg-live/` | required/non-stream/stream、config、accounts | ATG 端到端证据 |
| `spike*-evidence.json` | A/B/C/D、Build、App、signer、native 的实验结果 | Build/页面/签名逆向证据 |
| `direct-run*.log` | Build 直连多轮完整日志 | 失败路径和协议细节 |
| `mem-dual.csv`、`mem-private-2c.csv` | 双实例内存采样 | 资源规模 |
| `bundles-current/`、`chunks/` | 当前 bundle、console、resource URL | 函数/字段形态 |
| `mt-audit/` | 上游合并的 ours/theirs/base 快照 | 对照原始实现 |
| `wp7p2/` | 账号/单 auth/坏 auth 夹具 | UI/导入边界 |

注意：这些目录可能含真实账号 auth/cookie；**只引用路径和结论，不复制进仓库、不提交、不上传**。当前 IP 风控期也不重跑依赖真实 CDP/账号的脚本。

结论：**先行脚本 + raw evidence 是“原始行为证据”，仓库 TDD 是“当前实现回归”**；重启时必须先读前者，再决定后者哪些还能保留。

直接复用（作为回归，不作为原始行为证明）：

- `test_cookie_persistence.py`
- `test_cdp_browser_context.py`
- `test_cdp_client.py`
- `test_build_surface.py`
- `test_build_lease_identity.py`
- `test_stream_transport.py`
- `test_wire_codec.py`、`test_wire_parser.py`
- `test_format_openai.py`、`test_format_claude.py`、`test_format_responses.py`
- `docs/dev/evidence/p2-verify/`、`p3-build3/`、`wp5*/`、`wp11*/`
- `docs/ark-reference/build-channel/evidence-20261017/`

新增最小测试：

- 一个账号 → 一个 context → 一个 page；
- 同账号 chat/build → 两个 context；
- paid 模型只走 Pro 池；
- free 模型只走 Free 池；
- Pro 池失败不影响 Free 池；
- 关闭一个实例只销毁自己的 context/page。

## 5. 重启顺序

1. 新建 `rebuild/core` 分支；旧 `master` 冻结。
2. 只写 `rebuild/core` 包，不接 FastAPI/UI。
3. 先跑通：一个 Free 实例 + 一个 Free 请求（用已有 fixture/回放，不发真实请求）。
4. 再加一个 Pro 实例 + 一个 paid 请求（同样离线回放）。
5. 加池隔离：paid 失败不碰 free。
6. 通过后，再把现有 API 适配器接上；UI/统计/日志后置。

## 6. 完成标准

- 不需要任何真实 Google 请求就能证明：
  - paid 只进 Pro；
  - free/QQ bot 永远有独立池；
  - 一个 context 只有一个 page；
  - 关实例不误杀另一个实例。
- 旧项目只作为模块/证据来源，不再作为运行时骨架。

## 7. 不做的事

- 不写新 WP；
- 不再用 500 行设计稿替代可执行验证；
- 不在风控期做真实登录/请求；
- 不在第一版恢复自动切号、保活、UI 实例切换、统计页、日志页。
