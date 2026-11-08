# 10 Handoff Prompt

一页交接。写给下一任 agent：先读完本文，再按“必读顺序”查权威文件。

## 接手现状

- 工作分支 `rebuild`；当前 HEAD 以 `git rev-parse HEAD` 为准；A1-A6 已提交，A7 进行中；**未 push**；master 保持不动。
- A5 已把旧审计/Gate/问题清单移入 `docs/dev/rebuild/archive/`；A6 已把权威 docs 同步到目标 worktree，均随 A5/A6 提交。
- 目标 worktree `.kilo/worktrees/rebuild-upstream`（HEAD 以 `git rev-parse HEAD` 为准）；其 src 相对基准 diff 为 0，docs 为 A5/A6 同步版。
- 实际迁移代码 **0 行**；534 行只是 feature inventory，不是迁移成果。
- Gate0 / Gate0.5 **不再是独立门**；改为 A/B/C 阶段 + 每步审计。

## 目标

在 `rebuild-upstream` worktree 按 D-01..D-14 与 08-09 重建：先让最小链路真实跑通，再扩面。验收看代码与可复现证据，不看文档进度。

## 必读顺序

1. `08-09-decision-record.md` — 行为权威：仅 D-01..D-14；非 D 历史见 `archive/08-09-agent-record-20261028.md`。
2. `08-08-upstream-99c38ba-full-diff-audit.md` — 事实源：逐文件审计。
3. `05-restart-map.md` — 迁移边界与 agent 工作模式。
4. `06-decision-log.md` — P2/P3 落地；与 08-09 冲突时以 08-09 为准。
5. `06-feature-ledger.md` + `06-feature-index.csv` — inventory（534 行）。
6. `06-test-disposition.csv` — 测试处置；seam/config/event 以 06/08-09 为准，`archive/12-gate0-inputs.md` 归历史。
7. `domains/P5-generation/index.md` + `09-pg-toolcall-analysis.md` — P5 早期约束与 PG/toolcall/preset 详情；D7 的 PG-01..PG-04 是门槛。
8. `incidents/INC-20261028-01-build-400.md` — 仅 C 阶段 P4 启动时读 §4。

`archive/08-07-upstream-99c38ba-reaudit.md` 仅历史，不得作为行为依据。

D-14 指针：API 免密只由 `security.api_allow_anonymous` 控制且默认关闭；管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空=免密）；空 API Key ≠ 开放；不引入 IP/CIDR；API Key 与 Web 密码互不复用。

## 阶段与顺序

- **A 文档收敛**：A1-A6 已提交；A7 做 agent 工作流准备（任务包、A→B 检查、incident、真实测试闸门），A7 经审计后才可进入 B。
- **B 实现（顺序固定）**：P9-pre 装配/鉴权 → **B0 目标 `AGENT.md` 更新并经用户审计** → P1(D02) → P3-D03 浏览器/CDP/Context → P3-D04 多实例池 → P2(D05) 路由 → **P6 保活/24h** → **P7 结构化日志/错误码/请求关联** → **B5a PG/toolcall/preset 复现** → **B5b P5 最小 OpenAI chat**。每步先写测试项 + 预期，实现后报实际，用户审计确认后才 commit。
- **C 扩面**：P4 Build（启动时先按 `INC-20261028-01` §4 做前期探索）→ P8/UI → Claude/Responses → 统计页 → P10 收尾。
- 每个 Step 的范围与退出条件见 `05 §4.1`；前一 Step 审计未通过不得进入下一 Step。
- B 内部顺序不得改回 P5→P6→P7；任何改序须单独警示用户确认。

B5a/B5b 的范围与退出条件见 `05 §4.1`；B5a 的排查细节见 `09`。B5a 未过不得进入 B5b；B5b 不替代 B 阶段其他步骤。文档布局与每步不新建文档的规则见 `05 §5`。

## Agent 与审计规则

- 主 agent 是唯一 integrator：持有目标分支/worktree、接口与提交权，负责顺序、分派、回收报告和验收。
- worker/reviewer 的报告先原样交用户；用户审计前，主 agent 不直接改报告/代码/文档，也不自行派下一步。
- worker 只接一个 Dxx-NNN 或明确文件域，独立 worktree；不得改 05/06/08-09/架构文档；报告必须含测试项、预期、实际。
- reviewer 只读，在 P3 状态机、P2 路由、P5 PG、P7 统计、P4 前期探索、最终 handoff 独立审查。
- 并行仅限接口冻结且文件域不重叠，最多 2-3 个 worker；共享语义文件禁止同时修改。
- 每步闭环：测试项 + 预期 → 实现 → 实际结果 → 主 agent 自审并主动报缺陷 → 用户审计确认 → commit。用户确认前不得 commit。
- 不再预写纸面 PKG；旧纸面批次草稿与初审已归档至 `archive/batch-drafts-20261029/`，仅作历史留底，严禁作为施工依据。直接按 `06`/`08-09`/台账/测试表，参考主仓 `rebuild` 移植；单步问题只做当前步骤内局部返工，不推翻整体或重写 `05/06/08-09`。
- 不得把 ENGINEERING_DEFAULT 写成用户裁决。

## A7 工作流材料

- 任务包模板：`domains/_template/PKG.md`；审查模板：`domains/_template/REV.md`。
- 每个任务包必须含：ID、文件域、依赖、测试、命令、证据、失败停止、回滚。
- 审查报告必须含：结论 PASS/CONDITIONAL/FAIL、发现 file:line、验收门槛、给用户的裁决项、只读声明。
- 任务包和审查报告分别放 `domains/Px/packages/`、`domains/Px/reviews/`，按包一一对应。
- A→B 检查：A5/A6/A7 已审计提交；用户批准 B 计划；基线对账；任务包已审；测试/证据目录就绪；真实测试授权已单独获得。
- 真实测试闸门：环境配置核验通过后才可申请；申请含目标、次数、账号/IP、预计时长、证据路径、风控限制；用户实时审计并控制短时次数。
- 事故按 `incidents/README.md` 与 `INC-YYYYMMDD-nn` 处理，不塞进 534，不另造 Gate。
- 本仓库不改 `AGENT.md`；到干净目标仓库正式进入 B 后再更新。

## 停止条件

遇到以下情况停下问用户，不得自行决定：

- 需要改 06/08-09 的 D-xx 决策或路线图顺序；
- 需要真实账号、真实 IP、Google 真实接口或生产授权；
- 需要 merge、push、改写历史或其他破坏性 git 操作；
- 权威文档与仓库现状冲突且现有证据无法判定；
- 需要新增文档、扩大范围或引入新依赖。

## 禁止事项

- 合并 master/rebuild、push、整树 checkout。
- 恢复 Gate0/Gate0.5 为独立门，或重建 G0.x 计划体系。
- 把 534 行 inventory 当作迁移成果，或用文档进度替代实现。
- 为通过测试修改测试/配置/契约。
- 把 roadmap 改回 P5→P6→P7。
- 用 `archive/08-07` 作行为依据，或把 9998 重连当作 Build 400 修复。
- 在 P4 前期探索完成前写 native/transport/Q4 相关 Build 代码。
