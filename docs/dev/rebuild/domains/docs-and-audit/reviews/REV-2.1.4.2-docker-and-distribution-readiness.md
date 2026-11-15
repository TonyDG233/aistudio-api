# REV-2.1.4.2 — Step 2A Docker 与对外公开分发配置改造独立只读审查报告

- **审查结论（三审终态）**：**`PASS`**（初审 `2 P1 / 5 P2` → 二审 `0 P0 / 0 P1 / 1 P2` → 三审 `0 P0 / 0 P1 / 0 P2`；`P2-6` 与 `Q-4`/`Q-6`/`Q-7`/`Q-9`/`Q-10` 均已核验关闭，剩余 `Q-8`/`Q-11`/`Q-12` 为非阻断跟进项，详见 §7）
- **初审日期**：2026-11-15（原结论 `CONDITIONAL`：0 P0 / 2 P1 / 5 P2）
- **复审日期**：2026-11-15（第二轮只读复核；`P1-1`/`P1-2`/`P2-1`~`P2-5`/`Q-1`~`Q-5` 逐条核验，结果见 §6）
- **三审日期**：2026-11-15（第三轮只读复核；`P2-6`/`Q-4`/`Q-6`/`Q-7`/`Q-9`/`Q-10` 逐条核验并关闭，最终 `PASS`，结果见 §7）
- **审查基线**：初审 `569353b`；复审在同一工作树读取收口 Worker 的未提交改动（17 个文件修改 + 本报告为唯一未跟踪文件），暂存区为空
- **审查范围（全量 `git diff`）**：`.dockerignore`、`.github/workflows/ci.yml`、`Dockerfile`、`docker-compose.deploy.yml`、`docker-compose.yml`、`docker-entrypoint.sh`、`pyproject.toml`、`tests/unit/test_dockerfile_healthcheck.py`、`web/package.json`；对照 `main.py`、`src/aistudio_api/main.py`、`src/aistudio_api/config.py`、`src/aistudio_api/api/app.py`、`README.md`、`README_EN.md`、`docs/changelog.md`、`.github/workflows/docker.yml`、`.env.example`
- **100% 只读声明**：本审查以只读方式完成，**未修改任何业务代码、Docker 配置、CI 配置或测试文件**；**未启动真实服务（`main.py server`）、未拉起真实浏览器、未连接真实 Google 账号、未发起任何外网请求**；**未执行 `git commit` / `git push` / `git add`**；唯一写入为本报告与 `docs/dev/rebuild/domains/docs-and-audit/index.md`、`docs/dev/rebuild/domains/INDEX.md` 的索引行。

---

## 1. 四项指定核查点结论

### 1.1 核查点 1：配置路径与 `CMD` 命令兼容性

| 子项 | 结果 | 证据 |
|---|---|---|
| `docker-entrypoint.sh` 到底用哪个变量 | **PASS（行为正确，变量名需澄清）** | 脚本中**不存在** `CONFIG_TARGET`；真实变量是 `CONFIG_FILE`：`docker-entrypoint.sh:4` 默认值 `/app/data/config.yaml`，`:8-14` 解析优先级为 **非空 `AISTUDIO_CONFIG_PATH` > 非空 `AISTUDIO_CONFIG_FILE` > 默认**，`:16` 把解析结果导出为 `AISTUDIO_CONFIG_PATH`。与 `src/aistudio_api/config.py:110-126` 的 `os.getenv("AISTUDIO_CONFIG_PATH") or os.getenv("AISTUDIO_CONFIG_FILE")` 语义严格一致。 |
| compose 只传 `AISTUDIO_CONFIG_PATH`、镜像只设 `AISTUDIO_CONFIG_FILE` 是否冲突 | **PASS** | `Dockerfile:112` 设 `AISTUDIO_CONFIG_FILE=/app/data/config.yaml`；`docker-compose.yml:23` 与 `docker-compose.deploy.yml:18` 设 `AISTUDIO_CONFIG_PATH=/app/data/config.yaml`。两个变量值相同，且 PATH 优先，`docker-entrypoint.sh:9` 与 `config.py:123-125` 都会选中 `/app/data/config.yaml`，不存在路径分叉。冗余但无行为风险（见 Q-1）。 |
| `CMD ["python3","main.py","server"]` 不带 `--port`/`--browser-port` 是否 100% 读取 `settings.port` / `settings.browser_port` | **PASS** | 链路完整闭合：根 `main.py:5-11` 注入 `src/` → `src/aistudio_api/main.py:17-19` 的 argparse `default=settings.port` / `default=settings.browser_port` → `:43-51` 取 `args.port`/`args.browser_port`（非 None 即用，恒非 None，因为 default 已给出）→ `:53-60` 重写 `sys.argv` 为 `--port <v> --browser-port <v>` 后调用 `server_main` → `src/aistudio_api/api/app.py:1014-1027` 用该端口 `uvicorn.run(app, host="0.0.0.0", port=args.port)`。`settings` 取值在 `src/aistudio_api/config.py:393-396`（`AISTUDIO_PORT` 默认 `8080`，`AISTUDIO_BROWSER_PORT` 默认 `9222`）。故 `-e AISTUDIO_PORT=9000` 会被监听端口与 `Dockerfile:224-225` 健康检查同时采纳。 |

> 唯一边界：`config.py:393` 为 `int(os.getenv("AISTUDIO_PORT", "8080"))` 的类体求值，若把 `AISTUDIO_PORT` 设为非数字字符串（如误写成 `${AISTUDIO_PORT }`）会在 import 期 `ValueError` 崩溃；两份 compose 恒定注入合法整数（`${AISTUDIO_PORT:-8080}` 默认 8080），不构成实际风险（见 N-6）。

### 1.2 核查点 2：`.dockerignore` 是否误伤 `Dockerfile` 的 `COPY` 源

**PASS** — 逐条比对全部 6 处上下文内 `COPY` 源，无一被新增规则命中：

