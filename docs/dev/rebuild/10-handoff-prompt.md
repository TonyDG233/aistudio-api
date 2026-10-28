# 10 Handoff Prompt

一页交接。写给下一任 agent：先读完本文，再按“必读顺序”查权威文件。

## 接手现状

- 工作分支 `rebuild`；当前 HEAD 以 `git rev-parse HEAD` 为准（A 阶段起点 `b625df9`），**未 push**；master 保持不动。
- A 阶段文档收敛已改 6 个文件（05、10、06-decision-log、06-P8-questions、06-feature-index.csv、06-feature-ledger.md），**未提交**。
- 目标 worktree `.kilo/worktrees/rebuild-upstream` @ 9b5cdea，工作区干净；其 src 相对基准 diff 为 0，docs 仍是旧版（未同步 A 阶段收敛）。
- 实际迁移代码 **0 行**；534 行只是 feature inventory，不是迁移成果。
- Gate0 / Gate0.5 **不再是独立门**；改为 A/B/C 阶段 + 每步审计。

## 目标

在 `rebuild-upstream` worktree 按 D-01..D-14 与 08-09 重建：先让最小链路真实跑通，再扩面。验收看代码与可复现证据，不看文档进度。

## 必读顺序

1. `08-09-decision-record.md` — 行为权威：D-01..D-14、17 分类、20 冲突闭合、ENGINEERING_DEFAULT、F1-F8。
2. `08-08-upstream-99c38ba-full-diff-audit.md` — 事实源：逐文件审计。
3. `05-restart-map.md` — 迁移边界与 agent 工作模式。
4. `06-decision-log.md` — P2/P3 落地；与 08-09 冲突时以 08-09 为准。
5. `06-feature-ledger.md` + `06-feature-index.csv` — inventory（534 行）。
6. `06-test-disposition.csv` — 测试处置；seam/config/event 以 06/08-09 为准，`12-gate0-inputs.md` 归历史。
7. `08-10-build-surface-incident-20261028.md` — 仅 C 阶段 P4 启动时读 §4。

`08-07` 仅历史，不得作为行为依据。

D-14 指针：API 免密只由 `security.api_allow_anonymous` 控制且默认关闭；管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空=免密）；空 API Key ≠ 开放；不引入 IP/CIDR；API Key 与 Web 密码互不复用。

## 阶段与顺序

- **A 文档收敛**：收 05/06/10，消除 06 内部 P6→P7→P5 与旧 P5→P6→P7 的冲突。A 经用户审计确认并 commit、且目标 worktree docs 同步后，才可进入 B。
- **B 实现（顺序固定）**：P9-pre 装配/鉴权 → P1+P3+P2 → **P6 保活/24h** → **P7 结构化日志/错误码/请求关联** → **P5 最小 OpenAI chat（B5）**。每步先写测试项 + 预期，实现后报实际，用户审计确认后才 commit。
- **C 扩面**：P4 Build（启动时先按 08-10 §4 做前期探索）→ P8/UI → Claude/Responses → 统计页 → P10 收尾。
- B 内部顺序不得改回 P5→P6→P7；任何改序须单独警示用户确认。

## B5 第一个集成切片

B5 不是原生路径验收：99c38ba 已有 Gemini/Responses。B5 必须经过新 account/page/routing/active_requests/guard/log seam，用 1 账号 + 1 Context + 1 Page 发 1 个 OpenAI Chat 请求并收到完整回复；空 API Key 不放行，同账号仅 1 个 active Page，无 Page/Context 泄露，请求结束后 `active_requests` 归零。OpenAI Chat 是上游没有、fork 侧新增的适配器。证据：请求/响应、账号与 Page 计数、日志或测试输出。

## Agent 与审计规则

- 主 agent 是唯一 integrator：持有目标分支/worktree、接口与提交权，负责顺序、分派、回收报告和验收。
- worker 只接一个 Dxx-NNN 或明确文件域，独立 worktree；不得改 05/06/08-09/架构文档；报告必须含测试项、预期、实际。
- reviewer 只读，在 P3 状态机、P2 路由、P5 PG、P7 统计、P4 前期探索、最终 handoff 独立审查。
- 并行仅限接口冻结且文件域不重叠，最多 2-3 个 worker；共享语义文件禁止同时修改。
- 每步闭环：测试项 + 预期 → 实现 → 实际结果 → 主 agent 自审并主动报缺陷 → 用户审计确认 → commit。用户确认前不得 commit。
- 不得把 ENGINEERING_DEFAULT 写成用户裁决。

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
- 用 08-07 作行为依据，或把 9998 重连当作 Build 400 修复。
- 在 P4 前期探索完成前写 native/transport/Q4 相关 Build 代码。
