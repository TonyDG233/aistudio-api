# REV-B2-01 PKG-B2-01 P1 账号 / Cookie / 上下文 review

- 被审包：`docs/dev/rebuild/domains/P1-account/packages/PKG-B2-01-p1-account.md`
- reviewer：只读 agent
- 状态：FAIL
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（HEAD `755e28a`，`src`/`tests` 与 `9b5cdea` 一致）

## 结论

文件域与测试文件大体真实存在，D-05 toggle 早退链路引用准确（`routes_accounts.py:446-469`、`account_orchestrator.py:50-65,104-117`）；但包内首项 `_atomic_write_json` 是伪缺陷，D-01 口径与用户决策直接冲突，且 rebind 入口/服务层签名未冻结并形成 B2 依赖环，按现文不可执行。

## 字段核对

| 字段 | 结论 | 说明 |
|---|---|---|
| 范围 | 不闭合 | 首项为伪缺陷（P1-01）；D-01 冲突（P1-02）；D-13 字段未删（P1-06） |
| 文件域 | 可执行但冲突 | 修改/新增/不改已列；`cookie_parser.py` 自相矛盾（P1-10）；共享文件声明与 P3 相反（P1-05） |
| 依赖 | 不足 | 只声明 B1；rebind 入口未冻结且与 P3 形成环（P1-03）；service 签名未定（P1-04） |
| 测试 | 部分可执行 | 7 个用例，新增文件已声明；P1-07 可能空跑，P1-09 超出决策 |
| 命令 | 齐全 | 定向 pytest + 全量 pytest + ruff + pyright |
| 证据 | 齐全 | `docs/dev/evidence/B2/.../PKG-B2-01/`，脱敏明确；日期口径不一致（P1-12） |
| 失败停止 | 齐全 | 5 条停止条件可执行 |
| 回滚 | 齐全 | 逐文件 checkout + 备份 registry/presets + 前后端同步回退 |
| 未决 | 部分 | 5 条已列；D-01 与 D-13 字段未在未决中正面提出；12s 被误称「旧 fork」（实际在目标树 `session.py:278`） |

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| P1-01 | 高 | `PKG-B2-01:13`；`account_store.py:18-33`；`session_auth.py:390-391` | 包称 `_atomic_write_json`（引 `18-65`，函数实际止于 `:33`）「吞异常，改为失败上抛」；实际 `:29-33` 已清理 tmp 并 `raise`。真正吞掉写失败的是 `save_browser_cookies` 的 `except ... log.debug`（`session_auth.py:390-391`），包未列 | 首个范围项是伪缺陷，照包实现会做无效改动；真实吞异常点仍被遗漏 |
| P1-02 | 阻断 | `PKG-B2-01:19`；`08-09-decision-record.md:13` | D-01 明确「保留 surface 能力……不得以『上游单维』为由删除 surface；默认 `account`」；包写「目标树无 surface 维度，`quota_scope=account_surface` 不适用，不新增 surface」 | 以目标树现状否定用户已确认的 D-01；B2-03 又引入 surface 准入，两包对「B2 是否存在 surface」结论相反，B2 无法统一验收 |
| P1-03 | 阻断 | `PKG-B2-01:33,75`；`PKG-B2-02:36`；`session.py:182-272` | `transition_to_account(auth_file, force_refresh=False)` 已存在，但 B2-02 §2/§3 只列 `ensure_ready`/`active_requests`（及 `connect_page`/`request_scope`），未点名它作为冻结的 rebind 入口；P1 依赖 P3，P3 §3 又要求 P1 先完成，形成 B2 内依赖环 | P1/P3 无法并行启动；P1 的「切换成功后提交 registry」缺少冻结返回语义（成功/失败的判定点） |
| P1-04 | 高 | `PKG-B2-01:25`；`account_service.py:147-151,189-193` | `toggle_account_enabled`、`apply_preset` 为同步方法并直接委托 store；包要求「toggle/preset 走 rebind」，但未定义 async 化后的签名、返回值、异常与事务边界 | 路由层、测试与错误返回无契约可依；实现者只能自行发明接口 |
| P1-05 | 高 | `PKG-B2-01:25`；`PKG-B2-02:19,27-31` | P1 称 `session_auth.py` 为「共享文件、冲突风险高：P3 同改」；B2-02 明确「不负责 cookie/BotGuard」，其 §2 文件域也不含 `session_auth.py` | 共享文件声明与 P3 包相反，可能重复改或双方都不改；跨包协调基线错误 |
| P1-06 | 中 | `PKG-B2-01:13,18`；`account_store.py:108,133`；`08-09-decision-record.md:25` | D-13 要求「不实现 notes 字段/API……不得保留 notes 列」；包只删 `update_account_notes`、端点与前端，仍保留 `AccountMeta.notes` 字段及 `from_dict` 读取；§1 又称「只保留目标树现有字段」 | D-13 未闭合；旧 `meta.json` 的 notes 仍被读入并随 `to_dict()` 写回 |
| P1-07 | 中 | `PKG-B2-01:44-45`；`account_store.py:635-672` | 包称预设写失败需「上报失败、无 `.tmp.*` 残留」，但 `_save_presets` 已上抛、`_atomic_write_json` 已清 tmp；真实锁问题只在 `_presets is None` 且 presets 文件缺失时触发（`_load_presets:643` → `_save_presets:669`），而 `__init__` 的 `_ensure_default_presets:635-640` 通常已写文件 | 两个验收用例可能空跑或无法复现 D-08，「无死锁」结论不可靠 |
| P1-08 | 中 | `PKG-B2-01:16,25`；`routes_accounts.py:183,357,394,458,515,593,719,878`；`account_orchestrator.py:55,80,178,234`；`api_service_gemini.py:168`；`account_service.py:139` | 将 `get_active_account` 改为只读/可能返回 None，影响至少 14 处调用；文件域只列 `routes_accounts.py` 与 `account_orchestrator.py` 局部，未列其它消费方 | 回归面超出声明文件域；统计、导入、激活、路由的 None 分支未纳入测试 |
| P1-09 | 中 | `PKG-B2-01:41` | `test_disable_switch_failure_restores_enabled` 要求切换失败后把 A 重新 enabled；D-05 只要求「失败显式返回、不得静默不一致」，未要求补偿式回滚 toggle | 验收要求超出决策；与「禁用立即生效」语义可能冲突 |
| P1-10 | 中 | `PKG-B2-01:14,28` | §1 把 `cookie_parser.py:111-307` 纳入「只改内容与合并规则」，§2 又写「不改 `cookie_parser.py`（无缺口则零改动）」 | 范围自相矛盾，文件域是否包含 `cookie_parser.py` 不可判定 |
| P1-11 | 中 | `PKG-B2-01:43`；`account_store.py:764-767` | `apply_preset` 当前先把 `active_account_id` 清空再 `_save_registry`，正是 D-05「registry 先改」缺陷的另一半；包只在测试里要求「无 registry 先改后切」，§1/§2 未明确改 store 层 | preset 路径的 D-05 修复可能只改路由、遗漏 store，测试与实现脱节 |
| P1-12 | 低 | `PKG-B2-01:59` | 证据日期写 `2026-10-29`（相对今日为未来），B2-02/B2-03 写 `2026-10-28` | 证据归档口径不一致 |