| `Dockerfile` 行 | `COPY` 源 | `.dockerignore` 判定 |
|---|---|---|
| `:52` | `waa-signer/go.mod`、`waa-signer/go.sum` | 未命中任何规则 |
| `:72` | `waa-signer/`（递归） | `.dockerignore:62-63` 仅排除 `waa-signer/.deps/`、`waa-signer/bin/`（均在 Stage 1 内重新下载/编译），`go build ./cmd/waa-signer` 所需 `cmd/`、`internal/`、`go.mod`、`go.sum` 不受影响 |
| `:195` | `requirements.txt` | 未命中 |
| `:202` | `src/`（含 `static/` 前端产物） | 未命中；实测 `src/` 下无 `data/`、`build`、`dist`、`bin`、`tests/` 目录，也无 `test_*.py`、`*.log`（唯一目录为 `src/aistudio_api/static/assets`，不被任何规则覆盖） |
| `:203` | `main.py` | 未命中 |
| `:204` | `defaults/config.yaml` | 未命中 |
| `:205` | `docker-entrypoint.sh` | 未命中 |
| `:210` | `COPY --from=waa-signer-build` | 来自构建阶段，不受 `.dockerignore` 影响 |

反查证据：对 `src/`、`waa-signer/`、`defaults/` 递归枚举并套用新增/既有排除模式（`data/`、`data2/`、`dumps/`、`server-logs/`、`.venv/`、`venv/`、`android/`、`.kilo/`、`tools/reverse/`、`test_*.py`、`*.log`、`build`、`dist`）命中文件数为 **0**。`.dockerignore:74-76` 排除 `Dockerfile`/`docker-compose.yml`/`.dockerignore` 自身属正常做法（构建前单独读取）。唯一遗漏项为根 `config.yaml`（见 Q-5）。

### 1.3 核查点 3：两份 compose 的语法、插值与挂载

| 子项 | 结果 | 证据 |
|---|---|---|
| YAML 语法 | **PASS** | 两份文件 `yaml.safe_load` 均返回合法 `dict`；`git diff --check` exit 0；`tests/unit/test_dockerfile_healthcheck.py:23-35` 解析 compose 通过。 |
| 端口/环境变量展开一致性 | **PASS（有边界）** | `docker-compose.yml:11`（`${AISTUDIO_PORT:-8080}:${AISTUDIO_PORT:-8080}`）、`:16`（`AISTUDIO_PORT=${AISTUDIO_PORT:-8080}`）与 `:28`（健康检查）共用同一个宿主机变量，因此宿主机 `.env`/shell 设定端口时三处同步，默认 8080 时也同步。 |
| 健康检查 `${...}` 在哪一侧展开 | **需注意（Q-2）** | Docker Compose 会在**解析 compose 文件阶段（宿主机侧）**对全部字符串值做插值，故 `docker-compose.yml:28` 的 `${AISTUDIO_PORT:-8080}` 在容器启动前就被替换成宿主机值；容器内展开必须写成 `$${AISTUDIO_PORT:-8080}`。当前因三处同源而结果正确，但用 `-e`/override 文件单独改容器端口时探测会打错端口。`Dockerfile:224-225` 的 `HEALTHCHECK ... CMD`（shell 形式）由容器内 `/bin/sh -c` 展开，属正确用法；`docker-compose.deploy.yml` 无 healthcheck，沿用镜像 `HEALTHCHECK`，亦正确。 |
| 挂载 | **CONDITIONAL（见 P1-1）** | `docker-compose.yml:13-14` 由“`./data` + `./config.yaml` + `./.env`”三挂载收缩为仅 `./data:/app/data`；`docker-compose.deploy.yml:10-11` 保持命名卷 `aistudio-api-data:/app/data`。新目录布局本身自洽（entrypoint 首次播种 `data/config.yaml`），但老用户既有 `./config.yaml`、`./.env` 不再生效，且无迁移路径。 |

### 1.4 核查点 4：行尾 LF

**PASS** — `git ls-files --eol` 对 9 个改动文件全部返回 `i/lf w/lf attr/text=auto eol=lf`；`docker-entrypoint.sh` 原始字节中 `0x0D`（CR）计数为 **0**，不存在 `\r`；`.gitattributes:3` 的 `* text=auto eol=lf` 生效；`docker-entrypoint.sh` 文件模式保持 `100755`（`git ls-files -s`），且 `Dockerfile:216` 另做 `chmod +x` 双保险。

---

## 2. 必跑门禁实跑结果

