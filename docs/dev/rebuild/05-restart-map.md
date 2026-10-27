# 05 Restart Map

状态：2026-10-27 第二轮复审后修订；基线 `rebuild @ 当前 HEAD`（交接前先提交文档修复，并用 `git rev-parse HEAD` 记录实际 SHA）。P1-P10 已全部决定，D1-D8 已按 A 确认。当前**不是直接开工状态**：先完成 Gate 0（含 Gate 0.5 输入）的文档/索引/证据冻结与重审，再把 `10-handoff-prompt.md` 交给全新 agent。

## 1. 重构大方向

目标不是继续修当前 fork，而是：

- 以 `upstream/master` 的相对干净实现为骨架；
- 新建独立分支/worktree，不合并当前 `master` 或 `rebuild`；
- 当前 fork 只作为功能参考、失败样本和证据来源；
- 按垂直切片逐项移植，不做大爆炸式合并。

核心原则：

- `tier` 保留，以上游原生解析为准，fork 不重复定义。
- `paid` 模型进 Pro/paid 池；free 模型进 free 池；QQ bot 固定 free 池。
- 请求实例固定为 `1 账号 + 1 Context + 1 Page + 1 surface`；控制面只做登录/保活，不是请求实例。
- 不再按反代途径/channel 做第一层路由；只保留 `surface=chat/build` 和模型 tier。
- 旧策略组、打分、账号 tag、quota group、死 UI 不进入新骨架。
- 并发原语只用 `active_requests`，不引入 lease/reservation。
- 日志页保留为原始事件流；统计页保留为聚合视图，后端使用单一事件模型。
- Cookie 保活保留；OAuth/refresh_token 当前不存在，本轮不做。
- 上游原生实现无必要修改则不动；Vue UI 的布局、文案、交互细节不得无故重写或删除。
- `ModelCatalog` 是模型与 paid 元数据的唯一所有者；路由、日志、统计只消费。

## 2. 决策治理

- `D01-D12`：冻结的 domain。
- `Dxx-NNN`：功能唯一 ID，见 `06-feature-index.csv`。
- `P1-P10`：决策包/移植波次，只做分组，不是事实标准。
- 每个决定必须落到 feature ID；不能只写“某 domain 保留”。
- AI 默认建议不是决定；未获明确确认前只能记为提案。
- `05` 只写方向和步骤，不复制 518 行。
- `06-feature-ledger.md` 是人类台账；`06-feature-index.csv` 是机器索引；`06-decision-log.md` 是决策记录。P2/P3 决策日志是行为权威，CSV/ledger 只是索引。
- 变更 06 后必须同步重新生成/校验 index，禁止两套各改各的；方法见 `06-index-validation.md`。

## 3. 目标骨架

只保留五个稳定层：

1. **Account/Auth**：账号身份、tier、Cookie、expired 状态。
2. **Instance**：账号会话 + 一个 Context + 一个 Page + 一个 surface；这是请求实例。
3. **Pool/Router**：free/pro 池隔离；按模型 `paid` 标记选池；sticky + 简单 LRU/FIFO。
4. **Executor**：请求生命周期与 `active_requests`；不使用 lease；不承担账号选择策略。
5. **Event Sink**：日志原始事件 + 统计聚合事件，UI 只消费稳定接口。

控制面单独定义：只负责登录、Cookie 导入/回写和保活，不占请求实例容量。账号有活动实例时，Cookie 回写走实例页；没有活动实例时才允许独立控制页。

Build 是独立 surface，不是一个 pool tier。Build 页面、9998、signer 只通过明确接口接入，不反向控制 router。

## 4. 执行前置 / 状态机（D2-D8 + 交叉前置）

