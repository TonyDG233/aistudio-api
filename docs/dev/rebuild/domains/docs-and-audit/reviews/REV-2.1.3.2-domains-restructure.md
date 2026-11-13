# REV-2.1.3.2-domains-restructure：`docs/dev/rebuild/domains/` 重构与 `AGENT.md` / `05-restart-map.md` 同步只读审查报告

- **审查结论**：**PASS**（0 P0 / 0 P1 / 0 P2 / 0 Q / 2 N）
- **审查日期**：2026-11-13
- **审查对象**：`docs/dev/rebuild/domains/` 双层治理重构、`P10-android-apk` 新建、`AGENT.md` §10.1 与 `docs/dev/rebuild/05-restart-map.md` 同步更新
- **只读声明**：本次审查严格遵守只读 Reviewer 红线，未修改任何业务代码、配置、测试或其它受保护文档；未执行 `git add` / `git commit` / `git push`；未启动服务、浏览器或任何网络请求。仅将本审查报告写入 `docs/dev/rebuild/domains/docs-and-audit/reviews/REV-2.1.3.2-domains-restructure.md`（原命名 `REV-domains-restructure.md`，已按四位审查标号规范收口重命名），并在 `docs/dev/rebuild/domains/docs-and-audit/index.md` 登记本报告链接。

---

## 1. 发现统计

| 级别 | 数量 | 说明 |
|---|---:|---|
| **P0（阻断）** | 0 | 无 |
| **P1（重要缺陷）** | 0 | 无 |
| **P2（次要缺陷）** | 0 | 无 |
| **Q（待澄清）** | 0 | 无 |
| **N（非阻断观察）** | 2 | 见 §4（历史留档路径原文与 `upstream-merges/index.md` 格式差异） |

---

## 2. 逐项 `file:line` 核验表

### 2.1 五项核心重构目标核验

| 核验项 | 判定 | `file:line` 证据 |
|---|---|---|
| **1. `B-docs-and-audit` 去前缀并定位为文档与根 `.md` 专属域** | **PASS** | `git status --short` 显示 `B-docs-and-audit/index.md` 及 5 份 `REV-B7-*.md` 均已重命名迁移至 `docs/dev/rebuild/domains/docs-and-audit/`；原 `B-docs-and-audit/` 目录已不存在；`docs/dev/rebuild/domains/docs-and-audit/index.md:1-15` 明确域定位为专门针对 `docs/` 与仓库根目录 `.md`（`AGENT.md`、`README.md`、`README_EN.md`）变更的任务计划看板与审计留档域。 |
| **2. 重复 `P` 标号目录与空壳 `_template` 归并清理** | **PASS** | `git status --short` 与目录枚举（`docs/dev/rebuild/domains/` 共 12 个条目：`INDEX.md` + 11 子域目录）确认：<br>• `P3-browser/reviews/` 下全部 5 份 `REV-*.md` 已迁入 `P3-pool-instance/reviews/`，`.gitkeep` 已删，`P3-browser/` 移除（`P3-pool-instance/index.md:3`）；<br>• `P7-logs/reviews/REV-B4-p7-logs.md` 已迁入 `P7-logs-stats/reviews/`，`.gitkeep` 已删，`P7-logs/` 移除（`P7-logs-stats/index.md:3`）；<br>• `P10-tests-evidence/reviews/REV-C5a.md` 已迁入 `docs-and-audit/reviews/`，`P10-tests-evidence/` 移除；<br>• `_template/.gitkeep` 已删，`_template/` 移除。 |
| **3. 新建专属 APK 域 `P10-android-apk/`** | **PASS** | `docs/dev/rebuild/domains/P10-android-apk/index.md:1-33` 已创建，包含 Stage 2.2.0 架构红线（禁用桌面浏览器/Termux/proot、NDK `libwaa_signer.so` 路线 A、嵌入式 Python 锁定 `engine.type=browserless`、`MediaSession` + 静音 PCM + `PARTIAL_WAKE_LOCK` 无感保活、WebView 仅采凭据隔离）及 M1–M4 任务拆解看板；`docs/dev/rebuild/domains/P10-android-apk/reviews/.gitkeep` 占位就位。 |
| **4. 总索引 `INDEX.md` 与 11 子域双层结构完整性** | **PASS** | `docs/dev/rebuild/domains/INDEX.md:1-25` 定义双层治理规范与 11 个子域总览表；11 个子域目录根下均存在 `index.md`，表内全部 `根计划与看板` 与 `最新审查报告` 相对链接均指向真实存在文件（`INDEX.md:15-25`）。 |
| **5. `AGENT.md` 与 `05-restart-map.md` 同步更新** | **PASS** | `AGENT.md:429` 已补充「域双层治理与总索引」规范，精准指向 `docs/dev/rebuild/domains/INDEX.md`、域根 `index.md`、`reviews/` 与 `docs/dev/rebuild/domains/docs-and-audit/`；`docs/dev/rebuild/05-restart-map.md:16,61-62,103-106,124-126` 均同步使用 `domains/INDEX.md`、`domains/<Domain>/index.md`、`domains/<Domain>/reviews/`、`domains/docs-and-audit/` 与 `domains/P5-generation/09-pg-toolcall-analysis.md`，全文件 grep 未命中任何 `B-docs-and-audit`/`P3-browser`/`P7-logs/`/`P10-tests-evidence`/`_template` 残留。 |