| # | 命令 | 实际输出 | 判定 |
|---|---|---|---|
| 1 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check .` | `All checks passed!`，exit 0 | **PASS** |
| 2 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations`，exit 0 | **PASS** |
| 3 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest tests/unit/test_dockerfile_healthcheck.py tests/unit/test_config_schema.py -q` | `17 passed in 0.27s`（`test_dockerfile_healthcheck.py` 3 passed；`test_config_schema.py` 14 passed），exit 0 | **PASS** |
| 4 | `git diff --check`（并追加 `git diff --cached --check`） | 两者均无输出，exit 0 | **PASS** |
| 附加 | `uv run --no-sync ruff format --check .` | `62 files would be reformatted, 325 files already formatted`，exit 1 | **既有漂移，非本次引入**（见 N-3；`git show HEAD:tests/unit/test_dockerfile_healthcheck.py` 同样未格式化） |
| 附加 | 两份 compose `yaml.safe_load` | 均返回 `dict` | **PASS** |

未执行项：本机无 Docker CLI（`docker` 命令不存在），因此 `docker compose config` / `docker build` / 实际起容器均未执行（见 N-4）。

---

## 3. 完整问题清单（`P0 / P1 / P2 / Q / N`）

### P0（阻断级）
- 无。

### P1（建议本轮整改后再提交）

- **P1-1：根 compose 配置源迁移断裂，老用户 API Key 与全部自定义配置被静默忽略**
  - **位置**：`docker-compose.yml:13-14,23`（新挂载与 `AISTUDIO_CONFIG_PATH`）、`docker-entrypoint.sh:19-22`（仅“目标缺失且模板存在”时 `cp`）、`src/aistudio_api/config.py:128-141`（同样只播种、不迁移）、`Dockerfile:112`。
  - **事实**：HEAD 版 `docker-compose.yml` 曾以 `./config.yaml:/app/config.yaml` + `AISTUDIO_CONFIG_FILE=/app/config.yaml` 把仓库根 `config.yaml` 作为唯一运行时配置；本次改为 `./data:/app/data` + `AISTUDIO_CONFIG_PATH=/app/data/config.yaml`。升级用户磁盘上的根 `config.yaml` 仍存在，但既不挂载也不被读取；entrypoint 会在 `./data/config.yaml` 生成**全新默认模板**。
  - **影响**：`defaults/config.yaml:9` 的 `api_keys` 由 Web 控制台直接写入配置文件（`src/aistudio_api/api/routes_system.py:1072-1090,1116-1139,1166-1193`），模板播种后变为 `api_keys: []`，而 `defaults/config.yaml:14-15` 的 `security.api_allow_anonymous` 默认 `false` → **已发放的所有客户端 API Key 立即 401**；模型准入、tier 配额、路由/ATG/保活等全部自定义设置一并回落到默认值。`scripts/` 下无任何迁移脚本，README 也无迁移说明。
  - **修复方向（择一，需主控裁决）**：① `docker-entrypoint.sh` 增加一次性迁移分支：目标缺失且检测到旧 `/app/config.yaml` 时复制而非覆盖；② compose 增加 `./config.yaml:/app/config.yaml:ro` 兼容挂载；③ README 明确 `mv config.yaml data/config.yaml` 升级步骤并在 `docs/changelog.md` 标注 breaking change。

- **P1-2：`.env` 挂载被移除，非转发变量静默失效**
  - **位置**：HEAD 版 `docker-compose.yml:12`（本次 diff 删除的 `./.env:/app/.env:ro`）→ 现行 `docker-compose.yml:15-26`、`docker-compose.deploy.yml:12-22`；`src/aistudio_api/config.py:17` 的 `load_dotenv()`（工作目录 `/app`，读取 `/app/.env`）；`.env.example:39,45,52,59`。
  - **事实**：旧部署把宿主机 `.env` 整份挂进容器，由 `load_dotenv()` 全量加载；本次只显式转发 `AISTUDIO_PORT`、`AISTUDIO_WEB_PASSWORD`、`AISTUDIO_PROXY`、`TZ` 与路径类变量。`.env.example` 中其余文档化变量（`AISTUDIO_PERSIST_STATS`、`AISTUDIO_DEFAULT_TEXT_MODEL`、`AISTUDIO_SSE_KEEPALIVE_SEC`、`AISTUDIO_DUMP_RAW_RESPONSE` 等）不再进入容器，且无任何告警或文档说明。
  - **影响**：Compose 仍会自动读取 `.env` 做插值，用户会误以为整份 `.env` 生效；实际只生效 4 个键，属静默降级（如 SSE 心跳间隔、默认模型被悄悄改回默认值）。
  - **修复方向**：`env_file: .env`（Compose ≥ 2.24 可用 `required: false` 兼容文件缺失），或把 `.env.example` 中全部“容器相关”键补入转发清单，并在 README 明示“Compose 仅转发以下变量”。

### P2（非阻断，但属公开分发前应闭环）

- **P2-1：README 的 compose 指引已过期，且会主动制造 P1-1 的假配置**
  - **位置**：`README.md:124-126`（注释“使用仓库根目录 docker-compose.yml 前先准备宿主机配置文件” + `cp defaults/config.yaml config.yaml`）、`README_EN.md:125-127`；对照 `docker-compose.yml:13-14`。
  - **事实**：现行 compose 不再挂载仓库根 `config.yaml`，该 `cp` 步骤产生的文件完全不生效；用户按文档修改后会发现“配置没变化”，并可能误判为程序缺陷。
  - **修复方向**：删除该 `cp` 步骤，改为“`docker compose up -d`，首次启动自动在 `./data/config.yaml` 播种默认配置”；同步 `README_EN.md`。

- **P2-2：生产分发清单在文档中零覆盖**
  - **位置**：`docker-compose.deploy.yml:1-25`（`AISTUDIO_IMAGE_TAG`、`AISTUDIO_PORT`、`AISTUDIO_WEB_PASSWORD`、`AISTUDIO_PROXY`、`TZ`、`extra_hosts`）。
  - **事实**：在 `README.md` / `README_EN.md` 中检索 `deploy`、`AISTUDIO_IMAGE_TAG`、`docker-compose.deploy` 全部 0 命中。公开分发用户无法得知存在该生产清单、如何钉版本、如何改端口与凭据。
  - **修复方向**：README 增加 3~5 行“生产部署（预构建镜像）”小节，给出 `AISTUDIO_IMAGE_TAG=2.1.4 docker compose -f docker-compose.deploy.yml up -d` 与 `.env` 键位表。

- **P2-3：根 compose 同时声明 `build` + `image` + `pull_policy: missing`，源码用户可能跑到已发布镜像**
  - **位置**：`docker-compose.yml:3-5`。
  - **事实**：按 Compose 语义，`pull_policy: missing` 会在本地镜像缺失时先拉取 `ghcr.io/tonydg233/aistudio-api:${AISTUDIO_IMAGE_TAG:-latest}`，命中后即使用该镜像；仓库根 compose 面向“从源码构建”，源码改动需 `--build` 才会生效。
  - **影响**：用户 `git pull` 后直接 `docker compose up -d`，可能运行的是 GHCR 旧镜像而非本地代码，排查方向被误导。
  - **修复方向**：源码清单去掉 `image`/`pull_policy`（保持纯 `build: .`），或 README 明确要求 `docker compose up -d --build`。本条结论基于 Compose 规范静态推导，需在有 Docker 的环境用 `docker compose config` + 实际 `up` 复验（见 N-4）。

- **P2-4：Docker 相关改动没有 PR 级 CI 覆盖**
  - **位置**：`.github/workflows/ci.yml:37-49`（backend/frontend/android 三个路径过滤器均不含 `Dockerfile`、`docker-compose*.yml`、`docker-entrypoint.sh`、`.dockerignore`）；`.github/workflows/docker.yml:3-13`（仅在 push `master`/`main`、tag、`schedule`、`workflow_dispatch` 触发，无 `pull_request`）。
  - **影响**：只改 Docker/分发配置的 PR 不会触发任何校验；坏镜像要等合并进 `master` 后由 `docker.yml` 才暴露，与本步“公开分发就绪”的目标不匹配。
  - **修复方向**：在 `ci.yml` 增加 `docker:` 过滤器 + 一个 `docker build`（可 `push: false`、单平台）校验 job，或给 `docker.yml` 增加 `pull_request` 触发但保持 `push: false`。

- **P2-5：版本号与 `changelog` / 阶段编号口径不一致**
  - **位置**：`pyproject.toml:7`、`web/package.json:4`（`2.0.0` → `2.1.4`）；`docs/changelog.md:6`（最新章节仍为 `[2.1.3] - 2026-11-13`，无 `[2.1.4]`）；`AGENT.md` §10.3。
  - **事实**：仓库发布 tag 最新为 `2.1.3`（`git tag`），但并行推进的 Stage 2.2.0 审查已到 `REV-2.2.0.7`；本次直接把包版本写成 `2.1.4` 且未新增 changelog 章节，导致“包版本 2.1.4 < 在研阶段 2.2.0”的口径分叉；`tests/` 全库无任何版本一致性断言，`pyproject.toml` 与 `web/package.json` 的同步纯靠人工。
  - **修复方向**：与主控确认本次对外发布究竟为 `2.1.4` 还是 `2.2.0`，据此补 `docs/changelog.md` 对应章节（含 Docker/分发变更与 P1-1/P1-2 的 breaking 说明），并增加一条读取两处版本号 + 校验 changelog 标题存在的最小单测。

### Q（观察项，建议排期）

- **Q-1：配置路径双变量冗余** —— `Dockerfile:112` 写 `AISTUDIO_CONFIG_FILE`，两份 compose 写 `AISTUDIO_CONFIG_PATH`，entrypoint `:8-16` 再做一次归一。行为等价，但同一语义存在两条可写通路，未来任一侧改名即漂移。建议长期统一为 `AISTUDIO_CONFIG_PATH`（`Dockerfile` 的 `ENV` 改名或删除，entrypoint 保留 `AISTUDIO_CONFIG_FILE` 仅作对外兼容）。
- **Q-2：根 compose 健康检查应改为容器内展开** —— `docker-compose.yml:28` 建议改为 `"curl -fsS http://localhost:$${AISTUDIO_PORT:-8080}/health || exit 1"`，使探测端口永远跟随容器实际 `AISTUDIO_PORT`，消除“宿主机变量与容器覆盖不一致”时的误探。修好后需同步更新 `tests/unit/test_dockerfile_healthcheck.py` 的字符串断言范围（当前只断言 `Dockerfile` 块）。
- **Q-3：`TZ` 注入与 `resolve_timezone()` 语义不匹配** —— 两份 compose 新增 `TZ=${TZ:-Asia/Shanghai}`（`docker-compose.yml:19`、`docker-compose.deploy.yml:16`），但 `src/aistudio_api/config.py:262-277` 只在 `str(tz)` 含 `/` 时采纳，否则回退 `America/Los_Angeles`；glibc 下 `TZ=Asia/Shanghai` 的 `time.tzname` 通常为 `CST`，很可能仍回退美西。此时容器进程时区（日志）与 `settings.timezone`（`src/aistudio_api/infrastructure/gateway/wire_codec.py:227`、`src/aistudio_api/infrastructure/browser/browser_engine.py:224,1045` 的页面/指纹时区）不一致。建议在 Linux 容器内实测 `python -c "from aistudio_api.config import settings; print(settings.timezone)"`，并按结果决定是改 `resolve_timezone()` 还是改用 `AISTUDIO_TIMEZONE` 注入。
- **Q-4：`docker-entrypoint.sh:23-29` 欢迎横幅只在首次播种分支打印** —— 配置已存在时重启完全静默；且横幅在 `exec "$@"`（`:32`）之前就宣称“已就绪”，实际服务尚未监听。建议移出 `if` 块或改为“正在启动”，避免误导排障。
- **Q-5：根 `config.yaml` 未加入 `.dockerignore`** —— `defaults/config.yaml:9` 表明配置可存放 `api_keys`（用户自建时可能含真实 Key）。该文件虽未被任何 `COPY` 引用（不会烤进镜像），但会随构建上下文上传到 Docker daemon/远端 builder。建议 `.dockerignore` 补一行 `config.yaml`（`data/` 已被排除，新路径不受影响）。
- **Q-6：最低运行版本未标注** —— `pull_policy`（`docker-compose.yml:5`）与 `extra_hosts: host.docker.internal:host-gateway`（`docker-compose.yml:8-9`、`docker-compose.deploy.yml:6-7`）分别需要较新的 Compose（约 v2.22+）与 Docker 20.10+；README 未给出最低版本，老版本 Compose 会因未知字段直接报错。建议在 README 标注 `Docker Engine 20.10+ / Compose v2.22+`。

### N（非阻断事实记录）

- **N-1**：`Dockerfile:224` 的 `--start-period=5s` 与 `docker-compose.yml:31` 的 `start_period: 10s` 不一致（两者皆为旧值，本次未改），Python 冷启动较慢时镜像自带探测更易先触发 unhealthy。
- **N-2**：`tests/unit/test_dockerfile_healthcheck.py:75-83` 对 `docker-compose.deploy.yml` 因“无 healthcheck”而空循环通过，该清单实际零覆盖；若要守护 Q-2 的修法，需显式断言 deploy 依赖镜像 `HEALTHCHECK`。
- **N-3**：`ruff format --check .` 全库共 62 个文件待格式化，含本次改动文件的历史行（`tests/unit/test_dockerfile_healthcheck.py:48-51,77-79`）。用 `git show HEAD:` 取基线版本复核，同样未格式化 → **既有漂移，非本次引入**；本次新增的 `:71` 断言行本身格式合法。`ruff check` / `pyright` / 目标测试均全绿。
- **N-4**：本机未安装 Docker CLI，`docker compose config`、`docker build`、容器内时区实测均未执行；Dockerfile/compose 结论基于静态阅读 + Compose 规范推导，P2-3 与 Q-3 需在有 Docker 的环境复验后再定档。
- **N-5**：`docs/dev/rebuild/domains/docs-and-audit/index.md:36` 存在一条历史本机绝对路径记录（非本次引入、不影响本审查），建议后续文档治理时统一清理。
- **N-6**：`src/aistudio_api/config.py:393` 以 `int(os.getenv(...))` 在类体求值，非法 `AISTUDIO_PORT` 会在 import 期抛 `ValueError`，缺少友好报错；本次 compose 恒定注入合法值，不构成实际风险，仅记录。
- **N-7**：`.github/workflows/ci.yml:8,11` 移除 `rebuild-upstream` 触发分支，与“历史分支只读、不参与交付”的既定口径一致，无回归风险（本地与 `origin` 仍存在该分支，仅不再触发 CI）。

---

## 4. 整改优先顺序建议

1. **P1-1 + P2-1 一并处理**（同源问题）：先给 entrypoint 加一次性迁移分支或明确升级步骤，再改 README 的中英文 compose 指引，避免“文档让用户摆一个不生效的配置”。
2. **P1-2**：确认是否为有意收缩；若有意，必须在 README 列出“Compose 转发的完整变量表”，否则补 `env_file: .env`。
3. **P2-3 / P2-4**：在有 Docker 的机器上验证根 compose 的构建优先级，并补 PR 级镜像构建校验。
4. **P2-5**：与主控确认发布版本号口径，补 `[2.1.4]` 或对齐 `2.2.0`，再补 `changelog`。
5. **Q-2 / Q-3 / Q-5 / Q-6**：低成本高收益，可与上述任一提交合并处理。

---

## 5. 初审结论

Docker 配置本体（多阶段构建、`CMD` 去参数化、`HEALTHCHECK` 变量化、entrypoint 变量优先级、`.dockerignore` 与 `COPY` 的一致性、LF/可执行位）均核查通过，未发现 P0；四道指定门禁（`ruff check` / `pyright` / 目标 `pytest` 17 passed / `git diff --check`）全绿。

但作为“对外公开分发”步骤，**老用户升级路径与面向用户的文案尚未闭环**（P1-1 配置源迁移、P1-2 `.env` 静默失效、P2-1 README 过期指引），故本轮结论为 **`CONDITIONAL`**：建议整改 P1-1 / P1-2（并同步 P2-1）后由独立 Reviewer 复审，本报告所列其余 P2/Q 可作为同批或紧随其后的收口项。

---

## 6. 复审（第二轮，2026-11-15）核验与关闭结论

复审以 **100% 只读** 方式独立完成：未复用收口 Worker 的任何结论，逐条重跑门禁、逐条回读源码与文档；**未修改任何业务代码 / Docker 配置 / CI 配置 / 测试文件**，未启动服务与浏览器，未连接真实账号，未执行 `git add/commit/push`。复审时工作树为 17 个已修改文件 + 本报告 1 个未跟踪文件，暂存区为空。

### 6.1 复审门禁实跑（全部重跑，非引用初审）

| # | 命令 | 实际输出 | 判定 |
|---|---|---|---|
| 1 | `uv run --no-sync ruff check .` | `All checks passed!`，exit 0 | **PASS** |
| 2 | `uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations`，exit 0 | **PASS** |
| 3 | `uv run pytest tests/unit/test_dockerfile_healthcheck.py tests/unit/test_config_schema.py -q` | `17 passed in 0.26s`，exit 0 | **PASS** |
| 4 | `git diff --check`（含 `git diff --cached --check`） | 均无输出，exit 0 | **PASS** |
| 附加 | 两份 compose `yaml.safe_load` | 均为合法 `dict`（`env_file` 长语法可解析） | **PASS** |
| 附加 | `git ls-files --eol`（15 个改动文件） | 全部 `i/lf w/lf`；`docker-entrypoint.sh` 仍 `100755` | **PASS** |
| 附加 | `gofmt -l waa-signer/cmd/waa-signer/main.go waa-signer/internal/signerapi/types_test.go` | 无输出，exit 0 | **PASS** |

### 6.2 逐条关闭核验表

| 编号 | 初审问题 | 复审证据 | 终态 |
|---|---|---|---|
| **P1-1** | 配置源迁移断裂、`api_keys` 静默丢失 | `docker-entrypoint.sh:18-27` 新增 `LEGACY_CONFIG_FILE=/app/config.yaml` 非空迁移分支；`README.md:135`、`README_EN.md:136` 明确 `mv config.yaml data/config.yaml`；`docs/changelog.md:22` 记录分发重塑 | **RESOLVED**（升级路径已有明确手工迁移指引；同时衍生 `P2-6`） |
| **P1-2** | `.env` 挂载移除导致环境变量静默失效 | `docker-compose.yml:10-12`、`docker-compose.deploy.yml:8-10` 新增 `env_file: [{path: ./.env, required: false}]`，`environment:` 仍具更高优先级（`docker-compose.yml:18-28`）；`.env` 缺失不再报错 | **RESOLVED**（版本门槛见 `Q-6`，空值覆盖见 `N-8`） |
| **P2-1** | README compose 指引过期（`cp defaults/config.yaml config.yaml`） | `README.md:121-138`、`README_EN.md:123-140` 已重写为「默认部署 / 生产预构建镜像 / `--build` 本地重建」三条路径并删除失效 `cp` 步骤 | **RESOLVED**（文案准确性缺陷另见 `P2-6`） |
| **P2-2** | 生产清单与 `AISTUDIO_IMAGE_TAG` 无文档 | `README.md:127-128`、`README_EN.md:128-129` 给出 `AISTUDIO_IMAGE_TAG=2.1.4 docker compose -f docker-compose.deploy.yml up -d`；`docker-compose.deploy.yml:3` 已变量化 | **RESOLVED** |
| **P2-3** | 根 compose `build`+`image`+`pull_policy: missing` 语义歧义 | `docker-compose.yml:3-5` 未变更；`README.md:131` 已显式给出 `docker compose up -d --build` 路径 | **降级 `Q-7`**（默认路径最终镜像来源仍需 Docker 实测，本机无 Docker） |
| **P2-4** | Docker 改动无 PR 级 CI 覆盖 | `.github/workflows/ci.yml:43-46` 已将 `Dockerfile`、`docker-compose*.yml`、`docker-entrypoint.sh`、`.dockerignore` 纳入 backend 过滤器 → 相关 PR 会跑静态健康检查与全量后端测试 | **部分关闭，降级 `Q-8`**（仍无镜像 `docker build`；`docker.yml:3-13` 无 `pull_request`） |
| **P2-5** | 版本号与 `changelog` 口径不一致 | `docs/changelog.md:6` 新增 `## [2.1.4] - 2026-11-15`；`pyproject.toml:7`、`web/package.json:4`、`waa-signer/cmd/waa-signer/main.go:41`、`waa-signer/internal/signerapi/types_test.go:98`、`waa-signer/README.md:106` 已全部对齐 `2.1.4`；全仓检索 `2.1.0` 仅剩历史文档与第三方锁文件，无版本常量残留 | **RESOLVED**（阶段口径见 `N-9`） |
| **Q-1** | 配置路径双变量冗余 | 两份 compose 已不再注入 `AISTUDIO_CONFIG_PATH`（`docker-compose.yml:18-28`、`docker-compose.deploy.yml:15-24`），仅保留镜像 `Dockerfile:112` 的 `AISTUDIO_CONFIG_FILE`；entrypoint `:8-16` 仍负责归一与导出 | **RESOLVED** |
| **Q-2** | healthcheck 需容器内展开 | `docker-compose.yml:30` 已改为 `$${AISTUDIO_PORT:-8080}`（Compose 将 `$$` 转义为 `$`，交由容器内 `/bin/sh` 展开） | **RESOLVED** |
| **Q-3** | `TZ` 注入与 `resolve_timezone()` 不一致 | 两份 compose 已删除 `TZ`，改注入 `AISTUDIO_TIMEZONE=${AISTUDIO_TIMEZONE:-}`（`docker-compose.yml:22`、`docker-compose.deploy.yml:19`）；空值被 `src/aistudio_api/config.py:264-266` 的 `if override and override.strip()` 视为未设置，回落自动探测 | **RESOLVED** |
| **Q-4** | 欢迎横幅仅首次播种打印且在 `exec` 前称“已就绪” | `docker-entrypoint.sh:31-37` 仍位于 `if [ ! -f "$CONFIG_FILE" ]`（`:20-38`）分支内 | **未关闭（维持 `Q-4`，纯体验项）** |
| **Q-5** | 根 `config.yaml` 未加入 `.dockerignore` | `.dockerignore:25` 新增 `config.yaml`；按 Docker `.dockerignore` 语义（无斜杠模式锚定构建上下文根），不会误伤 `defaults/config.yaml`（`Dockerfile:204` 的 `COPY` 源仍在上下文内） | **RESOLVED**（需 Docker 构建复验，见 `N-4`） |

