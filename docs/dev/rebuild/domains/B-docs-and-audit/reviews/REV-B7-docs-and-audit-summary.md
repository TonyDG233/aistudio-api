# REV-B7 文档与审计总结（独立只读复核）

- 审计日期：2026-11-01（UTC+8）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 分支 / HEAD：`rebuild-upstream` / `55f1c649992264ecc19d079479865f2c7fc63993`
- 审计范围：`99c38ba..HEAD` 的文档、B7 审计工件、关键源码事实、Git/工作树状态
- 审计模式：严格离线、只读；未启动 `main.py server`、浏览器、Chromium、服务端或任何网络请求
- 代码改动：无；未修改 `src/`、`tests/`、配置、前端或已提交文档；未 stage/commit
- 复审状态：**PASS**（2026-11-01 修复后复审；§0–§10 为原始 FAIL 审计快照，§11 为修复记录，§12 为最终复审）

## 0. 结论

**Verdict: FAIL（可修复；不是代码失败，而是 B7 文档/审计工件存在阻断性事实与一致性问题）**

文档总体“有实质内容”，33 个提交、B0–B6 时间线、实例/路由、WIRE、BotGuard、协议链的主要事实与代码相符；但以下问题不能按 PASS 接受：

1. B7 架构审计把仍在生产路径使用的 `wire_parser.py` 判为“生产无 import、仅测试使用的死模块”，并据此提出删除/迁移任务；该结论错误。
2. `AGENT.md` 仍声称根 `config.yaml` 与 `defaults/config.yaml` 均为 tracked，与实际 untracked/ignored 状态及 changelog/Q-04 矛盾。
3. `config/config.schema.json` 仍是 Gate 0.5 示例 schema，未覆盖当前 `defaults/config.yaml` 的实际结构；作为 B7 更新工件属于 stale schema。
4. `AGENT.md` 与 `INSTANCE_POOL_AND_ROUTING_SPEC.md` 仍声称 `auth_errors` 用于“调度避让/动态避让”，与 `BOTGUARD_VERIFICATION_CHAIN.md` 及代码实际行为矛盾，并回归了已标记 RESOLVED 的 Q-09/N-30。
5. B6 `verdict=PASS` 的实际证据混合了“实机功能 PASS”和“离线导出脱敏复核”；changelog 已披露，但 PASS 标签应拆分/限定。
6. B6 复审文档称 `Dockerfile:10` 设置 `AISTUDIO_CONFIG_PATH`，实际设置的是 `AISTUDIO_CONFIG_FILE`；entrypoint 之后导出 PATH，行为等价但文档事实错误。

## 1. 工作树与只读完整性

| 检查 | 结果 | 证据 |
|---|---|---|
| HEAD | `55f1c649992264ecc19d079479865f2c7fc63993` | `git rev-parse HEAD` |
| 分支 | `rebuild-upstream` | `git rev-parse --abbrev-ref HEAD` |
| 已修改文件 | 6 个，全部为文档/schema | `AGENT.md`、`config/config.schema.json`、`docs/ARCHITECTURE.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`、`docs/WIRE_SPECIFICATION.md`、`docs/changelog.md` |
| 未跟踪文件 | 3 个新规格 + B7 目录 | `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md`、`docs/dev/rebuild/domains/B-docs-and-audit/` |
| `src/` / `tests/` 改动 | 无 | `git status --porcelain` 无匹配；`git diff --name-only` 不含 |
| 暂存区 | 空 | `git diff --cached --name-only` 为空 |
| `git diff --check` | exit 0 | 工作区无 whitespace 错误 |
| `git diff --cached --check` | exit 0 | 无 staged whitespace 错误 |
| 根 `config.yaml` | untracked + ignored | `git ls-files config.yaml` 空；`git check-ignore -v config.yaml` -> `.gitignore:15:/config.yaml` |
| 服务/浏览器/网络 | 未启动、未发起 | 本次仅执行 `git`、文件读取、`Get-FileHash`；未运行 pytest/ruff/pyright（docs-only，按要求） |

补充：`git diff --check 99c38ba..HEAD` 仍为 exit 2，报告的问题均为既有历史文档空白（`00-01-plan-audit.md` 行尾空格、evidence `.txt`/`06-decision-log.md` EOF 空行），与 B7 工作区无关；B7 审计 `REV-B7-commit-history-audit.md` 对此的记载准确。

## 2. B-docs 双写与字节一致性

目标树与主工作区的三个 B-docs 文件逐字节一致：

| 文件 | 目标 SHA256 | 主仓 SHA256 | 一致 |
|---|---|---|---|
| `index.md` | `53837AD244BEE993084CE6CB29A9219B27238E94A8ADA8D658BE1A27CA5D232E` | 同左 | 是 |
| `reviews/REV-B7-commit-history-audit.md` | `DF3720E23AA16A1F63541A4E6A9B69BAC8B73000FCF5B4041B93207666CA0628` | 同左 | 是 |
| `reviews/REV-B7-architecture-coupling-audit.md` | `0EFB15F0B7264216CD3011C66A8B52557E64F5C3672D2C568B3CFBD568571FAC` | 同左 | 是 |

`B-docs-and-audit/` 下目标与主仓均只有这三个文件，无额外副本或差异。

## 3. 阻断性问题与证据

### Blocker 1 — 架构审计误判 `wire_parser.py` 为生产死代码

- 报告位置：`docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md:185`（表格）与 `:257`（B7-11）。
- 报告原文：`wire_parser.py`（633 行）“生产无 import；仅 4 个测试文件引用”，据此建议删除或迁入测试支撑。
- 实际生产引用（均有调用点）：
  - `src/aistudio_api/infrastructure/gateway/client.py:36-39`，调用 `:298`、`:378`
  - `src/aistudio_api/infrastructure/gateway/stream_parser.py:9-13`，调用 `:249`、`:261`、`:277`
  - `src/aistudio_api/infrastructure/gateway/streaming.py:21-24`，调用 `:149`、`:162`、`:193-196`
  - `src/aistudio_api/infrastructure/gateway/wire_codec.py:10-13`，调用 `:394`、`:402-407`
  - `src/aistudio_api/infrastructure/gateway/instance_client.py:27`，调用 `:169`
- 影响：B7-11 若按报告删除 `wire_parser.py`，会直接破坏非流式/流式响应解析、图片解析、wire codec 与 `countTokens` 路径。该条必须改为“生产活跃模块”，并重新做死代码扫描后再给删除/迁移任务。
- 审计中“4 个测试文件引用”本身成立，但“生产无 import”不成立；这是事实错误，不是口径差异。

