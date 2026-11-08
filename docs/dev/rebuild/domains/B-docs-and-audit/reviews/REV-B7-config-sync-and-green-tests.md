# REV-B7 配置模板单一来源、启动迁移与 Windows 全绿测试独立复核

- 复核日期：2026-11-01（UTC+8）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标分支 / HEAD：`rebuild-upstream` / `7cf25883a4c361bddda5b1c3da81f9cf3668c1a2`
- 主工作树：`D:\LLM\self_programming\aistudio-api`（HEAD `e3354b4`，工作区 clean）
- 审计范围：A. Docker/defaults 单一来源与启动迁移；B. Windows 0 failed / 0 skipped；C. `/health` 200 静默
- 审计模式：严格离线、只读；未启动 `main.py server`、浏览器/Chromium、服务端、Docker 构建或任何网络请求
- 代码改动：无；未修改 `src/`、`tests/`、`config.yaml`、`defaults/config.yaml` 或既有文档；未 stage/commit/push
- 唯一写入：本报告的目标树副本与主仓副本

## 0. 结论

**Verdict: PASS。**

- A：`defaults/config.yaml` 已成为运行时默认值的唯一模板来源；Docker 镜像只复制一次模板，entrypoint 只在缺失时播种；启动迁移能移除 legacy 键、归一 Free-only selector、补齐当前配置块，并原子写回且跳过模板本身。核心行为均有真实测试与独立复现证据。
- B：Windows 本机实跑 **881 passed, 0 failed, 0 skipped**；CloakBrowser 严格契约、`new Function(...)` 包装表达式、watchdog Windows/POSIX 分支均仍被真实校验，未通过 skip 或弱断言换绿。
- C：`/health` 200 成功路径不再产生任何 access log 记录（INFO 与 DEBUG 均无），非 200 仍为 INFO，其它路径行为未变；测试以 `caplog.at_level(DEBUG)` 证明 200 无记录。
- 独立门禁全部通过：pytest 0 failed / 0 skipped，Ruff 0，Pyright 0，`git diff --check` / `--cached --check` 0，根 `config.yaml` 未跟踪。
- 无 P0/P1 阻断项；存在若干 P2（非关键 fallback 写回、`auth_error` 契约注释漂移、迁移重写丢注释/只读失败、测试覆盖缺口、entrypoint 窄边界优先级），均不影响本次 PASS。

## 1. 基线、范围与工作树状态

| 项 | 结果 | 证据 |
|---|---|---|
| 目标 HEAD | `7cf25883a4c361bddda5b1c3da81f9cf3668c1a2` | `git rev-parse HEAD` |
| 目标分支 | `rebuild-upstream` | `git rev-parse --abbrev-ref HEAD` |
| 主工作树 HEAD | `e3354b4`，`git status --porcelain` 为空 | 实跑 |
| 未暂存修改 | 13 个文件，`285 insertions(+), 281 deletions(-)` | `git diff --stat` |
| 未跟踪新增 | `tests/unit/test_config_migration.py`（271 行） | `git status --porcelain` |
| 暂存区 | 空（`git diff --cached --stat` 无输出） | 实跑 |
| 根 `config.yaml` | 未跟踪（`git ls-files config.yaml` 为空），复核前后大小 5166 bytes、mtime 未变 | 实跑 |
| `defaults/config.yaml` | 已跟踪（`git ls-files defaults/config.yaml` 非空） | 实跑 |
| Dockerfile / entrypoint | 已跟踪且本 diff 未修改；作为 HEAD 既有实现审计 | `git diff --name-only` 不含二者 |
| 复核新增 | 本报告 1 个未跟踪文件（目标树；主仓同路径副本） | 本文件 |

**未提交 / 未 push。**

## 2. A. Docker/defaults 单一来源与启动迁移

### 2.1 Dockerfile 与 entrypoint

