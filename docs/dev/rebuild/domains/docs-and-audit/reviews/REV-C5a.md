# REV-C5a 独立复审报告（终审）

- 复审日期：2026-11-08（终审更新）
- 目标区：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标区 HEAD：`d423efb`（C5a 未提交）
- 主仓：`D:\LLM\self_programming\aistudio-api`
- 主仓 HEAD：`79052b6`
- 复审范围：`README.md`、`README_EN.md`、`docs/changelog.md`、`docs/ARCHITECTURE.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md`、`docs/WIRE_SPECIFICATION.md`、`AGENT.md`、`.github/workflows/docker.yml`、`android/gradle/wrapper/gradle-wrapper.properties`
- 复审方法：独立静态阅读、`file:line` 与源码逐项交叉核对、离线门禁；未启动服务、浏览器或真实网络。
- 只读声明：本次复审未修改源码、测试、配置、文档、AGENT 或 static，未 stage/commit/push；仅写入本报告（目标区与主仓双写）。

## 结论

**PASS**。

- P0：0
- P1：0
- P2：0 未关闭（11 项全部 CLOSED）
- Q：0 未关闭（4 项全部 CLOSED）
- N：2（不阻断 PASS，按既定 C5b/CI 处理）

本轮针对上一版 CONDITIONAL 的 11 个 P2 与 4 个 Q 逐项复验；所有修复均能在当前工作区 `file:line` 与实际文本/代码中对上。代码、构建与离线门禁全部通过。

## 全量门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff | `uv run ruff check .` | PASS，All checks passed |
| Pyright | `uv run pyright src tests` | PASS，0 errors / 0 warnings |
| Pytest | `uv run pytest -q` | PASS，`1279 passed in 30.08s` |
| Web lint | `cd web && bun run lint` | PASS，exit 0 |
| Web type-check | `cd web && bun run type-check` | PASS，exit 0 |
| Web test | `cd web && bun test` | PASS，`69 pass / 0 fail`（604 expect calls，8 files） |
| Web build | `cd web && bun run build` | PASS；构建后 `git status -- src/aistudio_api/static` 为 0，产物与 HEAD 一致 |
| Whitespace | `git diff --check` | PASS（exit 0；仅 CRLF 提示，无 whitespace error） |
| 配置跟踪 | `git ls-files config.yaml`（双仓） | 空 |
| Staged | `git diff --cached --name-only`（双仓） | 空 |
| YAML 解析 | PyYAML `safe_load('.github/workflows/docker.yml')` | PASS |
| 文档行号 | 自写脚本校验显式/同句继承 `file:line[-line]` | 0 个缺失文件、0 个越界 |
| README 锚点 | 自写 GitHub slug 校验 | 中英 TOC 18 个锚点全部命中 |
| 代码围栏 | README/docs/AGENT 全量统计 | 全部成对平衡 |

## 逐项终审：上一轮 P2

### P2-01 CLOSED：README 中英 `dms` 已改为页面内签名函数，不再称“动态模型切换”

- 证据：`README.md:230` 明确“快照预热是页面内本地调用，不依赖切换模型”；触发条件仅为首次、距上次快照超过 600 秒、payload/hash prompt 达到 32 KiB、强制刷新。
- `README.md:232` 与 `README_EN.md:232` 写“暖机与正式快照都通过页面内的 `dms[snapKey](service, hash)` 完成”。
- 与代码一致：`session_state.py:20-41` 的 `should_warm_snapshot` 只有 `force`、首次、`>600s`、`>=32 KiB` 四类条件，没有模型切换参数；`snapshot_generate.js:70-95` 使用 `dms[snapKey]`。
- 中英文本一致；旧“动态模型切换（dms）/ Dynamic model switch (dms)”已不存在。

### P2-02 CLOSED：README 中英 ATG 已限定为按协议 `mode_by_format`，模型仅做 text/non_text 过滤