### Blocker 2 — `AGENT.md` 的根配置跟踪状态过期

- 位置：`AGENT.md:341`（“目标树当前 `config.yaml` 与 `defaults/config.yaml` 均为 tracked”）与 `AGENT.md:387`（树注释“当前骨架仍 tracked”）。
- 实际：`git ls-files config.yaml` 为空；`git check-ignore -v config.yaml` 命中 `.gitignore:15:/config.yaml`；`docs/changelog.md:36` 明确写“根 `config.yaml` 保持未跟踪”；合并复审 Q-04 也判 PASS。
- 影响：与 Q-04、changelog、实际仓库状态三方矛盾；会误导部署/提交检查。应改为“根 config.yaml untracked + ignored；defaults/config.yaml tracked”。

### Blocker 3 — `config/config.schema.json` 仍是 Gate 0.5 示例 schema，未覆盖当前默认配置

- Schema 位置与限制：
  - `config/config.schema.json:14` 根级 `additionalProperties: false`
  - `:61-73` 仅定义 `limits.max_instances`
  - `:82-100` `features` 仅允许 `formats` 数组与 `build_surface`
  - `:101-116` 顶层 `keepalive.enabled/interval_hours`
- 当前 `defaults/config.yaml` 的实际结构：
  - `:5-6` `security.api_allow_anonymous`
  - `:8-18` `instances` + `max_contexts`
  - `:23-24` `diagnostics.hash_include_code`
  - `:33-41` `rotator.max_retries/quota_scope/cooldowns`
  - `:44-55` `features.routing` + `features.keepalive`
  - `:59-64` `features.formats` 对象（openai/claude/responses/media）
  - `:69-85` `features.atg`
  - `:87-95` `paid_model_rules`
  - `:149-232` `tier_quotas`
  - `:234-263` `account_presets`
- 结论：schema 仍能校验 Gate 0.5 的 `config/config.example.yaml`，但无法校验当前 `defaults/config.yaml`；`docs/changelog.md:59` 的“配置样例通过 schema 校验（Gate 0.5 基线）”只能按 example 理解。B7 修改了 schema 的 tier 字段，但未把 schema 提升到当前运行配置，属于 stale schema。
- 影响：若 CI/用户用该 schema 校验 `defaults/config.yaml`，会因 `security`、`max_contexts`、`features.routing`、`features.keepalive`、`features.atg`、`paid_model_rules`、`tier_quotas`、`account_presets` 等未知键而失败。需要明确“example-only”或扩展 schema。

### Blocker 4 — `auth_errors` 调度避让表述回归（与 Q-09/N-30 冲突）

- 错误表述：
  - `AGENT.md:273`：“累加 `auth_errors` 供调度避让”
  - `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:637`：“只用 `auth_errors` 做动态避让”
- 对照文档：
  - `docs/BOTGUARD_VERIFICATION_CHAIN.md:333`：“调度准入不读取 `auth_errors`，不存在 `auth_errors * 200.0` 动态避让”
- 代码事实：
  - `src/aistudio_api/application/account_rotator.py:371-388` 只 `auth_errors += 1`、清 `auth_cooldown`；`grep` 全 `src/` 后，`auth_errors` 仅出现在 `account_rotator.py`（统计/持久化/展示），路由准入没有读取。
  - `account_rotator.py:383-384` 的代码注释仍写“动态避让”，属于与实现不一致的陈旧注释；新文档不应复制该注释作为事实。
- 影响：403 两阶段行为的解释被误导；Q-09/N-30 在合并复审中已标记 RESOLVED，B7 新文档又将其写回，属于文档回归。

### 条件项 1 — B6 `verdict=PASS` 混合了实机与离线证据

- `docs/dev/evidence/B6/results.json`：`verdict=PASS`，`logs.export_sanitized=true`，但同时：
  - `logs.export_bytes_note = "live-run measurement before offline sanitizer fix; not a new export"`
  - `logs.sanitizer_verification = "offline: archive export lines pass through sanitize_log_message; ..."`
  - `reason` 明确“no new live run was performed”
- `docs/changelog.md:222` 已如实披露；`:224` 仍写 `verdict=PASS`。
- 结论：功能实机 5/5 通过成立；日志导出脱敏不是修复后的实机复测。建议把 PASS 拆成“live functional PASS + offline sanitizer PASS”，或在修复后补一次导出实机验证。当前不应把它当作“所有 B6 子项均为实机验证”。

### 条件项 2 — B6 复审对 Dockerfile 环境变量的表述错误

- 报告：`docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md:90` 称 `Dockerfile:10` 设置 `AISTUDIO_CONFIG_PATH`。
- 实际：`Dockerfile:10` 是 `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`；`docker-entrypoint.sh:5` 以 `AISTUDIO_CONFIG_PATH` 优先、`:16` 再导出 PATH。行为最终等价，但报告的事实句错误。
- 相关：`docs/changelog.md:54` 正确写了 PATH > FILE 的优先级；根文档无需改，但 B6 复审应修正。

### 条件项 3 — `ARCHITECTURE.md` 将 infrastructure→application 称为“受控例外”，架构审计判为 P0 循环

- `docs/ARCHITECTURE.md:86-92`：把 `model_discovery.py`/`model_defaults.py` 对 `application/model_access.py` 的依赖称为“受控例外”，并强调 Infrastructure 不导入 API。
- `REV-B7-architecture-coupling-audit.md:21-23,67-75`：将 `infrastructure -> application` 判为 P0 循环依赖，并指出 `application -> infrastructure` 同时存在。
- 两者对“存在依赖”的事实一致，但对严重度/性质不一致。建议 `ARCHITECTURE.md` 明确写“当前已知 P0 例外/技术债”，或审计将“受控例外”限定为临时豁免并给出移除条件。

### 条件项 4 — `Field 77/83` 与 `item[77]/item[82]` 的术语歧义

- `docs/WIRE_SPECIFICATION.md:405` 写“任务口径中的‘Field 77/83’指数组下标 77/82”；表 `:419-420` 正确写 `item[77] = Field 78`、`item[82] = Field 83`。
- 代码：`src/aistudio_api/infrastructure/gateway/model_discovery.py:846-850` 确实读取 `item[77]` 与 `item[82]`；`docs/changelog.md:204` 也写“数组下标 77/82”。
- 结论：代码与文档事实一致；问题仅是“Field 77/83”这一任务口径容易与 1-based Proto 字段号混淆。建议以后统一写 `item[77] (Field 78)` / `item[82] (Field 83)`，不要用“Field 77/83”指代数组下标。

## 4. 关键事实抽查

