# 10 Handoff Prompt

复制以下提示词给全新 agent：

```text
项目路径：D:\LLM\self_programming\aistudio-api
基线：rebuild @ 当前 HEAD（不要写死旧 SHA）；upstream/master 必须现场 fetch 后现查，计划基线为 99c38baefc84d8cb66b05c28ede8a49ce050bfb2（2026-10-25）。
交接前：先提交本轮文档修复，把 `git rev-parse HEAD` 记录到 `13-gate0-execution-record.md` 与 `docs/changelog.md`；10 保持“当前 HEAD + git rev-parse 现查”，不要把 SHA 写回本行（避免自引用）。若未提交，必须显式从 rebuild 工作区复制 docs/dev/rebuild，并注明来源不是 commit。
目标：优先复用已存在且干净的 `.kilo/worktrees/rebuild-upstream`（branch rebuild-upstream，基于 99c38ba）；按 D1-D8 与 08-07 裁决重建；不合并 master/rebuild。

必读（按顺序）：
1. docs/dev/rebuild/05-restart-map.md（Gate 0/0.5 流程与迁移边界）
2. docs/dev/rebuild/08-07-upstream-99c38ba-reaudit.md（99c38ba 影响矩阵与逐项裁决；本提示词的上游口径）
3. docs/dev/rebuild/06-decision-log.md（P2/P3 与 D1-D8 是行为权威；文末 99c38ba 裁决为增量）
4. docs/dev/rebuild/06-index-validation.md（只读校验脚本）
5. docs/dev/rebuild/06-feature-ledger.md 与 06-feature-index.csv（唯一事实源；索引不覆盖决策；518 行不变）
6. docs/dev/rebuild/12-gate0-inputs.md（seam/状态表/config/event/acceptance 模板）
7. docs/dev/rebuild/06-test-disposition.csv（82 行：rebuild 80/80 + 99c38ba 新增 2 行）
8. docs/dev/rebuild/08-01-execution-readiness-audit.md、09-pg-toolcall-analysis.md、08-02-execution-readiness-reaudit.md（只读历史审计，不回改）
9. docs/dev/rebuild/08-06-final-readiness-audit.md（历史审计，结论基于 4623109，已被 08-07 取代，不能作为 handoff 依据）
10. docs/dev/rebuild/13-gate0-execution-record.md（Gate0 历史执行记录 + 99c38ba 修正记录）
11. docs/dev/evidence/INDEX.md、docs/dev/evidence/p1-ark-merge-archive/README.md、docs/changelog.md

本提示词只在独立 reviewer 明确判定“可 handoff”后使用；修复后须再由独立审计复核（05 §6.7）。

Gate 0（先做，不改业务代码）：
G0.1 基线：`git status --porcelain` 为空；`git rev-parse HEAD` 记录新 SHA；05/10 基线一致。
G0.2 上游硬门：`git fetch upstream --prune` 必须成功；`git rev-parse upstream/master` 必须记录；若不再是 99c38ba，先按 08-07 重审并更新裁决，不得离线沿用旧 ref。
G0.3 索引：跑 06-index-validation.md；518 行、ledger_line 全对、桥接 31/43/97/44/70/25/67/42/62/37、无 pending、errors=0。
G0.4 决策冲突：EXPECTED_DECISIONS 覆盖 D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D05-039 drop、D07-026 keep、D08-022 rewrite；P9-Q3 不得 drop 日志功能。
G0.5 测试处置：82 行；主 rebuild 80/80；99c38ba `tests/**/*.py` 28/28（含 conftest）；无 pending。26 是 b8af406 旧计数，不得冒充 80/80。
G0.6 Secret/evidence：只扫 docs/dev/rebuild + INDEX.md allowlist，不扫整个 docs；BOTGUARD 是 upstream 原生 grandfather，不复制/不改；spikeQ4-evidence.json/spikeQ4.py 不得进入新仓库。
G0.7 worktree/迁移：先查 `git worktree list`、路径和分支。确定性命中规则见 05 §6.4：优先复用已存在且干净的 `.kilo/worktrees/rebuild-upstream`（branch `rebuild-upstream`，必须 `merge-base(HEAD,99c38ba)=99c38ba`，工作区干净），不得再次 `worktree add`；只有路径与分支都不存在时才 `git worktree add .kilo/worktrees/rebuild-upstream -b rebuild-upstream 99c38ba`（路径不存在但分支已存在时只挂载该分支）。禁止 `rebuild/upstream`（与 `refs/heads/rebuild` 冲突）。复用旧 worktree 时先删除旧 `docs/dev/rebuild/acceptance`、`config/config.example.yaml`、`config/config.schema.json`、`tests/fixtures/events/event.sample.json`、`tests/fixtures/pg/f01..f04`，再由 Gate 0.5 按 12 §5.1 重建/验证；不得沿用旧工件。旧 worktree 的 12/13 只是参考，事实源是主工作区 docs。只在文档修复已 commit 后用 `git -C $new restore --source=rebuild -- docs/dev/rebuild`；未 commit 则显式 Copy-Item 并说明。禁止 `-- docs` 整树，禁止 checkout src/tests。迁移后确认 06-index-validation.md、10-handoff-prompt.md、12-gate0-inputs.md、06-test-disposition.csv、08-07-upstream-99c38ba-reaudit.md、13-gate0-execution-record.md、docs/dev/evidence/INDEX.md、docs/changelog.md 均存在；`git add -A` + `git status -uall`。
G0.8 归属：ledger-batches 仅历史输入（排除批次文件或保留 README 标注）；旧 WP 正文留在 rebuild，archive/INDEX/changelog 按 05 §6.5 归属。
G0.9 Gate0.5 文档：12-gate0-inputs.md、06-test-disposition.csv 已存在；acceptance/config/fixtures 必须在迁移 worktree 由 Gate 0.5 按 12 §5.1 重新创建并验证，不得沿用旧 worktree 工件。
G0.10 Handoff：本提示词已更新，能独立执行；不再依赖旧 08 行号。

Gate 0.5（P9-pre 前，输入见 12-gate0-inputs.md；必须在迁移 worktree 重新验证）：
1. 五层接口（Account/Auth、Instance、Pool/Router、Executor、Event Sink）与 Account 权威、Context/Page 工厂、surface 解析、active_requests、Event Sink。
2. 状态表：UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED + active_requests；消息唤醒、无 idle 自动回收、失败/stale 手动重启、手动显式 rebind、429/503。
3. config schema：count/surface/min_tier、api_keys、formats、keepalive、日志路径；rotator/paid_model_rules/tier_quotas/account_presets；删除浏览器 idle 超时键，timeout 默认 600；rotator 缺键 fail-fast。
4. event schema：request/attempt/usage/error/latency/surface/paid；usage 含 reasoning/cached；finish_reasons 归模型统计，account_breakdown 属 stats REST 响应，不是新事件类型；client/attempt 分母分离；P5 只扩展字段。
5. 06-test-disposition.csv 与 acceptance 模板（测试项+预期+命令+工件路径）；28/28 覆盖。
Gate 0.5 未通过，不得进入 P9-pre 编码。

Agent 工作模式（详见 05 §2.1）：
- 你是主线 integrator：唯一持有目标分支/worktree、接口、05/06/ARCHITECTURE 和提交权；负责顺序、合并、验收、changelog。
- worker 只接一个 Dxx-NNN 或明确文件域；独立 worktree；不得改 05/06/architecture/decision log；报告测试项+预期+实际。
- reviewer 只读；在 Gate0/0.5、P3 状态机、P2 路由、P5 PG、P7 统计、最终 handoff 独立审查，不参与实现。
- 并行仅限接口已冻结且文件域不重叠；最多 2-3 个 worker；禁止同时改 routing_pipeline.py、instance_pool.py、generation_service.py、05/06/ARCHITECTURE。
- Gate0/0.5、P9-pre、P1/P3/P2 核心、P5 parser、P6→P7 默认串行；P4/P8 在 API 稳定后并行。
- 不用“保持旧测试绿”复活旧契约；最终合并/提交只由 integrator 做。

顺序（D5）：
上游 fetch/记录 SHA → Gate 0 文档/索引/证据冻结 → Gate 0.5 在迁移 worktree 重新验证 → P9-pre 装配/API Key guard → P1+P3+P2 → P5 OpenAI/Gemini → P6 → P7 日志/导出 → P4 Build → P8/UI → Claude/Responses → 统计页 → P10 收尾。
每片测试先写“测试项 + 预期结果”，验收通过再进下一片。

状态机红线：
- 只用 active_requests；禁止 lease/reservation/30s/15s 预算；finally 释放。
- 控制面只做登录/保活，不算请求实例；有活动实例时 Cookie 回写走实例页。
- 消息唤醒所有合格 SLEEPING/UNSTARTED 实例；上游已移除 idle 回收/watchdog/liveness/CDP ping，不恢复；active_activity 置 disable。
- 失败/stale 才手动重启；禁止自动 stale 复活。
- rebind：固定 `READY`（`active_requests==0`）→ `RETIRING` → 关闭旧 Page/Context → `CLOSED` → 新建 Context+Page → `STARTING` → `READY`；禁止上游 `session.py:211` 的 blue-green（先建新页再 drain、`_retiring_contexts` 后台双页）；禁止后台自动 rebind、pinned/自动 rebalance。
- 429：先同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 503 + 等待/管理页提示；已出正文不切号。
- 付费模型无合格付费账号：本地 403 fast-fail，不发起上游请求；与 429 耗尽的 503 分开。
- API Key 默认拒绝，显式空 Key 才开放；upstream “无 Key 即免密”必须改写；loopback 免鉴权不保留。
- P7 日志先于统计；事件 schema 基线在 Gate 0.5 冻结（12 §4），P5 只扩展字段，P7 只聚合。
- upstream Vue 原生布局/文案/交互不得无故重写；ModelCatalog 单一所有者。

99c38ba 采用边界（详见 08-07）：
- adopt：账号 enabled/group/notes、预设 CRUD/UI、stats 新字段、wire/transport/CDP 修复、config defaults、gzip、finish_reason 继续语义、async ensure_ready。
- rewrite：软配额/tier 权重/required_tiers 抢占只能作用于现有实例候选，不得新增请求实例或后台改绑；API Key 守卫改默认拒绝；`D03-004` 删 pipe watchdog、`D03-009` 删 liveness checker/轮询、`D08-022` 只保留消息唤醒 SLEEPING/UNSTARTED 与失败/stale 手动重启；blue-green 先建新页再 drain 改为先关旧 Page/Context 再建新页。
- disable：idle 回收/watchdog/liveness/CDP ping、active_activity 旧语义、旧 groups/tag/channel 路由、blue-green 双页。
- keep：1 账号 + 1 Context + 1 Page + 1 surface；同账号 chat/build 不并存；OpenAI Chat 必做；不得为旧测试复活 lease/pinned/多页。

PG 门槛（P5 验收前）：
F-01 functionCall 缺 thoughtSignature 兜底；F-02 首事件前真实 HTTP 4xx/5xx；F-03 finish-only/工具流终止兜底；F-04 functionResponse/functionCall 强制 Struct；脱敏 [toolshape] 摘要。
上游 99c38ba 已原生修复角色守卫、结构化 functionCall/functionResponse、per-chunk usage、空 200/ malformed 归错；仍须用离线 fixture 证明 F-01..F-04，不重复造轮子。
F-05/06/07 后做，不阻塞 P5 最小闭环；F-08..F-13 在 P5/P4 前 triage。

禁止：修改测试/配置绕过契约；合并 master/rebuild；整树 checkout；离线沿用旧 upstream ref；访问真实账号/IP/Google；提交敏感内容。

验收：G0.1-G0.10 全过；G1-G11 固定出处仍为 08-05 附录 A / 08-01:129-140，并由 12 §5 模板落工件；Gate0 校验 0 错误；同账号 1 Context+1 Page；chat/build 不并存；无 lease 断言；API Key 默认拒绝；24h fake clock；日志/导出与统计口径；PG F-01..F-04 fixture 通过。
完成后报告：修改文件、测试命令/结果、未验证项、证据路径。
```