- 证据：`README.md:58` 写“可通过 `mode_by_format` 按协议启用 `prefer` / `required`；模型配置仅用于 text/non_text 过滤”；`README_EN.md:58` 同义。
- `README.md:260-261` 与 `README_EN.md:260-261` 进一步说明 `mode_by_format` 按四协议覆盖，且“不存在按模型设置 mode 的开关”。
- 与代码一致：`application/atg/config.py:43-58` 只解析 `features.atg.mode_by_format.<format>`；`:103-105` 的 `text_model_pattern`/`non_text_model_pattern` 仅构成 `ModelFilter`。

### P2-03 CLOSED：changelog 的 CI 分支说明已与 workflow 对齐

- 证据：`docs/changelog.md:60-61` 改为“支持 `master`/`main` 分支、版本标签、每日定时与手动触发”。
- 与 `.github/workflows/docker.yml:4-10` 一致：`branches: master/main`，`tags: 'v*.*.*' / '*.*.*'`；`rebuild-upstream` 已不在触发条件中。

### P2-04 CLOSED：AGENT §4.1.1 路径/行号/旧 C2 措辞已修正

- 路径：`AGENT.md:208` 已改为“实现事实在 `domain/model_access.py` 与 `infrastructure/gateway/model_defaults.py`”；实际文件为 `src/aistudio_api/domain/model_access.py`。
- 行号全部复验：
  - `AGENT.md:210` → `model_access.py:140-160`（`_selector_label`）与 `:179-182`（至少一个选择器）；`model_defaults.py:317-448`（`_normalize_instance_specs`）。
  - `AGENT.md:211` → `model_access.py:184-199`（`only_tier`）。
  - `AGENT.md:212` → `model_access.py:211-214`（`min_tier` 单独）。
  - `AGENT.md:213` → `model_access.py:201-209`（`max_tier` 单独）。
  - `AGENT.md:214` → `model_access.py:216-232`（`min_tier` + `max_tier`）。
  - `AGENT.md:215` → `model_access.py:235-240`、`instance_pool.py:109-114,182-191`；跨 lane 拒绝 `instance_pool.py:409-446`、`instance_manager.py:424-478`。
  - `AGENT.md:216` → `model_defaults.py:401-431`（合并键）。
  - `AGENT.md:217` → `instance_pool.py:72-114`、`domain/instances.py:20-35`、`instance_manager.py:1241-1253`。
- 旧措辞“前端 `.vue` 暂不改，留待 C2”已删除；改为“前端类型与页面已使用这些字段”，与 `web/src/types/instances.ts:16-18`、`web/src/utils/instances.ts:57-82` 一致。

### P2-05 CLOSED：KEEPALIVE 证据索引行号已改为真实语义位置

- 证据：`docs/KEEPALIVE_AND_LOGGING_SPEC.md:919` 现为 `account_keepalive.py:409-503`、`pooled_session.py:321-322`、`instance_manager.py:676-680`。
- 与代码一致：`pooled_session.py:321-322` 为 `save_current_cookies`；`instance_manager.py:676-680` 为 `pool_slots`。
- `:922` 现为 `api/app.py:194-301`、`:504-534`；`app.py:194-301` 为 `_refresh_startup_account_metadata`，`:513-534` 为 `_startup_metadata_and_tiers`。
- 正文 `:71-72`、`:248`、`:255`、`:284`、`:460`、`:467` 与上述一致。

### P2-06 CLOSED：PROTOCOL 行号/证据索引已修正且未越界

- `docs/PROTOCOL_AND_TOOLCALL_SPEC.md:480`：`format_sse_usage` → `api/gemini_service.py:152-184`；`format_sse_error` → `:185-200`。与文件实际边界一致（`format_sse_usage` 起于 152，`format_sse_error` 起于 185）。
- `:694`：PG-02 边界改为 `api/gemini_service.py:279-309`，并补 `routes_claude.py:101-164`、`routes_responses.py:138-191`；`gemini_service.py` 共 318 行，引用不再越界。
- `:953`：证据索引改为 `generation_service.py:49-80`、`:229-372`、`:382-611`；对应 `StreamEvent/GenerationOutcome`、`generate`、`stream_generation`，与 `generation_service.py` 当前结构一致。
- 全文显式 `file:line` 校验 0 越界、0 缺失文件。

### P2-07 CLOSED：ARCHITECTURE 的 gemini_service 行号已收敛到文件范围内