### 4.1 B0–B6、提交与合并

| 检查 | 结果 | 证据 |
|---|---|---|
| `99c38ba..HEAD` 提交数 | 33 | `git rev-list --count` = 33 |
| 重建分支 / 上游 | 28 / 5 | `git log --format` 作者核对；上游为 `f2b1e28`、`16450cc`、`99600c7`、`ccd15e3`、`852de53` |
| 合并提交 | 3 | `ca8496c`、`05a9df7`、`da937b6` |
| 最大提交 | `9bffda1`，50 文件 +8841/−324 | `git show --stat` |
| 第二/第三大 | `b30315e` 47 文件 +4611/−1876；`0cdfd35` 22 文件 +3515/−66 | `git show --stat` |
| changelog 阶段映射 | 全部命中 | B0 `a3ffbe6`+`d8fc901`；B1 `a9e7dc3`；B2-1 `8b80971`；B2-2 `a606fbe`；B2-3 `0cdfd35`；B2-4 `b30315e`；B3 `9aabdf3`+`4653ec7`；B4 `424f896`；B5a `629614f`；B5b `9bffda1`；B6 `b1a749e`+`da937b6`+`b000bb7`+`1f60fbc` |
| 三个上游合并链 | 正确 | `f2b1e28 -> ca8496c`；`16450cc+99600c7 -> 05a9df7`；`ccd15e3+852de53 -> da937b6` |

### 4.2 实例、Tier、路由、换绑

| 检查 | 结果 | 证据 |
|---|---|---|
| 1 实例 = 1 账号 + 1 Context + 1 Page + 1 surface | 成立 | `domain/instances.py:18-33`、`instance_pool.py:138-174` |
| `active_requests` 唯一并发原语、幂等释放 | 成立 | `instance_pool.py:116-135,205-214` |
| only/min/max 选择器 | 成立 | `model_access.py:119-188`；`model_defaults.py:261-392` |
| Free / paid lane 隔离 | 成立 | `model_access.py:273-285`；`instance_pool.py:327-364`；`instance_manager.py:389-425` |
| 负载均衡排序 | 成立 | `routing_pipeline.py:395-438`：in_flight -> account_tier_rank -> total_requests -> model_requests -> last_used -> warm -> sticky -> number |
| 403 首次重签、二次切号；429 重试/切号/503 | 成立 | `routing_pipeline.py:642-685,879-917` |
| 请求期失败槽改绑 | 成立 | `routing_pipeline.py:737-867`；`instance_manager.py:389-425` |
| D-05 有界 rebind / 12s drain | 成立 | `instance_manager.py:39,507-608,900-916`；`instance_pool.py:527-561`；`session.py:57` |
| D-10/D-11 关闭顺序 | 成立 | `instance_pool.py:592-627`（Page -> Context -> realm 回调） |

### 4.3 WIRE / 工具调用

| 检查 | 结果 | 证据 |
|---|---|---|
| `SEED=18` | 成立 | `wire_spec.py:63`；`chat_service.py:1009-1010`；`wire_types.py:223-232` |
| `CONTINUING=22` 非终止 | 成立 | `wire_spec.py:128`；`streaming.py:163-168,239-247` |
| `item[77]` / `item[82]` 字段解析 | 成立 | `model_discovery.py:846-850,906` |
| PG-01 缺签名：缓存 -> DUMMY | 成立 | `chat_service.py:720-734`；`signature_cache.py:25-27` |
| PG-04 Struct 强制 | 成立 | `wire_types.py:336-372` |
| 截断 JSON 修复 | 成立 | `stream_parser.py:29-148,232-273` |
| PG-03 finish-only / 2s 工具空闲 STOP | 成立 | `streaming.py:115-136,239-247` |

### 4.4 BotGuard / 保活 / 日志 / 协议

| 检查 | 结果 | 证据 |
|---|---|---|
| 池化启用时跳过控制面预热，防双 warmup | 成立 | `api/app.py:96-102,344-357`；`instance_manager.py:164-232` |
| SAPISIDHASH 三段格式 / 缺失回退 | 成立 | `cookie_parser.py:279-297` |
| Temporary Chat 激活 | 成立 | `botguard_bootstrap.py:71-77,242-252` |
| 真实模板捕获，拒绝假 fallback | 成立 | `botguard_bootstrap.py:199-209,365-369` |
| `finally` 清理输入/Hook | 成立 | `botguard_bootstrap.py:379-386` |
| 快照 5×250ms | 成立 | `pooled_session.py:163-188`；`session.py:410-436` |
| D-09 hash 含 code，默认 true | 成立 | `session.py:60-86`；`config.py:257-277` |
| 保活三 job / 单全局锁 / busy 不推进 | 成立 | `account_keepalive.py:33-40,212-251,253-288` |
| 日志 5000 ring / 7 天 / 三重脱敏 | 成立 | `log_broadcast.py:23-28,61-78,126-267,319-406`；`logger.py:310-349,445-565`；`routes_logs.py:224-228` |
| countTokens 走真实上游，返回 totalTokens | 成立 | `routes_gemini.py:48-61`；`token_counter.py:76-206`；`instance_client.py:173-220` |
| PG-08 宽松工具历史 | 成立 | `openai_request.py:27-49,64-128,217-323` |

## 5. 逐文档结论

| 文档 | 结论 | 主要发现 |
|---|---|---|
| `docs/changelog.md` | PARTIAL | 时间线、提交哈希、B0–B6 映射与 33 提交一致；B6 PASS 标签混合实机/离线证据（见条件项 1）；其余抽查准确 |
| `docs/ARCHITECTURE.md` | PARTIAL | 实例/Tier/路由/换绑/保活/日志/协议事实与代码相符；将 infra→application 称为“受控例外”，与架构审计 P0 循环的严重度冲突；未披露 D-06 违反 |
| `docs/WIRE_SPECIFICATION.md` | PASS（术语备注） | SEED=18、CONTINUING=22、item[77]/[82]、PG-01/04、截断修复均与代码一致；“Field 77/83”术语有歧义 |
| `docs/BOTGUARD_VERIFICATION_CHAIN.md` | PASS | 双 warmup、SAPISIDHASH、Temporary Chat、5×250、D-09 与代码一致；`auth_errors` 表述正确 |
| `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` | PARTIAL | 实例/Tier/路由/rebind 细节大部分准确；`:637` 的 `auth_errors` 动态避让错误 |
| `docs/KEEPALIVE_AND_LOGGING_SPEC.md` | PASS | 三 job、全局锁、按槽位成功记账、3 天 Free 跳过、三 sink、导出脱敏与代码一致 |
| `docs/PROTOCOL_AND_TOOLCALL_SPEC.md` | PASS | 生成链、OpenAI/Gemini 转换、PG-08、ATG 五信号、countTokens、toolshape 与代码一致；并已在残余限制中披露已知缺口 |
| `B-docs-and-audit/index.md` | PASS | 33 提交、28/5、3 合并、9bffda1 50/8841、根 config untracked、B6 证据与实测一致 |
| `REV-B7-commit-history-audit.md` | PASS | 33 个提交逐条覆盖；哈希、文件数、增删行、合并吸收集合与 `git` 输出一致；决策映射总体可追溯 |
| `REV-B7-architecture-coupling-audit.md` | FAIL（局部） | P0 反向依赖 6 文件、infra→app 3 文件、D-06 违反、全局状态等证据成立；P2 `wire_parser` 死代码结论错误，B7-11 需重写 |