### 6.3 复审新增项

- **P2-6（唯一未关闭的 P2）：升级“自动迁移”承诺在现行 compose 下不可达**
  - **位置**：`README.md:135`、`README_EN.md:136`、`docs/changelog.md:22` 与 `docker-entrypoint.sh:22-24`。
  - **事实**：`docker-entrypoint.sh:22` 仅在容器内存在 `/app/config.yaml` 时才迁移，但新部署清单只挂载 `./data:/app/data`（`docker-compose.yml:16-17`）与命名卷（`docker-compose.deploy.yml:13-14`），`Dockerfile` 也未 `COPY` 根 `config.yaml`（`:195-205`）→ 标准部署中 `/app/config.yaml` **永不出现**，该分支为不可达代码；README 却承诺“即使忘记迁移，容器首次启动也会自动把 `/app/config.yaml` 迁移到 `/app/data/config.yaml`”。
  - **影响**：跳过 `mv` 的用户会按文档预期认为配置已自动保留，实际得到全新默认模板（`api_keys: []` + `security.api_allow_anonymous: false`），又回到 P1-1 的 401 失败模式，且更难排查。
  - **一句话修法（二选一）**：① 删除/改写 README 与 changelog 中的自动迁移承诺，只保留 `mv config.yaml data/config.yaml`；② 在根 compose 补一行注释掉的兼容挂载 `# - ./config.yaml:/app/config.yaml:ro`，使自动迁移在用户手动开启后真正可达。