- 证据：`docs/ARCHITECTURE.md:251` 改为 `api/gemini_service.py:47-90`、`:222-309`。
- 与代码一致：`gemini_service.py` 共 318 行；`_build_gemini_streaming_response` 从 222 开始，`StreamingResponse` 收尾于 309；`:47-90` 为 `handle_gemini_generate_content`。
- 同段 `routes_openai.py:99-221`、`routes_claude.py:175-216`、`routes_responses.py:96-201` 均在各自文件范围内。

### P2-08 CLOSED：INSTANCE max_contexts 行号已改为 1645-1652

- 证据：`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:358` 现为 `model_defaults.py:454-473`、`:1645-1652`。
- 与代码一致：`model_defaults.py` 共 1652 行；`get_configured_max_contexts` 位于 `:1645-1652`（`:454-473` 为 `_normalize_max_contexts`）。
- `:914` 的 `max_contexts` 引用 `model_defaults.py:454-473` 亦正确。

### P2-09 CLOSED：WIRE 模型发现范围与 wire_spec 常量范围已修正

- 证据：`docs/WIRE_SPECIFICATION.md:406` 与 `:488` 均写 `model_discovery.py:838-980`；该文件共 980 行，`_parse_raw_models` 起于 838。
- `:468` 现明确拆分：`wire_spec.py:12-64`（`TopLevelIndex`/`PartIndex`/`GenerationConfigIndex`）与 `wire_spec.py:67-92`（`SchemaIndex`）；`:486` 汇总为 `wire_spec.py:12-92`。
- 与代码一致：`wire_spec.py` 的 `SchemaIndex` 位于 67-92，常量文件共 128 行。

### P2-10 CLOSED：全部文档已移除 `rebuild-upstream`/“目标树”过渡措辞

- 证据：对 README、README_EN、AGENT、docs 7 全文检索 `rebuild-upstream|目标树|目标施工区|只读主仓`，结果为 0 命中。
- `docs/ARCHITECTURE.md:3` 改为“本仓库”；`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:3`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md:3`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:3` 改为“当前主仓源码”；`docs/BOTGUARD_VERIFICATION_CHAIN.md:381-383` 不再称“目标树”。
- 与 `AGENT.md:5` 的“主工作区即唯一常态开发、审查与交付工作区”一致。

### P2-11 CLOSED：README 中英 Compose 段已补齐根 `config.yaml` 前置条件

- 证据：`README.md:346` 与 `README_EN.md:346` 明确：`docker run` 才自动初始化 `/app/data/config.yaml`。
- `README.md:348-353` 与 `README_EN.md:348-353` 明确：使用根 `docker-compose.yml` 前必须在宿主机准备 `config.yaml`，并给出 `cp defaults/config.yaml config.yaml` + `docker compose up -d`。
- 与 `docker-compose.yml:11,21`（bind mount `/app/config.yaml`、`AISTUDIO_CONFIG_FILE=/app/config.yaml`）及 `docker-entrypoint.sh:8-23` 的行为一致。
- `README.md:355` 与 `README_EN.md:355` 对 `docker-compose.deploy.yml` 的命名卷/自动初始化描述正确。

## 逐项终审：上一轮 Q

### Q-01 CLOSED：AGENT 已显式固化分层边界

- 证据：`AGENT.md:30` 新增“分层边界（硬约束）”：`application` 严禁 import `fastapi` 或 `aistudio_api.api`；`infrastructure` 严禁反向 import `aistudio_api.application`；依赖方向固定为 `api → application → domain ← infrastructure`。
- 与 `tests/unit/test_layer_boundaries.py` 的存在与 ARCHITECTURE §1 一致。

### Q-02 CLOSED：AGENT 已显式标注 D-04/D-06

- 证据：`AGENT.md:273` 标题内写“Paid 模型 tier 不足（D-04/D-06）”，并保留 403 契约、错误字段、不切号/不降级/不用 503 表达 tier 不足。
- 与 `docs/PROTOCOL_AND_TOOLCALL_SPEC.md` 的 D-04/D-06 错误映射一致。

### Q-03 CLOSED：AGENT 已删除旧阶段先后措辞