## 6. Q 项（全部）

状态以 `REV-MERGE-852de53-oct31.md` §12/§17.6 与 `06-decision-log.md` 为基础，并标注本次 B7 复核变化。

| 编号 | 当前状态 | 说明 |
|---|---|---|
| Q-01 | USER-APPROVED | 上游 UI 文案变更既往获批；Q-05 仍涉及 SettingsView 文案 |
| Q-02 | USER-AUTHORIZED | 无限流式移除请求级 deadline；`transport.py` 未恢复 timeout |
| Q-03 | PASS | 快照重试 5×250ms，`session.py:417/432`、`pooled_session.py:168/185` 一致 |
| Q-04 | PASS | 根 `config.yaml` untracked、`defaults/config.yaml` tracked；与 `git ls-files`/`.gitignore:15` 一致 |
| Q-05 | OPEN（非阻断） | `SettingsView.vue:64-77` 上游文案替换；未在 B7 处理 |
| Q-06 | CONFIRMED | 403 两阶段：首次原位重签、二次切号、Paid 不切号；代码/文档一致 |
| Q-07 | RESOLVED | `covered_ids` + 3 天 Free 新鲜度；closeout 已闭合 |
| Q-08 | RESOLVED（清理可选） | `auth_error` 配置键改为可选；`defaults/config.yaml` 仍保留 `300`，但不再导致校验失败 |
| Q-09 | **REGRESSED** | 合并复审已 RESOLVED（调度不读 `auth_errors`）；B7 `AGENT.md:273` 与 `INSTANCE...:637` 又写“调度避让/动态避让” |
| Q-10 | RESOLVED | 参数校验日志只投影 loc/type/msg；`test_logging.py:654` |
| Q-11 | RESOLVED | `set_dump_to_file` 恢复 `global`；`test_logging.py:627` |
| Q-12 | CLOSED | B6 `results.json`：31 models、5/5 generations、双协议 403；Q-12/N-40 证据闭合 |
| Q-13 | OPEN（非阻断） | `snapshot_probe.js` / `build_snapshot_probe_args` 生产未接线；N-32 |
| P1-E2 | DEFER | OAuth/refresh_token 当前不存在，保持 defer |
| P4-Q4 | PENDING | Build native/signer 需在 P4 重新溯源，当前不作实现依据 |
| P5-Q11 | DEFER | Build REST defer 到 P4 |
| P6-Q6 | DISABLED | `active_activity` 置 disable；无 idle 自动回收 |
| P10-Q3 | DEFERRED | E2E 离线优先，WARP 小流量后置 |

## 7. N 项（全部）

| 编号 | 状态 | 备注 |
|---|---|---|
| N-01 | RESOLVED | 控制台 dump key-based 脱敏，B4 关闭 |
| N-02 | RESOLVED | aware/naive ISO 范围 400，B4 关闭 |
| N-03 | OPEN（非阻断） | 多日导出静默跳过缺失归档 |
| N-04 | OPEN（非阻断） | uvicorn 日志只进 stderr |
| N-05 | RESOLVED | 噪音测试断言真实命名空间；httpx/httpcore WARNING |
| N-06 | OPEN（非阻断） | 启动/跨天清理“调用”未测试 |
| N-07 | OPEN（非阻断） | SSE 15s/5000 与非法 Last-Event-ID 未断言 |
| N-08 | OPEN（非阻断） | `test_stream_route_headers_and_spa_bypass` 名不副实 |
| N-09 | RESOLVED | SSE 重连清除陈旧 droppedNotice |
| N-10 | RESOLVED | tracked 静态前端已重建 |
| N-11 | RESOLVED | instance raw dump 模型分量白名单 |
| N-12 | OPEN（非阻断） | aware 输入按本地固定偏移归一化 |
| N-13 | OPEN（非阻断） | 非 JSON 表单/纯文本 body 仅正则脱敏 |
| N-14 | RESOLVED | `_dump_raw_exchange` 已 safe 字段 |
| N-15 | OPEN（非阻断） | `app.py:200-201` 仍直接写 `_auth_file` |
| N-16 | OPEN（非阻断） | 上游新页 Cookie 回写/`_cached_cookies` 清理未吸收 |
| N-17 | OPEN（非阻断） | safe dump 仍保留完整 prompt/body/response，仅按 key 脱敏 |
| N-18 | RESOLVED/CHANGED | 上游快照就绪轮询已移除；当前仅请求期 5×250ms |
| N-19 | OPEN（非阻断） | 启动账号同步无定向测试 |
| N-20 | OPEN（非阻断） | 历史文档仍含旧 timeout 键 |
| N-21 | OPEN（非阻断） | 前端门禁未独立复跑 |
| N-22 | OPEN（非阻断） | 非流式 hooked replay 无请求 deadline |
| N-23 | OPEN（非阻断） | `PooledSession.ensure_ready` 仍 `del timeout_s` |
| N-24 | OPEN（非阻断） | `PooledSession` 空快照重试缺直接测试 |
| N-25 | OPEN（非阻断） | `ruff check .` 历史 docs harness 告警 |
| N-26 | RESOLVED | B-01 修复已在 HEAD |
| N-27 | RESOLVED | 参数校验日志不含 `input_value`；closeout F1 |
| N-28 | RESOLVED | `global _dump_to_file_override` 已恢复；closeout F2 |
| N-29 | RESOLVED | 启动 metadata 重复探测按 covered_ids/3 天新鲜度闭合 |
| N-30 | **REGRESSED IN B7 DOCS** | 历史报告已修正；`AGENT.md`/`INSTANCE...` 又写 `auth_errors` 动态避让 |
| N-31 | RESOLVED（校验）/OPEN（清理） | `auth_error` 可选；`defaults/config.yaml` 仍保留 `300`，清理可选 |
| N-32 | OPEN（非阻断） | `snapshot_probe` 生产未接线；与 Q-13 同项 |
| N-33 | RESOLVED（重复探测）/OPEN（门控） | 配置门控子项拆为 N-43 |
| N-34 | RESOLVED | 手动 detect 写 ISO `checked_at` |
| N-35 | OPEN（非阻断） | 历史 float `tier_checked_at` 会按陈旧重探一次 |
| N-36 | OPEN（非阻断） | BotGuard 快照就绪轮询移除后的行为变化 |
| N-37 | OPEN（设计取舍） | 上游 session_auth Cookie 改动未吸收；目标完整注入优先 |
| N-38 | OPEN（设计取舍） | 上游 accounts store 回退行为保留 |
| N-39 | RESOLVED | Paid 403 不切号、403→429 策略测试已补 |
| N-40 | CLOSED | B6 实机证据闭合 |
| N-41 | OPEN（非阻断） | `dump_request_exchange` 的 `error_detail` 无调用；dump 元数据面扩大 |
| N-42 | OPEN（非阻断） | 手动 detect 的 ISO `checked_at` 缺直接路由单测 |
| N-43 | OPEN（非阻断） | `_refresh_startup_account_metadata` 在 metadata 关时仍执行 |
| N-44 | RESOLVED AT HEAD | 合并复审时“closeout 未 stage”的提示已由 `1f60fbc` 提交闭合 |