| 检查项 | 结论 | 证据 |
|---|---|---|
| 模板 COPY 恰好一处且目标正确 | **PASS** | `Dockerfile:93` 为唯一 `COPY defaults/config.yaml /app/defaults/config.yaml`；awk/Select-String 计数 = 1 |
| 源码与静态前端随镜像复制 | **PASS** | `Dockerfile:91` `COPY src/ src/` |
| 运行时配置环境变量 | **PASS** | `Dockerfile:10` `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`；`Dockerfile:11` `AISTUDIO_BROWSER_EXECUTABLE=/opt/cloakbrowser/chrome` |
| `AISTUDIO_CONFIG_PATH` 优先 | **PASS（窄边界见 P2-5）** | `docker-entrypoint.sh:5` 先取 `AISTUDIO_CONFIG_PATH`；`:8-14` 仅在 PATH 未设置/等于默认路径时回退旧 `AISTUDIO_CONFIG_FILE`；`:16` 导出最终路径 |
| 仅在缺失时播种 | **PASS** | `docker-entrypoint.sh:19-22`：`if [ ! -f "$CONFIG_FILE" ] && [ -f "$TEMPLATE_FILE" ]` 才 `cp`；已有文件绝不覆盖 |
| 入口脚本执行 | **PASS** | `docker-entrypoint.sh:24` `exec "$@"` |

### 2.2 `defaults/config.yaml` 作为唯一默认来源

| 检查项 | 结论 | 证据 |
|---|---|---|
| 默认模板解析 | **PASS** | `src/aistudio_api/config.py:80-102`：优先 `<repo>/defaults/config.yaml`，其次 `/app/defaults/config.yaml`；缺失抛 `FileNotFoundError`，不回退内嵌字典 |
| 仓库路径实跑 | **PASS** | 解析结果 `<worktree>/defaults/config.yaml`（独立 python 探针） |
| Docker 路径实跑 | **PASS（模拟）** | 将 `_PROJECT_ROOT` 指向不存在目录并仅让 `/app/defaults/config.yaml` 报告存在，解析结果命中 `/app/defaults/config.yaml` |
| 模板缺失行为 | **PASS** | `model_defaults.py:985-997` 将 `FileNotFoundError` 转 `ConfigValidationError`；测试 `tests/unit/test_config_migration.py:103-115` |
| 模板非法 YAML/非字典 | **PASS（实现）** | `model_defaults.py:998-1007` 对 `OSError`/`YAMLError`/非字典分别抛 `ConfigValidationError`（测试仅覆盖缺失，见 P2-4） |
| 深拷贝隔离 | **PASS** | `model_defaults.py:1009-1015` `copy.deepcopy(_default_config_template())`；测试 `test_config_migration.py:81-90` 修改嵌套 list/dict 后第二次读取不变 |
| 模板缓存失效 | **PASS（实现）** | `model_defaults.py:1022-1029` 的 `invalidate_config_cache()` 同步 `_default_config_template.cache_clear()` |
| 无内嵌默认基线残留 | **PASS** | 本 diff 删除旧 `_default_config()` 的 ~235 行内嵌基线；`src/` 内仅 `model_defaults.py` 的 `_default_config()` 返回模板深拷贝，无第二份默认字典 |
| YAML 可解析 | **PASS** | `defaults/config.yaml` `yaml.safe_load` 返回 dict |
| `features.atg.mode` 引号化 | **PASS（必要修复）** | `defaults/config.yaml:73` `mode: "off"`；PyYAML 实测未加引号 `off` 解析为 `False`，加引号为字符串 `'off'`，与旧内嵌基线的字符串语义一致 |
| 模板受版本控制 | **PASS** | `git ls-files defaults/config.yaml` 非空 |

### 2.3 启动迁移（`validate_config()` from lifespan）

`app.py:280` 在 `lifespan()` 首行调用 `validate_config()`。迁移主流程位于 `model_defaults.py:658-685`：