### 2.2 全域 11 子域文件与资产完整性核验

| 子域目录 | 根 `index.md` | `reviews/` 下 `REV-*.md`（数量与核对） | `reviews/assets/*.png`（数量与核对） | `index.md` 索引覆盖 |
|---|---|---|---|---|
| `P1-account` | `index.md:1-28` | 2 份：`REV-B2-1-p1-account.md`、`REV-C1b-vps-tier-and-403-fix.md`（+ `.gitkeep`） | 0 | 全覆盖（`index.md:26-27`） |
| `P2-routing` | `index.md:1-26` | 1 份：`REV-B2-4-p2-routing.md`（+ `.gitkeep`） | 0 | 全覆盖（`index.md:25`） |
| `P3-pool-instance` | `index.md:1-40` | 7 份：原 `P3-browser` 迁入 5 份（`REV-B2-2`、`REV-B2-3`、`REV-merge-99600c7`、`REV-C1`、`REV-C1d`）+ 原有 2 份（`REV-2.1-A`、`REV-2.1.3.1-session-expired-sibling-sync`［原 `REV-2.1.3`］） | 0 | 全覆盖（`index.md:33-39`） |
| `P5-generation` | `index.md:1-47` | 7 份：`REV-B5a`、`REV-B5b`、`REV-B6-{discovery-hardening,live-verification,stream-and-429-fix}`、`REV-C1c`、`REV-C3`（+ `.gitkeep` 与 `09-pg-toolcall-analysis.md`） | 0 | 全覆盖（`index.md:25-31`） |
| `P6-keepalive` | `index.md:1-21` | 1 份：`REV-B3-p6-keepalive.md`（+ `.gitkeep`） | 0 | 全覆盖（`index.md:20`） |
| `P7-logs-stats` | `index.md:1-36` | 3 份：原 `P7-logs` 迁入 1 份（`REV-B4-p7-logs.md`）+ 原有 2 份（`REV-C4`、`REV-C4b`） | 6 张：`c4-dashboard-*.png`（2）+ `c4b-settings-tooltip-*.png`（4） | 全覆盖（`index.md:26-36`） |
| `P8-api-ui` | `index.md:1-41` | 7 份：`REV-C2{a,b,c,d}.md`、`REV-2.1-B.md`、`REV-2.1.1.md`、`REV-2.1.2.md` | 16 张：`c2b-*`（2）、`c2c-*`（2）、`c2d-*`（3）、`c21b-*`（6）、`c211-*`（2）、`c212-*`（1） | 全覆盖（`index.md:27-41`） |
| `P9-boot` | `index.md:1-27` | 1 份：`REV-B1-p9-pre.md`（+ `.gitkeep`） | 0 | 全覆盖（`index.md:26`） |
| `P10-android-apk` | `index.md:1-33` | 0 份（规划中，`reviews/.gitkeep` 就位） | 0 | 一致（`index.md:31-33`） |
| `docs-and-audit` | `index.md:1-130` | 6 份（迁移后原文件）：5 份 `REV-B7-*.md` + `REV-C5a.md`（+ 本报告 `REV-2.1.3.2-domains-restructure.md`） | 0 | 全覆盖（`index.md:15,20-26`） |
| `upstream-merges` | `index.md:1-76` | 1 份：`REV-MERGE-852de53-oct31.md` | 0 | 已引用（`index.md:70`） |