- 证据：`AGENT.md:326` 现写“OpenAI Chat、Claude Messages、OpenAI Responses 与 Gemini 四协议已完成并默认启用；不再按阶段先后排序”。
- 全文检索 `Claude 在 Build`、`Responses 更后` 为 0 命中；`AGENT.md:439` 仅保留明确标注为“历史追溯”的 B 阶段顺序。
- 与 `docs/changelog.md:19-23`、`defaults/config.yaml:112-115` 的默认启用口径一致。

### Q-04 CLOSED：ARCHITECTURE formats 行号已精确到 112-117

- 证据：`docs/ARCHITECTURE.md:484` 现为 `defaults/config.yaml:112-117`。
- 与 `defaults/config.yaml:112-115`（`openai/claude/responses` 均 `true`，含 `media` 至 117）一致。

## N（保留，不阻断 PASS）

### N-01 版本号仍为 0.1.0 / 1.0.0，与 changelog 2.0.0 未统一

- `pyproject.toml:7` = `0.1.0`；`web/package.json:4` = `1.0.0`；`docs/changelog.md:6` = `[2.0.0] - 2026-11-08`。
- 结合 `AGENT.md:443` 的“尚未执行的 C5b 合并、打标与清理”，版本号提升保留到 C5b；本步不作为 C5a 阻断项。

### N-02 GHCR 镜像所有者大小写为既有静态风险

- `.github/workflows/docker.yml:20` 使用 `ghcr.io/${{ github.repository_owner }}/aistudio-api`；当前远端 owner 为 `TonyDG233`（混合大小写），容器镜像引用通常要求小写。
- 该行不属于 C5a 的修复 diff；本步未执行 GitHub Actions/真实网络，故保留为观察项。若 tag 推送时构建失败，应优先检查此处。

## 关键证据索引

- README C1d：`README.md:230-237`、`README_EN.md:230-237`；`session_state.py:20-41`、`snapshot_generate.js:70-95`。
- README ATG：`README.md:58,260-261`、`README_EN.md:58,260-261`；`application/atg/config.py:43-58,103-105`。
- changelog CI：`docs/changelog.md:60-61`；`.github/workflows/docker.yml:4-10,46-50`。
- AGENT §4.1.1：`AGENT.md:208-217`；`domain/model_access.py:140-240`、`model_defaults.py:317-448,401-431`、`instance_pool.py:72-114,409-446`、`instance_manager.py:424-478,1241-1253`。
- KEEPALIVE 证据：`docs/KEEPALIVE_AND_LOGGING_SPEC.md:919,922`；`pooled_session.py:321-322`、`instance_manager.py:676-680`、`api/app.py:194-301,504-534`。
- PROTOCOL 行号：`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:480,694,953`；`gemini_service.py:152-200,279-309`、`generation_service.py:49-80,229-372,382-611`。
- ARCHITECTURE 行号：`docs/ARCHITECTURE.md:251,484`；`gemini_service.py` 共 318 行、`defaults/config.yaml:112-117`。
- INSTANCE 行号：`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:358`；`model_defaults.py` 共 1652 行、`get_configured_max_contexts:1645-1652`。
- WIRE 行号：`docs/WIRE_SPECIFICATION.md:406,468,486,488`；`model_discovery.py:838-980`、`wire_spec.py:12-92`。
- 无过渡措辞：全范围检索 `rebuild-upstream|目标树|目标施工区|只读主仓` = 0。
- Compose：`README.md:346-355`、`README_EN.md:346-355`；`docker-compose.yml:11,21`、`docker-entrypoint.sh:8-23`。

## 双仓状态

- 目标区 HEAD 保持 `d423efb`，未 stage/commit/push；工作区只有 12 个 C5a 范围内文件修改与本报告目录，无其他临时未跟踪文件。
- 主仓 HEAD 保持 `79052b6`，仅新增本报告目录。
- 未启动任何服务或浏览器，未访问真实网络。

## 未解决项

- 仅 N-01（版本号 0.1.0/1.0.0，留 C5b）与 N-02（GHCR owner 大小写，需 CI 验证）。
- 0 个未关闭 P0/P1/P2/Q。