| 项 | 执行规则 |
|---|---|
| D2 并发原语 | 取消 lease/reservation 和 30s/15s 预算；acquire 返回 `active_requests` 句柄。成功、失败、取消、断连、流关闭都在 `finally` 释放；有 `active_requests` 时拒绝关闭/改绑。容量按实例数计算。 |
| D3 控制面 | 控制面=登录/保活面，不算请求实例；请求实例才占池容量。账号有活动实例时 Cookie 回写走实例页，否则允许独立控制页。控制面不做蓝绿。 |
| D4 sleep/wake/rebind | 消息到达时唤醒所有合格 SLEEPING/UNSTARTED 实例，候选包含休眠/未启动账号。idle browser 自动回收默认关闭（默认不自动关）；失败/stale 才手动重启，禁止自动 stale 复活。rebind：后台自动 rebind drop；手动显式关闭旧实例再重建允许。禁止 pinned、自动 rebalance。429 先同账号重试，再唤醒/切现有实例，保存消息后切换实例再重试；耗尽 503 + 等待/管理页提示。已出正文不切号。 |
| P9-pre | 在 P1/P3/P2 前完成 lifespan/config 装配钩子：启动/关闭顺序、manager/pool/keepalive 接线、关键配置 fail-fast。 |
| API Key guard | 在 P9-pre 完成，P5 暴露端点前必须生效：默认拒绝，显式配置空 Key 才开放；loopback 免鉴权不保留。 |
| P7 顺序 | 日志页/导出先于统计页；统计事件 schema 基线在 Gate 0.5 冻结（`12-gate0-inputs.md` §4），P5 只扩展字段并实际记账，P7 只做聚合。 |
| D7 PG 门槛 | P5 验收前完成 F-01、F-02、F-03、F-04 和脱敏 `[toolshape]` 摘要；F-05/06/07 后做，不阻塞 P5 最小闭环。 |
| D8 UI/目录 | upstream Vue 原生细节优先；新 UI 做增量，不重写/删除原生布局、文案、交互。`ModelCatalog` 单一所有者。 |

D6 迁移边界：只迁移 `docs/dev/rebuild` + 脱敏 evidence 显式 allowlist（清单见 `docs/dev/evidence/INDEX.md`）；禁止 `-- docs` 整树。secret scan 只扫迁移集合，不扫整个 `docs`。旧 WP 归档规则与清单见 `docs/dev/evidence/p1-ark-merge-archive/README.md`，关键文档可留 `docs/dev/` 根。`docs/dev/rebuild/ledger-batches/` 是历史输入，不是事实源；迁移时排除其批次文件或保留其 `README.md` 明确标注。

## 5. 移植顺序（D5）

0. **Gate 0 + Gate 0.5**：文档/索引/证据冻结、上游拉取、文档迁移、独立重审；Gate 0.5 产出 seam/状态机/config/event schema/测试处置/验收模板。
1. **P9-pre**：lifespan/config 装配钩子 + API Key guard；输入固定为 `12-gate0-inputs.md` 与 `06-test-disposition.csv`。
2. **P1 + P3 + P2**：账号/Cookie/tier；单实例单 Context 单 Page；free/pro 池；路由/切号/重试。只用 `active_requests`。
3. **P5**：最小 chat 生成 + OpenAI/Gemini + API 配置，形成最小可持续运行；通过 D7 PG 门槛。
4. **P6**：凭证保活与 24h；fake clock 验证。
5. **P7**：日志页 + 导出（D09，不含统计页）。
6. **P4**：Build Surface（先 fake 9998/signer）。
7. **P8/UI**：最小 API/UI、实例 UI；upstream Vue 原生优先。
8. **Claude/Responses**。
9. **统计页**（D10）。
10. **P10**：测试/证据收尾、证据索引、changelog、文档同步。

每一片只能在前一片验收通过后进入下一片；测试处置表 `06-test-disposition.csv` 在 Gate 0.5 前建立，不能整体后置。

## 6. Gate 0：文档/索引/证据冻结 + 上游拉取 + 文档迁移

所有命令从仓库根执行；先打印状态与 worktree：

```powershell
git status --short
git rev-parse HEAD
git worktree list
git remote -v
```

### 6.0 基线提交（先于迁移）

- 先提交本轮文档修复；`git status --porcelain` 为空；执行 `git rev-parse HEAD`，把 SHA 记录到最新最终复审/changelog；`10-handoff-prompt.md` 保持“当前 HEAD + 现查”，不写回 SHA（避免自引用）。
- `git restore --source=rebuild` **只在文档修复已 commit 后使用**。若未 commit，禁止用 `--source=rebuild`（它读取的是旧 commit 树）；必须显式从工作区复制并记录来源，例如 `Copy-Item docs/dev/rebuild/* $new/docs/dev/rebuild -Recurse -Force`，evidence/changelog 也按 allowlist 从工作区复制，并在交接说明中写明“来源为 rebuild 工作区而非 commit”。
- 基线只认 `rebuild @ 当前 HEAD`；不得在 05/10 写死旧 SHA。

### 6.1 冻结文档与索引