- **Q-6（维持并升级重要性）：最低 Docker/Compose 版本未标注**
  - `env_file` 的 `path`/`required` 长语法（`docker-compose.yml:10-12`、`docker-compose.deploy.yml:8-10`）需要 Compose v2.24+，`pull_policy`（`docker-compose.yml:5`）与 `host-gateway`（`:8-9`）亦要求较新版本；`README.md`/`README_EN.md` 检索 `Docker Engine`、`Compose v`、`2.24` 均 0 命中。旧版 Compose 会在 `docker compose up` 直接解析失败，与“无需任何前置准备”表述冲突。建议在 Docker 小节标注 `Docker Engine 20.10+ / Compose v2.24+`。

- **Q-7（原 P2-3 降级）**：`docker-compose.yml:3-5` 同时声明 `image` + `build` + `pull_policy: missing`，默认 `docker compose up -d` 的镜像来源（GHCR 已发布镜像 vs 本地构建）需在有 Docker 的环境以 `docker compose config` + 实际 `up` 验证；本机无 Docker（`N-4`），不做断言。

- **Q-8（原 P2-4 降级）**：`.github/workflows/docker.yml:3-13` 仍无 `pull_request` 触发，镜像级 `docker build` 未在 PR 阶段校验；建议追加 `pull_request` + `push: false`（单平台）校验 job。

