# 08-09 决策记录（D-01..D-14）

状态：**用户唯一负责内容**。本文件只保留 D-01..D-14 及其旧结论处置；其他 agent 产物（17 个 `NO_FEATURE_ROW`、20 个冲突、`ENGINEERING_DEFAULT`、旧 F1-F8、数字变更、状态声明）已归档到 `archive/08-09-agent-record-20261028.md`，不再作为用户决策。

事实源：`08-08-upstream-99c38ba-full-diff-audit.md`。上游基线：`99c38ba`。旧修复阶段 F1-F8 由 `05` 的 B1-B5b 取代，见归档记录。

## 1. 已确认决策 D-01..D-14

以下 14 项全部已确认；每家“不得再改的边界”是执行红线。若需变更，必须由用户重新裁决并新建决策记录。

| ID | 状态 | 用户选择 | 影响 | 不得再改的边界 |
|---|---|---|---|---|
| D-01 | 已确认 | 保留 surface 能力，配额口径由 UI 开关控制：`quota_scope=account`（默认，chat/build 共用额度）或 `quota_scope=account_surface`（chat/build 分开）。只影响记账键，不新增实例。 | rotator 的 surface 维度保留；`AccountStats` 存储键按 `quota_scope` 选择 `model` 或 `surface::model`；`/stats`、配额展示与 429 冷却共用同一口径。 | 不得以“上游单维”为由删除 surface；不得为切换口径新增实例/Context/Page；不得在请求期自动改口径；默认必须是 `account`。 |
| D-02 | 已确认 | A：保留 pipe watchdog，仅负责父进程死亡后的孤儿 Chromium 清理；不是账号自动复活。 | `browser_engine._spawn_process_watchdog` 保留；`test_browser_engine` 的 watchdog 用例保留。 | watchdog 只做孤儿进程回收；不得据此做账号 stale 复活、自动 rebind、自动重启浏览器会话；不得把 watchdog 与后台调度混为一谈。 |
| D-03 | 已确认 | C：只保留 WebSocket ping；删除 liveness checker/轮询。 | CDP 传输层保留 WS ping；删除 `set_liveness_checker` 与 `Runtime.evaluate` 轮询；半开连接由命令超时与断线失败兜底。 | 不得恢复周期性 liveness 轮询；不得用 liveness 轮询替代请求超时；页面崩溃/断线仍须立即失败 pending futures。 |
| D-04 | 已确认 | A：付费模型 tier 不足只返回 403，不自动切号；必须返回准确 `tier/model/required_tier/current_tier`，并按协议映射。 | `account_orchestrator` 不再在 attempt 0 主动 `try_switch_account`/`switch_auth`/BotGuard；403 由领域错误产生，API 层按 OpenAI/Gemini/Claude/Responses 映射。 | 不得请求期切号/重开浏览器；不得自动降级到 free 账号；403 不得与 429 耗尽的 503 混用；错误体必须含四项字段。 |
| D-05 | 已确认 | A：禁用账号/应用预设立即 rebind；但先 `RETIRING`、停止新请求、`active_requests==0` 或有界等待、关闭旧 Page/Context，再新建/切换；修复 toggle/preset 的 fallback 先于 switch 的缺陷。 | 控制面写操作（toggle/apply preset）语义从“只改 registry”变为“改 registry + 有界 rebind”；`get_active_account` 的 enabled 回退不得先于 rebind 生效。 | 不得先建新页再 drain（blue-green）；不得无界等待在途请求；不得后台自动 rebind；同一账号最多 1 个 active Page；drain 只在旧 Page/Context 上且期间不建新页（D-11）；操作失败必须显式返回，不得静默留下 registry/UI 与浏览器会话不一致。 |
| D-06 | 已确认 | A：403 用领域错误，API 层按 OpenAI/Claude 等协议映射。 | application 层不再直接 `raise HTTPException`；新增/复用一个 tier 不足的领域错误，由协议适配层生成标准错误体。 | 不得在 application 层依赖 FastAPI；不得让不同协议拿到同一种非标准 body；不得用 503 表达 tier 不足。 |
| D-07 | 已确认 | B：rotator 配置深合并默认 + 启动校验；不静默、不请求期 KeyError。 | `_load_yaml_config` 不再浅替换 `rotator` 块；缺失键用默认值深合并；启动时校验类型/必填；请求路径不再抛 `KeyError`。 | 不得保留“缺整块静默、缺键请求期 500”的组合；不得把校验推迟到首次请求；非关键配置仍可回退默认但必须显式记录。 |
| D-08 | 已确认 | A：账号预设存 `data/accounts/presets.json`，但必须修锁/嵌套锁与写失败处理。 | 预设持久化沿用上游位置；`_load_presets` 与 `_save_presets` 不得在非重入锁内互相调用；写失败必须上报且不得留下半写文件。 | 不得改回只存 `config.yaml`；不得用静默吞异常换“启动成功”；不得在持有锁时执行可能再次加锁的路径；只读目录/删除文件场景不得死锁。 |
| D-09 | 已确认 | B：模板 hash 纳入 `executable_code/code_execution_result`；代码旁注释“严重路由异常时优先怀疑/回退”；提供诊断开关 `hash_include_code`，默认开启，不放进普通用户 UI。 | snapshot hash 恢复代码 part；`hash_include_code=false` 时回退上游行为；诊断开关走配置/env 与诊断文档，不进常规设置卡片。 | 默认必须包含代码 part；不得把诊断开关做成普通用户设置项；不得静默改变默认；出现严重路由异常时按注释优先怀疑该 hash 并回退。 |
| D-10 | 已确认 | A：drain 旧页一次有界等待，超时强制关闭；不无限重试。 | `_drain_retiring_context` 的 `while in_flight>0` 改为一次有界等待；超时强制关 Page/Context；不得留下常驻后台 task。 | 只允许一次有界等待；不得无界重试/轮询；drain 只发生在旧 Page/Context 上，期间不新建新页，新页在旧页关闭后创建；不得把 drain 当作第二页/页预算例外；超时后必须强制关闭并记录。 |
| D-11 | 已确认 | C：关闭所有非当前请求、非当前活跃账号的 Page/Context；只允许一个明确处于 drain 的旧页且有界超时；不允许页面/context 泄露。 | `connect_page`/prune 从“只关 about:blank”改为关闭全部非活跃 target；drain 只发生在当前 rebind 的旧 Page/Context 上，且有界；drain 期间不新建新页。 | 不得保留其他账号/其他 surface 的遗留页或 context；不得把“1 active + 1 drain”当作页预算例外；drain 期间不新建新页；不得用“只关空白页”换实现简单。 |
| D-12 | 已确认 | A：新设置必须进现有 UI（`SettingsView`/`ApiKeyManagerCard` 等现有卡片/表单），不重构 Web 布局/导航。 | 新增 `quota_scope`、API 免密开关、presets/tier 配置等按现有卡片增量承载；`hash_include_code` 是诊断开关，不进普通用户 UI（见 D-09）。 | 不得新增导航/路由或重排页面结构；不得只改 YAML 让设置不可见；不得把诊断开关伪装成普通用户设置。 |
| D-13 | 已确认 | B：删除没有 UI 的 notes/批量分组 API。 | 不实现 `notes` 字段/API；删除 `updateNotes`、`batchToggleGroup` 及其后端端点；保留分组 CRUD 与分组过滤 UI。 | 不得保留死接口/实验性 API；不得保留 notes 列或前端类型；分组 CRUD 与 UI 过滤不受影响。 |
| D-14 | 已确认 | API 鉴权与管理页鉴权分开。API 只加一个免密开关，默认关闭；管理页保持现有 `AISTUDIO_WEB_PASSWORD` 语义（空 = 免密，不报错）。不引入 IP/CIDR/设备白名单/本机-局域网模式；CF Tunnel 不改绑定逻辑。管理页不被 API Key 锁死。 | 新增 `security.api_allow_anonymous`（默认 false）；管理页继续只由 `AISTUDIO_WEB_PASSWORD` 控制；API Key 列表与 Web 密码互不复用。 | 不得把空 Key 解释为开放；不得用 IP/CIDR/设备白名单/“本机-局域网模式”做替代；不得改变 CF Tunnel 绑定；不得让管理页依赖 API Key；管理页空密码必须仍是免密而非报错。 |

## 2. 与 D-01..D-14 对应的旧结论处置

| 旧结论 | 处置 |
|---|---|
| 08-07 §3.2“上游已删除 watchdog，本仓不恢复” | 失效；按 D-02 保留 watchdog（仅孤儿回收）。 |
| 08-07 §3.2“上游已删除 liveness checker 与 CDP ping，本仓不恢复” | 部分失效；按 D-03 只保留 WebSocket ping。 |
| 08-07 §3.3“非流式/流式继续使用上游实现” | 保留，但受 D-09 hash 诊断与 D-10/D-11 生命周期约束。 |
| 08-07 §3.4“rotator 缺失/部分配置 fail-fast” | 失效；按 D-07 深合并默认 + 启动校验。 |
| 08-07 §3.6“禁止 `_retiring_contexts`/双页” | 保留“禁止 blue-green”，但按 D-05/D-10/D-11 允许在旧 Page/Context 上有界 drain（期间不新建新页、不是页预算例外）。 |
| 08-07 §2“API Key 默认拒绝，显式空 Key 才开放” | 失效；按 D-14 改为 API 独立免密开关（默认关闭）+ 管理页密码语义不变。 |
