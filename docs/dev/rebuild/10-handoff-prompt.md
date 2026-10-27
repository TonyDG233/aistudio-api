# 10 Handoff Prompt

复制以下提示词给全新 agent：

```text
项目路径：D:\LLM\self_programming\aistudio-api
基线：rebuild @ 当前 HEAD（不要写死旧 SHA）；upstream/master @ 4623109（离线用现有 ref，fetch 可选/后置）
交接前：先提交本轮文档修复，把 `git rev-parse HEAD` 记录到 `08-06-final-readiness-audit.md` 与 `docs/changelog.md`；10 保持“当前 HEAD + git rev-parse 现查”，不要把 SHA 写回本行（避免自引用）。若未提交，必须显式从 rebuild 工作区复制 docs/dev/rebuild，并注明来源不是 commit。
目标：从 upstream/master 新建 rebuild/upstream worktree，按 D1-D8 重建；不合并 master/rebuild。

必读（按顺序）：
1. docs/dev/rebuild/05-restart-map.md（Gate 0/0.5 流程与迁移边界）
2. docs/dev/rebuild/06-decision-log.md（P2/P3 与 D1-D8 是行为权威）
3. docs/dev/rebuild/06-index-validation.md（只读校验脚本）
4. docs/dev/rebuild/06-feature-ledger.md 与 06-feature-index.csv（唯一事实源；索引不覆盖决策）
5. docs/dev/rebuild/12-gate0-inputs.md（seam/状态表/config/event/acceptance 模板）
6. docs/dev/rebuild/06-test-disposition.csv（942 单测逐文件处置）
7. docs/dev/rebuild/08-01-execution-readiness-audit.md、09-pg-toolcall-analysis.md、08-02-execution-readiness-reaudit.md（只读历史审计，不回改）
8. docs/dev/rebuild/08-06-final-readiness-audit.md（最终复审；只有结论为可 handoff 才使用本提示词）
9. docs/dev/evidence/INDEX.md、docs/dev/evidence/p1-ark-merge-archive/README.md、docs/changelog.md

本提示词仅在 `08-06-final-readiness-audit.md` 结论为“可 handoff”后使用；修复后须再由独立审计复核（05 §6.7）。

Gate 0（先做，不改业务代码；完整条件见 08-06 §6）：
G0.1 基线：`git status --porcelain` 为空；`git rev-parse HEAD` 记录新 SHA；05/10 基线一致。
G0.2 索引：跑 06-index-validation.md；518 行、ledger_line 全对、桥接 31/43/97/44/70/25/67/42/62/37、无 pending、errors=0。
G0.3 决策冲突：EXPECTED_DECISIONS 覆盖 D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D05-039 drop、D07-026 keep、D08-022 keep；P9-Q3 不得 drop 日志功能。
G0.4 Secret/evidence：只扫 docs/dev/rebuild + INDEX.md allowlist，不扫整个 docs；BOTGUARD 是 upstream 原生 grandfather，不复制/不改；spikeQ4-evidence.json/spikeQ4.py 不得进入新仓库。
G0.5 worktree/迁移：先查 git worktree list 和目标路径；`git worktree add .kilo/worktrees/rebuild-upstream -b rebuild/upstream upstream/master`。只在文档修复已 commit 后用 `git -C $new restore --source=rebuild -- docs/dev/rebuild`；未 commit 则显式 Copy-Item 并说明。禁止 `-- docs` 整树，禁止 checkout src/tests。迁移后确认 06-index-validation.md、10-handoff-prompt.md、12-gate0-inputs.md、06-test-disposition.csv、08-06-final-readiness-audit.md、docs/dev/evidence/INDEX.md、docs/changelog.md 均存在；`git add -A` + `git status -uall`。
G0.6 归属：ledger-batches 仅历史输入（排除批次文件或保留 README 标注）；旧 WP 正文留在 rebuild，archive/INDEX/changelog 按 05 §6.5 归属。
G0.7 Gate0.5 文档：12-gate0-inputs.md、06-test-disposition.csv 与 acceptance 模板已存在并通过评审。
G0.8 Handoff：本提示词已更新，能独立执行；不再依赖旧 08 行号。

Gate 0.5（P9-pre 前，输入见 12-gate0-inputs.md）：
1. 五层接口（Account/Auth、Instance、Pool/Router、Executor、Event Sink）与 Account 权威、Context/Page 工厂、surface 解析、active_requests、Event Sink。
2. 状态表：UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED + active_requests；消息唤醒、idle 默认不自动关、失败/stale 手动重启、手动显式 rebind、429/503。
3. config schema：count/surface/min_tier、api_keys、formats、keepalive、日志路径。
4. event schema：request/attempt/usage/error/latency/surface/paid；client/attempt 分母分离；P5 只扩展字段。
5. 06-test-disposition.csv 与 acceptance 模板（测试项+预期+命令+工件路径）。
Gate 0.5 未通过，不得进入 P9-pre 编码。

顺序（D5）：
Gate 0 + Gate 0.5 → P9-pre 装配/API Key guard → P1+P3+P2 → P5 OpenAI/Gemini → P6 → P7 日志/导出 → P4 Build → P8/UI → Claude/Responses → 统计页 → P10 收尾。
每片测试先写“测试项 + 预期结果”，验收通过再进下一片。

状态机红线：
- 只用 active_requests；禁止 lease/reservation/30s/15s 预算；finally 释放。
- 控制面只做登录/保活，不算请求实例；有活动实例时 Cookie 回写走实例页。
- 消息唤醒所有合格 SLEEPING/UNSTARTED 实例；idle browser 自动回收默认关闭（不自动关）。
- 失败/stale 才手动重启；禁止自动 stale 复活。
- rebind：后台自动 rebind 不实现；手动显式关闭旧实例再重建允许；禁止 pinned/自动 rebalance。
- 429：先同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 503 + 等待/管理页提示；已出正文不切号。
- API Key 默认拒绝，显式空 Key 才开放；loopback 免鉴权不保留。
- P7 日志先于统计；事件 schema 基线在 Gate 0.5 冻结（12 §4），P5 只扩展字段，P7 只聚合。
- upstream Vue 原生布局/文案/交互不得无故重写；ModelCatalog 单一所有者。

PG 门槛（P5 验收前）：
F-01 functionCall 缺 thoughtSignature 兜底；F-02 首事件前真实 HTTP 4xx/5xx；F-03 finish-only/工具流终止兜底；F-04 functionResponse/functionCall 强制 Struct；脱敏 [toolshape] 摘要。
F-05/06/07 后做，不阻塞 P5 最小闭环；F-08..F-13 在 P5/P4 前 triage。

禁止：修改测试/配置绕过契约；合并 master/rebuild；整树 checkout；访问真实账号/IP/Google；提交敏感内容。

验收：G0.1-G0.8 全过；G1-G11 见 08 §验收清单与 12 §5 模板（不要只按旧 08 行号）；Gate0 校验 0 错误；同账号 1 Context+1 Page；chat/build 不并存；无 lease 断言；API Key 默认拒绝；24h fake clock；日志/导出与统计口径；PG F-01..F-04 fixture 通过。
完成后报告：修改文件、测试命令/结果、未验证项、证据路径。
```