- **Q-9（既有问题，非本次引入）**：`.dockerignore:12-13` 的 `__pycache__`、`*.pyc` 为无斜杠模式（锚定上下文根），实测 `src/` 下 13 个 `__pycache__` 目录不被排除，会被 `Dockerfile:202` 的 `COPY src/ src/` 带入**本地构建**镜像（CI 干净检出不受影响）。建议改为 `**/__pycache__/` 与 `**/*.pyc`。
- **Q-10（测试守护缺口）**：`tests/unit/test_dockerfile_healthcheck.py:75-83` 只断言 compose healthcheck 的 `interval`，未断言 `docker-compose.yml:30` 保留容器内展开的 `$${AISTUDIO_PORT:-8080}`；若后续有人改回单 `$`（变成宿主机侧插值），现有测试不会失败。建议补一条 `"$${AISTUDIO_PORT:-8080}" in test_block` 断言。

### 6.4 复审新增 N 记录

- **N-8**：`env_file: ./.env` 会使 `.env` 中未在 `environment:` 重复声明的键覆盖镜像 `ENV`。若用户按 `.env.example:17` 直接得到 `AISTUDIO_BROWSER_EXECUTABLE=`（空串），会覆盖 `Dockerfile:113` 的 `/opt/cloakbrowser/chrome`；已核实 `src/aistudio_api/infrastructure/browser/browser_engine.py:104-116` 的第 4 级回退仍可命中 `/opt/cloakbrowser/chrome`，故无实际故障，仅记录。
- **N-9**：`docs/changelog.md:16` 的 `[2.1.4]` 章节收录了 Stage 2.2.0 的 Android 交叉编译 / `tools/verify_qr_adb.py` 内容，而 `AGENT.md` §10.3 将 Android APK 服务端化定性为 Minor（`Y`）级。当前发布口径（`2.1.4` vs `2.2.0`）需主控确认，不影响代码正确性。
- **N-10**：`waa-signer` 的 Go 单测未执行：`waa-signer/go.mod` 声明 `go 1.27`，本机为 `go1.26.4`，运行 `go test` 会触发 `GOTOOLCHAIN=auto` 下载与 CGO/SpiderMonkey 依赖；本次仅以 `gofmt -l`（两文件无输出）做静态格式校验，版本常量改动属纯字符串变更。
- **N-11**：本机仍未安装 Docker CLI，`.dockerignore` 语义、`env_file` 长语法兼容性、镜像构建与默认路径镜像来源均以 Docker 官方文档语义静态核验，需在有 Docker 环境复验（与 `N-4` 合并看待）。

