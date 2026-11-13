# REV-2.1.3.6-versioning-and-review-audit — `2.1.3` 发布前四位审查标号规范固化与五份审查报告质量核查

- **审查对象**：`2.1.3` 发布前最后一次文档规范化收口（5 份 `REV-2.1.3.1` ~ `REV-2.1.3.5` 审查报告重命名与写法质量核查、三段式发布版本号 + 隐藏第四位审查标号制度写入 `AGENT.md` 与 `domains/INDEX.md`、`docs/changelog.md` `[2.1.3]` 章节整理）
- **审查日期**：2026-11-13
- **工作区**：`D:\LLM\self_programming\aistudio-api`
- **最终判定**：**PASS**（0 P0 / 0 P1 / 0 P2 / 0 Q / 3 N）
- **只读与白名单声明**：本次收口严格遵守精确文件读写白名单与工具白名单，仅对白名单内的 `AGENT.md`、`docs/changelog.md`、`docs/dev/rebuild/domains/INDEX.md`、`P3-pool-instance/`、`docs-and-audit/`、`P5-generation/`、`P8-api-ui/` 执行重命名与文档更新，并将本核查报告写入 `docs/dev/rebuild/domains/docs-and-audit/reviews/REV-2.1.3.6-versioning-and-review-audit.md`；**未修改任何 `src/`、`web/`、`tests/`、`waa-signer/` 代码或配置文件**；未执行 `git commit` 或 `git push`；未启动真实服务、浏览器或外网请求。

---

## 1. 发现统计

| 级别 | 数量 | 说明 |
|---|---:|---|
| **P0（阻断）** | 0 | 无 |
| **P1（重要缺陷）** | 0 | 无 |
| **P2（次要缺陷）** | 0 | 无 |
| **Q（待澄清）** | 0 | 无 |
| **N（非阻断观察）** | 3 | 见 §5（`P1-account`/`P2-routing` 白名单外旧链接提示【已同步关闭】、`REV-2.1.3.3` 两视口覆盖说明、`REV-2.1.3.5` 截图清单表格化引用） |

---

## 2. `REV-2.1.3.1` ~ `REV-2.1.3.5` 四位标号整改映射与写法质量核查

### 2.1 文件重命名与路径收敛映射（`git mv`）

| 序号 | 原路径 | 新路径（四位标号） | 归属域 | 截图资产核查 |
|---|---|---|---|---|
| **1** | `P3-pool-instance/reviews/REV-2.1.3.md` | `P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md` | `P3-pool-instance` | 不适用（纯后端） |
| **2** | `docs-and-audit/reviews/REV-domains-restructure.md` | `docs-and-audit/reviews/REV-2.1.3.2-domains-restructure.md` | `docs-and-audit` | 不适用（纯文档） |
| **3** | `P5-generation/reviews/REV-2.1.4-atg-input-wrap.md` | `P5-generation/reviews/REV-2.1.3.3-atg-input-wrap.md` | `P5-generation` | `assets/c214-atg-wrap-input-1440.png` 存在且链接有效 |
| **4** | `P5-generation/reviews/REV-2.1.5-atg-ttft-hydrogen.md` | `P5-generation/reviews/REV-2.1.3.4-atg-ttft-hydrogen.md` | `P5-generation` | `assets/c215-atg-templates-1440.png`、`assets/c215-atg-mobile-375.png` 存在且链接有效 |
| **5** | `P8-api-ui/reviews/REV-2.1.6-ui-polish-mobile.md` | `P8-api-ui/reviews/REV-2.1.3.5-ui-polish-mobile.md` | `P8-api-ui` | `assets/c216-*.png`（6 张）全部存在且索引链接有效 |

### 2.2 五份审查报告写法质量逐项核查表

