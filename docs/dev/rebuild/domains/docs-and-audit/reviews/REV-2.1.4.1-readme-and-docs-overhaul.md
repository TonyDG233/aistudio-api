# REV-2.1.4.1 — README 重写与 9 份核心文档契约一致性独立只读审查报告

- **审查结论**：**PASS**（复审通过：初审指出的 `P1-1`、`P2-1`、`P2-2`、`P2-3`、`Q-1` 已全部核验关闭，0 P0 / 0 P1 / 0 P2 阻塞）
- **初审日期**：2026-11-15（原结论：`CONDITIONAL`）
- **复审日期**：2026-11-15（最终结论：**`PASS`**）
- **审查范围（9 份目标文档 + 对照基线）**：
  - `README.md`、`README_EN.md`（对照 `upstream/master:README.md`）
  - `waa-signer/README.md`
  - `docs/ARCHITECTURE.md`
  - `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md`
  - `docs/PROTOCOL_AND_TOOLCALL_SPEC.md`
  - `docs/KEEPALIVE_AND_LOGGING_SPEC.md`
  - `docs/BOTGUARD_VERIFICATION_CHAIN.md`
  - `docs/WIRE_SPECIFICATION.md`
  - 对照基准：`defaults/config.yaml` 与仓库源码/配置
- **100% 只读声明**：本审查全程以只读方式核对目标文档与仓库基准，**未修改任何业务文档、`README` 或代码文件**，**未启动真实服务或连接真实 Google 账号**，**未执行 `git commit` / `git push`**；仅按审查白名单写入本报告及 `docs/dev/rebuild/domains/docs-and-audit/index.md`、`docs/dev/rebuild/domains/INDEX.md`。

---

## 1. 核查结果总览

### 1.1 `README.md` 与 `README_EN.md`（对照 `upstream/master:README.md`）

| 核查项 | 结果 | 证据与说明 |
|---|---|---|
| 骨架对齐与去冗 | **PASS** | `README.md`（578 行）与 `README_EN.md`（579 行）均采用与 `upstream/master:README.md` 一致的 10 节清爽骨架（徽章/导语 → 目录 → 核心特性 → 技术架构与文档 → 快速开始 → 账号凭据获取与导入 → 第三方客户端配置 → 配置与环境变量 → API 调用示例 → Web 管理控制台 → 开发与测试 → 借物表与致谢 → 开源协议）；原 `HEAD:README.md` 开头 270 行重构黑话及末尾重复“相关文档”节已彻底清除，`README.md:102` / `README_EN.md:103` 即进入「快速开始」。 |
| 目录（TOC）锚点匹配 | **PASS** | `README.md:24-41`（17 个锚点）与 `README_EN.md:24-41`（17 个锚点）逐一按 GFM Slug 规则核对，与正文各级标题 100% 匹配可用。 |
| 相对文件链接存在性 | **PASS（P2-1 已关闭）** | `./README_EN.md`、`./README.md`、`./docs/{ARCHITECTURE,INSTANCE_POOL_AND_ROUTING_SPEC,PROTOCOL_AND_TOOLCALL_SPEC,KEEPALIVE_AND_LOGGING_SPEC,WIRE_SPECIFICATION,BOTGUARD_VERIFICATION_CHAIN,WAA_VM_ARCHITECTURE,changelog}.md`、`./AGENT.md`、`./waa-signer/README.md`、`defaults/config.yaml`、`scripts/{setup-env.sh,setup-browser.sh,install-browser.bat}`、`waa-signer/build.{sh,bat}` 全部存在；`README.md:9,578` 与 `README_EN.md:9,579` 已改指 `https://opensource.org/license/mit`，消除断链（P2-1 已关闭）。 |
| 快速开始 / 凭据导入 / 客户端指引 | **PASS** | 涵盖 Docker（`ghcr.io/tonydg233/aistudio-api:latest`，无旧 `:2.1.0` 标签残留）、Termux、Windows、Linux/macOS 四平台；包含 Tampermonkey 油猴脚本（`/api/tools/aistudio-cookie-grabber.user.js`，已核实 `src/aistudio_api/api/routes_system.py:214`）导出 `.json` + Cookie-Editor + 管理台创建 API Key；第三方客户端配置覆盖 OpenAI / Gemini / Claude / Responses 四协议；常用模型名与 `defaults/config.yaml:209-215` 一致。 |
| 中英文对等一致性 | **PASS（Q-1 已关闭）** | `README.md` 与 `README_EN.md` 章节顺序、表格列项、命令、环境变量默认值完全对等；`README.md:51`（“仅 Free / 仅 Pro+ / 全部 / 指定分组”）与 `README_EN.md:51`（“Free-only / Paid-only / all / custom groups”）已同步补齐“全部 / all”。 |