### 6.5 复审终态统计与结论

| 状态 | 初审 | 复审 |
|---|---|---|
| P0 | 0 | **0** |
| P1 | 2 | **0**（`P1-1`、`P1-2` 均 RESOLVED） |
| P2 | 5 | **1**（`P2-6`；`P2-3`→`Q-7`、`P2-4`→`Q-8`） |
| Q | 6 | **6**（`Q-4`、`Q-6`、`Q-7`、`Q-8`、`Q-9`、`Q-10`；`Q-1`/`Q-2`/`Q-3`/`Q-5` 已 RESOLVED） |
| N | 7 | **11** |

**复审结论：`CONDITIONAL`。** 初审的两项 P1 已实质关闭：升级路径有明确 `mv` 指引与 changelog 记录，`.env` 全量注入已恢复且 `required: false` 缺失不报错；`Q-1`/`Q-2`/`Q-3`/`Q-5` 与 `P1-1`/`P1-2`/`P2-1`/`P2-2`/`P2-5` 全部核验关闭，四道指定门禁与三项附加静态校验全绿。

唯一阻断 PASS 的 `P2-6` 属“文档承诺强于实现可达性”的一致性问题（不是构建或运行错误）：标准 compose 部署中 `/app/config.yaml` 不存在，README/changelog 宣称的自动迁移不会发生，可能让跳过 `mv` 的用户误以为配置已保留。按 §6.3 的任一“一句话修法”修正（改写承诺文案，或补一行可选兼容挂载）后，即可由主控直接改判 **`PASS`**；`Q-4`/`Q-6`/`Q-7`/`Q-8`/`Q-9`/`Q-10` 与 `N-8`~`N-11` 均为非阻断跟进项。

---

## 7. 三审（第三轮，2026-11-15）核验与最终 `PASS` 结论

三审仍为 **100% 独立只读**：未复用收口 Worker 的自述结论，逐条回读源码/文档并重跑全部门禁；**未修改任何业务代码、Docker 配置、CI 配置或测试文件**，未启动服务与浏览器，未连接真实账号，未执行 `git add/commit/push`。三审时工作树为 17 个已修改文件 + 本报告 1 个未跟踪文件，暂存区为空。

### 7.1 三审门禁实跑（全部重跑）

| # | 命令 | 实际输出 | 判定 |
|---|---|---|---|
| 1 | `uv run --no-sync ruff check .` | `All checks passed!`，exit 0 | **PASS** |
| 2 | `uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations`，exit 0 | **PASS** |
| 3 | `uv run pytest tests/unit/test_dockerfile_healthcheck.py tests/unit/test_config_schema.py -q` | `18 passed in 0.26s`（较二审新增 1 条 compose 断言用例），exit 0 | **PASS** |
| 4 | `git diff --check`（含 `git diff --cached --check`） | 均无输出，exit 0 | **PASS** |
| 附加 | 两份 compose `yaml.safe_load` | 服务键集合均为 `image/container_name/restart/extra_hosts/env_file/ports/volumes/environment/healthcheck` | **PASS** |
| 附加 | `git ls-files --eol`（8 个本轮相关文件） | 全部 `i/lf w/lf`；`docker-entrypoint.sh` 原始字节 CR=0 | **PASS** |

### 7.2 三审逐条关闭核验

