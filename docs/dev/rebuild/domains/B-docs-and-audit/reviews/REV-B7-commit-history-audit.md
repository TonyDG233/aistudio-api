# REV-B7 提交历史审计（`99c38ba..HEAD`）

- 审计日期：2026-11-01（UTC+8）。
- 目标树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`。
- 分支 / HEAD：`rebuild-upstream` / `55f1c64`。
- 基线 / 范围：`99c38ba` / `99c38ba..HEAD`。
- 审计方式：只读 `git log/show/numstat/diff-filter`、源码与文档交叉核对；严格离线，未启动服务/浏览器/网络。
- 计数：**33 提交** = 28 个重建分支提交 + 5 个上游提交；其中 3 个合并提交、30 个非合并提交。

## 1. 总表

| # | hash | 日期 | 类别 | 阶段/切片 | 文件 | + | − | 范围判定 |
|---:|---|---|---|---|---:|---:|---:|---|
| 1 | `271321b` | 2026-10-28 | 文档 | A5/A6 基线迁移 | 236 | 8933 | 0 | 仅文档/证据，非代码 |
| 2 | `414b79b` | 2026-10-28 | 文档/夹具 | Gate 0 | 12 | 450 | 6 | 配置示例/Schema/fixture，非运行代码 |
| 3 | `9b5cdea` | 2026-10-28 | 工具链 | Gate 0 收尾 | 1 | 2 | 0 | `.gitattributes` 二进制稳定 |
| 4 | `c18fdaf` | 2026-10-29 | 文档 | A5/A6 权威文档 | 42 | 2719 | 1262 | 文档权威切换 + 08-08/08-09 落地 |
| 5 | `875f9fb` | 2026-10-29 | 文档 | A7.1 | 6 | 256 | 213 | 拆分 D 决策/归档 agent 记录 |
| 6 | `bc36904` | 2026-10-29 | 文档 | A7 | 2 | 3 | 2 | 工作流准备 |
| 7 | `4e4e16e` | 2026-10-29 | 文档 | A7.2-A7.6 | 23 | 868 | 12 | 模板/事故入口/ledger-batches 回填 |
| 8 | `755e28a` | 2026-10-29 | 文档 | A7 清理 | 13 | 0 | 749 | 删除 ledger-batches（与 #7 反复） |
| 9 | `9b671f2` | 2026-10-29 | 文档 | B 任务包草案 | 16 | 1074 | 0 | PKG/REV 草案，无代码 |
| 10 | `d8fc901` | 2026-10-29 | 文档 | A 阶段冻结 | 31 | 1015 | 10 | 归档 batch-drafts，冻结 00-01/00-02 |
| 11 | `a3ffbe6` | 2026-10-29 | 文档 | A/B 交接 | 1 | 240 | 65 | `AGENT.md` 对齐 B 架构 |
| 12 | `a9e7dc3` | 2026-10-29 | 代码 | B1 / P9-pre | 16 | 1337 | 490 | 深合并/启动校验/API 鉴权/lifespan；删 tracked `config.yaml` |
| 13 | `8b80971` | 2026-10-29 | 代码 | B2-1 / P1 | 34 | 3479 | 366 | Cookie/刷新/预设/Paid；新增三个应用/基础设施模块 |
| 14 | `f2b1e28` | 2026-10-29 | 上游 | upstream | 24 | 338 | 327 | 内存/看门狗/Ultra_20X/配置编辑器；后由 `ca8496c` 吸收 |
| 15 | `a606fbe` | 2026-10-29 | 代码 | B2-2 / P3-D03 | 18 | 1729 | 366 | 浏览器隔离/单页生命周期；删 liveness/blue-green 残留 |
| 16 | `0cdfd35` | 2026-10-30 | 代码 | B2-3 / P3-D04 | 22 | 3515 | 66 | 实例池/InstanceManager/实例路由 |
| 17 | `ca8496c` | 2026-10-30 | 合并 | 吸收 `f2b1e28` | 20 | 200 | 293 | 上游内存/看门狗/Ultra_20X/Web 编辑器 |
| 18 | `b30315e` | 2026-10-30 | 代码 | B2-4 / P2 | 47 | 4611 | 1876 | 路由主链/领域错误/实例客户端；删旧全局 failover |
| 19 | `9aabdf3` | 2026-10-30 | 代码 | B3 / P6 | 12 | 1831 | 50 | 24h 保活/元数据刷新/锁加固 |
| 20 | `4653ec7` | 2026-10-30 | 测试/文案 | B3 收尾 | 4 | 133 | 2 | 保活断言补强，无新模块 |
| 21 | `16450cc` | 2026-10-30 | 上游 | upstream | 32 | 121 | 171 | 删除请求级超时 watchdog；经 `05a9df7` 进入 |
| 22 | `99600c7` | 2026-10-30 | 上游 | upstream | 11 | 84 | 76 | BotGuard 就绪/SessionContext 固定/去 gzip 拒绝 |
| 23 | `424f896` | 2026-10-31 | 代码 | B4 / P7 | 25 | 3406 | 188 | 三 Sink/SSE/导出/前端静态产物 |
| 24 | `05a9df7` | 2026-10-31 | 合并 | 吸收 `16450cc`+`99600c7` | 29 | 667 | 199 | 合并 subject 只写 `99600c7`，实际含两个上游提交 |
| 25 | `629614f` | 2026-10-31 | 代码 | B5a / PG | 15 | 1632 | 162 | 工具调用/PG-01..04/signature cache |
| 26 | `9bffda1` | 2026-10-31 | 代码 | B5b / P5 | 50 | 8841 | 324 | 生成主链/ATG/格式转换/OpenAI 路由；最大单提交 |
| 27 | `ccd15e3` | 2026-10-31 | 上游 | upstream | 48 | 943 | 436 | 403 级联/流中断/新账号 bootstrap/日志 UI |
| 28 | `852de53` | 2026-10-31 | 上游 | upstream | 14 | 80 | 89 | 403 文档对齐/冗余清理/AST hash |
| 29 | `b1a749e` | 2026-10-31 | 代码 | B6 / 发现加固 | 24 | 2697 | 150 | 真实 ListModels/动态 Paid/防重复预热 |
| 30 | `da937b6` | 2026-10-31 | 合并 | 吸收 `ccd15e3`+`852de53` | 57 | 1999 | 391 | 合并 subject 只写 `852de53` |
| 31 | `b000bb7` | 2026-10-31 | 代码 | B6 / 路由修复 | 26 | 2409 | 296 | 均衡/故障槽换绑/Tier Schema/403 透传 |
| 32 | `1f60fbc` | 2026-11-01 | 代码/CI | B6 closeout | 17 | 411 | 123 | B6 证据/日志脱敏/配置路径 env/Docker CI |
| 33 | `55f1c64` | 2026-11-01 | CI | B6 后置 | 1 | 0 | 9 | GHCR-only；无业务代码变化 |

> 合并提交的 `文件/+/-` 为 `git diff <merge>^1 <merge>` 的首父差异，等价于该次合并实际落入目标分支的范围。

## 2. 上游提交识别

以下 5 个提交作者为 `DeconstructedCube`，不是重建实现，而是上游历史；它们因 `99c38ba..HEAD` 范围与合并进入本审计：

| 上游提交 | 日期 | 进入路径 | 备注 |
|---|---|---|---|
| `f2b1e28` | 2026-10-29 | `ca8496c` 合并 | 唯一由 `ca8496c` 吸收 |
| `16450cc` | 2026-10-30 | `05a9df7` 合并 | subject 未点名 |
| `99600c7` | 2026-10-30 | `05a9df7` 合并 | subject 点名 |
| `ccd15e3` | 2026-10-31 | `da937b6` 合并 | subject 未点名 |
| `852de53` | 2026-10-31 | `da937b6` 合并 | subject 点名；上游 `master` 尖端 |

## 3. 逐提交审计

### 3.1 A 阶段：文档、Gate 0、任务包（#1–#11）

#### #1 `271321b` docs(rebuild): 迁移 rebuild 文档与 evidence allowlist 到 upstream 基线

- **真实范围**：把重建文档树与脱敏 evidence 白名单迁入 upstream 基线；新增 `docs/dev/rebuild/01..13`、`06-P*-questions`、`ledger-batches/README`、`docs/dev/evidence/wp5..wp8` 等 236 个文件；更新 `docs/changelog.md`。
- **关键文件/模块**：`docs/dev/rebuild/*`、`docs/dev/evidence/*`；无 `src/`。
- **决策**：无 D-xx 代码决策；为后续 D-01..D-14 与 P1-P10 提供事实源。
- **移除/新增**：无代码移除；新增审计语料与证据索引。
- **影响**：确定“文档与证据先入库、代码后落地”的顺序；后续所有文档修订基于此语料。

#### #2 `414b79b` docs(rebuild): 记录 Gate 0 执行并冻结 Gate 0.5 输入

- **真实范围**：新增 Gate 0 执行记录、`config/config.example.yaml`、`config/config.schema.json`、event/PG fixture（`f01..f04`）、P9-pre 验收模板；修改 Gate 0 输入文档与 changelog。
- **关键文件/模块**：`config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/{events,pg}`。
- **决策**：把 `quota_scope`、`security.api_allow_anonymous`、rotator/paid/tier/presets、`hash_include_code` 纳入单一 schema（D-07/D-14 的输入冻结）。
- **移除/新增**：无移除；新增配置示例/Schema 与离线 fixture。
- **影响**：后续 B1 的配置校验与 B5a 的 PG fixture 有正式输入。

#### #3 `9b5cdea` chore(tests): 保持 f03 binary fixture 字节稳定

- **真实范围**：`.gitattributes` 增加 2 行（`tests/fixtures/pg/*.bin` 等）。
- **关键文件/模块**：`.gitattributes`；`tests/fixtures/pg/f03_finish_only.bin`。
- **决策**：无 D-xx；测试资产稳定性。
- **移除/新增**：无。
- **影响**：防止二进制 fixture 被 EOL 转换污染，保证 B5a 离线复现。

#### #4 `c18fdaf` docs(rebuild): 同步 A5/A6 权威文档与 P5 域

- **真实范围**：42 文件 +2719/−1262。新增 `00-chat-postmortem.md`、`08-08-upstream-99c38ba-full-diff-audit.md`、`08-09-decision-record.md`、`08-10/08-11` 事故；重写 `05-restart-map.md`、`06-decision-log.md`、`06-feature-index.csv`、`06-feature-ledger.md`；把 `01..08-07`、`12/13` 归档；把 `09-pg-toolcall-analysis.md` 迁入 `domains/P5-generation/`。
- **关键文件/模块**：`docs/dev/rebuild/06-*`、`08-08`、`08-09`、`archive/*`、`domains/P5-generation/*`。
- **决策**：正式建立 D-01..D-14 与 P1-P10 的权威记录（D 表在 `08-09`），并把 99c38ba 上游差异审计固定为事实源。
- **移除/新增**：移除活动区 `acceptance/P9-pre.md`、`12-gate0-inputs.md`、`13-gate0-execution-record.md`、`ledger-batches/README.md`（迁 archive 或后续重建）；新增 08-08/08-09/08-10/08-11。
- **影响**：`08-09` 成为后续所有实现的决策来源；文档权威从旧 F1-F8 切到 P1-P10。

#### #5 `875f9fb` docs(rebuild): A7.1 同步 D 决策拆分与 PG 编号

- **真实范围**：把 `08-09` 中的 agent 产物（17 个 `NO_FEATURE_ROW`、20 个冲突、ENGINEERING_DEFAULT、旧 F1-F8 等）归档到 `archive/08-09-agent-record-20261028.md`，`08-09` 只保留 D-01..D-14 与旧结论处置；同步 PG 编号与 P5 域文档。
- **关键文件/模块**：`08-09-decision-record.md`（177 行缩减）、`archive/08-09-agent-record-20261028.md`（+210）、`domains/P5-generation/*`。
- **决策**：D-01..D-14 是“用户唯一负责内容”；agent 推断不得冒充决策（与项目记忆中的决策溯源修正一致）。
- **移除/新增**：移出 agent 产物；新增归档记录。
- **影响**：决策可追溯性提高；后续审计必须以原始记录为准。

#### #6 `bc36904` docs(rebuild): A7 同步 agent 工作流准备步骤

- **真实范围**：`05-restart-map.md`、`10-handoff-prompt.md` 各 1-2 行，补工作流准备步骤。
- **决策**：无。
- **影响**：仅流程文档。

#### #7 `4e4e16e` docs(rebuild): A7.2-A7.6 同步工作流材料与事故入口

- **真实范围**：新增 `domains/_template/PKG.md`/`REV.md`、`incidents/README.md`；把两个 INC 文档迁入 `incidents/`；**回填 `ledger-batches/D01-D12`（+749 行）**；更新 restart/decision/handoff。
- **关键文件/模块**：`docs/dev/rebuild/domains/_template/*`、`incidents/*`、`ledger-batches/*`。
- **决策**：任务包/审查模板标准化；事故入口固定。
- **移除/新增**：新增模板与事故索引；新增 ledger-batches（下一提交即删除）。
- **影响**：模板成为后续 PKG/REV 的格式来源；ledger-batches 出现“加了又删”的文档抖动。

#### #8 `755e28a` docs(rebuild): 移除目标树历史 ledger-batches

- **真实范围**：删除 `ledger-batches/D01..D12` + README（13 文件 −749）。
- **关键文件/模块**：`docs/dev/rebuild/ledger-batches/*`。
- **决策**：不保留逐 domain 的 batch 副本；功能点保留在 `06-feature-*` 与域文档。
- **移除/新增**：移除 #7 新增的 13 个文件。
- **影响**：与 #7 构成同日反复；不丢功能行，但增加历史噪音。

#### #9 `9b671f2` docs(rebuild): 同步 B 任务包草案与只读审查报告

- **真实范围**：新增 8 个 `PKG-*` 与 7 个 `REV-*`（P1/P2/P3/P5/P6/P7/P9 域），共 16 文件 +1074。
- **关键文件/模块**：`domains/*/packages/*`、`domains/*/reviews/*`。
- **决策**：把 D-01..D-14 与 P 切片写入任务包（如 B1 API guard、B2 单页、B5a PG 门槛）。
- **移除/新增**：无移除；新增计划/审查草案。
- **影响**：后续实现按包推进；这些草案后被 `d8fc901` 归档。

#### #10 `d8fc901` docs(rebuild): freeze phase A docs and archive failed batch drafts

- **真实范围**：新增 `00-01-plan-audit.md`（+110）、`00-02-chat-plan-audit.md`（+894）；把 17 个 batch drafts 迁入 `archive/batch-drafts-20261029/`；给各 domain reviews 加 `.gitkeep`；更新 restart/decision/handoff。
- **关键文件/模块**：`00-01`、`00-02`、`archive/batch-drafts-20261029/*`、`domains/*/reviews/.gitkeep`。
- **决策**：A 阶段文档冻结；失败草案归档，不再作为执行依据；00-01/00-02 保留为警示牌。
- **移除/新增**：移出活动区 batch drafts；新增审计复盘。
- **影响**：B 阶段执行以域内 PKG/REV 为准。

#### #11 `a3ffbe6` docs(rebuild): 对齐目标工作树 AGENT.md 到 B 阶段架构

- **真实范围**：`AGENT.md` +240/−65，写入 B 阶段边界（单页、D-02/D-03、D-04/D-06、D-05/D-10/D-11、日志/证据规则）。
- **关键文件/模块**：`AGENT.md`。
- **决策**：把 D-01..D-14 的执行红线固化到 agent 规则；无新 D。
- **影响**：后续 agent 行为以目标树 AGENT.md 为准。

### 3.2 B 阶段：代码落地（#12–#33）

#### #12 `a9e7dc3` feat(rebuild): 落地 P9-pre 配置深合并、API 鉴权与 lifespan 钩子

- **真实范围**：16 文件 +1337/−490。`model_defaults.py` 变更 381 行（深合并、非关键回退、启动校验、security getter、config 写回）；`dependencies.py` 变更 34 行（API guard 重写）；`api/app.py` 变更 66 行（lifespan 装配/关闭）；`routes_system.py` 变更 88 行（配置读写）；`account_rotator.py` 变更 92 行（净删 54）；**删除 tracked 根 `config.yaml`（−230）并向 `.gitignore` 加 2 行**；新增 `tests/unit/test_features_config.py`（+305）。
- **关键文件/模块**：`infrastructure/gateway/model_defaults.py`、`api/dependencies.py`、`api/app.py`、`config.yaml`（删除 tracked）、`.gitignore`。
- **决策**：**D-07**（深合并默认 + 启动校验，不静默/不请求期 `KeyError`）；**D-14**（API 免密开关独立、默认 false；管理页密码语义不变）。
- **移除/新增**：移除 `_load_yaml_config` 的浅替换与 tracked 根配置；新增深合并引擎、启动校验、security getter、lifespan 装配。
- **影响**：根 `config.yaml` 从此 untracked + ignored；配置权威转向 `defaults/config.yaml` + 本地覆盖。该提交也是后续 `model_defaults.py` 膨胀到 1531 行的起点。

#### #13 `8b80971` feat(rebuild): 落地 P1 账号 Cookie、刷新、预设与 Paid 识别

- **真实范围**：34 文件 +3479/−366。新增 `account_info_refresher.py`（579）、`model_access.py`（119）、`model_catalog.py`（81）；`account_store.py` 变更 300 行、`cookie_parser.py` 变更 187 行、`session_auth.py` 变更 89 行、`account_rotator.py` 调整；`routes_accounts.py` 变更 292 行；新增 6 个测试文件；Web 类型/接口收敛（`web/src/api/accounts.ts` −14）。
- **关键文件/模块**：`infrastructure/account/*`、`application/model_access.py`、`application/model_catalog.py`、`api/routes_accounts.py`。
- **决策**：**D-08**（`presets.json` 持久化与锁/写失败处理）；**D-13**（删除 notes/批量分组，保留分组 CRUD/UI）；**D-14**（API Key 与 Web 密码分离的账号侧配套）；P1-C 的 Paid 识别。
- **移除/新增**：移除旧账号接口/前端调用；新增账号信息刷新、模型访问元数据、模型目录投影。
- **影响**：Paid/Tier 元数据开始集中到 `model_access`/`model_catalog`，但 `model_discovery`/`cookie_parser`/`account_info_refresher` 的重复探测也在本提交后固化（见耦合审计）。

#### #14 `f2b1e28` fix: resolve tab/stream memory leaks, readiness watchdog timeouts, Ultra_20X tier normalization, and web config editor bugs（上游）

- **真实范围**：24 文件 +338/−327。清理标签页/流内存泄漏、readiness watchdog 超时、`Ultra_20X` tier 归一化、Web 配置编辑器缺陷；删除 `tools/reverse/test_dirty_tokens_all_accounts.py`（−218）。
- **关键文件/模块**：`gateway/session.py`、`browser/cdp_client.py`、`browser/js/*`、`model_defaults.py`、`web/src/components/config/*`。
- **决策**：无 D-xx（上游提交）；部分能力与 D-03“命令超时/断线失败”相关，但实现口径由目标树合并时复核。
- **移除/新增**：删除反向测试工具；新增/修复 watchdog 与 tier 归一化。
- **影响**：本提交当时尚未进入目标分支；由 `ca8496c` 吸收，合并时需保护单页/D-05 架构。

#### #15 `a606fbe` feat(rebuild): 落地 P3-D03 浏览器隔离、单页生命周期与架构文档同步

- **真实范围**：18 文件 +1729/−366。`browser_engine.py` +84（保留 `_spawn_process_watchdog`）、`cdp_client.py` 变更 136 行（WS ping）、新增 `pooled_session.py`（237）、`session.py` 变更 336 行、`session_state.py` 变更 95 行（净删 83）；`config.py` +24（隔离/生命周期开关）；同步 `docs/ARCHITECTURE.md`；新增/改写 8 个测试。
- **关键文件/模块**：`infrastructure/browser/{browser_engine,cdp_client}.py`、`infrastructure/gateway/{session,pooled_session,session_state}.py`。
- **决策**：**D-02**（保留 pipe watchdog，仅孤儿回收）；**D-03**（只保留 WebSocket ping，删 liveness 轮询）；D-05/D-10/D-11 的单页/有界 drain 前身。
- **移除/新增**：移除 `session_state` 的 liveness/blue-green 残留；新增 `PooledSession` 生命周期封装。
- **影响**：确立“1 账号 + 1 Context + 1 Page + 1 surface”的物理边界；后续 D-05 控制面 rebind 在此基础上实现。

#### #16 `0cdfd35` feat(rebuild): 落地 P3-D04 多实例池、Free/Pro 隔离与 D-05 控制面换绑

- **真实范围**：22 文件 +3515/−66。新增 `instance_manager.py`（975）、`instance_pool.py`（573）、`routes_instances.py`（137）、`domain/instances.py`（40）；`routes_accounts.py` +105；`model_access.py` 扩展；新增 `test_instance_manager.py`（537）、`test_instance_pool.py`（332）、`test_routes_instances.py`（226）。
- **关键文件/模块**：`application/instance_manager.py`、`infrastructure/gateway/instance_pool.py`、`api/routes_instances.py`、`domain/instances.py`。
- **决策**：**D-05**（禁用账号/预设触发 RETIRING→停新→有界 drain→关旧页→切号）；**D-11**（关闭非活跃 Page/Context）；**D-01** 的 Free/paid lane 与实例级配额承载。
- **移除/新增**：移除请求期临时开页/账号级页面管理；新增实例池、实例管理器、实例 API、领域实例模型。
- **影响**：容量与页面生命周期从“请求路径”迁到“实例管理器”；后续 P2 路由只消费实例池。

#### #17 `ca8496c` chore(rebuild): 合并上游 f2b1e28 内存清理、超时与 Ultra_20X 修复（合并）

- **真实范围**：首父差异 20 文件 +200/−293；吸收 `f2b1e28` 的标签页/流内存清理、watchdog 超时、`Ultra_20X` 归一化、Web 配置编辑器修复；合并冲突按目标单页/pool 架构取舍。
- **关键文件/模块**：`gateway/session.py`、`browser/cdp_client.py`、`model_defaults.py`、`web/src/components/config/*`。
- **决策**：无新 D；合并守则为“目标架构（D-05/D-10/D-11）优先于上游实现”。
- **移除/新增**：吸收上游修复；保留 `PooledSession`/单页路径。
- **影响**：上游修复进入 B 阶段；`upstream-merges/index.md` 记录此合并。

#### #18 `b30315e` feat(rebuild): 落地 P2 多实例池分流路由、Tier 403 准入与 429 重试切换

- **真实范围**：47 文件 +4611/−1876。新增 `routing_pipeline.py`（762）、`routing_eligibility.py`（244）、`domain/errors.py`（135）、`instance_client.py`（194）、`error_mapping.py`（135）、`routing_safety.py`（52）、`model_access.py` 扩展；`account_orchestrator.py` 变更 267 行（净删 201，删除旧全局 failover）、`api_service_gemini.py` 变更 541 行（重写）、`account_rotator.py` 重做；测试重写（`test_account_rotation_concurrency.py` 等）。
- **关键文件/模块**：`application/routing_pipeline.py`、`application/routing_eligibility.py`、`domain/errors.py`、`infrastructure/gateway/instance_client.py`、`api/error_mapping.py`。
- **决策**：**D-01**（quota/tier lane）；**D-04**（Paid tier 不足仅 403，不自动切号）；**D-06**（领域错误 + 协议映射）；**D-07**（routing 配置默认/校验）。
- **移除/新增**：删除 `ensure_active_account`/`try_switch_account` 全局回退、lease/defer/打分；新增“资格→候选→尝试→错误处理”管线、领域错误、协议错误映射、实例请求客户端。
- **影响**：**本范围最大的架构提交**；subject 只写“P2 路由”，实际同时完成错误模型、实例客户端、旧 orchestrator 删除与测试体系重写。

#### #19 `9aabdf3` feat(rebuild): 落地 P6 24h 保活、元数据刷新与锁加固

- **真实范围**：12 文件 +1831/−50。新增 `account_keepalive.py`（534）、`routes_keepalive.py`（60）；`model_defaults.py` +119（keepalive/model defaults）；`api/app.py` 启动/关闭重写；新增 `test_account_keepalive.py`（494）等。
- **关键文件/模块**：`application/account_keepalive.py`、`api/routes_keepalive.py`、`api/app.py`、`model_defaults.py`。
- **决策**：**D-02/D-03**（保活不复活 stale、不做 liveness 轮询）；**D-08**（锁/写失败加固）；**D-05**（保活/元数据刷新与实例 rebind 协调）。
- **移除/新增**：移除旧的零散刷新/启动预热逻辑；新增 keepalive service、路由与 24h 调度。
- **影响**：VPS 长时间运行依赖从此建立；启动时 metadata 探测与 keepalive 的重复问题也在此埋下（N-29/N-33）。

#### #20 `4653ec7` fix(rebuild): 补齐 B3 保活加固断言与路由文案

- **真实范围**：4 文件 +133/−2；`test_account_keepalive.py` +103、`test_routes_keepalive.py` +17、review 追加、路由文案 1 行。
- **决策**：D-02/D-03 的断言固化；无新模块。
- **影响**：B3 验收证据补强。

#### #21 `16450cc` refactor: eliminate all request timeout watchdogs for unlimited streaming duration（上游）

- **真实范围**：32 文件 +121/−171。删除请求级超时 watchdog 与 `.env.example` 超时键；移除 `streaming_init.js` 超时逻辑（−22）；`transport.py` −21、`replay.py`、`session.py`、`routes_accounts/system` 调整；Web 设置文案同步。
- **关键文件/模块**：`gateway/transport.py`、`gateway/replay.py`、`browser/js/streaming_init.js`、`config.py`、`api/app.py`。
- **决策**：上游 Q-02（无限流式、非流式由上层兜底）；与 D-03 的“命令超时兜底”存在张力，合并时需明确边界。
- **移除/新增**：移除超时 watchdog/相关配置；无新模块。
- **影响**：经 `05a9df7` 进入目标分支；属于行为变化，非纯重构。

#### #22 `99600c7` fix(gateway): await BotGuard Wasm snapshot readiness, pin SessionContext per request, remove upstream gzip rejection, and sync startup account（上游）

- **真实范围**：11 文件 +84/−76。BotGuard Wasm 快照就绪等待、每请求固定 `SessionContext`、移除上游 gzip 拒绝、启动账号同步；删除 `dom_gc_cleanup.js` 6 行与 `scripts.py` 22 行。
- **关键文件/模块**：`gateway/botguard_bootstrap.py`、`gateway/session.py`、`gateway/capture.py`、`gateway/streaming.py`、`api/app.py`。
- **决策**：无新 D；影响 D-05 的 session/page 固定语义。
- **移除/新增**：移除部分浏览器清理脚本；新增就绪等待/固定上下文。
- **影响**：后续 `ccd15e3`/合并复审将其中“快照就绪轮询”再次移除（N-18），说明上游行为并非全部保留。

#### #23 `424f896` feat(rebuild): 落地 P7 日志三 Sink、SSE、导出与前端静态构建

- **真实范围**：25 文件 +3406/−188。新增 `log_broadcast.py`（423）、`routes_logs.py`（282）；`logger.py` 重写（263）；Web 新增 `LogsView.vue`（888）、`api/logs.ts`（94）、侧栏/路由；更新 `src/aistudio_api/static/assets/*`（含 vendor 重建）。
- **关键文件/模块**：`infrastructure/utils/{logger,log_broadcast}.py`、`api/routes_logs.py`、`web/src/views/LogsView.vue`、`src/aistudio_api/static/*`。
- **决策**：**D-12**（在现有 UI 增量承载，不重构导航）；D-09 的日志诊断/脱敏口径。
- **移除/新增**：替换旧单 Sink 日志；新增 5000 ring / 7d archive / SSE 三 Sink、导出、前端日志页。
- **影响**：提交中包含已构建静态产物（vendor-vue/icons 等），后续审计需区分“源码”与“构建产物”。

#### #24 `05a9df7` chore(rebuild): 合并上游 99600c7 并完成网关流式与快照对齐（合并）

- **真实范围**：首父差异 29 文件 +667/−199。实际吸收 **`16450cc` + `99600c7`** 两个上游提交（`99600c7` 的祖先包含 `16450cc`）；冲突解决保留目标树的池化/流式路径。
- **关键文件/模块**：`gateway/{session,botguard_bootstrap,streaming,transport,replay}.py`、`api/app.py`、`web/src/views/SettingsView.vue`。
- **决策**：Q-02（去超时）与 D-03 的边界需在合并评审中明确；合并守则仍为目标架构优先。
- **移除/新增**：吸收上游就绪等待/去 gzip/固定上下文；保留 B5a 首事件真实状态与单页架构（后续 `629614f` 固化）。
- **影响**：**合并 subject 点名不足**：只写 `99600c7`，实际是两个上游提交的共同落地；`upstream-merges/index.md:22-33` 已按两提交记录。

#### #25 `629614f` fix(rebuild): 完成 PG 工具调用离线硬门槛

- **真实范围**：15 文件 +1632/−162。新增 `signature_cache.py`（164）、`wire_types.py`（51）；`chat_service.py` 变更 208 行（工具/工具形状）；`streaming.py` 变更 132 行；`api_service_gemini.py` 变更 274 行；更新 `f03_finish_only.bin`；新增/改写 8 个测试。
- **关键文件/模块**：`application/{chat_service,signature_cache}.py`、`infrastructure/gateway/{streaming,wire_types}.py`、`tests/unit/test_pg_toolcall_harness.py`。
- **决策**：D-09（thought signature/hash 诊断）；D-06（领域错误）；B5a 的 PG-01..04 门槛。
- **移除/新增**：移除临时工具兜底分支；新增 signature cache、wire 类型、工具形状摘要。
- **影响**：B5b 的前置硬门槛；`test_gemini_request_normalization` 仍引用 `wire_parser` 私有函数（测试耦合）。

#### #26 `9bffda1` feat(rebuild): 完成 OpenAI/ATG 最小闭环与生成主链

- **真实范围**：50 文件 +8841/−324。新增 `generation_service.py`（514）、`application/atg/` 10 个模块（约 1900 行）、`application/format_converters/` 5 个模块（约 1150 行）、`api/routes_openai.py`（194）、`schemas_openai.py`（33）、`format_errors.py`（235）、`token_counter.py`（209）；`api_service_gemini.py` 变更 382 行（净删 228，把生成主链迁出）；新增 16 个测试文件。
- **关键文件/模块**：`application/generation_service.py`、`application/atg/*`、`application/format_converters/*`、`api/routes_openai.py`、`api/format_errors.py`。
- **决策**：**D-06**（协议中立核心 + 领域错误）；**D-09**（signature/hash 诊断）；**D-12**（不重构 UI）。
- **移除/新增**：移除 `api_service_gemini` 内的生成/重试/路由逻辑；新增协议中立生成服务、ATG 注入/抽取/修复/流解码、OpenAI 请求/响应转换、token 计数、OpenAI 路由。
- **影响**：**最大单提交**；subject 的“最小闭环”严重低估实际范围——它同时建立 P5 生成主链与 OpenAI 协议面。也是后续 `api_service_gemini`/`generation_service` 职责重叠的来源。

#### #27 `ccd15e3` fix: resolve 403 cascading rotation, stream aborts, and new account bootstrap; overhaul logging & UI（上游）

- **真实范围**：48 文件 +943/−436。403 级联旋转、流中断（截断 JSON/finish fallback）、新账号 bootstrap、日志/UI 大改；`botguard_bootstrap.py` 316、`stream_parser.py` 203、`session.py`、`api/app.py` 129；`config.yaml` 2 行（上游 tracked）。
- **关键文件/模块**：`gateway/botguard_bootstrap.py`、`stream_parser.py`、`session.py`、`transport.py`、`api/app.py`、`web/*`、`static/*`。
- **决策**：上游对 403 的“首次重签、二次切号”策略；与目标 D-04（Paid 403 不切号）在合并时重新对齐。
- **移除/新增**：上游日志/UI 重构；新增 bootstrap 修复与流解析兜底。
- **影响**：经 `da937b6` 吸收；是“subject 是 fix、实际跨 48 文件”的典型上游压缩提交。

#### #28 `852de53` refactor: align 403 rotator documentation, clean redundancies, and verify AST hash specs（上游）

- **真实范围**：14 文件 +80/−89。403 rotator 文档对齐、冗余清理、AST hash 校验；`cookie_parser.py` +49、`botguard_bootstrap.py` −52、`model_defaults.py` +8、`session.py` +7；`config.yaml` 2 行。
- **关键文件/模块**：`docs/ARCHITECTURE.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`、`account/cookie_parser.py`、`gateway/botguard_bootstrap.py`、`model_defaults.py`。
- **决策**：D-04 文档口径（403 避让/切号）对齐；无新 D。
- **移除/新增**：删除 botguard 冗余逻辑；调整 cookie/tier 解析。
- **影响**：上游尖端；由 `da937b6` 吸收，合并后根 `config.yaml` 仍保持 untracked。

#### #29 `b1a749e` fix(rebuild): 接通池化会话真实模型发现、动态 Paid 识别与防重复预热加固

- **真实范围**：24 文件 +2697/−150。`model_discovery.py` 变更 434 行、`api/app.py` 变更 134 行（启动预热/发现）、`api/state.py` 变更 58 行（discovery sessions）、`instance_manager.py` +62；新增 `test_model_discovery_live_wiring.py`（429）、`test_app_startup_warmup.py`（155）；B6 证据。
- **关键文件/模块**：`infrastructure/gateway/model_discovery.py`、`api/app.py`、`api/state.py`、`application/instance_manager.py`。
- **决策**：Paid 动态识别（D-01/P1-C）；真实 ListModels 接线；D-05 的“池会话优先、控制面不重复开页”。
- **移除/新增**：移除仅缓存/静态 fallback 的发现路径；新增 READY 池会话发现、动态 paid 规则、启动去重。
- **影响**：B6 的 `live_page` 模型来源成立；但 `model_discovery` 同时含浏览器 XHR 与 httpx 两套发现实现（见耦合审计）。

#### #30 `da937b6` chore(rebuild): 合并上游 852de53 并吸收 403、流式与启动修复（合并）

- **真实范围**：首父差异 57 文件 +1999/−391。实际吸收 **`ccd15e3` + `852de53`** 两个上游提交（`852de53` 的祖先包含 `ccd15e3`）；冲突解决保护池化/单页/B5a/B4 边界。
- **关键文件/模块**：`api/app.py`、`application/{routing_pipeline,generation_service}.py`、`gateway/{session,pooled_session,streaming,stream_parser,botguard_bootstrap,client,transport,capture}.py`、`browser/js/*`、`logger.py`、`account_store.py`、`web/*`、`static/*`。
- **决策**：D-04/D-06 与上游 403 策略重新对齐；D-05/D-09/D-10/D-11 边界优先。
- **移除/新增**：吸收上游 403/流式/bootstrap/日志/UI；保留目标树的控制面不预热、单页有界 drain、B5a 工具门槛、B4 脱敏。
- **影响**：**合并 subject 点名不足**：只写 `852de53`，实际含 `ccd15e3`；`upstream-merges/index.md:35-53` 已记录两提交与 closeout。

#### #31 `b000bb7` fix(rebuild): 修复路由均衡、故障槽换绑与实例 Tier Schema

- **真实范围**：26 文件 +2409/−296。`model_access.py` +162、`routing_pipeline.py` 变更 198 行、`instance_manager.py` 变更 84 行、`instance_pool.py` 变更 74 行、`model_defaults.py` 变更 134 行；`error_mapping.py`/`format_errors.py` 403 透传；`routes_instances.py` +4；新增/扩充测试约 2.1k 行（`test_routes_gemini.py` +307、`test_routing_pipeline.py` +338 等）。
- **关键文件/模块**：`application/model_access.py`、`application/routing_pipeline.py`、`infrastructure/gateway/{instance_pool,model_defaults}.py`、`api/error_mapping.py`。
- **决策**：**D-01**（`only_tier/min_tier/max_tier` 闭区间与 Free 归一）；**D-04**（403 字段透传、不切号）；**D-11**（故障槽换绑有界）；**D-07**（tier schema 校验）。
- **移除/新增**：移除 Free-only 的历史歧义（`min_tier: Free` 归一）；新增动态 paid 规则、实例 Tier Schema、路由均衡修复。
- **影响**：subject 的“修复”低估了测试与规则面扩张；B6 的 Tier 403/实例字段证据由本提交固化。

#### #32 `1f60fbc` fix(rebuild): 完成 B6 实机验收闭环、日志导出脱敏加固与 Docker CI 构建适配

- **真实范围**：17 文件 +411/−123。`routes_logs.py` 变更 24 行（导出脱敏）、`routes_system.py` 变更 6 行、`config.py` 变更 13 行（配置路径 env）、`log_broadcast.py` 变更 24 行；`Dockerfile`/`docker-entrypoint.sh`/`.github/workflows/{ci,docker}.yml`；B6 evidence 更新；新增 `test_config_path_env.py`（48）。
- **关键文件/模块**：`api/routes_logs.py`、`api/routes_system.py`、`config.py`、`Dockerfile`、`docker-entrypoint.sh`、`.github/workflows/*`、`docs/dev/evidence/B6/*`。
- **决策**：无新 D；落实 D-14 相关的“导出脱敏/安全”与 `AISTUDIO_CONFIG_PATH` 运行时可配置（项目记忆中的 config path 决策）。
- **移除/新增**：移除硬编码配置路径假设；新增配置路径 env、日志导出脱敏、GHCR 构建适配。
- **影响**：B6 从 FAIL/PARTIAL 收口为 5/5 成功（`results.json`）；Docker 构建链路就绪。

#### #33 `55f1c64` ci(rebuild): 移除上游 DockerHub 硬编码依赖以适配 GHCR 自动构建

- **真实范围**：仅 `.github/workflows/docker.yml` −9。删除 `DOCKERHUB_REPO` env、镜像列表中的 DockerHub 目标、Docker Hub 登录步骤。
- **关键文件/模块**：`.github/workflows/docker.yml`。
- **决策**：无 D-xx；CI 供应方从 DockerHub 改为 GHCR。
- **移除/新增**：移除 DockerHub 推送/登录。
- **影响**：CI 只推 `ghcr.io/<owner>/aistudio-api`；无业务代码变化。

## 4. 大/压缩提交与 subject 低估清单

按“实际改动 / subject 暗示”的偏差排序：

| 排名 | 提交 | subject 暗示 | 实际范围 | 偏差证据 |
|---:|---|---|---|---|
| 1 | `9bffda1` | “OpenAI/ATG 最小闭环与生成主链” | 50 文件 +8841；生成服务、ATG 10 模块、格式转换 5 模块、OpenAI 路由、token 计数、20 个测试 | `git show --numstat 9bffda1` |
| 2 | `b30315e` | “P2 分流路由” | 47 文件 +4611/−1876；路由管线、领域错误、实例客户端、错误映射、旧 orchestrator 删除、测试重写 | 同上 |
| 3 | `0cdfd35` | “P3-D04 多实例池” | 22 文件 +3515；InstanceManager 975 + InstancePool 573 + 领域/路由/测试 | 同上 |
| 4 | `8b80971` | “P1 账号 Cookie” | 34 文件 +3479；新增刷新/模型访问/目录三模块，账号存储+300，Cookie+187 | 同上 |
| 5 | `b000bb7` | “修复路由均衡/Tier Schema” | 26 文件 +2409；模型访问+162、路由+198、测试约 2.1k | 同上 |
| 6 | `a9e7dc3` | “P9-pre 配置/鉴权/lifespan” | 16 文件 +1337/−490；删除 tracked 根 `config.yaml`，配置引擎+381 | `git show --stat a9e7dc3` |
| 7 | `1f60fbc` | “B6 闭环/日志脱敏/Docker” | 17 文件 +411；配置路径 env、导出脱敏、CI/entrypoint、证据更新 | `git show --stat 1f60fbc` |
| 8 | `05a9df7` | “合并上游 99600c7” | 首父差异 29 文件 +667/−199；实际含 `16450cc`+`99600c7` | `git log 99c38ba..99600c7` |
| 9 | `da937b6` | “合并上游 852de53” | 首父差异 57 文件 +1999/−391；实际含 `ccd15e3`+`852de53` | `git log 99c38ba..852de53` |
| 10 | `424f896` | “P7 日志三 Sink/前端构建” | 25 文件 +3406；含 888 行 LogsView 与已构建静态 vendor 产物 | `git show --stat 424f896` |

补充：

- `271321b`（236 文件 +8933）虽巨大，但纯文档/证据迁移，不属于“业务压缩提交”。
- `c18fdaf`（42 文件 +2719/−1262）是文档权威重排，不是代码落地。
- `ccd15e3`（上游，48 文件 +943/−436）subject 是 `fix`，实际跨 gateway/browser/logging/UI/config，属上游大修。

## 5. 决策映射（D-01..D-14）

说明：下表为“代码/文档证据对应”，不把上游提交视为用户决策；`推断` 表示由实现与文档交叉得出。

| 决策 | 主要落点提交 | 证据 |
|---|---|---|
| D-01 surface/quota_scope | `b30315e`、`0cdfd35`、`b000bb7` | `account_rotator.py:73-85`、`model_access.py:40-61`、`b000bb7` Tier Schema |
| D-02 pipe watchdog | `a606fbe`、`9aabdf3`、`4653ec7` | `browser_engine.py:768,1068` |
| D-03 只保留 WS ping | `a606fbe`、`9aabdf3`、`4653ec7` | `cdp_client.py:120-122`；无 liveness 轮询 |
| D-04 403 不切号/四字段 | `b30315e`、`b000bb7`、`da937b6` | `domain/errors.py:24-61`；`routing_pipeline.py:628` |
| D-05 控制面有界 rebind | `0cdfd35`、`9aabdf3` | `instance_manager.py:10,39,516,905`；`instance_pool.py:8-9` |
| D-06 领域错误/API 映射 | `b30315e`、`9bffda1` | `domain/errors.py`、`api/error_mapping.py`；**同时被 `api_service_gemini.py:15` 违反，见耦合审计** |
| D-07 深合并+启动校验 | `a9e7dc3` | `model_defaults.py:1065-1085`、`app.py:280` |
| D-08 presets 锁/写失败 | `8b80971`、`9aabdf3` | `account_store.py:299-321`（RLock/presets.json） |
| D-09 hash 含代码 | `a606fbe`、`629614f`、`9bffda1` | `session.py:65-83`；`signature_cache.py` |
| D-10 有界 drain | `0cdfd35`、`9aabdf3` | `instance_manager.py:39,144-150` |
| D-11 关闭非活跃页 | `0cdfd35` | `instance_pool.py:8-9`、`instance_manager.py` prune/retire |
| D-12 新设置进现有 UI | `424f896`（日志） | **部分/延后**：`quota_scope`/`api_allow_anonymous` 在 `web/` 无结构化表单引用；仅 YAML 编辑器可见 |
| D-13 删除 notes/批量分组 | `8b80971` | 仅测试断言不存在：`test_routes_account_presets.py:132-135`、`test_routes_accounts.py:77` |
| D-14 API/管理页鉴权分离 | `a9e7dc3` | `dependencies.py:69-121`、`model_defaults.py:1486-1505`、`app.py:454-471` |

## 6. 未闭合 Q/N 与本范围的关系

- `06-decision-log.md` 仍标：P4-Q4 pending re-audit（`:206,214,227`）、P1-E2 OAuth defer（`:92-96`）、P5-Q11 build REST defer（`:286`）、P6-Q6 active_activity disable（`:437`）、P10-Q3 WARP 后置（`:411`）。
- `REV-MERGE-852de53-oct31.md` 的 open 项：Q-05（UI 文案）、Q-13/N-32（`snapshot_probe` 未接线）、N-33 的“配置门控”子项（`app.py:411` 无条件探测）、N-42（detect 路由单测）、N-43（延续 N-33）。Q-12/N-40 已由 `1f60fbc` 的 B6 证据闭合。
- 这些 Q/N 均不阻塞本只读审计；但应进入耦合审计的施工清单或残余风险。

## 7. 审计结论

1. 33 个提交全部核对，无遗漏；3 个合并均确认了实际吸收的上游提交集合。
2. A 阶段（#1–#11）为文档/证据/任务包，未改业务代码；B 阶段（#12–#33）按 P9-pre→P1→P3→P2→P6→P7→B5a→B5b→B6 的顺序落地。
3. 最大范围偏差在 `9bffda1`、`b30315e`、`0cdfd35`、`8b80971`；两个合并提交的 subject 各少报一个上游提交。
4. 决策映射总体可追溯；D-06 存在代码级违反（application 依赖 FastAPI/API 模块），D-12 的 UI 可见性未在 HEAD 完成。
