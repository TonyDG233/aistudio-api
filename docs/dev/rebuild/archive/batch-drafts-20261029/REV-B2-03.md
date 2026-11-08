# REV-B2-03 PKG-B2-03 P2 Routing / Retry / Tier review

- 被审包：`docs/dev/rebuild/domains/P2-routing/packages/PKG-B2-03-p2-routing.md`
- reviewer：只读 agent
- 状态：FAIL
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（HEAD `755e28a`，`src`/`tests` 与 `9b5cdea` 一致）

## 结论

D-04/D-06 的 403 不切号、错误分类、429 耗尽返回 503 方向正确，文件域中的源码文件都存在；但 D-07 与 B1 重复且包内自相矛盾，未声明对 B2-01 的依赖却要改同一 `account_orchestrator.py`，且引入目标树不存在的 `surface` 入参，按现文不可并行执行。

## 字段核对

| 字段 | 结论 | 说明 |
|---|---|---|
| 范围 | 不闭合 | D-07 重复（P2-01）；surface 入参不存在（P2-03）；「实例」术语冲突（P2-04） |
| 文件域 | 可执行但冲突 | src/tests 已列；`account_orchestrator.py` 与 B2-01 重叠（P2-02），`app.py`/`model_defaults.py` 与 B1 重叠（P2-01、P2-07） |
| 依赖 | 不足 | 只列 B2-02/B1；缺 B2-01；D-07 主改方自相矛盾（P2-01、P2-02） |
| 测试 | 部分可执行 | 新文件已声明；`test_tier_preemption.py` 与目标相反且未标重写（P2-05），metadata 边界未定（P2-06） |
| 命令 | 不完整 | 定向 pytest + ruff；缺全量 pytest 与 pyright（P2-10） |
| 证据 | 齐全 | `docs/dev/evidence/B2/.../PKG-B2-03/`，脱敏明确；日期口径不一致（P2-12） |
| 失败停止 | 齐全 | 4 条停止条件，覆盖 403/429/lease/KeyError |
| 回滚 | 齐全 | 逐文件还原 + 备份 rotator_state.json，不 commit/合并 |
| 未决 | 部分 | 5 条已列；surface 来源、P1 依赖、D-07 归属未在未决中正面闭合（P2-01、P2-02、P2-03） |

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| P2-01 | 阻断 | `PKG-B2-03:18,32,79`；`PKG-B1-01:24,69`；`08-09-decision-record.md:19` | §1/§2 把「`rotator` 深合并默认值 + 启动校验」列为本包职责，§3 又说「B1/P9-pre 完成」，§8 再问「归 P9-pre 还是本包」；B1 已声明主改并等用户确认 | 同一 `model_defaults.py`/`api/app.py` 两包重复改，D-07 测试重复，B2 无法并行 |
| P2-02 | 高 | `PKG-B2-03:29`；`PKG-B2-01:25`；`account_orchestrator.py:50-65,104-117` | B2-03 主改 `account_orchestrator.py`，但 §3 依赖只列 B2-02 与 B1，未列 B2-01；B2-01 明确要改同一文件的 `:50-65,104-117` 成功判定 | 依赖边缺失；P2 可能覆盖或绕过 D-05 的早退修复，账号切换顺序回归 |
| P2-03 | 高 | `PKG-B2-03:14,43,47`；`PKG-B2-01:19`；`08-09-decision-record.md:13` | §1/§4 要求 `surface=chat/build` 准入、`build` 前显式拒绝；目标树 `src` 无任何 `surface` 符号，请求 schema/路由文件不在 §2 文件域；B2-01 又写「目标树无 surface 维度，不新增 surface」 | 验收用例无法落地（不知道 surface 入参从哪来）；与 B2-01、D-01 口径冲突 |
| P2-04 | 高 | `PKG-B2-03:15,26,45`；`05-restart-map.md:57` | §1/§4 反复使用「唤醒/切现有实例」「在既有实例间切换」，而 B2 退出条件是「1 账号 1 Context 1 Page」，§1 Out-of-scope 又禁止 `instance_pool.py`/lease | 术语与禁用架构冲突，执行者可能把「实例」实现成多实例池，直接违反 05 与 D-05 |
| P2-05 | 中 | `PKG-B2-03:33,48`；`test_tier_preemption.py:18,63,96,137` | 现有 `test_tier_preemption.py` 的 4 个用例以「付费模型 preempt 到更高 tier」为主线（其中 `:63` 断言无 Pro 时 fast fail）；B2-03 要求「attempt 0 不因 paid 模型主动抢切」，但未把该文件标为「重写/删除」 | 至少 3 个现有用例与目标相反，验收命令会跑失败；实现者无法判断是改实现还是改测试，D-04 验收不闭合 |
| P2-06 | 中 | `PKG-B2-03:46,49`；`test_api_responses.py:262` | 现有 `test_streaming_metadata_event_does_not_block_failover` 断言 metadata 事件后仍可 failover；B2-03 要求「流式已出数据后 429/403 不再切号」，但未定义「metadata 是否算已出正文」 | 两条验收可能互相否定；流式边界未定义 |
| P2-07 | 中 | `PKG-B2-03:30,32`；`PKG-B1-01:24` | §2 把 `api/app.py` 列为领域错误映射文件，但未声明与 B1（启动校验/关闭顺序）共享及主改顺序；B1 只单方写「冲突时本包优先」 | app.py 并行编辑冲突；错误映射可能覆盖启动装配改动 |
| P2-08 | 中 | `PKG-B2-03:49`；`PKG-B1-01:39-41` | B2-03 在 `test_model_defaults.py` 再要求「rotator 缺块/缺键深合并、启动校验显式报错、请求期无 KeyError」，与 B1 的 `test_boot_config_validation.py` 覆盖重复 | 两套验收可能对默认值/失败方式给不同断言，重复维护 |
| P2-09 | 中 | `PKG-B2-03:30`；`app.py:161-175`；`api_service_gemini.py:8` | §2 只列 `domain/errors.py` 与 `api_service_gemini.py` 承载 403/429 语义；协议错误体当前由 `app.py:161` 的 HTTPException handler 生成，`api/responses.py`/`routes_gemini.py` 未列入文件域 | 「API 层按协议映射」的具体落点不完整，403 四字段位置无法验收 |
| P2-10 | 中 | `PKG-B2-03:54-58` | 命令只有定向 pytest 与 ruff，缺 `uv run pytest -q` 全量与 `uv run pyright`；B1/B2-01 都包含 | 路由/重试改动缺全量回归与类型门 |
| P2-11 | 中 | `PKG-B2-03:31,49`；`model_discovery.py:396-441` | 包要求「空规则不得回退 Ultra-only」「paid 但无规则按 accessModes/paid 判定」；当前 `get_required_tiers` 在 `accessModes` 含 4 且不含 3 时直接返回 Ultra（`:400-403`），发生在读取 paid 规则之前 | 「空规则」与「accessModes 4-only」的边界未定义，测试可能把正确行为判成回归 |
| P2-12 | 低 | `PKG-B2-03:60` | 证据日期 `2026-10-28`，B1/B2-01 用 `2026-10-29` | 跨包证据归档口径不一致 |
| P2-13 | 低 | `PKG-B2-03:5` | 基线写「docs HEAD 以 `git rev-parse HEAD` 为准」，但本包位于主仓库 `rebuild` 分支未跟踪目录，目标 worktree `755e28a` 内无该文档 | 文档版本锚点不明确，审计无法定位 |