## 验收门槛

- 范围是否闭合：**否**，D-01 冲突（P1-02），D-13 字段未删（P1-06），`cookie_parser.py` 范围矛盾（P1-10）。
- 文件域是否可并行：**否**，`session_auth.py` 共享声明与 P3 相反（P1-05），`account_orchestrator.py` 与 P2 重叠，rebind 入口未冻结（P1-03）。
- 测试是否可执行：**部分**，测试文件存在且新增用例已声明；但 P1-07 可能空跑，P1-09 超出决策。
- 证据是否可复现：**基本满足** `docs/dev/evidence/B2/`，日期口径需统一。
- 失败停止/回滚是否明确：**是**，停止条件与逐文件回滚可执行；但「A 仍 enabled」的回滚语义需先裁决（P1-09）。
- 是否误用旧 fork 路径或 534 当任务：**否**，未引用旧 fork 模块；但 B2-01 §8.3 把目标树已有的 12s（`session.py:278`）误称为「旧 fork 12s」。
- 真实测试授权：**未要求**，§4 全部离线；真实账号测试另行审批且不得替代本包验收。

## 给用户的裁决项

1. D-01：是否确认 B2 阶段不实现 `quota_scope`/surface 记账，将 D-01 推迟到 C1/C2；若否，本包 §1 的「不新增 surface」必须撤回。
2. rebind 入口：是否确认 `BrowserSession.transition_to_account(auth_file)` 为唯一入口，并由 P3 在 B2 开工前冻结返回值/失败语义。
3. 服务层签名：`toggle_account_enabled`/`apply_preset` 的 async 化与返回值/异常，由 P1 定义还是由 P3 一并定义。
4. 切换失败后的状态：是否要求把账号重新 enabled（失败即回滚 toggle），还是只要求显式报错且 registry 不变。
5. D-13：`AccountMeta.notes` 字段是否随 API 一起删除，旧 `meta.json` 的 notes 是否需要迁移/忽略。
6. 预设写失败的可观测形式（500 vs 409）与 `get_active_account` 返回 None 时的 API 语义。

## 只读声明

未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
