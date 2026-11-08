# REV-B1-01 PKG-B1-01 P9-pre review

- 被审包：`docs/dev/rebuild/domains/P9-boot/packages/PKG-B1-01-p9-pre.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（HEAD `755e28a`，`src`/`tests` 与 `9b5cdea` 一致）

## 结论

模板字段均已提供（范围/文件域/依赖/测试/命令/证据/失败停止/回滚/未决），仓库内引用行号全部可对上（`api/app.py:53-149`、`dependencies.py:78-105`、`model_defaults.py:460-461`、`account_rotator.py:712-715`、`config.py:275-278`、`test_api_auth.py:23-29`），可离线执行；但 D-07 归属尚未闭合、rotator 深合并用例判别力不足、启动失败口径与未决项自相矛盾，需先裁决后再放行。

## 字段核对

| 字段 | 结论 | 说明 |
|---|---|---|
| 范围 | 基本闭合 | 做/不做明确；D-07 与 B2-03 重叠（B1-01） |
| 文件域 | 可执行但冲突 | 修改/新增/共享已列；`model_defaults.py`、`api/app.py` 与 B2-03 冲突 |
| 依赖 | 部分 | 声明无前置并冻结 `get_configured_security_settings()`；D-07 主改方未定 |
| 测试 | 部分可执行 | 6 个用例，命令可跑；深合并与安全默认值判别力不足（B1-02、B1-05） |
| 命令 | 齐全 | 定向 pytest + 全量 pytest + ruff + pyright |
| 证据 | 齐全 | `docs/dev/evidence/B1/...`，脱敏明确；日期口径不一致（B1-07） |
| 失败停止 | 齐全 | 4 条停止条件；与 §8.4 未决口径冲突（B1-03） |
| 回滚 | 齐全 | 逐文件 checkout + 删除 `security:` 块；默认 false 需测试支撑（B1-05） |
| 未决 | 不完整 | 6 条已列；§8.3 缺少候选键，§8.4 与测试冲突 |

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| B1-01 | 高 | `PKG-B1-01:24,69`；`PKG-B2-03:18,32,79` | B1 断言「B1 主改 `model_defaults.py`/`api/app.py`，P2 只读」，但 B2-03 §1/§2 仍把 D-07 深合并/启动校验列为本包职责，§8 又写「需先定主改方」；B1 的单方仲裁未被 B2-03 接受 | B2 并行时两包改同一文件、重复测试；B1 不能作为 P2 的稳定前置 |
| B1-02 | 高 | `PKG-B1-01:39`；`model_defaults.py:756-766`；`account_rotator.py:712-715` | 用例输入 `rotator: {tier_weights: {}}`，期望「缺失键取默认，不抛」，但未断言任何缺省键值；`get_configured_rotator_settings()` 对 dict 原样返回，浅合并下该输入同样不抛（空权重取 0.0） | 该用例在未修复代码上也会通过，D-07 深合并验收可能假阳性 |
| B1-03 | 中 | `PKG-B1-01:40,59,72` | §4 期望 `tier_weights: "x"` 在 lifespan 启动失败，§6 以「校验失败但服务照常启动」为停止条件；§8.4 却仍把「进程退出 vs 降级启动 + 告警」列为未决 | 验收口径自相矛盾，实现者无法同时满足测试、停止条件与未决项 |
| B1-04 | 中 | `PKG-B1-01:40,71` | §8.3 称「必填键清单需 reviewer 冻结，本包只列候选」，但正文没有列出任何候选键；§4 只有类型错误用例，没有缺必填键用例 | 启动校验的必填范围与验收不可执行 |
| B1-05 | 中 | `PKG-B1-01:21,41` | D-14 开关测试只检查 `defaults/config.yaml` 文本；`_default_config()`（`model_defaults.py:271`）目前无 `security` 段，`get_configured_security_settings()` 在目标树不存在，测试未验证代码默认值与缺失配置时的行为 | D-14「默认 false」未被程序化断言；§7 回滚声明（删块即默认 false）无测试支撑 |
| B1-06 | 中 | `PKG-B1-01:21,29` | `GET /config`（`routes_system.py:250-275`）当前返回 `yaml_content`/`auth_enabled` 等；包只写「返回该开关」，未定义字段名、层级与失败语义；冻结接口 `get_configured_security_settings()` 为新函数，未给缓存/异常契约 | P2/P8 无法按冻结接口消费，前端字段位置不可审计 |
| B1-07 | 低 | `PKG-B1-01:54` | 证据目录写 `docs/dev/evidence/B1/2026-10-29/`，B2-02/B2-03 写 `2026-10-28`，且 B1 无 `PKG-*` 子目录 | 证据归档口径不一致，跨包审计时对不上 |
| B1-08 | 低 | `PKG-B1-01:14,73` | 关闭顺序写「cancel → await → `client.close()`」，但未定义 await 取消任务时的 `CancelledError`/超时处理；§8.5 又把 close 超时列为未决 | 关闭流程的验收边界不清，但不影响启动/guard 主项 |

## 验收门槛

- 范围是否闭合：**基本闭合**，但 D-07 与 B2-03 重叠（B1-01），`GET /config` 字段契约未定（B1-06）。
- 文件域是否可并行：**否**，`model_defaults.py`/`api/app.py` 与 B2-03 冲突，需先裁决主改方（B1-01）。
- 测试是否可执行：**是**，命令可在目标树运行；但 rotator 深合并与安全默认值用例判别力不足（B1-02、B1-05）。
- 证据是否可复现：**基本满足** `docs/dev/evidence/B1/`，日期口径需统一（B1-07）。
- 失败停止/回滚是否明确：**是**，停止条件与逐文件回滚可执行；启动失败口径需先定（B1-03）。
- 是否误用旧 fork 路径或 534 当任务：**否**，未引用旧 fork 模块，未把 534 当清单。
- 真实测试授权：**未要求**，§4 全部离线，未要求真实账号/IP/Google。

## 给用户的裁决项

1. D-07 主改方：按 05 的 B1→B2 顺序由 B1 主改、B2-03 只读，是否确认；若不确认，B2-03 必须先改。
2. D11-015 归属：后端 guard 在 B1、UI 卡片在 C2/P8，是否接受（B1 自述否则无法独立验收）。
3. rotator 启动校验的必填键清单，以及校验失败是「进程退出」还是「降级启动 + 显式告警」。
4. `security.api_allow_anonymous` 是否允许 env 覆盖（如 `AISTUDIO_API_ALLOW_ANONYMOUS`）。
5. `GET /config` 返回该开关的字段名/位置，是否允许本包修改 `routes_system.py`。
6. `client.close()` 是否加超时。

## 只读声明

未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
