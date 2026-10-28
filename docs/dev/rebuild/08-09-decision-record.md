# 08-09 决策记录与冲突闭合（99c38ba）

状态：**文档修复产物**。本文件把 `08-08-upstream-99c38ba-full-diff-audit.md` 的 14 项待决策项按用户确认结果固定下来，并逐项闭合 17 个 `NO_FEATURE_ROW` 与 20 个冲突。本轮只改 `docs/`，不改 `src/tests/config/fixtures`，不提交、不推送、不合并、不访问真实账号/IP/Google、不使用 Agent Manager、不派生子 agent。

本文件是 99c38ba 修复阶段的**行为权威入口**；`08-08` 是事实源（逐文件审计），`08-07` 已降级为历史文档。Gate 0/Gate 0.5 均**未通过**，本文件不宣告 handoff。

---

## 0. 基线与输入

| 项 | 值 |
|---|---|
| 仓库 / 工作分支 | `D:\LLM\self_programming\aistudio-api` @ `rebuild` |
| 本轮修复起点 HEAD | `cabcb97`（工作区干净） |
| 旧基线（仅 diff 左端） | `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| 上游基线（diff 右端） | `99c38baefc84d8cb66b05c28ede8a49ce050bfb2`（= `upstream/master`） |
| 全量审计 | `08-08-upstream-99c38ba-full-diff-audit.md`：76/76 文件、17 `NO_FEATURE_ROW`、20 冲突、14 待决策 |
| 历史审计（不得作事实源） | `08-07-upstream-99c38ba-reaudit.md`（领域级初审，已被 08-08 指出 16 处漏项/错项） |
| 旧计划文档 | `05/06/10/12/13`；本轮按 08-08/08-09 修订 |

输入约束：

- 08-08 的 `file:line` 一律指 `99c38ba` 树内行号；复查用 `git show 99c38ba:<path>`。
- 08-07 的领域级结论不得再作为 handoff 依据；其正文保留为历史。
- 用户未确认的项不得写成已确认；工程默认必须标注 `ENGINEERING_DEFAULT` 且可回退。

---

## 1. 已确认决策 D-01..D-14

以下 14 项**全部已确认**，不再 pending；状态列统一为“已确认”。每项的“不得再改的边界”是执行红线，后续文档/实现不得越过；若确需变更，必须由用户重新裁决并新建决策记录，不得在 worker 或 reviewer 层自行改写。

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

### 1.1 与 D-01..D-14 对应的旧结论处置

| 旧结论 | 处置 |
|---|---|
| 08-07 §3.2“上游已删除 watchdog，本仓不恢复” | 失效；按 D-02 保留 watchdog（仅孤儿回收）。 |
| 08-07 §3.2“上游已删除 liveness checker 与 CDP ping，本仓不恢复” | 部分失效；按 D-03 只保留 WebSocket ping。 |
| 08-07 §3.3“非流式/流式继续使用上游实现” | 保留，但受 D-09 hash 诊断与 D-10/D-11 生命周期约束。 |
| 08-07 §3.4“rotator 缺失/部分配置 fail-fast” | 失效；按 D-07 深合并默认 + 启动校验。 |
| 08-07 §3.6“禁止 `_retiring_contexts`/双页” | 保留“禁止 blue-green”，但按 D-05/D-10/D-11 允许在旧 Page/Context 上有界 drain（期间不新建新页、不是页预算例外）。 |
| 08-07 §2“API Key 默认拒绝，显式空 Key 才开放” | 失效；按 D-14 改为 API 独立免密开关（默认关闭）+ 管理页密码语义不变。 |

---

## 2. 17 个 NO_FEATURE_ROW 逐项分类

分类口径：**feature** = 真实功能，补独立 feature 行；**out-of-scope** = 按已确认决策删除或不进入本阶段，不塞进 feature 数。编号 N01..N17 与 08-08 §4 一一对应，无遗漏。

| 上游能力 | 分类 | 映射 | 说明 |
|---|---|---|---|
| N01 账号 enabled/disabled + 候选过滤 + active 只在 enabled 回退 | feature | `D02-032` | 与 D-05 的 rebind 修复绑定；禁用即退出候选/活跃。 |
| N02 账号分组 CRUD + 分组过滤 UI（批量分组启停） | feature（部分 out-of-scope） | `D02-033` | 分组 CRUD 与 UI 过滤保留；`batchToggleGroup` 按 D-13 删除，不计入行。 |
| N03 账号 notes 字段/API | out-of-scope | — | D-13 明确删除无 UI 的 notes；不新增 feature 行。 |
| N04 账号预设 CRUD/snapshot/apply/system presets | feature | `D02-034` | 按 D-08 修锁与写失败；持久化 `data/accounts/presets.json`。 |
| N05 订阅层级每日软配额（tier_quotas + resolve_tier_quota + 打分/展示） | feature | `D05-044` | 记账键由 `quota_scope` 决定（D-01）。 |
| N06 rotator 打分配置（strategy/stickiness/drip_penalty/tier_weights） | feature | `D05-045` | 按 D-07 深合并默认 + 启动校验；只作用于现有实例候选。 |
| N07 paid_model_rules 静态准入规则 | feature | `D05-046` | 规则→required_tiers；解析顺序与空规则回退见 `ENGINEERING_DEFAULT E-07`。 |
| N08 tier 抢占（required_tiers + attempt 0 主动切号 + 本地 403） | feature（裁剪） | `D05-047` | 按 D-04 只保留“准入 + 本地 403 + 准确 tier 信息”；主动抢占/请求期切号 out-of-scope。 |
| N09 `/stats` 按账号明细 `account_breakdown` | feature | `D10-033` | stats REST 派生视图，不另建事件账本。 |
| N10 finish_reasons 聚合与展示 | feature | `D10-034` | `ENGINEERING_DEFAULT E-02`：先进 stats API；UI 随统计页实现。 |
| N11 生成参数 seed（wire index 18 / Field 19） | feature | `D06-071` | `ENGINEERING_DEFAULT E-04`：仅上游支持面生效；其他协议接受、告警、忽略。 |
| N12 请求体 ≥96KiB 自动 gzip + base64（浏览器内解压） | feature | `D06-072` | 上游实现可直接采用；已有 `Content-Encoding` 时放行。 |
| N13 wire 字段 12/14 语义改名 | feature | `D06-073` | `cached_content→session_token`、`location→request_context`；文档同步。 |
| N14 finish reason 22 CONTINUING（非终止）+ 18 MISSING_THOUGHT_SIGNATURE | feature | `D06-074` | 按 `ENGINEERING_DEFAULT E-03` 采用上游新表，保留兼容映射与客户端可见字符串测试。 |
| N15 video_models（Veo）profile + imagen 图像 profile | feature | `D01-064` | 原生配置面，纳入 model defaults。 |
| N16 CDP `$rpc/` Network.loadingFailed 证据链 | feature | `D03-036` | 失败带 CDP net 证据；不影响重试分类语义。 |
| N17 `defaults/config.yaml` 作为运行时引导模板（缺失即复制） | feature | `D01-063` | 首启配置初始化是运行时行为，不是 build artifact。 |

**分类结果：16 个 feature（新增 16 行）+ 1 个 out-of-scope（N03）。** 合计 17，无遗漏；不把任何项塞进“其他”。

说明：这 17 项本身没有纯 tooling/build artifact；08-08 §3 中另有一组 `NO_FEATURE_ROW`（`bun.lock`/`package.json`/`tsconfig.tsbuildinfo`/`static/assets`），按 §2.1 单列 out-of-scope，不混入这 17 项，也不进入 feature 数。

### 2.1 工具/构建产物（不在这 17 项内，单独标 out-of-scope）

08-08 §3 中以下行同样是 `NO_FEATURE_ROW`，但属于 tooling/build artifact，不进入 feature 数，也不新增 feature 行：

| 文件 | 分类 | 处置 |
|---|---|---|
| `web/bun.lock`、`web/package.json` | tooling | out-of-scope；仅记录上游 devDependency 版本变化。 |
| `web/tsconfig.tsbuildinfo` | build artifact | out-of-scope；不提交增量构建缓存。 |
| `static/assets/*.js`（7 个） | build artifact | out-of-scope；只要求源码改动后重新构建、产物与源码一致，不逐产物记 feature。 |

### 2.2 08-08 编号缺陷说明

08-08 §3 的个别行引用了 `C21`/`C22`，但 §5 冲突登记表只定义 `C01..C20`。本轮按“20 冲突”口径闭合 `C01..C20`；`C21` 对应 API Key 空值语义（由 **D-14** 闭合），`C22` 对应预设持久化缺陷（由 **D-08** 闭合），不另立冲突编号，也不把它们算作第 21/22 项。

---

## 3. 20 个冲突逐项闭合

格式：`现有 ID → 决策/工程默认 → 文档/测试影响`。“用户决策”不得被改写成工程默认；未获用户裁决的取舍一律标 `ENGINEERING_DEFAULT`。

| ID | 闭合依据 | 具体闭合 | 文档/测试影响 |
|---|---|---|---|
| C01 | D-01 | 保留 `surface` 维度与 `record_*(usage=...)` 能力；只移植打分/软配额；调用点适配 `quota_scope`。 | 05 §3/§4、10 状态机、12 §3；`test_rotator_surface_keys.py`、`test_account_rotation_concurrency.py` 补 `account_surface` 键用例。 |
| C02 | D-01 | 存储键按 `quota_scope`：`account` 用 `model`，`account_surface` 用 `surface::model`；不放弃分面能力，也不默认分面。 | 12 §3 config；`test_runtime_persistence.py` 补迁移/双口径断言。 |
| C03 | D-05 + D-10 + D-11 | 禁止 blue-green“先建新页再 drain”；rebind 先 `RETIRING`/停在途/有界等待/关旧页再建新页；drain 只在旧 Page/Context 上且有界，期间不新建新页；不是页预算例外。 | 05 §4 D4、10 状态机红线段、12 §2；`test_session_lifecycle.py` 补 rebind 序列与 drain 超时。 |
| C04 | D-10 | drain 一次有界等待，超时强制关闭；删除 `while in_flight>0` 无限重试。 | 12 §2；`test_session_lifecycle.py` 补“超时强制关闭/无后台 task”。 |
| C05 | D-05 | 禁用/应用预设立即 rebind；先 `RETIRING`、停在途、`active_requests==0` 或有界等待、关旧 Page/Context，再切换；修 toggle/preset 的 fallback 先于 switch。 | 05 §4、10 状态机、12 §2；新增 `test_routes_account_presets.py` 覆盖 8 端点与禁用生效。 |
| C06 | D-04 + **ENGINEERING_DEFAULT E-07** | 403 必须带准确 `required_tier/current_tier`；`required_tiers` 解析顺序为“配置规则 → accessModes → paid/capability”，空规则不得回退成“Ultra-only”误判。 | 10 错误口径、12 §3；新增 `test_model_tier_admission.py`。可回退：若规则解析顺序造成回归，恢复“仅 accessModes”并记录。 |
| C07 | D-04 | 取消 attempt 0 主动抢占/整段 rebind；付费模型只做准入拒绝。 | 05 §4、10 状态机；`test_tier_preemption.py` 改写为“403 准入 + 不触发 switch_auth”。 |
| C08 | D-06 | 403 由领域错误产生，API 层按协议映射。 | 10 错误口径、12 §4；`test_api_responses.py`、`test_format_error_routing.py` 补四协议体。 |
| C09 | D-07 | rotator 配置深合并默认 + 启动校验；不静默、不请求期 KeyError。 | 12 §3、10 Gate0.5；新增配置校验用例（缺键/部分键/错类型）。 |
| C10 | D-02 | 保留 pipe watchdog（仅孤儿进程清理）；恢复 watchdog 用例。 | 05 §4、12 §2；`test_browser_engine.py` 改回 keep。 |
| C11 | D-03 | 保留 WebSocket ping；删除 liveness checker 与轮询。 | 05 §4、12 §2；`test_cdp_client.py` 保留 ping/断线，删轮询用例。 |
| C12 | D-11 | prune 关闭所有非当前请求/非当前活跃账号的 Page/Context；drain 只在旧 Page/Context 上且有界，期间不建新页。 | 12 §2；`test_cdp_client.py` 补“非空白遗留页也关闭/drain 期间不建新页”。 |
| C13 | D-08 | 预设持久化 `data/accounts/presets.json`；修锁/嵌套锁与写失败。 | 12 §3；`test_account_presets.py` 补锁/写失败/只读目录。 |
| C14 | **ENGINEERING_DEFAULT E-01** | 为 8 个新 REST 端点补路由级测试；28/28 只是文件覆盖，不等于行为覆盖。 | 06-test-disposition 新增 4 个测试文件行；13 G0.5 不得再以 28/28 宣称行为通过。 |
| C15 | D-12 | 新设置进入现有 `SettingsView`/`ApiKeyManagerCard` 等卡片；不重构布局/导航；诊断开关不进普通 UI。 | 10 设置清单、12 §3；Web 测试/验收补“设置可见且可保存”。 |
| C16 | D-13 | 删除 notes/批量分组 API 与前端调用；保留分组 CRUD 与过滤 UI。 | 05 §3、10 采用边界、12 §3；`test_routes_account_presets.py` 不得再测 notes/batch。 |
| C17 | **ENGINEERING_DEFAULT E-02** | `finish_reasons` 先进 stats API；UI 随统计页实现，不在 P5 强行加。 | 10、12 §4；`test_stats_account_breakdown.py` 断言 API 字段，UI 后置。可回退：若统计页提前，则同步补 UI 消费。 |
| C18 | **ENGINEERING_DEFAULT E-03** | 采用上游新 finish reason 编号表；保留兼容映射与客户端可见字符串测试；客户端破坏则回退兼容表。 | 10 协议边界、12 §4；`test_api_responses.py`、`test_wire_codec.py` 补编号/兼容映射。 |
| C19 | D-09 | 模板 hash 纳入 `executable_code/code_execution_result`；`hash_include_code` 默认 true，诊断用；代码旁注释回退提示。 | 10 诊断段、12 §3；`test_browser_session.py` 期望恢复为包含代码 part，并补开关用例。 |
| C20 | **ENGINEERING_DEFAULT E-04** | seed 只在上游支持面（Gemini）生效；其他协议接受但忽略并告警，不静默丢弃、不 500。 | 10 协议边界、12 §4；`test_wire_codec.py` 补 seed；协议适配补“忽略 + 告警”。 |

**闭合计数：20/20。** 其中用户决策直接闭合 15 项（C01-C05、C07-C13、C15、C16、C19），工程默认闭合 4 项（C14、C17、C18、C20）；C06 由 D-04 + E-07 共同闭合。无“其他/杂项”兜底。

---

## 4. 工程默认清单（ENGINEERING_DEFAULT，可回退）

以下不是用户裁决，不得在 05/06/10/12/13 或实现中写成“用户确认”。每项都给出回退条件。

| ID | 内容 | 可回退条件 |
|---|---|---|
| E-01 | 为 `POST /{id}/toggle`、`PUT /{id}/group`、`GET /groups/list`、`GET /presets/list`、`POST /presets`、`POST /presets/snapshot`、`DELETE /presets/{id}`、`POST /presets/{id}/apply` 补路由级测试（按最终保留端点集调整；notes 与批量分组 API 已按 D-13 删除）。 | 若端点集变更，测试随端点增删；不得以“文件已覆盖”替代。 |
| E-02 | `finish_reasons` 先进入 stats API；Dashboard/统计页消费后置。 | 若统计页提前实现，补 UI 消费并同步 12 §4；API 字段不移除。 |
| E-03 | 采用上游 finish reason 新编号表；保留兼容映射与客户端可见字符串测试。 | 若下游客户端出现破坏性回归，回退到兼容表，并记录兼容窗口。 |
| E-04 | seed 仅 Gemini 生效；OpenAI/Claude/Responses 接受但忽略并告警。 | 若上游后续支持其他协议，按上游实现扩展；回退时保持“接受 + 告警”，不得静默丢弃或 500。 |
| E-05 | 17 个 `NO_FEATURE_ROW` 按 §2 分类：16 feature + 1 out-of-scope；tooling/build artifact 不进入 feature 数。 | 若用户后续要求 notes/batch 回归，需新建决策并补行。 |
| E-06 | 任何未覆盖冲突只写 `ENGINEERING_DEFAULT` + 可回退，不写 adopt/disable 的最终产品裁决。 | 用户明确裁决后，升级为用户决策并更新 08-09 §1。 |
| E-07 | `required_tiers` 解析顺序：配置规则 → accessModes → paid/capability；空规则不得回退成“Ultra-only”。 | 若该顺序导致准入回归，回退为“仅 accessModes + paid 标记”，并在 403 中保持准确 tier 信息。 |
| E-08 | 08-08 §3 的 `C21/C22` 是编号缺陷：C21 由 D-14 覆盖，C22 由 D-08 覆盖，不另立冲突。 | 若后续审计要求正式编号，再补登记，不改变闭合结论。 |

---

## 5. 修复阶段 F1-F8（更新版）

顺序原则：先事实/契约，再核心账本，最后生命周期/统计/UI。任何阶段在 Gate 0.5 通过前不得进入 P9-pre 编码。

| 阶段 | 内容 | 文件域 | 依赖 | 禁止并行 |
|---|---|---|---|---|
| F0 | **用户确认**：D-01..D-14。**worker 工程产物（非用户确认）**：17 分类、20 冲突闭合、`ENGINEERING_DEFAULT`、F1-F8。 | `08-09-decision-record.md`（只读输入；不改 src/tests/config）。 | 08-08 事实源。 | 无（已完成）。 |
| F1 | rotator 契约与 `quota_scope`：保留 surface、`record_*(usage=...)`、状态 schema 迁移、打分/软配额只作用于现有实例候选；`rotator/tier_quotas` 深合并默认 + 启动校验。 | `application/account_rotator.py`、`application/routing_pipeline.py`（仅 rotator 调用点）、`infrastructure/gateway/model_defaults.py`（rotator/quota 加载）、config schema。 | F0（本文件） | 与 F3 同时改 active/候选语义；与 F4 同时改 `account_orchestrator`；与任何阶段同时改 `model_defaults` 的 rotator 块。 |
| F2 | 纯协议/传输修复：wire 改名 + seed + CONTINUING/18 + 角色守卫 + 结构化 payload；transport net evidence；gzip；finish 表；空 200/空流。 | `infrastructure/gateway/{wire_spec,wire_types,wire_codec,wire_parser,streaming,transport,client}.py`、`infrastructure/browser/{scripts.py,js/hooked_request.js,js/streaming_init.js}`、`domain/errors.py`、`application/api_service_gemini.py`（finish 表）、`docs/WIRE_SPECIFICATION.md`。 | 无（可在 F1 契约冻结后并行） | 与 F5 同时改 `cdp_client.py`/`session.py`/`pooled_session.py`；与 F6 同时改 stats schema。 |
| F3 | 账号域：enabled/group（不含 notes/batch）、presets store + 路由 + UI 过滤；只负责 registry 与 active fallback 修复（D-05 的 fallback 先于 switch）；**不实现**页面/Context rebind 生命周期（归 F5）。 | `infrastructure/account/account_store.py`、`application/account_service.py`、`api/routes_accounts.py`、`web/src/api/accounts.ts`、`web/src/stores/accounts.ts`、`web/src/types/accounts.ts`、`AccountRow.vue`/`AccountsView.vue`（分组/启停部分）。 | F1（active/候选语义） | 与 F1 同时改 rotator 契约；与 F4 同时改 `account_orchestrator.py`；与 F5 同时改 `session.py`/`instance_switch.py`（rebind 生命周期只在 F5）。 |
| F4 | 准入/403：`is_paid_model`/`required_tiers` 去歧义（E-07）、403 领域错误与协议映射、取消请求期抢占。 | `infrastructure/gateway/model_discovery.py`、`application/account_orchestrator.py`、`api/app.py`（错误处理）、各协议适配器。 | F1、F3 | 与 F3 同时改账号路由；与 F5 同时改 `account_orchestrator`/`session.py` rebind 路径。 |
| F5 | 会话/生命周期：rebind 有界 drain、无 idle、`ensure_ready`、page prune（D-11）、watchdog 保留（D-02）、WS ping only（D-03）、hash 纳入代码 part + 诊断开关（D-09）。 | `infrastructure/gateway/session.py`、`session_auth.py`、`infrastructure/browser/{browser_engine.py,cdp_client.py}`、`application/{instance_switch.py,instance_manager.py}`、`pooled_session.py`、`session_state.py`、`config.py`（idle 删除/超时默认）。 | F3、F4 | 与 F4 同时改 rebind 路径；与 F2 同时改 transport/CDP；与 F6 同时改 stats 状态字段。 |
| F6 | 统计：`/stats` breakdown、finish_reasons、reasoning/cached；只读端点去副作用；`quota_scope` 口径展示。 | `api/state.py`、`api/routes_system.py`、`api/response_models.py`、`api/responses.py`、`web/src/types/stats.ts`、`ModelStatsTable.vue`（数据消费部分）。 | F2（usage）、F3（账号元数据） | 与 F2 同时改 usage/finish schema；与 F7 同时改 Dashboard 统计组件。 |
| F7 | Web：Accounts 预设/分组/启停、Dashboard 卡片与 breakdown、ProfileCard 修复、新设置进现有卡片/表单（D-12）。 | `web/src/views/{AccountsView,DashboardView,SettingsView}.vue`、`web/src/components/accounts/*`、`web/src/components/dashboard/*`、`web/src/components/config/*`、`static/assets/*`（重新构建产物）。 | F3、F4、F6 | 与 F6 同时改 stats 组件；与任何阶段同时提交 `static/assets`（构建产物只在该阶段末重建）。 |
| F8 | 测试处置落地：路由级/行为级测试、acceptance、`06-test-disposition.csv`、13 记录。 | `tests/**`、`docs/dev/rebuild/06-test-disposition.csv`、`docs/dev/rebuild/13-gate0-execution-record.md`。 | F1-F7 | 与任何 src 阶段同时改测试契约；F8 只收口，不并行开新 src 改动。 |

阶段红线：

- F2 与 F3 虽文件域可分离，但共享 `active_requests`/错误分类语义；必须先冻结 F1 契约。
- F5 必须在 F3/F4 之后，因为 rebind 依赖 active/准入规则；F5 不得与 F4 同时改 `account_orchestrator.py`。
- `static/assets` 只在 F7 末重建一次；禁止源码与产物跨阶段各改各的。
- 任何阶段不得以“保持旧测试绿”为由恢复 lease/pinned/多页/blue-green。

---

## 6. 本轮数字变更

| 项 | 旧值 | 新值 | 原因 |
|---|---:|---:|---|
| feature 行（CSV/ledger） | 518 | **534** | 16 个真实功能新增行（N01/N02/N04/N05/N06/N07/N08/N09/N10/N11/N12/N13/N14/N15/N16/N17）；N03 out-of-scope。 |
| P1 (D02) | 31 | 34 | +N01/N02/N04 |
| P2 (D05) | 43 | 47 | +N05/N06/N07/N08 |
| P3 (D03+D04) | 97 | 98 | +N16（D03） |
| P5 (D06) | 70 | 74 | +N11/N12/N13/N14 |
| P7 (D09+D10) | 67 | 69 | +N09/N10 |
| P9 (D01) | 62 | 64 | +N15/N17 |
| P4/P6/P8/P10 | 44/25/42/37 | 不变 | 无新增行；**P4 受 08-10/P4-B0a 阻塞**：Build native 400 `PROXY_GENERATION_DISABLED` 未完成离线溯源/Q4 写回前，不得写 native/transport/Q4 相关实现；signer-only 离线 spike 与非传输 P4 可继续；B0b 未通过不得进入 P4 验收。 |
| 测试处置行 | 82 | **86** | 新增 4 个行为/路由测试文件行；现有行处置按 D-02/D-03/D-05/D-09/D-11 修正。 |

索引脚本同步更新后必须以 `errors=0` 为准；数字变化不得通过把 17 项/20 冲突塞进“其他”实现。

---

## 7. 仍未完成 / 不得声称

- **Gate 0 未通过**：计划文档已提交 b3f09d1，Build 事故记录已提交 e64a4d9，P4-B0 修订已提交 1bc0efc/b1cce0c 且工作区干净；P4-B0 文档复审 PASS，但 Gate0 其他项未闭合，G0.1 不得写通过。
- **Gate 0.5 未执行**：config/event schema、fixture、acceptance 必须在迁移 worktree 基于 99c38ba 重新验证；本文件只冻结输入。
- **不宣告 handoff**：是否可交接由后续独立 reviewer 判定；本文件不自称通过。
- 未运行上游测试套件、未访问真实账号/IP/Google、未验证 `static/assets` 本地可复现构建。
- 未完成项：F1-F8 均未实施；`06-test-disposition.csv` 的新增行只是处置建议，不是已通过证据。
