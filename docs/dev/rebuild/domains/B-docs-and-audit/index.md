# B-docs-and-audit / B7 文档与审计域

- 状态：B7 审计与文档定稿已完成；未修改 `src/`、`tests/`，未 stage/commit。
- 审计日期：2026-11-01（UTC+8）。
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`。
- 分支 / HEAD：`rebuild-upstream` / `55f1c64 ci(rebuild): 移除上游 DockerHub 硬编码依赖以适配 GHCR 自动构建`。
- 提交范围：`99c38ba..HEAD`，共 **33** 个提交（28 个重建分支提交 + 5 个上游提交），其中 **3** 个合并提交。
- 基线：`99c38ba`（2026-10-25，上游 99c38ba 基线）。
- 约束：严格离线；未启动 `main.py`/浏览器/网络；未触发服务；未修改业务代码与测试。

## 1. 审计目标

1. 对 `99c38ba..HEAD` 的每个提交做历史审计：真实范围、阶段、关键文件、决策、移除/新增机制、合并影响；识别 subject 低估实际范围的提交。
2. 对落地代码做分层与耦合审计：`api -> application -> domain <- infrastructure` 的反向依赖与抽象泄漏、`api_service_gemini` 与 `generation_service` 职责重叠、硬编码公共 Web 常量、重复的发现/鉴权/Tier 逻辑、全局状态、死分支。
3. 交叉核对 `06-decision-log.md`、`08-09-decision-record.md`（D-01..D-14）与未闭合 Q/N。
4. 产出《B 阶段后独立架构微调与解耦施工清单》，只提任务，不实施修复。

## 2. 工件

| 工件 | 内容 |
|---|---|
| `index.md` | 范围、方法、事实基线、证据入口、残余缺口 |
| `reviews/REV-B7-commit-history-audit.md` | 33 个提交逐条审计 + 低估提交清单 + 决策映射 |
| `reviews/REV-B7-architecture-coupling-audit.md` | 分层耦合、重复/硬编码/全局状态/死分支、D-01..D-14 对照、施工清单 |
| `reviews/REV-B7-docs-and-audit-summary.md` | 独立复核、修复记录、复审结论与定稿状态（含 B-docs 四件套与关键文档哈希） |

上述四件套在目标树与主工作区 `docs/dev/rebuild/domains/B-docs-and-audit/` 下必须逐字节一致；定稿哈希见 `reviews/REV-B7-docs-and-audit-summary.md` §13。

## 3. 方法

- 提交证据：`git log --oneline --decorate 99c38ba..HEAD`、`git log --format`（作者/日期）、`git show --stat`、`git show --numstat`、`git diff --diff-filter=D`、`git diff <merge>^1 <merge>`、`git rev-list --count`。
- 分层证据：AST 扫描 `src/aistudio_api/{api,application,domain,infrastructure}` 的跨层 import；逐文件 `file:line` 锚点；模块级定义/引用计数（脚本仅在系统 Temp 运行，未入库）。
- 文档证据：`06-decision-log.md`、`08-09-decision-record.md`、`domains/upstream-merges/{index.md,reviews/REV-MERGE-852de53-oct31.md}`、`domains/P5-generation/reviews/REV-B6-*.md`。
- 校验：`git diff --check`（工作区）、`git diff --check 99c38ba..HEAD`（范围）、`git ls-files config.yaml`。
- 未使用：任何服务启动、浏览器、网络请求、真实账号。

## 4. 事实基线（可复验）

| 项 | 结果 | 证据 |
|---|---|---|
| 提交数 | 33 | `git rev-list --count 99c38ba..HEAD` = 33 |
| 分支提交 / 上游提交 | 28 / 5 | 作者 `TonyDG` 28；`DeconstructedCube` 5（`f2b1e28`、`16450cc`、`99600c7`、`ccd15e3`、`852de53`） |
| 合并提交 | 3 | `ca8496c`、`05a9df7`、`da937b6` |
| 最大单提交 | `9bffda1` 50 文件 +8841/−324 | `git show --numstat` |
| 根 `config.yaml` | 未跟踪（且被 `.gitignore:15` 忽略） | `git ls-files config.yaml` 为空；`git check-ignore -v config.yaml` |
| 工作区 `git diff --check` | 通过（exit 0） | 本地命令 |
| 范围 `git diff --check 99c38ba..HEAD` | exit 2；**均为既有文档空白问题**（`00-01-plan-audit.md` 行尾空格、evidence `.txt`/`06-decision-log.md` EOF 空行） | 见 §6 残余 |
| B6 实机证据 | 5/5 generation success，5 条授权路径 | `docs/dev/evidence/B6/results.json`（`generated_at 2026-10-31T23:50+08:00`） |
| 上游合并台账 | `upstream-merges/index.md` 记录 3 组合并；本审计与其一致 | `domains/upstream-merges/index.md:8-53` |

## 5. 证据指针

### 5.1 提交历史

- 逐提交正文：`reviews/REV-B7-commit-history-audit.md` §3。
- 低估提交：同文件 §4（`9bffda1`、`b30315e`、`0cdfd35`、`8b80971`、`a9e7dc3`、`b000bb7`、`05a9df7`、`da937b6`）。
- 决策映射：同文件 §5。

### 5.2 架构耦合

- 层间 import 边：`reviews/REV-B7-architecture-coupling-audit.md` §2。
- D-06 违反：`src/aistudio_api/application/api_service_gemini.py:15`（`fastapi.responses.StreamingResponse`）、`:17-23`（`api.response_models`/`api.responses`/`api.schemas`）、`:309-360`（在 application 构造 `StreamingResponse`）。
- 反向依赖：`application/routing_pipeline.py:608,930`、`application/account_orchestrator.py:30`、`application/token_counter.py:16`、`application/format_converters/openai_request.py:10`。
- 基础设施反向依赖：`infrastructure/gateway/model_defaults.py:14`、`instance_pool.py:23-25`、`model_discovery.py:16,583,596`。
- 硬编码公共常量：`infrastructure/account/cookie_parser.py:21-24`、`account_info_refresher.py:44-53`、`gateway/model_discovery.py:675-679`、`gateway/session.py:90-98`、`gateway/session_auth.py:31-39,191`。
- 重复发现/Tier：`model_discovery.py:675,762`、`account_info_refresher.py:46`、`cookie_parser.py:564,599,654`、`session_auth.py:191`、`instance_manager.py:633`；Tier 映射见 §5。
- 全局状态：`api/state.py:240`、`application/signature_cache.py:158`、`application/model_access.py:63-64`、`config.py:325`、`model_defaults.py:1011-1012`、`application/testing_hooks.py:13-14`。
- 死分支：`application/format_converters/common.py:56,102,122,130,145`、`api/format_errors.py:257`、`infrastructure/browser/scripts.py:22,35`、`model_defaults.py:1413`、`config.py:211`、`api/dependencies.py:124`、`log_broadcast.py:315`。

### 5.3 决策与未闭合项

- `06-decision-log.md`：D1-D8 执行口径（`:43-56`）、P1-P10（`:58-416`）、08-09 闭合（`:444-479`）。
- `08-09-decision-record.md`：D-01..D-14 边界（`:11-26`）。
- 未闭合：P4-Q4（`:206,214,227`）、P1-E2 OAuth defer（`:92-96`）、P5-Q11 build REST defer（`:286`）、P6-Q6 active_activity disable（`:437`）、P10-Q3 WARP 后置（`:411`）。
- 合并复审未闭合：`REV-MERGE-852de53-oct31.md:252-264` 的 Q-05/Q-12/Q-13；`:303-317` 的 N-32/N-33/N-40/N-42/N-43；其中 Q-12/N-40 已由 `1f60fbc` 的 B6 证据闭合，Q-05（上游 UI 文案）与 Q-13/N-32（`snapshot_probe` 未接线）在 HEAD 仍成立，N-33 的“配置门控”子项仍成立（`app.py:411` 无条件探测）。

## 6. 残余缺口

1. `git diff --check 99c38ba..HEAD` 报出的空白问题是历史文档既有问题（非本次审计引入）；工作区 `git diff --check` 通过。若后续要求“范围内 diff-check 全绿”，需独立清理文档空白任务。
2. 本审计为静态/只读；未重新执行 `pytest`/`ruff`/`pyright`（避免任何运行期副作用与耗时），因此不新增测试通过数结论；仅引用既有 B6 证据。
3. D-12 的 UI 可见性未在 HEAD 满足：`quota_scope`、`security.api_allow_anonymous` 在 `web/` 无结构化表单引用（仅 YAML 编辑器可改）；按既有口径属 C2/P8 UI 阶段，本审计标为“延后/部分”。
4. 上游提交 `16450cc` 删除请求级超时 watchdog，与 D-03“半开连接由命令超时兜底”的存在张力；本审计只记录，不改行为。
5. `wire_parser.py` 是生产活跃模块（被 `client.py`、`streaming.py`、`stream_parser.py`、`wire_codec.py`、`instance_client.py` 直接 import），不是死代码；仅剩 gateway 内部 API/归属清理（B7-11b）。

## 7. 审计期间工作树并发观察

审计开始时工作树为干净状态（`git status --short` 无输出）。审计期间观察到同工作树另有会话（`Update core docs and schema`）写入以下**未提交**文档，**不属于本任务，本审计未触碰**：

- 修改：`AGENT.md`、`config/config.schema.json`、`docs/ARCHITECTURE.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`、`docs/WIRE_SPECIFICATION.md`、`docs/changelog.md`
- 新增：`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md`

HEAD 仍为 `55f1c64`，`99c38ba..HEAD` 的 33 个提交与本审计结论不受影响；`git diff --check` 在这些并发文档变更存在时仍通过（exit 0）。本任务在 `docs/dev/rebuild/domains/B-docs-and-audit/` 下产出四件套：`index.md`、`reviews/REV-B7-commit-history-audit.md`、`reviews/REV-B7-architecture-coupling-audit.md` 与 `reviews/REV-B7-docs-and-audit-summary.md`。

## 8. 定稿状态（2026-11-01）

- HEAD：`55f1c649992264ecc19d079479865f2c7fc63993`（`rebuild-upstream`）。
- 全仓 Ruff：`uv run ruff check .` 0 errors。修复为 14 个 docs evidence harness 补齐文件尾换行，以及 `wp5_set_config.py` 的 `startswith` 合并与列表拼接改星号展开（行为不变）。
- Pyright：`uv run pyright src tests` 0 errors, 0 warnings, 0 informations。
- Pytest：2 failed, 820 passed, 1 skipped；两个失败均为既有 Windows 环境问题（CloakBrowser 路径语义、`bun.CMD` shim）。
- CI：`.github/workflows/ci.yml` 后端 job 增加 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`，Linux pytest 不再误判 UTF-8 fixture。
- changelog：重写为 Keep a Changelog 结构（`[2.0-rebuild] - 2026-10-31`、`[0.1.0] - 2026-05-16`），移除阶段代码、提交哈希表与任务日志式条目。
- N-45/N-46/N-47 已闭合：B6 报告改为当前定稿 HEAD 与文件计数；架构文档补齐 `instance_pool.py:23-25`；本目录工件清单计入 summary（四件套）。
- 工作区：24 个已跟踪文件修改 + 7 个未跟踪文件；`src/`、`tests/` 无改动；根 `config.yaml` untracked；`git diff --cached` 为空；全部改动留待 Integrator 提交。
