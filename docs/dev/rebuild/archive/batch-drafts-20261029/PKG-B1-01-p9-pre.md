# PKG-B1-01 P9-pre 启动装配 / 配置校验 / API guard

- 步骤：B1
- 状态：draft / 未开始；中断时保留本文件与下方 status 行，按 §8 续跑，只完成部分记 `partial`
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（docs HEAD `755e28a`）
- 来源：D-07、D-14；D01-001/002/003/004/006/014/015/039/043/063；D11-015（rewrite）；`05 §4.1 B1`
- 边界：与 PG-01..PG-13 编号无关（PG 只属 B5a）；不写任何账号/页面/路由/生成业务逻辑
- status：`scope=frozen, files=pending, tests=pending, evidence=pending`

## 1. 范围

做：
- 启动装配：`install_process_cleanup_handlers` → client → AccountStore/AccountService → rotator → warmup/tier task（`api/app.py:53-134`）；关闭顺序 cancel → await → `client.close()` → 清空 runtime_state（`:136-149` 现 cancel 后未 await）。
- 配置校验（D-07）：`_load_yaml_config` 仅 `merged.update(loaded)` 浅合并（`model_defaults.py:460-461`），`rotator` 缺键时请求期在 `account_rotator.py:712-715` 抛 `RuntimeError`；改深合并默认 + 启动时校验类型/必填，不推迟到首个请求。
- API guard（D-14）：新增 `security.api_allow_anonymous`（默认 false）；空 Key 不再等于开放（`dependencies.py:78-80`）；删除 `require_api_key` 接受 `web_password` 的超级通道（`:98-105`）；管理页仍只由 `AISTUDIO_WEB_PASSWORD` 控制，空密码=免密不报错（`config.py:275-278`）。

不做：账号/Cookie/页面/路由/生成逻辑；UI 卡片（D-12 绑定属 C2/P8）；`/api-keys` CRUD；D-04 的 403 领域错误。

## 2. 文件域

修改：`config.py`（`api_allow_anonymous`）、`api/dependencies.py`（guard 重写）、`api/app.py`（启动校验调用点、关闭顺序）、`infrastructure/gateway/model_defaults.py`（rotator 深合并、security getter、校验）、`api/routes_system.py`（仅在既有 `GET /config` 返回该开关，不新增路由）、`config.yaml` 与 `defaults/config.yaml`（`security.api_allow_anonymous: false`）。
新增：`tests/unit/test_boot_config_validation.py`。改写：`tests/unit/test_api_auth.py`（`:23-29` 现断言空 Key→200，须反转）。

共享语义/冲突：两个 `config.yaml` 本包独占，P1 不得改；`api/app.py` 与 PKG-B2-03（错误映射）冲突时本包优先（装配先于业务）；`model_defaults.py` 与 PKG-B2-03 共享，**D-07 深合并/启动校验由本包主改**（该包 §3 亦依赖本包），本包只动 `rotator`/`security` 两段。

## 3. 依赖

- 无前置包（B 阶段第一步）。
- 冻结接口：`get_configured_security_settings() -> {"api_allow_anonymous": bool}`，P2/P8 只读，不得各自解析 YAML。
- 配置：`security.api_allow_anonymous`（默认 false）；`AISTUDIO_WEB_PASSWORD` 语义不变。fixture：临时 `config.yaml` + monkeypatch `settings`，不需账号/IP/Google。

## 4. 测试

| 用例 | 输入 | 期望 | 方式 |
|---|---|---|---|
| `test_api_auth.py::test_empty_keys_and_anonymous_flag` | `api_keys=∅`；flag=false / flag=true | 401 / 200（非静默开放） | 离线 |
| `test_api_auth.py::test_web_password_not_api_key` | `x-api-key=web_password` | 401 | 离线 |
| `test_api_auth.py::test_web_auth_empty_password_is_open` | `web_password=""` | 管理路由 200 | 离线 |
| `test_boot_config_validation.py::test_rotator_partial_block_deep_merges` | `rotator: {tier_weights: {}}` | 缺失键取默认，不抛 | 离线 |
| `...::test_rotator_bad_type_fails_at_startup` | `tier_weights: "x"` | lifespan 启动失败并记录 | 离线 |
| `...::test_default_config_template_has_security_block` | `defaults/config.yaml` | 含 `api_allow_anonymous: false` | 离线 |

授权要求：全部离线，禁止真实账号/IP/Google。

## 5. 命令与证据

```powershell
# workdir = .kilo/worktrees/rebuild-upstream
uv run pytest tests/unit/test_api_auth.py tests/unit/test_boot_config_validation.py -q
uv run pytest -q
uv run ruff check src tests
uv run pyright
```
证据 `docs/dev/evidence/B1/2026-10-29/`：pytest 日志、启动成功/校验失败日志、脱敏 `GET /config` 响应。脱敏：不落 Key、密码、cookie、真实邮箱/IP。

## 6. 失败停止

- 空 Key 仍 200，或 `web_password` 仍可当 API Key → 停。
- 请求期仍出现 rotator 缺键异常，或校验失败但服务照常启动（静默回退）→ 停。
- 关闭流程仍有未 await 的后台任务告警 → 停。
- 同一用例重试 >2 次、需新依赖、需新增 D-xx 或改 `05` 顺序 → 停并上报用户。

## 7. 回滚

回滚点：包开始前目标树 HEAD。受影响文件同 §2，全为文本改动，逐文件 `git checkout -- <path>`。配置回滚：删除 `security:` 块（缺失即默认 false，**不会**退回开放）。无 data/ 迁移。回滚后重跑 §5 全量。

## 8. 未决

1. D11-015 台账归 P8，但 guard 语义须在 B1 冻结；请裁决「后端 guard 在 B1、UI 卡片在 C2/P8」，否则 B1 无法独立验收。
2. `security.api_allow_anonymous` 是否允许 env 覆盖（如 `AISTUDIO_API_ALLOW_ANONYMOUS`）；本包默认只读 `config.yaml`。
3. rotator 启动校验的必填键清单（`tier_weights`/冷却/阈值）需 reviewer 冻结；本包只列候选。
4. 校验失败是「进程退出」还是「降级启动 + 显式告警」——D-07 只禁止推迟到首次请求，两者未定。
5. `client.close()` 是否加超时；本包按现状不加重试，需确认。
6. 与 PKG-B2-03 的 D-07 归属重叠：该包 §2 也列 `model_defaults.py` 深合并/校验、§8 列为未决；本包按「B1 主改、P2 只读」执行，需确认该仲裁。
