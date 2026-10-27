# 13 Gate 0 执行记录

日期：2026-10-27。执行者：主线 integrator。范围：Gate 0（G0.1-G0.9）与迁移到 upstream 基线；不改业务代码。

## 1. 基线与来源

| 项 | 值 |
|---|---|
| 源仓库/分支 | `D:\LLM\self_programming\aistudio-api` @ `rebuild` |
| 源基线（交接 SHA） | `8e0ab11`（`7cd92f7` 为文档内容基线；`8e0ab11` 仅追加 SHA/changelog 记录） |
| 上游基线 | `upstream/master` @ `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| 新 worktree | `.kilo/worktrees/rebuild-upstream` |
| 新分支 | `rebuild-upstream`（基于 `upstream/master`） |
| 迁移提交 | `cf6e681`（236 files, +8933） |
| 上游 fetch | 未执行（离线）；使用现有 `upstream/master` ref |

分支名偏差：原计划 `rebuild/upstream` 与现有分支 `rebuild` 在 git ref 命名空间冲突（`refs/heads/rebuild` 是文件，无法同时创建 `refs/heads/rebuild/upstream`）。改用等价分支 `rebuild-upstream`；worktree 路径、基线和“不合并 master/rebuild”约束不变。

## 2. G0.1-G0.9 结果

| ID | 结果 | 证据 |
|---|---|---|
| G0.1 | 通过 | 源仓库开始时 `git status --porcelain` 为空；本轮文档修复提交 `7cd92f7`；SHA 记录提交 `8e0ab11`；`05`/`10` 均保持“当前 HEAD + 现查”，未写死旧 SHA |
| G0.2 | 通过 | `06-index-validation.md:20-127` 原样脚本输出 `OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0`（源分支与新 worktree 各跑一次） |
| G0.3 | 通过 | `EXPECTED_DECISIONS` 覆盖 D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D05-039 drop、D07-026 keep、D08-022 keep；`REQUIRED_NOTES` 与 P9-Q3 检查无错误 |
| G0.4 | 通过 | 只扫 `docs/dev/rebuild` + evidence allowlist；`git grep` 命中仅为扫描命令/feature 名称/审计说明文字；11 个 ignored `.log` 用 `Select-String` 全扫，命中仅为 `authorization`/`x-goog-api-key` 等 header 名列表，无真实值；`spikeQ4-evidence.json`/`spikeQ4.py` 未迁移；`docs/BOTGUARD_VERIFICATION_CHAIN.md` 为 upstream 原生，未复制/未改写 |
| G0.5 | 通过（分支名偏差见 §1） | `git worktree add .kilo/worktrees/rebuild-upstream -b rebuild-upstream upstream/master`；`git restore --source=rebuild -- docs/dev/rebuild`；`ledger-batches/*.md` 删除仅留 `README.md`；evidence allowlist 逐项 restore；11 个 `.log` 用 `Copy-Item -Force` + `git add -f`；`$mustExist` 全部存在；`git status -uall` 无意外 untracked |
| G0.6 | 通过 | `ledger-batches/` 仅保留 `README.md`；旧 WP 正文留在 `rebuild`，新分支只保留归档规则 README；`docs/changelog.md`/`evidence/INDEX.md` 已迁移 |
| G0.7 | 通过 | `12-gate0-inputs.md`、`06-test-disposition.csv`、acceptance 模板存在；Gate 0.5 工件见 §3 |
| G0.8 | 通过 | `10-handoff-prompt.md` 已更新：Gate0 完整条件指向 `08-06 §6`，G1-G11 固定为 `08-06 §6.3`（`08-05` 附录 A / `08-01:129-140`），不依赖旧行号 |
| G0.9 | 通过 | `08-06` 已提交并被 `10` 引用；`10` 可独立执行 |

## 3. 迁移清单与校验

- 迁移内容：`docs/dev/rebuild`（含 `ledger-batches/README.md`）、`docs/dev/evidence` allowlist、`docs/changelog.md`。
- 11 个 ignored `.log`（全部 force-add）：
  - `p2-verify/`：`service-off.log`、`service-prefer.log`、`service-required.log`
  - `p3-build3/`：`acceptance-run1.log`、`acceptance-run2.log`、`service.err.log`
  - `wp5-20261018/`：`service-f-build.log`、`service-f-quota.log`、`service-p-chat.log`、`service-p-guard.log`、`service-p-guard2.log`
- 未迁移：`p3-build3/spikeQ4-evidence.json`、`p3-build3/spikeQ4.py`（敏感形状，只留 `rebuild` ref）。
- 关键文件存在性：`06-index-validation.md`、`10-handoff-prompt.md`、`12-gate0-inputs.md`、`06-test-disposition.csv`、`08-06-final-readiness-audit.md`、`evidence/INDEX.md`、`changelog.md` 均存在。
- 测试处置覆盖：`06-test-disposition.csv` 80 行；对新 worktree 实际 `tests/**/*.py` 26 个文件覆盖 26/26（missing 0）；对 `rebuild` 的 80 个测试文件覆盖 80/80；无 `pending`；keep 38 / rewrite 37 / delete 5。
- Gate 0.5 工件校验（`python` + `jsonschema`）：`config/config.example.yaml` 对 `config/config.schema.json` 校验 0 错误；`event.sample.json` 字段集合与 `12 §4` 完全一致；4 个 JSON fixture 均可解析；`f03_finish_only.bin` 为 22 字节 `)]}'\n[[],null,[5,3,8]]`。

## 4. Gate 0.5 工件

| 工件 | 说明 |
|---|---|
| `config/config.example.yaml` | §3 配置样例：instances/limits/api_keys/features/keepalive/logging |
| `config/config.schema.json` | JSON Schema 2020-12；实例级 count/surface/min_tier；api_keys 默认拒绝语义 |
| `tests/fixtures/events/event.sample.json` | §4 事件样例（request/attempt 共用字段基线） |
| `tests/fixtures/pg/f01_missing_signature.json` | F-01：历史 functionCall 缺 thoughtSignature，期望 wire[14] 占位签名 |
| `tests/fixtures/pg/f02_prestream_400.json` | F-02：首事件前 400 必须保留真实 HTTP 状态 |
| `tests/fixtures/pg/f03_finish_only.bin` | F-03：空 candidates + usage 的终止块，期望合成终止 |
| `tests/fixtures/pg/f04_string_response.json` | F-04：functionResponse 裸字符串强制 Struct |
| `docs/dev/rebuild/acceptance/README.md` | 验收模板与规则 |
| `docs/dev/rebuild/acceptance/P9-pre.md` | 首个切片计划（8 项，状态 planned） |

## 5. 未验证项

- `git fetch upstream --prune` 未执行（离线）；若后续 fetch 改变 `upstream/master`，需重跑相关审计。
- Gate 0.5 的 config/event schema 尚未接入运行期（P9-pre 实现）；`config.example.yaml` 只是契约样例。
- acceptance/P9-pre.md 为计划，未执行；不能当作已通过。
- 真实账号/IP/Google 未访问；build 9998/signer、24h 保活等仍需后续切片验证。

## 6. 约束遵守

- 未合并 `master`/`rebuild`；未整树 checkout；未改 `src`/`tests` 业务实现；未提交敏感内容。
- 主仓库 `rebuild` 分支在本记录生成时保持 `8e0ab11`，未再改动。