- 运行 `06-index-validation.md` 的校验脚本：CSV 518 行、ledger 518 行、`ledger_line` 全部指向实际行、feature 文本/顺序/decision 一致。
- 核对桥接行数：P1 31、P2 43、P3 97、P4 44、P5 70、P6 25、P7 67、P8 42、P9 62、P10 37，总 518。
- 检查无 `pending`；`EXPECTED_DECISIONS` 必须覆盖 D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D05-039 drop、D07-026 keep、D08-022 keep。
- 人工复核 D05-016..019、D05-021、D05-039、D06-066 等 lease/pinned/defer/rebind 冲突已按 P2/P3 改写；`06-decision-log.md` 的 P2/P3 是行为权威。
- 冻结后 `rebuild` 的 `docs/dev/rebuild` 只读参考；新分支是唯一事实源。`ledger-batches/` 只作历史输入，不是事实源。

### 6.2 Secret scan（只扫迁移集合）

只扫描迁移集合：`docs/dev/rebuild` + `docs/dev/evidence/INDEX.md` 中的 evidence allowlist；**不扫整个 `docs`**。

```powershell
git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:|Bearer [A-Za-z0-9._-]{20,})" -- docs/dev/rebuild
# evidence 按 allowlist 逐路径扫描；不要用 -- docs 整树
$scan = @(
  "docs/dev/evidence/INDEX.md"
  "docs/dev/evidence/p1-ark-merge-archive/README.md"
  "docs/dev/evidence/p2-verify"
  "docs/dev/evidence/p3-build3/acceptance-run1.sse"
  "docs/dev/evidence/p3-build3/acceptance-run2.sse"
  "docs/dev/evidence/p5-final-20261021"
  "docs/dev/evidence/ui-p1-20261020"
  "docs/dev/evidence/wp5-20261018"
  "docs/dev/evidence/wp5b-20261019"
  "docs/dev/evidence/wp5v3-20261019"
  "docs/dev/evidence/wp6-20261020"
  "docs/dev/evidence/wp7-20261020"
  "docs/dev/evidence/wp7-v2-20261020"
  "docs/dev/evidence/wp8-20261019"
  "docs/dev/evidence/wp10-20261019/README.md"
  "docs/dev/evidence/wp11-count-20261020/README.md"
  "docs/dev/evidence/wp11-responses-20261020/README.md"
  "docs/changelog.md"
)
foreach ($item in $scan) {
  git grep -n -I -E "(AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:|Bearer [A-Za-z0-9._-]{20,})" -- $item
}
# *.log 被 .gitignore 忽略，git grep 会假阴性；用 Select-String 直接扫工作区
$ignoredLogs = @(
  "docs/dev/evidence/p2-verify/service-off.log"
  "docs/dev/evidence/p2-verify/service-prefer.log"
  "docs/dev/evidence/p2-verify/service-required.log"
  "docs/dev/evidence/p3-build3/acceptance-run1.log"
  "docs/dev/evidence/p3-build3/acceptance-run2.log"
  "docs/dev/evidence/p3-build3/service.err.log"
  "docs/dev/evidence/wp5-20261018/service-f-build.log"
  "docs/dev/evidence/wp5-20261018/service-f-quota.log"
  "docs/dev/evidence/wp5-20261018/service-p-chat.log"
  "docs/dev/evidence/wp5-20261018/service-p-guard.log"
  "docs/dev/evidence/wp5-20261018/service-p-guard2.log"
)
foreach ($item in $ignoredLogs) {
  if (Test-Path -LiteralPath $item) {
    Select-String -LiteralPath $item -Pattern "AIza[0-9A-Za-z_-]{20,}|SAPISIDHASH|authorization:|x-goog-api-key|cookie:|Bearer [A-Za-z0-9._-]{20,}"
  }
}
```

- 通过条件：迁移集合只命中占位符/说明文字（如扫描命令本身、D06-031 的 `SAPISIDHASH` 字样、`08-01-execution-readiness-audit.md` 对敏感形状的描述）；不得出现真实 key/cookie/header 值。
- `docs/BOTGUARD_VERIFICATION_CHAIN.md` 是 upstream 原生文件，按 grandfather 处理：**不复制、不改写、不计入 secret scan 失败**。
- `docs/dev/evidence/p3-build3/spikeQ4-evidence.json` 与 `spikeQ4.py` 含敏感形状，**不得进入新仓库**；只在 `rebuild` 中通过 `git show` 查阅。
- evidence 必须按 `docs/dev/evidence/INDEX.md` 的 allowlist 迁移；未列出的路径默认不迁移。

### 6.3 上游拉取（可选/后置网络步骤）