## 验收门槛

- 范围是否闭合：**否**，D-07 重复（P2-01）、surface 入参不存在（P2-03）、「实例」术语冲突（P2-04）。
- 文件域是否可并行：**否**，`account_orchestrator.py` 与 B2-01 重叠且未声明依赖（P2-02），`model_defaults.py`/`app.py` 与 B1 重叠（P2-01、P2-07）。
- 测试是否可执行：**部分**，新测试文件已声明；但现有 `test_tier_preemption.py` 与目标相反且未标重写（P2-05），metadata failover 边界未定（P2-06），命令缺全量/类型门（P2-10）。
- 证据是否可复现：**满足** `docs/dev/evidence/B2/`，日期口径需统一（P2-12）。
- 失败停止/回滚是否明确：**是**，停止条件、逐文件回滚、禁 lease/pinned/多页明确；但「实例」表述需先纠正（P2-04）。
- 是否误用旧 fork 路径或 534 当任务：**否**，§1 Out-of-scope 明确禁止 `routing_pipeline.py`/`surface_orchestrator.py`/`instance_pool.py`，未把 534 当任务。
- 真实测试授权：**未要求**，§4 全部离线；真实 429/账号切换测试另走真实测试闸门。

## 给用户的裁决项

1. D-07 最终归属：若按 05 顺序由 B1 主改，B2-03 是否删去 §1/§2 的深合并/启动校验职责，只保留消费与测试。
2. B2-03 是否必须声明 `PKG-B2-01` 为前置，并约定 `account_orchestrator.py` 的修改顺序/分区。
3. `surface` 的来源与边界：是 D-01 的配额记账口径，还是请求级 `chat/build` 路由；若 B2 不实现，§1/§4 的 build 拒绝用例应删除或推迟到 C1。
4. 「现有实例」是否统一改为「现有账号会话/Page」，以对齐 05 的「1 账号 1 Context 1 Page」。
5. `test_tier_preemption.py` 与 `test_api_responses.py::test_streaming_metadata_event_does_not_block_failover` 是重写、删除还是保留；metadata 是否算「已出正文」。
6. 403 四字段在 Gemini/Responses 的精确位置、429 耗尽错误码（`switch_exhausted`/`quota_exhausted`）、paid tier 集合与「paid 但无规则」回退口径。

## 只读声明

未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