## 8. 精确哈希

HEAD：`55f1c649992264ecc19d079479865f2c7fc63993`

| 文件（目标树） | SHA256 |
|---|---|
| `AGENT.md` | `41DA4D74D17EA1135CEE8A9972E9A06BA87C904A14F58F3D46B74A28E1079EC0` |
| `config/config.schema.json` | `657421931BBFCD7535108577957DE05F6F06C57800EB9676DFB3042729EB660F` |
| `docs/ARCHITECTURE.md` | `11554B9B970E372514C623187585EADBBD409A1F260135956E71A52D51F3F5D3` |
| `docs/BOTGUARD_VERIFICATION_CHAIN.md` | `DC0046BE3FECECFD358659330A5696833504031A6394F187674F6C83D884F72D` |
| `docs/WIRE_SPECIFICATION.md` | `3D40EE5CB7694797CB7710AF1A487978B2B32843B90B4D29A05B60A1E3308595` |
| `docs/changelog.md` | `A265C21D29A840F12DAAFB3CBE6FBA553F1503FF0845828267FC85B27E1037C9` |
| `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` | `C15495B61B0539F97A78DDB9636F297C316A9F1481832AC5EA47F57F10ADE66A` |
| `docs/KEEPALIVE_AND_LOGGING_SPEC.md` | `D1A5D16CBA04A3741A554BBDC5B9A1668F17E6EED9F13134EC0EE913227872FB` |
| `docs/PROTOCOL_AND_TOOLCALL_SPEC.md` | `9AA47F49E8A46DFF9820F81FDB7423AAD72F7CED4D8EEEB843E4E16608E6CFE5` |
| `docs/dev/rebuild/domains/B-docs-and-audit/index.md` | `53837AD244BEE993084CE6CB29A9219B27238E94A8ADA8D658BE1A27CA5D232E` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-commit-history-audit.md` | `DF3720E23AA16A1F63541A4E6A9B69BAC8B73000FCF5B4041B93207666CA0628` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md` | `0EFB15F0B7264216CD3011C66A8B52557E64F5C3672D2C568B3CFBD568571FAC` |

目标树与主仓的三个 B-docs 文件哈希逐一相同（见 §2）。

## 9. 优先级跟进

1. **P0（审计正确性）**：重写 `REV-B7-architecture-coupling-audit.md:185,257` 的 `wire_parser` 结论；用 AST/`grep` 重扫生产引用，确认其为活跃解析模块；在修正前不要执行 B7-11 的删除/迁移。
2. **P0（根文档一致性）**：更新 `AGENT.md:341,387`，明确根 `config.yaml` untracked + ignored；同步 Q-04/changelog。
3. **P0（schema）**：决定 `config/config.schema.json` 是 example-only 还是当前 runtime schema；若为后者，补齐 `security`、`max_contexts`、`diagnostics`、`rotator`、`features.routing/keepalive/atg/formats`、`paid_model_rules`、`tier_quotas`、`account_presets`，并增加 `defaults/config.yaml` 校验。
4. **P1（文档回归）**：删除/修正 `AGENT.md:273` 与 `INSTANCE_POOL_AND_ROUTING_SPEC.md:637` 的 `auth_errors` 调度避让表述；与 `BOTGUARD_VERIFICATION_CHAIN.md:333` 和 `account_rotator.py` 实际行为对齐；可一并清理 `account_rotator.py:383-384` 陈旧注释（需另立代码任务）。
5. **P1（B6 证据表述）**：把 `verdict=PASS` 拆分为 live functional + offline sanitizer，或补一次修复后的导出实机验证；修正 `REV-B6-live-verification.md:90` 的 Dockerfile 环境变量名。
6. **P2（跨文档口径）**：在 `ARCHITECTURE.md` 中把 infra→application 从“受控例外”改为“已知 P0 技术债/临时豁免”，与架构审计对齐；统一 WIRE“Field/index”术语。
7. **P2（遗留 Q/N）**：按 §6/§7 处理 Q-05、Q-13/N-32、N-42、N-43、N-37/N-38、P4-Q4、P1-E2、P5-Q11、P6-Q6、P10-Q3；这些不阻塞 B7 文档事实核对，但应在 C 阶段前排期。

## 10. 只读声明

本次审计未启动 `main.py`、未拉起 Chromium/CloakBrowser、未请求任何本地或外部 HTTP/网络服务；未运行 pytest/ruff/pyright；未修改业务代码、测试、配置、前端或既有文档；未 stage/commit。仅创建本报告的目标树与主仓两份副本。

---

## 11. 修复记录（复审后追加，不改变 §0–§10 原始审计结论）

> 本节由文档修复会话追加；§0–§10 保持原始只读审计记录，§11 记录阻断项/条件项的实际处置。