| 检查项 | 结论 | 证据 |
|---|---|---|
| 移除 legacy 键 `groups/channel_registry/channels` | **PASS** | `model_defaults.py:554-575`；测试 `test_config_migration.py:138-141` 断言三键消失 |
| 仅 Free-only selector 归一为 `only_tier: Free` | **PASS** | `model_defaults.py:578-610`；测试 `:180-202` 覆盖 Free 闭区间换算，Paid 区间原样保留 |
| 拒绝 Free/Paid 混合而非静默改写 | **PASS** | `_migrate_legacy_instance` 对 `only=Free` 仅删除 Free 上下界；`min_tier: Free, max_tier: Pro` 保留后由 `validate_config_mapping` 抛错；测试 `test_config_migration.py:234, 250-254` |
| 补齐当前配置块 | **PASS** | `validate_config` 用 `_deep_merge(_default_config(), migrated)`；测试 `:163-173` 断言 `diagnostics/features/tier_quotas/paid_model_rules/account_presets` 均出现 |
| 保留 rich `api_keys` | **PASS** | 测试 `test_config_migration.py:145-151`；独立探针同样保留 `key/name/created_at` |
| 保留 `security` | **PASS** | 测试 `:152`；深合并仅覆盖同名键 |
| 保留自定义实例 count/surface | **PASS** | 列表整体覆盖（`_deep_merge` `:92-102`）；测试 `:142-144` 保留 `count: 3` 与 `surface: chat` |
| 保留模型覆盖 | **PASS** | 测试 `:157-162` 保留 custom `model_defaults.models` |
| 保留未知 `proxy`/自定义键 | **PASS** | 测试 `:153`；`_deep_merge` 对未知键直接保留 |
| 只在内容实际变化时写回 | **PASS** | `model_defaults.py:676-680`：`exists && not template && (migration_changed or merged != raw)`；测试 `:205-224` 对干净模板副本断言 `os.replace` 0 次且 mtime 不变 |
| 原子写回 | **PASS** | `_atomic_write_text` `:1031-1049`：同目录 `mkstemp` + `fsync` + `os.replace`；测试 `:122-136` 跟踪 `os.replace` 恰 1 次、临时目录为同目录 |
| 写后缓存失效 | **PASS（实现）** | `validate_config` 写后调用 `invalidate_config_cache()` `:683`；未在测试中断言该调用（P2-4） |
| 绝不改写模板 | **PASS** | `_is_default_template_path` `:650-655` + 条件 `:678`；测试 `:257-271` 令写函数直接失败仍不触碰模板 |
| 非法配置不部分写入 | **PASS** | `_read_config_file`/`validate_config_mapping` 均在写前抛错；参数化测试 `:227-254` 断言 `os.replace` 0 次且原文件字节不变 |
| 测试真实性 | **PASS（含一处近似恒真断言）** | 迁移测试读取真实临时文件、跟踪真实 `os.replace`、断言落盘内容，均非 mock 自证。`test_config_migration.py:69` 的 `_default_config() == template` 因二者同源而近似恒真，但 `:81-90` 的嵌套变异隔离断言是真实校验；其余迁移/非法输入/模板保护测试均非恒真 |

**独立复现（离线 python 探针）**：用 legacy 配置调用 `validate_config()` 后，落盘结果无 `groups/channel_registry/channels`，`instances=[{count:3,surface:chat,only_tier:Free}]`，`api_keys/security/proxy/model_defaults.models` 完整保留，`tier_quotas/paid_model_rules/account_presets/diagnostics` 已补齐；`_default_config()` 深拷贝隔离成立。

## 3. B. Windows 0 failed / 0 skipped

