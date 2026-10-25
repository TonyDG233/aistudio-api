# 05 Restart Map

状态：2026-10-24 重写。本文只保留重构方向、决策规则和大致步骤；逐功能决定在 `06-feature-ledger.md`、`06-feature-index.csv`、`06-decision-log.md`。当前进度：P1-P10 已全部决定；下一步先更新架构文档，再做多实例/路由/保活/日志。

## 1. 重构大方向

目标不是继续修当前 fork，而是：

- 以 `upstream/master` 的相对干净实现为骨架；
- 新建独立分支/worktree，不合并当前 `master` 或 `rebuild`；
- 当前 fork 只作为功能参考、失败样本和证据来源；
- 按垂直切片逐项移植，不做大爆炸式合并。

核心原则：

- `tier` 保留，以上游原生解析为准，fork 不重复定义。
- `paid` 模型进 Pro/paid 池；free 模型进 free 池；QQ bot 固定 free 池。
- 一个 Context 只放一个 Page；同账号 chat/build 需要两个 Context。
- 不再按反代途径/channel 做第一层路由；只保留 `surface=chat/build` 和模型 tier。
- 旧策略组、打分、账号 tag、quota group、死 UI 不进入新骨架。
- 日志页保留为原始事件流；统计页保留为聚合视图，后端使用单一事件模型。
- Cookie 保活保留；OAuth/refresh_token 当前不存在，本轮不做。

## 2. 决策治理

- `D01-D12`：冻结的 domain。
- `Dxx-NNN`：功能唯一 ID，见 `06-feature-index.csv`。
- `P1-P10`：决策包/移植波次，只做分组，不是事实标准。
- 每个决定必须落到 feature ID；不能只写“某 domain 保留”。
- AI 默认建议不是决定；未获明确确认前只能记为提案。
- `05` 只写方向和步骤，不复制 515 行。
- `06-feature-ledger.md` 是人类台账；`06-feature-index.csv` 是机器索引；`06-decision-log.md` 是决策记录。
- 变更 06 后必须同步重新生成 index，禁止两套各改各的。

## 3. 目标骨架

建议只保留五个稳定层：

1. **Account/Auth**：账号身份、tier、Cookie、expired 状态。
2. **Instance**：账号会话 + 一个 Context + 一个 Page + 一个 surface。
3. **Pool/Router**：free/pro 池隔离；按模型 `paid` 标记选池。
4. **Executor**：请求生命周期与 `active_requests`；不使用 lease；不承担账号选择策略。
5. **Event Sink**：日志原始事件 + 统计聚合事件，UI 只消费稳定接口。

Build 是独立 surface，不是一个 pool tier。Build 页面、9998、signer 只通过明确接口接入，不反向控制 router。

## 4. 移植顺序

0. 冻结 P1-P10 的逐项决定。
1. 新分支先更新 `ARCHITECTURE.md` 与 upstream 扩展缝文档。
2. P1 基础 + P3 多实例/Context/Page + P2 路由/重试。
3. P6 凭证保活与 24h。
4. P7 日志页 + 导出（D09，不含统计页）。
5. P5 最小 chat 生成 + OpenAI Chat Completions + API 配置，形成最小可持续运行；OpenAI 是这一步的前置硬需求。
6. P4 Build Surface。
7. Claude Messages（Build 之后）。
8. 其他协议/渠道、P8 UI。
9. P7 统计页（D10）。
10. P9 Boot/Config、P10 Tests/Evidence。

每一片只能在前一片的验收通过后进入下一片。

## 5. 干净迁移与实时对比

可以做，而且应该作为标准流程：旧分支保留为只读参考，新分支从 `upstream/master` 起步；用两个 worktree 并行查看，用 git 对比差异，但不把旧代码整体 checkout 过来。

### 5.1 分支/worktree 布局

- `rebuild`：当前完整 fork 与决策文档，只读参考。
- `upstream/master`：新骨架基线。
- `rebuild/upstream`：从 upstream/master 新建的干净目标分支。
- 当前工作区继续保留 `rebuild`；新分支放到独立目录，避免切分支破坏文档。

```powershell
git fetch upstream --prune
git worktree add ..\aistudio-api-upstream -b rebuild/upstream upstream/master
```

### 5.2 文档迁移

只迁移文档，不迁移代码：

```powershell
git -C ..\aistudio-api-upstream restore --source=rebuild -- docs
```

也可以只迁移 `docs/dev/rebuild`。迁移后先检查：

```powershell
git -C ..\aistudio-api-upstream status --short
git -C ..\aistudio-api-upstream diff --stat -- docs
```

约束：

- 禁止 `git checkout rebuild -- src`、`git checkout rebuild -- tests` 或整树 checkout。
- 文档中的 evidence 必须脱敏；原始 auth/cookie/key 不进入新仓库。
- 旧 WP 按 P10 归档到 `docs/dev/evidence/p1-ark-merge-archive/`，不把旧规划正文当新设计。

### 5.3 实时对比命令

```powershell
git log --oneline --decorate upstream/master..rebuild
git diff --stat upstream/master...rebuild
git diff upstream/master...rebuild -- <path>
git show rebuild:<path>
git blame rebuild -- <path>
git range-diff upstream/master...rebuild
git diff --no-index <old-worktree>\<path> <new-worktree>\<path>
```

查旧实现/符号：

```powershell
git grep -n "<symbol>" rebuild -- src tests
git log -S "<symbol>" --oneline rebuild -- <path>
git diff --name-status rebuild -- <path>
```

### 5.4 逐片迁移循环

1. 从 `06-feature-index.csv` 选一个 `Dxx-NNN`；先确认决定和验收项。
2. 用 `git show rebuild:<path>` 看旧实现，用 `git diff upstream/master...rebuild -- <path>` 看 fork 改了什么。
3. 只在新分支实现该切片；上游原生无必要修改则不动。
4. 测试先写清“测试项 + 预期结果”，再实现；旧测试只作参考。
5. 每片单独提交，并在 `docs/changelog.md` 记录可审计变更。
6. 用 `git diff --name-status rebuild -- <path>` 和 `git grep` 检查是否有旧实现残留；不整树合并。

### 5.5 安全边界

- 不 merge/cherry-pick 整条 `rebuild` 历史；需要时只按 feature ID 手工移植。
- 不把旧 `master` 合并进新分支。
- 不在风控期访问真实账号/IP/Google。
- 旧分支 `rebuild` 保持只读，所有新提交只进 `rebuild/upstream`。

## 6. 验收底线

- 不需要真实 Google 请求即可证明：
  - paid 只进 Pro 池；
  - free/QQ bot 有独立池；
  - 一个 Context 只有一个 Page；
  - 同账号不同时开 chat/build；需要并发时使用不同账号；
  - 关闭实例不误杀其他实例；
  - 429/auth/transient 错误的重试路径符合新契约。
- 统计页能回答：哪个账号、模型、surface、尝试、错误、用量、延迟。
- 日志页保留原始事件，不把统计聚合混进日志总线。

## 7. 不做的事

- 不修复当前 fork 的业务代码；
- 不合并当前 `master`；
- 不把 `rebuild` 的 95 个提交整体移植；
- 不恢复旧策略组、打分、channel 路由、tag、quota group；
- 上游原生实现无必要修改则不动；UI 细节、布局、文案、交互不得无故重写或删除；
- 不在风控期访问真实账号/IP/Google；
- 不写新 WP，不用长文档替代可执行验证。