| 编号 | 处置 | 落点 |
|---|---|---|
| Blocker 1 | `wire_parser.py` 改判为生产活跃模块；从 B7-11 删除/迁移任务移除，新增 B7-11b 仅做 gateway 内部 API/归属清理 | `REV-B7-architecture-coupling-audit.md` §4.1、§7；`index.md` §5.2、§6 |
| Blocker 2 | 根 `config.yaml` 明确为 untracked + gitignored；`defaults/config.yaml` 为唯一 tracked 默认 | `AGENT.md` §8、§9 树注释 |
| Blocker 3 | `config/config.schema.json` 扩为当前运行配置契约：覆盖 `security`、`max_contexts`、`diagnostics`、`rotator`、`features.{routing,keepalive,formats,atg}`、`paid_model_rules`、`tier_quotas`、`account_presets`，并兼容 Gate 0.5 示例；跨字段语义仍由运行期校验 | `config/config.schema.json`；`AGENT.md:336`；`docs/changelog.md:59` |
| Blocker 4 | 删除 `auth_errors` 调度避让表述；明确仅累加计数、`auth_cooldown=0.0`、调度准入不读取 | `AGENT.md:273`；`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:637` |
| 条件项 1 | B6 verdict 拆为 `functional_live: PASS` / `b4_export_redaction: PASS (offline-verified)` / `overall: PASS`，并注明未新增实机导出 | `docs/dev/evidence/B6/results.json`、`diagnostics.json`；`REV-B6-live-verification.md`；`docs/changelog.md:224` |
| 条件项 2 | Dockerfile 事实修正为 `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`；entrypoint 优先 `AISTUDIO_CONFIG_PATH` | `REV-B6-live-verification.md:90` |
| 条件项 3 | `ARCHITECTURE.md` 将 `infrastructure -> application` 改称已知 P0 技术债，引用 B7-02/B7-03，不再称“受控例外” | `docs/ARCHITECTURE.md:89-92` |
| 条件项 4 | WIRE 术语统一为 `item[77] (Field 78)` / `item[82] (Field 83)`，明确 0-based 下标与 1-based protobuf 字段号 | `docs/WIRE_SPECIFICATION.md:405-420`；`docs/ARCHITECTURE.md:362-371`；`docs/changelog.md:204` |

修复会话仅改文档与 schema，未改 `src/`、`tests/`，未启动服务/浏览器/网络，未 stage/commit。

### 11.1 修复后哈希（§2/§8 为修复前审计快照）

| 文件（目标树） | 修复后 SHA256 |
|---|---|
| `AGENT.md` | `9DF63EE5F917D9D919D4801029B7EDB01E386632E006731DFC0A13D919F09D1D` |
| `config/config.schema.json` | `58702F39624248880C426F8590759594EB6E605AE75674CC1C80145612FE5D9F` |
| `docs/ARCHITECTURE.md` | `716BCF14818AD0E4588407BEE6FB1DF86B7ECFB38FDE39EFA41D28C0D991845B` |
| `docs/WIRE_SPECIFICATION.md` | `B721607A65D3C617348236D0418CF8F1CE3896D3C5C77DC10E09E89F3BE993C7` |
| `docs/changelog.md` | `D2E34886C34C2BB26EE973735BC0E6947F700CD53E920A7E6C86372D20EA6828` |
| `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` | `BE94BCB0C4B9602F66C888F55E7635EBC7074D44574651C27561243F37A9B694` |
| `docs/dev/evidence/B6/results.json` | `1C2F9C79D16020C37AB1D0A2EA75B9DF0541FF10494A03DB99507EF20294995F` |
| `docs/dev/evidence/B6/diagnostics.json` | `5A932EF9E33B613265DEC3F771F79A3253BCFCAB878F64E04209F78B99C5425F` |
| `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md` | `68F0ED50177802ACEB5812B9C6041EA6F260310808CB23A96B72A97A99CD9975` |
| `docs/dev/rebuild/domains/B-docs-and-audit/index.md` | `A8ABD2D2ADB5E7123595A784C8C90E58222EB82DA60AA78ABF73567A2DC24C09` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md` | `2B9AAEF12215C699DD9D8A1DD3AB712AAFC5DBB516F9B5CB25730DE7DFDD1E27` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-commit-history-audit.md` | `DF3720E23AA16A1F63541A4E6A9B69BAC8B73000FCF5B4041B93207666CA0628` |

`REV-B7-docs-and-audit-summary.md` 自身哈希因追加本节而变化，由修复会话在交付说明中报告；目标树与主仓副本保持逐字节一致。§2/§8 表格保留修复前快照，避免改写原始审计记录。

---

## 12. 修复后独立复审（2026-11-01）

### 12.1 最终判定

**Verdict: PASS。无阻断项。** §0–§10 的 FAIL 为原始审计快照；§11 的 8 项修复已逐项复核，均成立。剩余项均为非阻断遗留或历史快照说明。

### 12.2 十项复核