### 1.2 6 份 `docs/*.md` 与 `waa-signer/README.md` 契约及配置一致性

| 核查项 | 结果 | 证据与说明 |
|---|---|---|
| 旧表述清理 | **PASS（P1-1 已关闭）** | 1. “429 先同账号重试”：`docs/ARCHITECTURE.md:322,556`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:540,583,648,858,880,1005` 及复审后的 `docs/KEEPALIVE_AND_LOGGING_SPEC.md:991`（已改写为“429 切号或 403/瞬态错误重试成功后……”）已全部清理对齐。<br>2. “`select_rebind_account` 按 `tier_rank` 第一优先级”：`docs/ARCHITECTURE.md:315,340` 与 `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:622` 均已更新为 `(last_used, tier_rank, total_requests, model_requests_total)`（`last_used` LRU 优先）。<br>3. “`go 1.23` 桥接 / `.modfile`”：`waa-signer/README.md:22-27,62` 已全面更新为 `go 1.27` + 原生 `go.mod`、`GOTOOLCHAIN=auto`（构建阶段 `GOTOOLCHAIN=local`），无旧表述残留。<br>4. `docs/dev/evidence/` 临时路径：`docs/WIRE_SPECIFICATION.md:361,454` 已改写为通用验证描述；9 份文档中 `docs/dev/evidence/` 命中数为 0。 |
| 配置键名与默认值对照 `defaults/config.yaml` | **PASS** | 已逐项核对：`proactive_switch_mode`（默认 `"off"`，`off/free_only/paid_only/all/groups`）、`proactive_switch_threshold`（默认 `15`）、`proactive_switch_groups`（默认 `[]`）、`switch_backoff_sec`（默认 `4.0`，`±25%` 抖动）、`retry_same_delay_sec`（默认 `2.0`，仅用于上游瞬态错误 `204/404/408/5xx` 与传输超时）、`wrap_input`（默认 `false`）、`wrap_output`（默认 `true`）、`control_template`、`input_context_instruction`、`AISTUDIO_SWITCH_BACKOFF_SECONDS`、`AISTUDIO_RETRY_SAME_DELAY_SEC`、`AISTUDIO_CONFIG_PATH` / `AISTUDIO_CONFIG_FILE` 等均与 `defaults/config.yaml` 及源码实现 100% 一致。 |
| 规格内部路径与配置行号引用 | **PASS（P2-2, P2-3 已关闭）** | `docs/ARCHITECTURE.md:88,161` 已修正为 `src/aistudio_api/tools/aistudio-cookie-grabber.user.js`；4 份规格文档中列出的 `defaults/config.yaml:<line>` 行号漂移已全部逐行校准（另于 N-4 记录 `docs/ARCHITECTURE.md:474,516` 既有行号微瑕）。 |

---

## 2. 完整问题与观察清单（`P0 / P1 / P2 / Q / N` 复审终态）

### P0（阻断级）
- 无。

### P1（已关闭）
- **P1-1（遗漏清理的“429 同号重试”旧表述）— CLOSED** — `docs/KEEPALIVE_AND_LOGGING_SPEC.md:991`
  - **初审发现**：仍写为 `- 429 同号重试或切号成功后……`。
  - **复审核验**：已于 `docs/KEEPALIVE_AND_LOGGING_SPEC.md:991` 同步改写为 `- 429 切号或 403/瞬态错误重试成功后，Client 只记 1 次成功且 rescued_requests += 1；Attempt 记录失败尝试和最终成功两条。`，与现行 `REV-2.2.0.6` 契约及 `docs/ARCHITECTURE.md:556`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:583,1005` 完全一致。

### P2（已全部关闭）
- **P2-1（`./LICENSE` 相对链接失效）— CLOSED** — `README.md:9,578`、`README_EN.md:9,579`
  - **复审核验**：四处 `LICENSE` 链接已全部改为 `https://opensource.org/license/mit`，仓库内无失效 `./LICENSE` 相对路径。