**迁移零丢失确认**：全仓共 **36** 份既有 `REV-*.md` 报告与 **22** 张 `assets/*.png` 截图；`git status --short` 中仅存在 3 个空壳目录的 `.gitkeep` 删除记录（`P3-browser/reviews/.gitkeep`、`P7-logs/reviews/.gitkeep`、`domains/_template/.gitkeep`），没有任何历史 `REV-*.md` 或 `assets/*.png` 被删除或遗漏。

---

## 3. 必跑门禁命令与实际输出

```text
$ git status --short
 M AGENT.md
 M docs/dev/rebuild/05-restart-map.md
D  docs/dev/rebuild/domains/P3-browser/reviews/.gitkeep
R  docs/dev/rebuild/domains/P3-browser/reviews/REV-B2-2-p3-d03-browser.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-B2-2-p3-d03-browser.md
R  docs/dev/rebuild/domains/P3-browser/reviews/REV-B2-3-p3-d04-pool.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-B2-3-p3-d04-pool.md
R  docs/dev/rebuild/domains/P3-browser/reviews/REV-C1-fingerprint-and-auth-noise.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-C1-fingerprint-and-auth-noise.md
R  docs/dev/rebuild/domains/P3-browser/reviews/REV-C1d-waa-snapshot-warmup-fix.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-C1d-waa-snapshot-warmup-fix.md
R  docs/dev/rebuild/domains/P3-browser/reviews/REV-merge-99600c7.md -> docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-merge-99600c7.md
 M docs/dev/rebuild/domains/P5-generation/index.md
R  docs/dev/rebuild/domains/P7-logs/reviews/REV-B4-p7-logs.md -> docs/dev/rebuild/domains/P7-logs-stats/reviews/REV-B4-p7-logs.md
D  docs/dev/rebuild/domains/P7-logs/reviews/.gitkeep
D  docs/dev/rebuild/domains/_template/.gitkeep
RM docs/dev/rebuild/domains/B-docs-and-audit/index.md -> docs/dev/rebuild/domains/docs-and-audit/index.md
R  docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-B7-architecture-coupling-audit.md
R  docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-commit-history-audit.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-B7-commit-history-audit.md
R  docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-config-sync-and-green-tests.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-B7-config-sync-and-green-tests.md
R  docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-docs-and-audit-summary.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-B7-docs-and-audit-summary.md
R  docs/dev/rebuild/domains/B-docs-and-audit/reviews/REV-B7-p0-decoupling-and-vps-fix.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-B7-p0-decoupling-and-vps-fix.md
R  docs/dev/rebuild/domains/P10-tests-evidence/reviews/REV-C5a.md -> docs/dev/rebuild/domains/docs-and-audit/reviews/REV-C5a.md
?? docs/dev/rebuild/domains/INDEX.md
?? docs/dev/rebuild/domains/P1-account/index.md
?? docs/dev/rebuild/domains/P10-android-apk/
?? docs/dev/rebuild/domains/P2-routing/index.md
?? docs/dev/rebuild/domains/P3-pool-instance/index.md
?? docs/dev/rebuild/domains/P6-keepalive/index.md
?? docs/dev/rebuild/domains/P7-logs-stats/index.md
?? docs/dev/rebuild/domains/P8-api-ui/index.md
?? docs/dev/rebuild/domains/P9-boot/index.md

$ git diff --check
(no output, exit 0)
```

---

## 4. 非阻断观察（N）

1. **N-1（历史留档原文路径）**：`docs/dev/rebuild/domains/docs-and-audit/index.md:52,117` 位于 `## B7 历史基线记录（2026-11-01 留档）` 分隔线下方，与历史 `REV-B7-*.md`、`REV-B4-p7-logs.md`、`REV-C1-*.md` 等报告中的原始产出路径（`B-docs-and-audit/`、`P3-browser/`、`P7-logs/`）一样属于既成历史快照描述；域定位与看板头（`:1-25`）已完全切换为 `docs-and-audit`。
2. **N-2（`upstream-merges/index.md` 格式差异）**：`docs/dev/rebuild/domains/upstream-merges/index.md:70` 已以行内文本链接 `reviews/REV-MERGE-852de53-oct31.md`，功能完整，但未单独设立与其余域对齐的 `## reviews/ 审查报告索引` 小节，后续增量维护时可按需补齐。
