# 10 Handoff Prompt

复制以下提示词给全新 agent：

```text
项目路径：D:\LLM\self_programming\aistudio-api
基线：rebuild @ 当前 HEAD（不要写死旧 SHA）；upstream/master 必须现场 fetch 后现查，计划基线为 99c38baefc84d8cb66b05c28ede8a49ce050bfb2（2026-10-25）。
交接前：先提交本轮文档修复，把 `git rev-parse HEAD` 记录到 `13-gate0-execution-record.md`、`08-09-decision-record.md` 与 `docs/changelog.md`；10 保持“当前 HEAD + git rev-parse 现查”，不要把 SHA 写回本行（避免自引用）。若未提交，必须显式从 rebuild 工作区复制 docs/dev/rebuild，并注明来源不是 commit。
目标：优先复用已存在且干净的 `.kilo/worktrees/rebuild-upstream`（branch rebuild-upstream，基于 99c38ba）；按 D-01..D-14 与 08-09 重建；不合并 master/rebuild。

必读（按顺序）：
1. docs/dev/rebuild/08-09-decision-record.md（**行为权威**：D-01..D-14、17 分类、20 冲突闭合、ENGINEERING_DEFAULT、F1-F8）
2. docs/dev/rebuild/08-08-upstream-99c38ba-full-diff-audit.md（事实源：76/76 逐文件审计）
3. docs/dev/rebuild/05-restart-map.md（Gate 0/0.5 流程与迁移边界）
4. docs/dev/rebuild/06-decision-log.md（P2/P3 + 08-09 落地；99c38ba 部分以 08-09 为准）
5. docs/dev/rebuild/06-index-validation.md（只读校验脚本；534 行）
6. docs/dev/rebuild/06-feature-ledger.md 与 06-feature-index.csv（唯一事实源；534 行）
7. docs/dev/rebuild/12-gate0-inputs.md（seam/状态表/config/event/acceptance 模板）
8. docs/dev/rebuild/06-test-disposition.csv（86 行；行为覆盖要求见 08-09 C14/E-01）
9. docs/dev/rebuild/13-gate0-execution-record.md（Gate0 历史 + 2026-10-28 轮次）
10. docs/dev/rebuild/08-07-upstream-99c38ba-reaudit.md（**仅历史**，已被取代，不得作为 handoff 依据）
11. docs/dev/evidence/INDEX.md、docs/dev/evidence/p1-ark-merge-archive/README.md、docs/changelog.md

本提示词只在独立 reviewer 明确判定“可 handoff”后使用；修复后须再由独立审计复核（05 §6.7）。

Gate 0（先做，不改业务代码）：
G0.1 基线：`git status --porcelain` 为空；`git rev-parse HEAD` 记录新 SHA；05/10 基线一致。
G0.2 上游硬门：`git fetch upstream --prune` 必须成功；`git rev-parse upstream/master` 必须记录；若不再是 99c38ba，先按 08-08/08-09 重审并更新裁决，不得离线沿用旧 ref。
G0.3 索引：跑 06-index-validation.md；534 行、ledger_line 全对、桥接 34/47/98/44/74/25/69/42/64/37、无 pending、errors=0。
G0.4 决策：D-01..D-14 已确认（见 08-09 §1）；17 个 NO_FEATURE_ROW（16 feature + 1 out-of-scope）与 20 冲突（C01..C20）已闭合；EXPECTED_DECISIONS 覆盖 D03-004 keep、D03-006 rewrite、D05-044 port、D05-045 rewrite 及新增 16 行；P9-Q3 不得 drop 日志功能。不得把“已确认”写成“Gate 0 已通过”。
G0.5 测试处置：86 行；主 rebuild 80/80；99c38ba `tests/**/*.py` 28/28（含 conftest）；无 pending；28/28 只是文件覆盖，必须补 8 个新 REST 端点的路由级测试与行为断言（C14/E-01）。
G0.6 Secret/evidence：只扫 docs/dev/rebuild + INDEX.md allowlist，不扫整个 docs；BOTGUARD 是 upstream 原生 grandfather，不复制/不改；spikeQ4-evidence.json/spikeQ4.py 不得进入新仓库。
G0.7 worktree/迁移：先查 `git worktree list`、路径和分支。确定性命中规则见 05 §6.4：优先复用已存在且干净的 `.kilo/worktrees/rebuild-upstream`（branch `rebuild-upstream`，必须 `merge-base(HEAD,99c38ba)=99c38ba`，工作区干净），不得再次 `worktree add`；只有路径与分支都不存在时才 `git worktree add .kilo/worktrees/rebuild-upstream -b rebuild-upstream 99c38ba`（路径不存在但分支已存在时只挂载该分支）。禁止 `rebuild/upstream`（与 `refs/heads/rebuild` 冲突）。复用旧 worktree 时先删除旧 `docs/dev/rebuild/acceptance`、`config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`，再由 Gate 0.5 按 12 §5.1 重建/验证；不得沿用旧工件。旧 worktree 的 12/13 只是参考，事实源是主工作区 docs。只在文档修复已 commit 后用 `git -C $new restore --source=rebuild -- docs/dev/rebuild`；未 commit 则显式 Copy-Item 并说明。禁止 `-- docs` 整树，禁止 checkout src/tests。迁移后确认 05/06*/08-07/08-08/08-09/10/12/13、06-test-disposition.csv、docs/dev/evidence/INDEX.md、docs/changelog.md 均存在；`git add -A` + `git status -uall`。
G0.8 归属：ledger-batches 仅历史输入（排除批次文件或保留 README 标注）；旧 WP 正文留在 rebuild，archive/INDEX/changelog 按 05 §6.5 归属。
G0.9 Gate0.5 文档：12-gate0-inputs.md、06-test-disposition.csv 已存在；acceptance/config/fixtures 必须在迁移 worktree 由 Gate 0.5 按 12 §5.1 重新创建并验证，不得沿用旧 worktree 工件。当前 Gate 0.5 **未执行**。
G0.10 Handoff：本提示词已更新，能独立执行；不依赖旧 08 行号。是否可交接由独立 reviewer 判定。

Gate 0.5（P9-pre 前，输入见 12-gate0-inputs.md；必须在迁移 worktree 重新验证）：
1. 五层接口（Account/Auth、Instance、Pool/Router、Executor、Event Sink）与 Account 权威、Context/Page 工厂、surface 解析、active_requests、Event Sink。
2. 状态表：UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED + active_requests；消息唤醒、无 idle 自动回收、失败/stale 手动重启、D-05 有界 rebind、429/403/503。
3. config schema：count/surface/min_tier、api_keys、security.api_allow_anonymous（默认 false）、security.web_password（空=管理页免密）、formats、keepalive、日志路径；rotator/paid_model_rules/tier_quotas/account_presets/account_presets.path；quota_scope=account|account_surface（默认 account）；diagnostics.hash_include_code（默认 true，不进普通 UI）；删除浏览器 idle 超时键，timeout 默认 600；rotator 深合并默认 + 启动校验（D-07）；不引入 IP/CIDR/设备白名单。
4. event schema：request/attempt/usage/error/latency/surface/paid；usage 含 reasoning/cached；finish_reasons 归模型统计，account_breakdown 属 stats REST 响应；client/attempt 分母分离；P5 只扩展字段；403 tier 不足记 tier_insufficient 并带 model/required_tier/current_tier。
5. 06-test-disposition.csv 与 acceptance 模板（测试项+预期+命令+工件路径）；28/28 文件覆盖 + 8 个新端点路由测试 + 行为级断言；quota_scope/鉴权/page prune/drain/hash 诊断均有验收项。
Gate 0.5 未通过，不得进入 P9-pre 编码。

Agent 工作模式（详见 05 §2.1）：
- 你是主线 integrator：唯一持有目标分支/worktree、接口、05/06/ARCHITECTURE 和提交权；负责顺序、合并、验收、changelog。
- worker 只接一个 Dxx-NNN 或明确文件域；独立 worktree；不得改 05/06/architecture/decision log；报告测试项+预期+实际。
- reviewer 只读；在 Gate0/0.5、P3 状态机、P2 路由、P5 PG、P7 统计、最终 handoff 独立审查，不参与实现。
- 并行仅限接口已冻结且文件域不重叠；最多 2-3 个 worker；禁止同时改 routing_pipeline.py、instance_pool.py、generation_service.py、05/06/ARCHITECTURE；禁止 F1↔F3、F3↔F4、F4↔F5、F6↔F7 同时改共享语义文件。
- Gate0/0.5、P9-pre、P1/P3/P2 核心、P5 parser、P6→P7 默认串行；P4/P8 在 API 稳定后并行。
- 不用“保持旧测试绿”复活旧契约；最终合并/提交只由 integrator 做。

顺序：Gate 0 → Gate 0.5 → P9-pre 装配/鉴权 → P1+P3+P2 → P5 OpenAI/Gemini → P6 → P7 日志/导出 → P4 Build → P8/UI → Claude/Responses → 统计页 → P10 收尾。
99c38ba 增量的内部顺序用 08-09 §5 的 F1-F8：F1 rotator/quota_scope → F2 协议/传输 → F3 账号域 → F4 准入/403 → F5 生命周期 → F6 统计 → F7 Web → F8 测试收口；F2 可在 F1 契约冻结后并行，其余按依赖串行。
每片测试先写“测试项 + 预期结果”，验收通过再进下一片。

状态机红线：
- 只用 active_requests；禁止 lease/reservation/30s/15s 预算；finally 释放。
- 控制面只做登录/保活，不算请求实例；有活动实例时 Cookie 回写走实例页。
- 消息唤醒所有合格 SLEEPING/UNSTARTED 实例；上游已移除 idle 回收，不恢复 idle 键/任务；`active_activity` 置 disable。
- **watchdog 保留（D-02）**：仅父进程死亡后的孤儿 Chromium 清理，不做账号自动复活。
- **CDP 只保留 WebSocket ping（D-03）**：删除 liveness checker/轮询；断线/崩溃仍立即失败 pending futures。
- 失败/stale 才手动重启；禁止自动 stale 复活。
- rebind（D-05）：常规从 `READY`（`active_requests==0`）→ `RETIRING`（拒绝新请求）；**禁用/应用预设允许从 `BUSY` → `RETIRING`**（停止新请求，有界等待 `active_requests=0` 或超时强制关闭），**手动 close 在 `BUSY` 可拒绝**。之后 → 关闭旧 Page/Context → `CLOSED` → 新建 Context+Page → `STARTING` → `READY`；禁止 blue-green“先建新页再 drain”；禁止后台自动 rebind；修复 fallback 先于 switch。
- drain（D-10）：旧页只做一次有界等待，超时强制关闭；不无限重试、不留常驻后台 task。
- page prune（D-11）：关闭所有非当前请求、非当前活跃账号的 Page/Context；drain 只发生在旧 Page/Context，drain 期间不新建新页，新页在旧页关闭后创建；不存在“1 active + 1 drain”的页预算例外；不得页面/context 泄露。
- 429：先同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 503 + 等待/管理页提示；已出正文不切号。
- 付费模型 tier 不足（D-04/D-06）：只返回 403，带准确的 model/required_tier/current_tier，按协议映射；不请求期切号/重开浏览器；不得与 429→503 混用。
- 鉴权（D-14）：API 免密只由 `security.api_allow_anonymous` 控制（默认 false）；管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空=免密，不报错）；API Key 不锁管理页；不引入 IP/CIDR/设备白名单；CF Tunnel 绑定不变。
- 配额（D-01）：`quota_scope=account`（默认，chat/build 共用）或 `account_surface`（surface::model）；只影响记账键，不新增实例，不在请求期改口径。
- 模板 hash（D-09）：默认纳入 executable_code/code_execution_result；`hash_include_code` 为诊断开关（默认 true，不进普通 UI）；严重路由异常时优先怀疑该 hash 并回退。
- 配置（D-07）：rotator 深合并默认 + 启动校验；不静默、不请求期 KeyError。
- presets（D-08）：`data/accounts/presets.json`；锁可重入/无嵌套死锁；写失败上报。
- notes/批量分组（D-13）：不实现；分组 CRUD 与过滤 UI 保留。
- API Key 语义：`api_keys: []` 不等于开放（D-14）。
- 统计：P7 日志先于统计；事件 schema 基线在 Gate 0.5 冻结（12 §4），P5 只扩展字段，P7 只聚合；finish_reasons 先 API、UI 随统计页（E-02）。
- 协议：seed 仅 Gemini 生效，其他协议接受并告警忽略（E-04）；finish 采用上游新表 + 兼容映射（E-03）。
- upstream Vue 原生布局/文案/交互不得无故重写；新设置进现有 SettingsView/ApiKeyManagerCard 等卡片/表单（D-12），不重构布局/导航；ModelCatalog 单一所有者。

99c38ba 采用边界（详见 08-09）：
- 用户决策：D-01 quota_scope；D-02 保留 watchdog；D-03 只留 WS ping；D-04 403 准入；D-05 有界 rebind；D-06 领域错误→协议映射；D-07 深合并+启动校验；D-08 presets.json 修锁；D-09 hash 含代码 part + 诊断开关；D-10 有界 drain；D-11 全量 prune；D-12 现有 UI 承载；D-13 删 notes/batch；D-14 鉴权分离。
- 工程默认（可回退）：E-01 8 端点路由测试；E-02 finish_reasons 先 API；E-03 finish 编号兼容；E-04 seed 仅 Gemini；E-05 17 分类；E-06 未覆盖冲突只写 ENGINEERING_DEFAULT；E-07 required_tiers 解析顺序；E-08 C21/C22 编号缺陷归 D-14/D-08。
- 不得把工程默认写成用户裁决；不得用“其他”兜底。

PG 门槛（P5 验收前）：
F-01 functionCall 缺 thoughtSignature 兜底；F-02 首事件前真实 HTTP 4xx/5xx；F-03 finish-only/工具流终止兜底；F-04 functionResponse/functionCall 强制 Struct；脱敏 [toolshape] 摘要。
上游 99c38ba 已原生修复角色守卫、结构化 functionCall/functionResponse、per-chunk usage、空 200/malformed 归错；仍须用离线 fixture 证明 F-01..F-04，不重复造轮子。
F-05/06/07 后做，不阻塞 P5 最小闭环；F-08..F-13 在 P5/P4 前 triage。

禁止：修改测试/配置绕过契约；合并 master/rebuild；整树 checkout；离线沿用旧 upstream ref；访问真实账号/IP/Google；提交敏感内容；使用 08-07 作为行为依据。

验收：G0.1-G0.10 全过；G1-G11 固定出处仍为 08-05 附录 A / 08-01:129-140，并由 12 §5 模板落工件；Gate0 校验 0 错误；同账号 1 Context+1 Page；chat/build 不并存；无 lease 断言；API 免密默认关闭且管理页空密码可用；UI 验收：现有 SettingsView/ApiKeyManagerCard 中 quota_scope、API 免密开关、管理页空密码=免密均可见且可保存，不新增 IP/CIDR/本机-局域网模式，不重构布局；24h fake clock；日志/导出与统计口径；PG F-01..F-04 fixture 通过；08-09 的 F1-F8 与 20 冲突闭合有对应测试。
完成后报告：修改文件、测试命令/结果、未验证项、证据路径。
```