- **P2-2（油猴脚本仓库路径不准确）— CLOSED** — `docs/ARCHITECTURE.md:88,161`
  - **复审核验**：架构图（`:88`）与模块职责表（`:161`）均已更新为真实路径 `src/aistudio_api/tools/aistudio-cookie-grabber.user.js`。
- **P2-3（`defaults/config.yaml` 历史行号引用漂移）— CLOSED** — 4 份规格文档逐行核验通过：
  - `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:268,539,541,542,684,964,1006` 已全部校准为 `instances`（`:61-67`）、`rotator`（`:106-113`）、`features.routing`（`:128-134`）。
  - `docs/PROTOCOL_AND_TOOLCALL_SPEC.md:96,263,516,988,998,1026` 已全部校准为 `features.formats`（`:157-162`、`:160`、`:161-162`）、`features.atg`（`:184-204`）、合并区间（`:157-204`）。
  - `docs/KEEPALIVE_AND_LOGGING_SPEC.md:83,97,352,940` 已全部校准为 `logging`（`:93-97`）、`features.keepalive`（`:138-145`）、`active_activity`（`:144`）。
  - `docs/BOTGUARD_VERIFICATION_CHAIN.md:485` 已校准为 `browser.fingerprint_spoof_enabled`（`:84-85`）。

### Q（已关闭 / 说明）
- **Q-1（`proactive_switch_mode` 文案粒度）— CLOSED** — `README.md:51`、`README_EN.md:51`
  - **复审核验**：`README.md:51` 已更新为“支持按仅 Free / 仅 Pro+ / 全部 / 指定分组框选”，`README_EN.md:51` 已更新为“scoped to Free-only / Paid-only / all / custom groups”，与 `defaults/config.yaml:123`（`off/free_only/paid_only/all/groups`）对齐。
- **Q-2（`waa-signer/README.md:104` 示例 `"version": "2.1.0"` 核实）— RESOLVED**
  - 经核对 `waa-signer/cmd/waa-signer/main.go:41`（`const signerVersion = "2.1.0"`）与 `waa-signer/internal/signerapi/types_test.go:98`，`GET /health` 实跑返回值确为 `"version": "2.1.0"`，非过期标签。
- **Q-3（快速开始小节排序）— RESOLVED**
  - `README.md:102-242` 将「Docker 部署」置为第一小节，贴合开箱部署优先级且保持 10 节骨架一致。

### N（观察与已验证通过项）
- **N-1**：复审实跑 `git diff --check` 返回空输出（exit `0`），所有修改文件无尾随空白、无行尾换行缺失及 CRLF 污染。
- **N-2**：所有新增章节锚点（`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:45,53`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md:30,36`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:45`）均与对应正文标题 slug 精确一致。
- **N-3**：所有环境变量名（`AISTUDIO_SWITCH_BACKOFF_SECONDS`、`AISTUDIO_RETRY_SAME_DELAY_SEC`、`AISTUDIO_CONFIG_PATH`、`AISTUDIO_CONFIG_FILE`、`AISTUDIO_DEFAULT_TEXT_MODEL`、`AISTUDIO_DEFAULT_IMAGE_MODEL`、`AISTUDIO_SSE_KEEPALIVE_SEC`、`AISTUDIO_TIMEZONE`、`AISTUDIO_PERSIST_STATS`、`AISTUDIO_PERSIST_ROTATOR`）均已核对代码声明，无虚构或拼写错误变量。
- **N-4（非阻断既有行号微瑕）**：`docs/ARCHITECTURE.md:474`（写 `defaults/config.yaml:134-150`，实际为 `:184-204`）与 `docs/ARCHITECTURE.md:516`（写 `defaults/config.yaml:112-117`，实际为 `:157-162`）为初审未列入 `P2-3` 的既有配置行号偏移；配置键名与默认值描述本身完全准确，不阻断本次交付，后续文档例行整理时顺手更新即可。

---

## 3. 门禁实跑结果（复审实跑）

1. **`git diff --check`**：
   - 命令：`git diff --check`
   - 结果：exit `0`（无输出，0 处空白/行尾问题）。
2. **配置 Schema 与 Features 单测**：
   - 命令：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest tests/unit/test_config_schema.py tests/unit/test_features_config.py -q`
   - 结果：`69 passed in 1.04s`（exit `0`；初审 `1.26s` / 复审 `1.04s` 两次均全绿）。