| 检查项 | 结论 | 证据 |
|---|---|---|
| CloakBrowser 严格契约 | **PASS** | `tests/unit/test_browser_engine.py:60-133`：CloakBrowser 存在 -> 返回；全部目录不存在 -> `FileNotFoundError("未检测到 CloakBrowser")`；显式路径 -> 返回；无 skip、无弱化 |
| `/opt/cloakbrowser` 分支 | **PASS** | 生产常量 `browser_engine.py:25-27`，使用点 `:105`；测试 `:112` 先断言 `Path("/opt/cloakbrowser") == _OPT_CLOAKBROWSER_DIR`，再 patch 模块常量为真实临时目录并断言命中 `chrome`；本仓无 `.cloakbrowser` 目录，`Path.home` 被指向空目录，分支隔离成立 |
| 包装表达式语法校验 | **PASS** | `tests/unit/test_browser_scripts_syntax.py:176-230`：将 `({expr})({arg})` 经 `json.dumps` 保真写入临时 `.js`，用真实 `bun`/`node` 执行 `new Function(...)`；只有 `SyntaxError` 会中止，非空校验 |
| Windows 无 skip（runtime 存在时） | **PASS** | 本机 `bun` 存在，实跑 0 skipped；仅当 `bun`/`node` 均不存在时才走通用 skip（`:178-180`） |
| watchdog 不再 skip | **PASS** | `tests/unit/test_browser_engine.py:171-181` Windows 分支直接断言 `(None, None)`，无 `pytest.skip` |
| Windows Job Object 回退 | **PASS** | `browser_engine.py:784-785` Windows 返回 `(None, None)`；测试 `:164-168` 与 `:177-181` 双重覆盖 |
| POSIX live pipe-EOF kill 分支 | **PASS（未改）** | `test_browser_engine.py:183-207` 仍启动真实 sleep 子进程、关闭 pipe 写端并断言目标被杀 |
| 编码修复仅测试侧 | **PASS** | `test_response_parser.py`/`test_wire_codec.py`/`test_wire_parser.py` 仅给 `read_text` 补 `encoding="utf-8"`；`src/` 无对应解码改动，属测试文件读取修复，不掩盖生产解码问题 |

## 4. C. `/health` 200 静默

| 检查项 | 结论 | 证据 |
|---|---|---|
| 200 无 INFO | **PASS** | `src/aistudio_api/api/app.py:692-702`：`elif not is_static` 下仅当 `not (path == "/health" and status == 200)` 才 `logger.info` |
| 200 无 DEBUG | **PASS** | 该分支已无任何 `logger.debug`；此前 DEBUG 分支被整体移除 |
| 无 broadcast ring 记录 | **PASS（逻辑必然）** | 200 路径不创建任何 `LogRecord`，`BroadcastHandler` 无记录可入 ring；测试 `tests/unit/test_logging.py:385-394` 在 `caplog.at_level(logging.DEBUG)` 下断言无 `HTTP GET /health -> 200` |
| 非 200 仍 INFO | **PASS** | `app.py:695-702` 条件对 503 为真；测试 `test_logging.py:419-436` 断言 INFO + `HTTP GET /health -> 503` |
| 其它路径不变 | **PASS** | diff 仅收敛 `/health` + 200 条件；其余路径仍走原 `logger.info` |
| 静态资源路径不变 | **PASS** | `is_static` 判定 `:604` 未改 |

## 5. 独立门禁（目标工作树实跑）