| 报告标号 | 明确结论（PASS） | 完整 P0/P1/P2/Q/N 统计 | 精确 `file:line` 取证 | 门禁实跑输出 | 只读声明 | UI 截图引用 |
|---|---|---|---|---|---|---|
| **`REV-2.1.3.1`** | **合格**：§0 (`:10`)、§5 (`:137`)、R2 §6.6 (`:191`) 均明确标明 `PASS` | **合格**：R1 §4 (`:109-131`) 与 R2 §6.6 (`:193`) 完整给出 `P0=0, P1=0, P2=0 (4项已关闭), Q=2, N=3` | **合格**：§2.1–2.6 (`:35-90`) 与 R2 §6.2–6.3 (`:150-170`) 全量附带精确源码与单测行号 | **合格**：§3 (`:93-105`) 与 R2 §6.4 (`:171-181`) 实跑 Ruff/Pyright/pytest（1475→1482 passed）/`git diff --check`/`git ls-files config.yaml` | **合格**：§0 (`:9`) 与 R2 §6.1 (`:148`) | 不适用（纯后端改动） |
| **`REV-2.1.3.2`** | **合格**：文首 (`:3`) 明确标明 `PASS（0 P0 / 0 P1 / 0 P2 / 0 Q / 2 N）` | **合格**：§1 (`:10-18`) 完整统计表 | **合格**：§2.1–2.2 (`:24-48`) 精确锚定 `AGENT.md:429`、`05-restart-map.md` 与 11 子域 `index.md` 行号及 36+22 资产核对 | **合格**：§3 (`:54-89`) 实跑 `git status --short` 与 `git diff --check` | **合格**：文首 (`:6`) | 不适用（纯文档目录重组） |
| **`REV-2.1.3.3`** | **合格**：文首 (`:6`) 明确标明 `PASS` | **合格**：§4 (`:100-109`) 完整列出 P0/P1/P2/Q（均为无）与 N1..N3 | **合格**：§2.1–2.4 (`:35-77`) 覆盖四协议入口、`injector.py`、`input_wrapper.py`、`ProtocolAtgCard.vue` 与 `schema.ts` 行号 | **合格**：§5 (`:113-122`) 实跑 Ruff/Pyright/pytest（1493 passed）/web lint/type-check/`bun test`（95 pass）/`git diff --check` | **合格**：文首 (`:8`) | **合格（见 N-2）**：§3 (`:89, 96`) 实测 `1440x900` 与 `768x900` 沙盒并内嵌引用 `assets/c214-atg-wrap-input-1440.png` |
| **`REV-2.1.3.4`** | **合格**：文首 (`:6`) 明确标明 `PASS` | **合格**：§4 (`:157-169`) 完整列出 P0/P1/P2/Q（均为无）与 N1..N5 | **合格**：§2.1–2.4 (`:41-131`) 覆盖 `capture.py`、Go `runtime.go`/`transport_browser.go`、`defaults/config.yaml`、`model_discovery.py`、`atg/*` 与 `ProtocolAtgCard.vue` 行号 | **合格**：§5 (`:172-183`) 实跑 Ruff/Format/Pyright/pytest（1504 passed）/web（96 pass）/`go test`/`git diff --check` | **合格**：文首 (`:8`) | **合格**：§3 (`:135-153`) 实测 `1440x900`/`768x900`/`375x812` 并内嵌引用 `assets/c215-atg-templates-1440.png` 与 `assets/c215-atg-mobile-375.png` |
| **`REV-2.1.3.5`** | **合格**：结论段 (`:11-26`) 明确标明 `PASS（0 项 P0 / 0 项 P1 / 0 项 P2 / 0 项 Q / 3 项 N）` | **合格**：结论段 (`:21-25`) 与 §四 (`:132-152`) 完整给出 P0/P1/P2（R1 2 项已关闭）/Q/N 明细 | **合格**：§三 (`:89-128`) 逐一核对 14 个 `.vue` 文件与 `dashboardStatsUi.test.ts` 的精确行号改动 | **合格**：§一 (`:29-38`) 实跑 `git diff --check`/`git ls-files config.yaml`/`bun run lint`/`bun run type-check`/`bun test`（96 pass） | **合格**：文首 (`:7`) | **合格（见 N-3）**：§二 (`:47-86`) 实测四视口 DOM 数据并在 §2.3 清单登记 6 张 `c216-*.png`（链接登记于 `P8-api-ui/index.md`） |

**质量核查结论**：五份报告在技术事实、只读边界、门禁实跑与证据粒度上均已达标；此前存在的主要规范偏差为**版本标号管理失序**（`REV-2.1.3.md` 将部分 `2.1.3` 条目暂记入 `changelog.md` `[2.1.2]` 节；`REV-domains-restructure.md` 缺少版本标号；`REV-2.1.4`/`2.1.5`/`2.1.6` 误将第三位发布版本号递增，并在 `changelog.md` 形成孤立的 `[2.1.6]` 标题），本次已全部收口修正。

---

## 3. 文档规范化收口核验（`file:line`）