`git fetch upstream --prune` 是 Gate 0 的可选网络步骤，**不是开工阻塞**。离线时直接使用现有 `upstream/master`。

```powershell
# 固定 remote 与 base；offline 时只记录当前 SHA
git remote -v
git rev-parse upstream/master
# 有网络/代理时再执行；失败不阻塞本地 gate
git fetch upstream --prune
git rev-parse upstream/master
```

- 固定 `upstream` = DeconstructedCube；审计基线 SHA 为 `4623109`。fetch 后 SHA 若变化，记录新 SHA 并重跑相关审计。
- 不访问真实账号/IP/Google；fetch 只拉 git 元数据。

### 6.4 创建 upstream worktree

从仓库根执行，先查分支与路径；已存在则停止，不要覆盖：

```powershell
$repo = (git rev-parse --show-toplevel)
git -C $repo worktree list
Test-Path "$repo\.kilo\worktrees\rebuild-upstream"
git -C $repo worktree add .kilo/worktrees/rebuild-upstream -b rebuild/upstream upstream/master
```

- 路径必须在仓库根下解析；不要从 `.kilo/worktrees/steady-ketch` 等子目录执行。
- `rebuild/upstream` 已存在时，用 `git -C $repo worktree add .kilo/worktrees/rebuild-upstream rebuild/upstream`，不要重复 `-b`。
- 新 worktree 放到 `.kilo/worktrees/` 内，便于 VS Code 权限与清理。

### 6.5 文档迁移（只允许 allowlist）

```powershell
$repo = (git rev-parse --show-toplevel)
$new = "$repo\.kilo\worktrees\rebuild-upstream"

# 前提：6.0 已完成提交。未提交时不要执行下面这行，改用显式工作区复制。
git -C $new restore --source=rebuild -- docs/dev/rebuild

# 未提交时的等价做法（必须记录来源）：
# Copy-Item docs/dev/rebuild/* "$new\docs\dev\rebuild" -Recurse -Force

# ledger-batches 是历史输入：删除批次文件，只留说明（若已存在 README.md）
Remove-Item "$new\docs\dev\rebuild\ledger-batches\*.md" -Exclude README.md -Force -ErrorAction SilentlyContinue

# evidence 仅在 INDEX.md allowlist 内逐路径复制/restore；本仓当前不复制 spikeQ4*
$evidence = @(
  "docs/dev/evidence/INDEX.md"
  "docs/dev/evidence/p1-ark-merge-archive/README.md"
  "docs/dev/evidence/p2-verify"
  "docs/dev/evidence/p3-build3/acceptance-run1.sse"
  "docs/dev/evidence/p3-build3/acceptance-run2.sse"
  "docs/dev/evidence/p5-final-20261021"
  "docs/dev/evidence/ui-p1-20261020"
  "docs/dev/evidence/wp5-20261018"
  "docs/dev/evidence/wp5b-20261019"
  "docs/dev/evidence/wp5v3-20261019"
  "docs/dev/evidence/wp6-20261020"
  "docs/dev/evidence/wp7-20261020"
  "docs/dev/evidence/wp7-v2-20261020"
  "docs/dev/evidence/wp8-20261019"
  "docs/dev/evidence/wp10-20261019/README.md"
  "docs/dev/evidence/wp11-count-20261020/README.md"
  "docs/dev/evidence/wp11-responses-20261020/README.md"
  "docs/changelog.md"
)
foreach ($item in $evidence) {
  git -C $new restore --source=rebuild -- $item
}

# .log 被 .gitignore 忽略，git restore 不会带过去；显式从工作区复制并 force-add
$evidenceLogs = @(
  "docs/dev/evidence/p2-verify/service-off.log"
  "docs/dev/evidence/p2-verify/service-prefer.log"
  "docs/dev/evidence/p2-verify/service-required.log"
  "docs/dev/evidence/p3-build3/acceptance-run1.log"
  "docs/dev/evidence/p3-build3/acceptance-run2.log"
  "docs/dev/evidence/p3-build3/service.err.log"
  "docs/dev/evidence/wp5-20261018/service-f-build.log"
  "docs/dev/evidence/wp5-20261018/service-f-quota.log"
  "docs/dev/evidence/wp5-20261018/service-p-chat.log"
  "docs/dev/evidence/wp5-20261018/service-p-guard.log"
  "docs/dev/evidence/wp5-20261018/service-p-guard2.log"
)
foreach ($item in $evidenceLogs) {
  $dest = Join-Path $new $item
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  Copy-Item -LiteralPath $item -Destination $dest -Force
  git -C $new add -f -- $item
}

# 迁移后存在性检查（含最终复审报告）
$mustExist = @($evidence + $evidenceLogs + @("docs/dev/rebuild/08-06-final-readiness-audit.md"))
foreach ($item in $mustExist) {
  if (-not (Test-Path -LiteralPath (Join-Path $new $item))) {
    throw "Gate0 migration missing: $item"
  }
}
```