| # | 要求 | 结果 | 复核证据 |
|---|---|---|---|
| 1 | `wire_parser.py` 不再被判为 test-only/dead；列出生产 import；B7-11 不删除/迁移；B7-11b 仅归属清理 | **PASS** | `REV-B7-architecture-coupling-audit.md:185` 改为“生产活跃模块”；`:194` 列出 5 个生产 import 与调用点；`:196` B7-11 明确“除 `wire_parser.py` 外”；`:197,262` B7-11b 仅 gateway 内部 API/归属收敛，严禁删除/迁测试 |
| 2 | `AGENT.md` 根配置 untracked+ignored，`defaults/config.yaml` tracked；无 stale tracked 声明 | **PASS** | `AGENT.md:341-342,387-388`；`git ls-files config.yaml` 空；`git check-ignore -v config.yaml` -> `.gitignore:15:/config.yaml` |
| 3 | schema 是 truthful current contract；defaults 与 example 均可解析/校验；无 overclaim | **PASS** | `config/config.schema.json:4-6` 标为当前运行配置契约并限定“结构与基本类型”；实际执行 `jsonschema.validate`：`defaults/config.yaml` VALID、`config/config.example.yaml` VALID；顶层键与两文件匹配 |
| 4 | 不再有 `auth_errors` 调度避让声明；与代码一致 | **PASS** | `AGENT.md:273`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:637`、`docs/BOTGUARD_VERIFICATION_CHAIN.md:333` 均明确“仅累加、调度准入不读取、无动态避让”；代码 `account_rotator.py:371-388` 仅累加/清冷却，无调度消费者 |
| 5 | B6 拆分 `functional_live` + `b4_export_redaction (offline)` + `overall PASS`；无新实机导出；Dockerfile 为 `AISTUDIO_CONFIG_FILE` | **PASS** | `results.json:102-107`；`diagnostics.json:45-47`；`changelog.md:224`；`REV-B6-live-verification.md:90` 明确 `Dockerfile:10` 为 `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`；`:68` 写明未新增实机导出 |
| 6 | `ARCHITECTURE.md` P0 debt 与耦合审计对齐 | **PASS（有措辞小项）** | `ARCHITECTURE.md:89-92` 已改为“P0/P1 技术债”“已知 P0 技术债（不是受控例外）”，引用 B7-02/B7-03；`REV-B7-architecture-coupling-audit.md:251-253` 的 3 个 P0 项一致。小项见 N-46 |
| 7 | WIRE `item[77]/Field 78`、`item[82]/Field 83` 术语一致 | **PASS** | `WIRE_SPECIFICATION.md:405,419-420`、`ARCHITECTURE.md:370-371`、`changelog.md:204` 均统一为 0-based `item[77]/[82]` 与 1-based `Field 78/83` |
| 8 | B-docs 目标/主仓 SHA256 全部一致（index、commit audit、architecture audit、summary） | **PASS** | 四个文件逐一 `Get-FileHash` 比对均为 `EQ=True`；见 §12.4 |
| 9 | 无 docs 阶段的 `src/`/`tests/` 改动；HEAD `55f1c64`；根配置 untracked；无 staged；diff checks 干净 | **PASS** | `git rev-parse HEAD` = `55f1c64…`；`git status` 无 `src/`/`tests/`；`git diff --cached --name-only` 空；`git diff --check`/`--cached --check` 均 exit 0；根 `config.yaml` untracked + ignored |
| 10 | 保留全部旧 Q/N；新增 doc gap；无阻断才 PASS | **PASS** | §6/§7 原表完整保留；新增 N-45/N-46/N-47 均为非阻断；无新增 blocker |

### 12.3 剩余 Q/N（无新增阻断）

§6/§7 的完整表保持不变。当前仍为非阻断/阶段外的主要项：

- **Q 项**：Q-05（SettingsView 上游文案）、Q-13/N-32（`snapshot_probe` 未接线）、P4-Q4（native/signer 阶段外重审）、P1-E2（OAuth defer）、P5-Q11（Build REST defer）、P6-Q6（`active_activity` disable）、P10-Q3（WARP 后置）。
- **N 项**：N-03/N-04/N-06/N-07/N-08/N-12/N-13/N-15/N-16/N-17/N-19/N-20/N-21/N-22/N-23/N-24/N-25/N-32/N-35/N-36/N-37/N-38/N-41/N-42/N-43 保持原状态；N-31 仅剩可选清理；N-33 的配置门控子项仍为 N-43。
- **已复核闭合**：Q-09/N-30 的文档回归已修正；Blocker 1–4 与条件项 1–4 均已处置。
- **新增非阻断**：
  - **N-45**：`REV-B6-live-verification.md` 保留 B6 运行时的历史 `git status`/`HEAD b000bb7` 快照；当前 HEAD 已是 `55f1c64`。建议标注“B6 run-time snapshot”，避免被读成当前状态。（已在 §13 闭合：改为当前定稿 HEAD 与文件计数）
  - **N-46**：`ARCHITECTURE.md:91` 说 3 个 gateway 文件反向依赖 application，`:92` 只点名 `model_discovery.py` 与 `model_defaults.py`；第三个 `instance_pool.py:23` 未点名，建议补全或改为“3 个文件（含 instance_pool.py）”。（已在 §13 闭合：§92 补齐 `instance_pool.py:23-25`）
  - **N-47**：`B-docs-and-audit/index.md:92` 说本任务只新增三个文件；该目录现在同时包含本 summary（第四个文件）。建议把 summary 标为“审计输出/非 B7 原始三件套”，避免计数歧义。（已在 §13 闭合：index §2 工件表与 §7 均改为四件套）

### 12.4 修复后精确哈希

HEAD：`55f1c649992264ecc19d079479865f2c7fc63993`

| 文件（目标树） | 修复后 SHA256 |
|---|---|
| `AGENT.md` | `9DF63EE5F917D9D919D4801029B7EDB01E386632E006731DFC0A13D919F09D1D` |
| `config/config.schema.json` | `58702F39624248880C426F8590759594EB6E605AE75674CC1C80145612FE5D9F` |
| `docs/ARCHITECTURE.md` | `716BCF14818AD0E4588407BEE6FB1DF86B7ECFB38FDE39EFA41D28C0D991845B` |
| `docs/BOTGUARD_VERIFICATION_CHAIN.md` | `DC0046BE3FECECFD358659330A5696833504031A6394F187674F6C83D884F72D` |
| `docs/WIRE_SPECIFICATION.md` | `B721607A65D3C617348236D0418CF8F1CE3896D3C5C77DC10E09E89F3BE993C7` |
| `docs/changelog.md` | `D2E34886C34C2BB26EE973735BC0E6947F700CD53E920A7E6C86372D20EA6828` |
| `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` | `BE94BCB0C4B9602F66C888F55E7635EBC7074D44574651C27561243F37A9B694` |
| `docs/KEEPALIVE_AND_LOGGING_SPEC.md` | `D1A5D16CBA04A3741A554BBDC5B9A1668F17E6EED9F13134EC0EE913227872FB` |
| `docs/PROTOCOL_AND_TOOLCALL_SPEC.md` | `9AA47F49E8A46DFF9820F81FDB7423AAD72F7CED4D8EEEB843E4E16608E6CFE5` |
| `docs/dev/evidence/B6/results.json` | `1C2F9C79D16020C37AB1D0A2EA75B9DF0541FF10494A03DB99507EF20294995F` |
| `docs/dev/evidence/B6/diagnostics.json` | `5A932EF9E33B613265DEC3F771F79A3253BCFCAB878F64E04209F78B99C5425F` |
| `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md` | `68F0ED50177802ACEB5812B9C6041EA6F260310808CB23A96B72A97A99CD9975` |
| `docs/dev/rebuild/domains/B-docs-and-audit/index.md` | `A8ABD2D2ADB5E7123595A784C8C90E58222EB82DA60AA78ABF73567A2DC24C09` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-commit-history-audit.md` | `DF3720E23AA16A1F63541A4E6A9B69BAC8B73000FCF5B4041B93207666CA0628` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md` | `2B9AAEF12215C699DD9D8A1DD3AB712AAFC5DBB516F9B5CB25730DE7DFDD1E27` |

B-docs 四件套（index、commit audit、architecture audit、本 summary）目标树与主仓逐字节一致；本 summary 的最终 SHA256 在交付说明中报告。

### 12.5 无提交/无服务状态

- HEAD 仍为 `55f1c649992264ecc19d079479865f2c7fc63993`；`git diff --cached --name-only` 为空，未 stage/commit。
- 根 `config.yaml` 仍 untracked + ignored；无 `src/`/`tests/` 改动。
- `git diff --check` 与 `git diff --cached --check` 均 exit 0。
- 本次复审未启动 `main.py`、浏览器/Chromium、服务端，未发起任何本地或外部网络请求；未运行 pytest/ruff/pyright。
- 仅更新目标树与主仓两份 summary 报告；未修改其他文件。

### 12.6 复审结论

所有 10 项要求均满足；Blocker 1–4 与条件项 1–4 已由修复会话落实并经独立证据复核。剩余 N-45/N-46/N-47 为措辞/历史快照类非阻断项，不影响 PASS。

---

## 13. 定稿状态（2026-11-01）

### 13.1 定稿范围

- HEAD：`55f1c649992264ecc19d079479865f2c7fc63993`（`rebuild-upstream`）。
- 全仓 Ruff：`uv run ruff check .` **0 errors**。修复为 14 个 `docs/dev/evidence/{wp5-20261018,wp5b-20261019,wp5v3-20261019}/harness/*.py` 补齐文件尾换行，以及 `wp5_set_config.py` 的 `startswith` 合并（`PIE810`）与列表拼接改星号展开（`RUF005`），行为不变。
- CI：`.github/workflows/ci.yml` 后端 job 增加 job 级 `PYTHONUTF8: "1"` 与 `PYTHONIOENCODING: utf-8`；测试命令未改动。
- changelog：`docs/changelog.md` 重写为 Keep a Changelog 结构（`[2.0-rebuild] - 2026-10-31`、`[0.1.0] - 2026-05-16`），按 Added/Changed/Fixed/Removed/Security 归类，移除阶段代码、提交哈希表、REV 追溯与任务日志式条目；溯源自本报告与 `REV-B7-commit-history-audit.md` 承担。
- N-45：`REV-B6-live-verification.md` 的 §7.1 已改为当前定稿 HEAD `55f1c64` 与工作区计数（24 个已跟踪文件修改 + 7 个未跟踪文件），移除 `HEAD b000bb7` 与“16 个预期文件”的过期表述；§7.2 门禁更新为 `uv run ruff check .`、`uv run pyright src tests` 与 `uv run pytest -q`（2 failed / 820 passed / 1 skipped）。
- N-46：`docs/ARCHITECTURE.md:92` 的 P0 技术债句已补齐第三个文件 `instance_pool.py:23-25`，与 `:91` 的“3 个 gateway 文件”一致。
- N-47：`B-docs-and-audit/index.md` §2 工件表已加入本 summary，§2 末尾与 §7 的“三个文件”改为四件套。

### 13.2 定稿门禁

| 门禁 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed（exit 0） |
| `uv run pyright src tests` | 0 errors, 0 warnings, 0 informations（exit 0） |
| `uv run pytest -q`（`PYTHONUTF8=1`） | 2 failed, 820 passed, 1 skipped in 11.17s（两个失败均为既有 Windows 环境问题） |
| `git diff --check` | exit 0 |
| `git diff --cached --check` | exit 0 |
| `git ls-files config.yaml` | 空（untracked + ignored） |

### 13.3 定稿哈希（目标树；主仓逐字节一致）

| 文件 | SHA256 |
|---|---|
| `AGENT.md` | `9DF63EE5F917D9D919D4801029B7EDB01E386632E006731DFC0A13D919F09D1D` |
| `.github/workflows/ci.yml` | `C80845E9983DD87C9B262A88D347BDEE70E9914674E614F5E1152D79A4AB2B89` |
| `config/config.schema.json` | `58702F39624248880C426F8590759594EB6E605AE75674CC1C80145612FE5D9F` |
| `docs/ARCHITECTURE.md` | `D232C679803E84AD280FDFA760D4F15A25C0BC218C510E6B762C6B0307079C8B` |
| `docs/BOTGUARD_VERIFICATION_CHAIN.md` | `DC0046BE3FECECFD358659330A5696833504031A6394F187674F6C83D884F72D` |
| `docs/WIRE_SPECIFICATION.md` | `B721607A65D3C617348236D0418CF8F1CE3896D3C5C77DC10E09E89F3BE993C7` |
| `docs/changelog.md` | `55A16CF85967A3F9AE29A9E6AD6C1404D8A60A904818CC10A583C9C977FE3BB1` |
| `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md` | `BE94BCB0C4B9602F66C888F55E7635EBC7074D44574651C27561243F37A9B694` |
| `docs/KEEPALIVE_AND_LOGGING_SPEC.md` | `D1A5D16CBA04A3741A554BBDC5B9A1668F17E6EED9F13134EC0EE913227872FB` |
| `docs/PROTOCOL_AND_TOOLCALL_SPEC.md` | `9AA47F49E8A46DFF9820F81FDB7423AAD72F7CED4D8EEEB843E4E16608E6CFE5` |
| `docs/dev/evidence/B6/results.json` | `1C2F9C79D16020C37AB1D0A2EA75B9DF0541FF10494A03DB99507EF20294995F` |
| `docs/dev/evidence/B6/diagnostics.json` | `5A932EF9E33B613265DEC3F771F79A3253BCFCAB878F64E04209F78B99C5425F` |
| `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md` | `31D8F5E38FE699FA64F24184FFFC5B9D5FB1EB301A84043876B3205330A3B9FF` |
| `docs/dev/rebuild/domains/B-docs-and-audit/index.md` | `FE2934EB16E6931CA2A77C7D5D4F10325A8021C179B7CDFCDDEB89DD7EA81B22` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-commit-history-audit.md` | `DF3720E23AA16A1F63541A4E6A9B69BAC8B73000FCF5B4041B93207666CA0628` |
| `docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md` | `2B9AAEF12215C699DD9D8A1DD3AB712AAFC5DBB516F9B5CB25730DE7DFDD1E27` |

说明：

- §2（B-docs 三件套）、§8、§11.1、§12.4 为定稿前的历史快照；当前哈希与文件计数以本节为准。B-docs 现为四件套（`index.md` + 两份 audit + 本 summary）。
- 本 summary 因追加本节而改变自身哈希，其最终 SHA256 在交付说明中报告；目标树与主仓副本保持逐字节一致。

### 13.4 未提交状态

- 工作区：24 个已跟踪文件修改 + 7 个未跟踪文件（3 个规格文档 + B-docs 四件套）；`src/`、`tests/` 无改动。
- `git diff --cached` 为空；未 stage/commit、未 amend；全部改动留待 Integrator 提交。
- 定稿过程严格离线：未启动服务、浏览器或网络请求，未执行 Docker build。