执行环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; $env:UV_OFFLINE='1'`；工作目录为目标工作树。`UV_OFFLINE=1` 用于强制严格离线，命令主体仍为任务指定命令。

| 门禁 | 精确结果 |
|---|---|
| `uv run pytest -q` | **881 passed in 12.45s**（0 failed / 0 skipped） |
| `uv run ruff check .` | `All checks passed!`（0 errors） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `git diff --check` | exit 0 |
| `git diff --cached --check` | exit 0 |
| `git ls-files config.yaml` | 空 |

## 6. Findings（P0/P1/P2）

### P0

无。

### P1

无。

### P2（均为非阻断）

1. **迁移写回的是 `merged` 而非已归一化的 `validated`，非关键 fallback 不会被持久化**
   - `validate_config` `:672` 得到 `validated`（其中 `validate_config_mapping` 于 `:442` 调用 `_apply_noncritical_fallbacks` 修正非法值），但 `:674` 重新 `_deep_merge` 得到 `merged`，`:681` 写回 `merged`。
   - 独立复现：临时配置 `logging: {level: BOGUS}` -> 运行时告警并回退 INFO，但写回文件仍为 `level: BOGUS`。
   - 影响：运行时行为安全（读取侧继续 fallback），不会崩溃或丢数据；但“自愈”只补齐缺失块，不修正已存在的非关键非法值。建议写回 `validated` 或对 `merged` 也执行 `_apply_noncritical_fallbacks`。

2. **`auth_error` 的契约注释与实际默认值漂移**
   - `model_defaults.py:473-474` 注释仍写 “optional ... defaulting to 0 (403 has no hard lock)”，但模板 `defaults/config.yaml:41` 为 `auth_error: 300`；`_default_config()` 改为模板后，深合并使有效默认值变为 300，`else: cooldowns["auth_error"] = 0`（`:482-483`）实际不可达。
   - 该键当前是 dead knob：`account_rotator.py:340-345` 只读取 `rate_limited_short/drip/drip_extended`，无任何代码读取 `cooldowns["auth_error"]`；且 `record_auth_error` 固定 `auth_cooldown = 0.0`（`:383-385`），故当前无运行时影响。
   - 测试 `tests/unit/test_features_config.py:648-667` 已改为“继承模板值”，但这也掩盖了旧内嵌基线 0 -> 模板 300 的语义漂移。建议同步注释，或将模板改回 0 以保持 403 无硬锁的不变量。

3. **迁移重写丢弃 YAML 注释；只读配置会使启动失败**
   - 写回使用 `yaml.dump`（`model_defaults.py:681`），旧运行配置中的注释/格式在发生迁移时全部丢失（模板本身受 `_is_default_template_path` 保护，不受影响）。
   - `_atomic_write_text`（`:1031`）的 `OSError` 未捕获，`validate_config` 在需要迁移但配置只读/磁盘不可写时会直接抛错并阻断 `lifespan()` 启动；此前版本不会写回。
   - 影响：Docker `/app/data` 通常可写；只读挂载或权限不足的部署需注意。属行为变化，建议至少记录或降级为告警。

4. **迁移/模板测试覆盖缺口（不影响当前通过）**
   - `test_default_config_matches_tracked_template_and_is_deep_copied` 的 `:69` 等值断言与实现同源，近似恒真；可保留作为解析冒烟，但真正的回归价值在 `:81-90` 的深拷贝隔离。
   - 模板“非法 YAML / 顶层非字典”分支（`:998-1007`）无测试；仅测了缺失模板 `:103-115`。
   - 写后 `invalidate_config_cache()` 未被断言；`test_config_migration.py:136` 用 `tmp_path.glob("*.tmp")`，而临时文件以 `.` 开头，该 glob 不保证能发现隐藏残留（实现本身由 `os.replace` 成功与异常清理保证）。
   - 建议补一条非法模板测试与一条缓存失效断言。

5. **entrypoint 的窄边界优先级：显式 `AISTUDIO_CONFIG_PATH` 恰等于默认路径时会输给 `AISTUDIO_CONFIG_FILE`**
   - `docker-entrypoint.sh:10-13`：当 `AISTUDIO_CONFIG_PATH=/app/data/config.yaml`（等于默认值）且 `AISTUDIO_CONFIG_FILE` 为非默认路径时，最终采用后者。
   - 这是有意的向后兼容（注释 `:8-9` 明确“PATH 显式设为非默认路径时才胜出”），与 `config.py:118-121` 的“PATH 无条件优先”在窄边界上不完全一致；常规部署（PATH 为非默认自定义路径）不受影响。属既有实现，非本 diff 引入。

## 7. 残余风险

1. **无实机/镜像验证**：本次未执行 `docker build`、未启动服务/浏览器；Dockerfile/entrypoint 结论基于静态审计、路径解析探针与单元测试。
2. **迁移会改写旧运行配置**：首次启动旧配置会丢失注释并补齐全部默认块；这是有意自愈，但对人工维护的注释型配置有一次性破坏性。建议在发布说明中提示备份。
3. **`auth_error` 潜在语义漂移**：当前 dead knob，若未来有代码读取该键，默认 300 会与“403 无硬锁”策略冲突（见 P2-2）。
4. **测试环境依赖**：`test_js_ast_syntax_with_external_runtime` 与 `test_evaluate_wrapper_expressions_parse_cleanly` 在无 `bun`/`node` 的环境会通用 skip；本次 Windows 主机具备 `bun`，故达成 0 skipped。
5. **未验证真实上游/真实账号**：严格离线要求下无 live 行为复核。

## 8. 只读与提交状态声明

- 本次复核未修改 `src/`、`tests/`、`config.yaml`、`defaults/config.yaml`、前端或既有文档；仅新增本报告。
- 未启动 `main.py server`、浏览器/Chromium、服务端、Docker 构建；未发起任何本地或外部网络请求。
- 未 stage、未 commit、未 amend、未 push。
- **未提交 / 未 push。**
- 本报告在目标树与主仓逐字节一致；两份文件的路径与 SHA256 由交付说明给出。

---

## 9. B7 闭环复核（2026-10-31）

- 复核对象：目标树 `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`（HEAD `a089bdb`，6 个未提交修改）；主仓 `D:\LLM\self_programming\aistudio-api`（HEAD `084ad7c`）
- 复核模式：严格离线、只读；未启动 `main.py server`、浏览器/Chromium、服务端、Docker 构建或任何网络请求；未修改 `src/`、`tests/`、`config.yaml`、`defaults/config.yaml` 或既有文档章节
- 唯一写入：本节追加到本报告的目标树副本与主仓副本；未 stage、未 commit、未 push
- 本轮范围：P2-1..P2-5 关闭复核 + `_normalize_retry_same_delay` 拆分 + 全量门禁复跑

### 9.1 P2-1..P2-5 关闭结论

| 项 | 结论 | 证据（`file:line`） |
|---|---|---|
| **P2-1** 写回 post-fallback `validated`，非关键非法值自愈落盘 | **RESOLVED** | `validate_config_mapping` 在深合并副本上执行 `_apply_noncritical_fallbacks(config)` 后返回该 `config`（`model_defaults.py:431-442`，`:442`）；非法 `logging.level` 在该函数内被改写为 `INFO`（`:124-132`）；`validate_config` 以 `validated != raw` 判定变化并写回 `yaml.dump(validated, ...)`（`:672-679`，`:677`、`:679`）。关键块非法由 `validate_config_mapping` 在写回块之前抛错（`:444-551`），不会进入写回。测试：`tests/unit/test_config_migration.py:275-283`（`validated` 与磁盘均为 `INFO`）、`:285-294`（关键非法抛错、原字节不变、无 `.tmp` 残留）。独立探针：`p1_validated_level='INFO'`、`p1_persisted_level='INFO'`；`p2_raised=True`、`p2_unchanged=True`、`p2_tmp_residue=[]` |
| **P2-2** `auth_error` 默认值与契约注释对齐，根配置不跟踪不泄密 | **RESOLVED** | 模板 `defaults/config.yaml:41` 为 `auth_error: 0`；契约注释与兜底同值（`model_defaults.py:473-483`）；测试显式断言模板值为 0 且有效值为 0（`tests/unit/test_features_config.py:648-670`，`:668-669`）。根 `config.yaml` 已更新为 `auth_error: 0`（`:23`），但未跟踪：`git ls-files config.yaml` 空、`.gitignore:15` 为 `/config.yaml`、`git status --ignored` 显示 `!! config.yaml`，且不在 6 个已修改文件中；未把任何密钥写入跟踪文件 |
| **P2-3** 原子写 `OSError` 被捕获、告警后继续、无临时残留 | **RESOLVED（写失败容错口径）** | `_atomic_write_text` 在 `os.replace` 失败时经 `except BaseException` 删除同目录隐藏临时文件并重抛（`model_defaults.py:1048-1063`，`:1060-1063`）；`validate_config` 捕获 `OSError` 记录 warning、跳过缓存失效并返回内存中 `validated`（`:680-691`，`:682-687`、`:691`）；`lifespan()` 首行调用后继续启动（`api/app.py:280`）。测试：`tests/unit/test_config_migration.py:297-320`（写函数抛 `PermissionError` → warning + 返回 `validated` + 原文件不变 + 无残留）、`:323-344`（`os.replace` 失败 → 隐藏临时文件清理 + 无残留）。独立探针：warning 命中、`p3_instances=[{'count': 2, 'surface': 'chat', 'only_tier': 'Free'}]`、`p3_file_unchanged=True`、`p3_tmp_residue=[]` |
| **P2-4** 模板非法输入/缓存失效/隐藏残留断言补齐且非恒真 | **RESOLVED** | 模板 YAML 语法错误与顶层非字典分支分别由 `_default_config_template` 抛 `ConfigValidationError`（`model_defaults.py:1014-1022`）；对应参数化测试构造真实临时模板并断言错误消息（`tests/unit/test_config_migration.py:347-369`）。缓存失效测试先向 `_CONFIG_CACHE` 注入 `stale_marker`，调用 `validate_config` 后断言键被 `invalidate_config_cache()` 清除（`model_defaults.py:1039-1045`），再通过真实 `_load_yaml_config`（`:1090-1113`）验证后续读取无 `stale_marker`、已迁移且已重新缓存（`tests/unit/test_config_migration.py:372-400`）。残留断言改为 `iterdir()` + `name.endswith(".tmp")`，可发现隐藏 `.{name}.*.tmp`，替代原先可能漏检的 `glob("*.tmp")`（`tests/unit/test_config_migration.py:137,294,320,344`）。这些断言均为真实文件读写/缓存状态校验，非自证；原 `:70` 同源等值断言仍保留为解析冒烟，但 `:81-90` 深拷贝隔离是实际回归校验 |
| **P2-5** entrypoint 精确优先级与 `config.py` 对齐 | **RESOLVED** | `docker-entrypoint.sh:6-14`：非空 `AISTUDIO_CONFIG_PATH` 无条件胜出，否则非空 `AISTUDIO_CONFIG_FILE`，否则默认；`:16` 导出最终路径；`:19-23` 仅目标缺失且模板存在时 `cp`，已有文件绝不覆盖；`:25` `exec "$@"`。`config.py:105-121` 使用 `os.getenv("AISTUDIO_CONFIG_PATH") or os.getenv("AISTUDIO_CONFIG_FILE")`，与 `-n` 语义一致（空串视为未设置）。测试断言 `if`/`elif` 顺序、两处赋值、默认分支且不再存在旧的 `AISTUDIO_CONFIG_FILE !=` 条件（`tests/unit/test_config_migration.py:403-415`）。`Dockerfile:10` 默认 `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`，与容器默认路径一致 |

### 9.2 `_normalize_retry_same_delay` 拆分复核

- 启动迁移路径 `_apply_noncritical_fallbacks` 只调用 `_normalize_retry_same_delay`（`model_defaults.py:213-215`），该函数不读取任何环境变量（`:958-976`）。
- 环境变量只在 `_resolve_retry_same_delay` 中叠加（`:986-997`），消费点为 `get_configured_routing_settings`（`:1480`）。
- 结论：`AISTUDIO_RETRY_SAME_DELAY_SEC` 只影响运行时返回值，不会把 env 值写脏/持久化进 `config.yaml`。
- 证据：测试全局设置 `AISTUDIO_RETRY_SAME_DELAY_SEC=0`（`tests/conftest.py:182`），而迁移测试仍断言落盘 `retry_same_delay_sec == 2.0`（`tests/unit/test_config_migration.py:166`）；独立探针在 env 覆盖下得到 `p4_persisted_delay=1.5`、`p4_runtime_delay=0.0`、`p4_persisted_is_float_1_5=True`。

### 9.3 本轮门禁（目标工作树实跑）

执行环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; $env:UV_OFFLINE='1'`；工作目录为目标工作树。