约束：

- 禁止 `git restore --source=rebuild -- docs` 整树；这会带入旧 WP、evidence 和疑似凭据，并覆盖 upstream 文档。
- 禁止 checkout `src`/`tests` 或整树 checkout。
- 不要用 fork 旧版覆盖 upstream 的 `docs/ARCHITECTURE.md`；目标架构文档在新分支单独更新。
- 旧 WP 归档规则/清单见 `docs/dev/evidence/p1-ark-merge-archive/README.md`；实际旧 WP 留在 `rebuild`，新分支只通过 `git show rebuild:<path>` 引用，不复制正文。
- archive/INDEX/changelog 归属：`docs/changelog.md` 与 `docs/dev/evidence/INDEX.md` 在新分支维护并由 Gate 0 迁移；`docs/dev/evidence/p1-ark-merge-archive/` 仅存归档规则 README，正文不进入新 worktree。
- 旧 WP 清单（正文留 `rebuild`，逻辑归档到 `docs/dev/evidence/p1-ark-merge-archive/`，新分支不复制）：`PLAN-ark-merge-20261017.md`、`PRE-P1-EXPERIMENTS-20261018.md`、`SPIKE-RESULTS-20261017.md`、`AUDIT-P2P3-20261018.md`、`FRONTEND-MERGE-20261018.md`、`HANDOFF-NEXT-PHASE-20261020.md`、`HANDOFF-WP5-UI-20261020.md`、`INSTANCE-SWITCH-SPEC-20261021.md`、`UI-CHANNEL-DESIGN-20261018.md`、`WP5-V3-DESIGN-20261019.md`、`WP5-V3-IMPLEMENTATION-PLAN-20261019.md`、`WP5-V4-DESIGN-20261020.md`、`WP6-STATS-PAGE-DESIGN-20261020.md`、`WP6-STATS-PAGE-DESIGN-BRIEF-20261020.md`、`WP7-UI-DESIGN-20261020.md`、`WP7-V2-UI-SYNC-20261020.md`、`WP8-FORMAT-LAYER-PLAN.md`（均在 `docs/dev/` 根）。

迁移后必须显式 stage 并检查 untracked，同时确认关键文件存在：

```powershell
git -C $new add -A docs/dev/rebuild docs/dev/evidence docs/changelog.md
git -C $new status -uall
git -C $new diff --cached --stat -- docs/dev/rebuild docs/dev/evidence docs/changelog.md
foreach ($f in @(
  "docs/dev/rebuild/06-index-validation.md",
  "docs/dev/rebuild/10-handoff-prompt.md",
  "docs/dev/rebuild/12-gate0-inputs.md",
  "docs/dev/rebuild/06-test-disposition.csv",
  "docs/dev/evidence/INDEX.md",
  "docs/changelog.md"
)) { if (-not (Test-Path (Join-Path $new $f))) { throw "missing: $f" } }
```

### 6.6 Gate 0.5：P9-pre 输入冻结（编码前）

在进入 P9-pre 前产出并通过评审，内容全部落在 `12-gate0-inputs.md`：

1. 目标 seam/五层接口：Account/Auth、Instance、Pool/Router、Executor、Event Sink；Account 权威、Context/Page 工厂、surface 解析、`active_requests`、Event Sink 接口。
2. 实例状态表：UNSTARTED/SLEEPING/STARTING/READY/BUSY/RETIRING/CLOSED + `active_requests`；消息唤醒、idle 默认不自动关、失败/stale 手动重启、手动显式 rebind、429/503 转换。
3. config schema：`count/surface/min_tier`、api_keys、formats、keepalive、日志路径；实例配置与全局上限分离。
4. event schema：request/attempt/usage/error/latency/surface/paid；client 与 attempt 分母分离；P5 只扩展字段，不另起账本。
5. `06-test-disposition.csv`：逐测试文件 keep/rewrite/delete + feature ID/原因；acceptance 模板含测试项、预期、命令、工件路径。

Gate 0.5 未通过，不得进入 P9-pre 编码。

### 6.7 重新独立审计