| 文档 | 变更要点 | 核验状态 |
|---|---|---|
| `AGENT.md` | §10.1 (`:428`) 更新 Reviewer 报告命名模板为 `REV-X.Y.Z.W-<slug>.md`；新增 **§10.3「版本号与审查文档四位标号规范（`X.Y.Z` + `.W`）」**（`:443-450`，定义 `0 -> 1 -> 2` 主版本、Minor 功能位、Patch 维护位及隐藏第四位审查步进位），原 §10.3/§10.4 顺延为 §10.4/§10.5（`:452-458`） | **PASS** |
| `docs/dev/rebuild/domains/INDEX.md` | §1 (`:8`) 更新审查报告命名格式；新增 **§2「版本号与审查文档四位标号制度（`X.Y.Z` + `.W`）」**（`:11-18`）；§3 总览表（`:20-34`）更新 `P3-pool-instance`（`:26`）、`P5-generation`（`:27`）、`P8-api-ui`（`:30`）与 `docs-and-audit`（`:33`）的最新审查报告链接至 `REV-2.1.3.1`、`REV-2.1.3.4`、`REV-2.1.3.5` 与 `REV-2.1.3.6` | **PASS** |
| `docs/changelog.md` | 设立独立的 **`## [2.1.3] - 2026-11-13`** 章节（`:6-29`）并按 `Added` / `Changed` / `Fixed` 条理归拢 `2.1.3` 全量变更（ATG 双独立开关与公开模板、输入侧 `wrap_input` 图文双轨、流式 TTFT 加速、502 隔离登录态失效终态与同源兄弟同步/整组隔离、思维链截断告警、`[toolshape]` 降级、`gemini-4-hydrogen` / `gemini-3.1-pro-preview` 准入与 Playground 每日配额矩阵对齐、`domains/` 双层重构与四位标号固化、全站 UI 极简对齐与移动端防挤压）；恢复 **`## [2.1.2] - 2026-11-11`**（`:31-38`）仅保留 2.1.2 已发布 4 项修复 | **PASS** |
| `P3-pool-instance/index.md` / `docs-and-audit/index.md` / `P5-generation/index.md` / `P8-api-ui/index.md` | 里程碑看板与 `reviews/` 索引全部切换为 `REV-2.1.3.1` ~ `REV-2.1.3.6` 新路径；`P5-generation/reviews/assets/c214-*`、`c215-*` 与 `P8-api-ui/reviews/assets/c216-*` 截图引用保持有效未破损 | **PASS** |

---

## 4. 门禁实跑结果

```text
$ git status --short
 M AGENT.md
 M docs/changelog.md
 M docs/dev/rebuild/domains/INDEX.md
 M docs/dev/rebuild/domains/P3-pool-instance/index.md
RM docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-2.1.3.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md
 M docs/dev/rebuild/domains/P5-generation/index.md
RM docs/dev/rebuild/domains/P5-generation/reviews/REV-2.1.4-atg-input-wrap.md -> docs/dev/rebuild/domains/P5-generation/reviews/REV-2.1.3.3-atg-input-wrap.md
RM docs/dev/rebuild/domains/P5-generation/reviews/REV-2.1.5-atg-ttft-hydrogen.md -> docs/dev/rebuild/domains/P5-generation/reviews/REV-2.1.3.4-atg-ttft-hydrogen.md
 M docs/dev/rebuild/domains/P8-api-ui/index.md
RM docs/dev/rebuild/domains/P8-api-ui/reviews/REV-2.1.6-ui-polish-mobile.md -> docs/dev/rebuild/domains/P8-api-ui/reviews/REV-2.1.3.5-ui-polish-mobile.md
 M docs/dev/rebuild/domains/docs-and-audit/index.md
RM docs/dev/rebuild/domains/docs-and-audit/reviews/REV-domains-restructure.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-2.1.3.2-domains-restructure.md
?? docs/dev/rebuild/domains/docs-and-audit/reviews/REV-2.1.3.6-versioning-and-review-audit.md

$ git diff --check
(no output, exit 0)
```

---

## 5. 非阻断观察（N）

1. **N-1（白名单外残留旧引用待顺延更新 — 已同步关闭）**：`docs/dev/rebuild/domains/P1-account/index.md:21` 与 `docs/dev/rebuild/domains/P2-routing/index.md:20` 中原引用旧路径 `P3-pool-instance/reviews/REV-2.1.3.md`，已同步更新为 `P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md`。
2. **N-2（`REV-2.1.3.3` 视口覆盖记录）**：`REV-2.1.3.3` 实测了 `1440x900` 与 `768x900` 两个视口（未单独记录 `1024x900` 测量值）；同一组件 `ProtocolAtgCard.vue` 的响应式三列/单列布局随后已在 `REV-2.1.3.4` 于 `1440x900`/`768x900`/`375x812` 补齐验证。
3. **N-3（`REV-2.1.3.5` 截图引用方式）**：`REV-2.1.3.5` §2.3 以表格列出 6 张 `c216-*.png` 实测截图，并在 `P8-api-ui/index.md` 提供了完整相对链接，未在报告正文使用 `![]()` 内嵌渲染，不影响资产可追溯性。