| 门禁 | 精确结果 |
|---|---|
| `uv run pytest -q` | **889 passed in 12.19s**（0 failed / 0 skipped） |
| `uv run ruff check .` | `All checks passed!`（exit 0，0 errors） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| `git diff --check` | exit 0 |
| `git diff --cached --check` | exit 0 |
| `git diff --cached --name-only` | 空（无 staged 文件） |
| `git ls-files config.yaml` | 空（根配置未跟踪） |
| `git status --porcelain=v1` | 门禁时 6 个代码/配置/测试文件 ` M`，无新增未跟踪文件；追加本报告后为 7 个（含本报告） |
| HEAD / 分支 | `a089bdb` / `rebuild-upstream` |
| commit / push | 无 |

### 9.4 未关闭项（逐项原文）

- **P1 项**：无。
- **P2 项**：P2-1、P2-2、P2-3（写失败容错口径）、P2-4、P2-5 均已关闭，无新增未关闭 P2。
- **Q/N 项**：本报告自始不存在 Q/N 编号项。
- **原 P2-3 内未随本轮关闭的子项（原文逐字引用）**：
  > 写回使用 `yaml.dump`（`model_defaults.py:681`），旧运行配置中的注释/格式在发生迁移时全部丢失（模板本身受 `_is_default_template_path` 保护，不受影响）。
  - 该子项在本轮 P2-3 的验收口径（只读/写失败容错）之外，当前实现仍为 `yaml.dump(validated, ...)`（当前行号 `model_defaults.py:679`），故转入残余风险 R-2，不虚报为已修复。