- 修复文档后，由独立审计重新检查 05/06/07/08/09/11/12 与 CSV 的一致性、Gate 0/0.5 可行性、P9-pre/状态机/顺序是否可执行。
- `08-01-execution-readiness-audit.md`、`09-pg-toolcall-analysis.md`、`08-02-execution-readiness-reaudit.md` 保留为历史审计，不回改原文；新审计结论写入新文档。
- 通过后，把 `10-handoff-prompt.md` 交给全新 agent；未通过不得进入 P9-pre 编码。

## 7. 索引校验

`06-feature-index.csv` 的 `ledger_line` 必须是 `06-feature-ledger.md` 的实际表行号，且按 domain 顺序与 feature 文本一一对应。可重复的校验脚本、预期输出和修复方法见 `06-index-validation.md`。

关键不变量：

- CSV 518 行（不含表头），ledger 518 行表项；
- 每个 domain 的 CSV 行数与 ledger 行数相同，顺序一致，feature 文本完全相同；
- CSV `ledger_line` = 对应 ledger 表行号；
- CSV `decision` = ledger 决定列；
- 无 `pending`；
- P1-P10 桥接行数与 CSV 实际计数一致。

## 8. 实时对比与逐片迁移

旧分支保留为只读参考，新分支从 `upstream/master` 起步；用两个 worktree 并行查看，用 git 对比差异，但不把旧代码整体 checkout 过来。

```powershell
git log --oneline --decorate upstream/master..rebuild
git diff --stat upstream/master...rebuild
git show rebuild:<path>
git grep -n "<symbol>" rebuild -- src tests
git diff --name-status rebuild...HEAD -- <path>
```

逐片循环：

1. 从 `06-feature-index.csv` 选一个 `Dxx-NNN`；先确认决定和验收项。
2. 用 `git show rebuild:<path>` 看旧实现，用 `git diff upstream/master...rebuild -- <path>` 看 fork 改了什么。
3. 只在新分支实现该切片；上游原生无必要修改则不动。
4. 测试先写清“测试项 + 预期结果”，再实现；旧测试只作参考。
5. 每片单独提交，并在 `docs/changelog.md` 记录可审计变更。
6. 用 `git diff --name-status rebuild...HEAD -- <path>` 和 `git grep` 检查旧实现残留；不整树合并。

## 9. 验收底线

- Gate 0：CSV/ledger 518 行且映射 0 mismatch；桥接行数一致；无 `pending`；无 decision/feature 冲突；secret scan 只扫迁移集合且只命中占位符；`docs/BOTGUARD_VERIFICATION_CHAIN.md` 按 grandfather 处理；spikeQ4 敏感证据未进入新仓库。
- 不需要真实 Google 请求即可证明：
  - paid 只进 Pro 池；free/QQ bot 有独立池；
  - 一个请求实例只有 1 Context + 1 Page；同账号不同时开 chat/build；
  - 控制面不计入请求实例容量；
  - 消息可唤醒 SLEEPING/UNSTARTED 合格账号；保存消息后切实例可重试；
  - 关闭实例不误杀其他实例；idle 自动回收默认关闭；失败/stale 才手动重启；
  - rebind 只允许手动显式关闭旧实例再重建，后台自动 rebind 不保留；
  - 429 先同账号重试→唤醒/切现有实例→保存后重试，耗尽为 503；
  - 无 lease 断言，`active_requests` 在 finally 释放。
- OpenAI Chat/Gemini 契约通过；API Key 默认拒绝，显式空 Key 才开放；token 口径与代码一致。
- 24h 回写覆盖 chat/build-only/active；保存失败不 mark；fake clock 不真实等待。
- 日志 SSE/1/3/7 天导出；client 与 attempt 分母分离；token 汇总不含 countTokens。
- PG：F-01/F-02/F-03/F-04 离线 fixture 通过，`[toolshape]` 已脱敏；F-05/06/07 有后置计划。
- upstream 原生 UI 未被无必要重写；每片有 acceptance 工件，P10 证据索引可查。

## 10. 不做的事

- 不修复当前 fork 的业务代码；
- 不合并当前 `master` 或 `rebuild`；
- 不把 `rebuild` 的 95 个提交整体移植；
- 不恢复旧策略组、打分、channel 路由、tag、quota group、lease、pinned、自动 rebind、defer；
- 上游原生实现无必要修改则不动；UI 细节、布局、文案、交互不得无故重写或删除；
- 不在风控期访问真实账号/IP/Google；
- 不写新 WP，不用长文档替代可执行验证。