| 编号 | 二审遗留问题 | 三审证据 | 终态 |
|---|---|---|---|
| **P2-6** | “忘记迁移也会自动迁移”承诺在现行 compose 下不可达 | `README.md:135`、`README_EN.md:136` 已改为「启动前执行 `mkdir -p data && mv config.yaml data/config.yaml`」，并把自动迁移限定为「自定义 compose 仍将旧配置挂载到 `/app/config.yaml`」时生效；`docs/changelog.md:22` 同步同样的前置步骤与条件；`docker-entrypoint.sh:22-24` 分支因此准确对应“自定义挂载”这一唯一可达路径 | **RESOLVED** |
| **Q-4** | 横幅仅在首次初始化打印且在 `exec` 前称“已就绪” | `docker-entrypoint.sh:32` 文案改为「已完成首次配置初始化，正在启动 AI Studio API 服务...」，语义与“仅首次初始化分支打印”完全一致 | **RESOLVED** |
| **Q-6** | 最低 Docker/Compose 版本未标注 | `README.md:121`「建议使用 `Docker Compose v2.24+`」、`README_EN.md:122`「`Docker Compose v2.24+` recommended」，与 `env_file` 长语法（`docker-compose.yml:11-13`）、`host-gateway`（`:9-10`）的实际门槛匹配 | **RESOLVED** |
| **Q-7** | `build`+`image`+`pull_policy` 导致默认路径镜像来源不明 | `docker-compose.yml:4-6` 已注释掉 `build: .` 并删除 `pull_policy`，默认 `docker compose up -d` 确定拉取 GHCR 镜像；`README.md:130`、`README_EN.md:131` 明确“本地重建需先取消注释”；`docs/changelog.md:22` 记录该口径 | **RESOLVED** |
| **Q-9** | `__pycache__`/`*.pyc` 根锚定导致嵌套缓存进入构建上下文 | `.dockerignore:12-14` 已改为 `**/__pycache__/`、`**/*.py[cod]`、`**/*$py.class`，覆盖任意深度；`config.yaml`（`:25`）与 `defaults/config.yaml`（`Dockerfile:204` 的 `COPY` 源）互不冲突 | **RESOLVED** |
| **Q-10** | compose 侧 `$$` 无测试守护 | `tests/unit/test_dockerfile_healthcheck.py:86-96` 新增 `test_every_compose_file_defines_port_aware_healthcheck`，对两份 compose 断言存在 healthcheck 且包含 `$${AISTUDIO_PORT:-8080}/health`；`docker-compose.deploy.yml:25-30` 同时补齐 healthcheck，与镜像 `HEALTHCHECK`（`Dockerfile:224-225`）口径统一 | **RESOLVED** |

### 7.3 三审新增观察（均非阻断）

- **Q-11（既有，非本次引入）**：`.dockerignore:16` 的 `*.egg-info` 仍为无斜杠根锚定模式；工作树实测存在 `src/aistudio_api.egg-info`（可编辑安装产物），会被 `Dockerfile:202` 的 `COPY src/ src/` 带入**本地构建**镜像（CI 干净检出不受影响）。建议同步改为 `**/*.egg-info/`。
- **Q-12（观察）**：`docker-compose.yml` 与 `docker-compose.deploy.yml` 的服务定义已高度重合（差异仅在卷：绑定挂载 vs 命名卷，以及被注释的 `build: .`）。后续可考虑 `extends`/YAML 锚点复用；本地源码构建需手动修改受版本控制的 compose 文件，`git pull` 时存在冲突概率，可考虑独立 `docker-compose.build.yml` 覆盖文件。
- **N-12**：两份 compose 的服务键集合完全一致（`image/container_name/restart/extra_hosts/env_file/ports/volumes/environment/healthcheck`），`docker-compose.deploy.yml` 已不再注入任何 `AISTUDIO_CONFIG_*`，配置路径完全由镜像 `ENV AISTUDIO_CONFIG_FILE`（`Dockerfile:112`）与 `docker-entrypoint.sh:8-16` 的归一逻辑决定，无分叉风险。
- **N-13**：`docker-compose.yml` 注释掉 `build: .` 后，未取消注释时执行 `docker compose up -d --build` 不会构建本地镜像（Compose 对无 `build` 的服务忽略该标志）；`README.md:130` / `README_EN.md:131` 已把“先取消注释”写成前置条件，无歧义。

### 7.4 三轮终态统计

| 状态 | 初审 | 二审 | 三审（终态） |
|---|---|---|---|
| P0 | 0 | 0 | **0** |
| P1 | 2 | 0 | **0** |
| P2 | 5 | 1 | **0** |
| Q（未关闭） | 6 | 6 | **3**（`Q-8` 无 PR 镜像构建、`Q-11` `*.egg-info`、`Q-12` 两份 compose 复用） |
| N | 7 | 11 | **13** |

### 7.5 最终结论：`PASS`

三审逐条核验后：初审 2 项 P1、5 项 P2 与二审 1 项 P2（`P2-6`）已全部关闭，`Q-4`/`Q-6`/`Q-7`/`Q-9`/`Q-10` 亦全部落地；四道指定门禁（`ruff check` / `pyright` / 目标 `pytest` 18 passed / `git diff --check`）全绿，外加 compose YAML 解析与全文件 LF 校验通过。

配置链路（`Dockerfile:112` → `docker-entrypoint.sh:8-16` → `src/aistudio_api/config.py:110-126`）单一无分叉，`CMD ["python3","main.py","server"]` 经 `src/aistudio_api/main.py:17-19,43-59` 与 `src/aistudio_api/config.py:393-396` 默认值 100% 落到 `settings.port`/`settings.browser_port`；两份 compose 的健康检查均在容器内用 `$${AISTUDIO_PORT:-8080}` 展开，`.dockerignore` 不再误伤任何 `COPY` 源；版本号 `2.1.4` 在 `pyproject.toml`、`web/package.json`、`waa-signer` 三处与 `docs/changelog.md:6` 一致。

剩余 `Q-8`（PR 阶段镜像构建）、`Q-11`（`*.egg-info`）、`Q-12`（compose 复用）与 `N-*` 均为非阻断跟进项，不构成发布阻塞。**本报告最终结论：`PASS`。** 唯一限制：本机未安装 Docker CLI，`docker build` / `docker compose config` / 容器内时区实测未执行，镜像级行为结论基于 Docker/Compose 官方语义与静态核验（`N-4`/`N-11`），建议发布前在有 Docker 的构建机复验一次。