### 9.5 残余风险（非阻断）

1. **无实机/镜像验证**：本次未执行 `docker build`、未启动服务/浏览器；Dockerfile/entrypoint 结论基于静态审计、路径解析探针与单元测试。
2. **迁移仍会改写旧运行配置并丢弃注释**：首次启动旧配置会补齐全部默认块；`yaml.dump` 不保留旧注释/格式（原 P2-3 子项）。属有意自愈，但建议发布说明提示备份。
3. **`auth_error` 仍是 dead knob**：默认值已归 0，注释已对齐；当前无代码读取 `cooldowns["auth_error"]`，`record_auth_error` 固定 `auth_cooldown = 0.0`。若未来接入读取方，需重新确认“403 无硬锁”语义。
4. **测试环境依赖外部 JS 运行时**：`bun`/`node` 均缺失时相关语法用例会通用 skip；本次 Windows 主机具备 `bun`，故达成 0 skipped。
5. **未验证真实上游/真实账号**：严格离线要求下无 live 行为复核。

### 9.6 最终结论

**Verdict: PASS（B7 配置同步闭环轮）。**

- P2-1..P2-5 按本轮定义全部关闭，均有源码行号与真实测试/独立探针双重证据。
- 全量门禁：pytest 889 passed、0 failed、0 skipped；Ruff 0；Pyright 0；`git diff --check` / `--cached --check` 0；根 `config.yaml` 未跟踪；无 staged 文件。
- 未发现新的 P0/P1 阻断项；保留的残余风险均为非阻断。
- **未提交 / 未 push。**
