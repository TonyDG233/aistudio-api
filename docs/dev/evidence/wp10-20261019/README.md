# WP10 账号保活后端验收证据（2026-10-19）

分支：`p1-ark-merge`
状态：实现完成，质量门通过，真机最小额度验收通过；**未提交**，等待用户批准。

## 1. 实现范围

三条独立机制，均可单独开关：

| 机制 | 开关 | 落点 | 说明 |
|---|---|---|---|
| 定时切号 | `auto_switch` / `auto_switch_interval_hours` | active 账号 + `auth.json` | 默认 false/3h；按 `auth_saved_at` 最久未回写优先选号 |
| 自动刷新 Auth | `keepalive_auth_update` / `..._hours` | `auth.json` cookies/origins | 默认 true/24h；切号前、初始化、重连、24h、关闭前回写 |
| 自动更新账号信息 | `keepalive_metadata_refresh` / `..._hours` | `registry.json` / `meta.json` | 默认 true/24h；tier/email/expired/tier_checked_at；与手动刷新共享 409 |

附加：
- 启动预热会激活 active 账号并回写 Auth，不阻塞启动。
- 活跃页轻量活动 `keepalive_active_activity` 默认关闭。
- `GET /api/keepalive` 查询最近结果；`POST /api/keepalive/run` 手动触发。
- `PUT /config/features` 提供未来 WP7 UI 的结构化配置接口。
- 切号门槛聚合 chat + build 在途；切号时关闭/重建 build 页；手动/自动切号共用切号锁。

## 2. 质量门

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
135 files already formatted

bun x pyright src tests
0 errors, 0 warnings, 0 informations

uv run pytest tests/unit -q
487 passed in 19.46s
```

## 3. 真机最小额度验收

未发起任何模型请求，以下均为零模型额度操作。

### 3.1 元数据 tier 探测

命令：`uv run python <tmp>/wp10_real_refresh.py`

```text
accounts=2
result id=acc_b1a36ccf ok=True tier=free expired=False error=
```

说明：使用账号已有 cookies 调用 `GetAiStudioBenefitTier`，未打开浏览器、未写 auth。

### 3.2 真实浏览器 Auth 回写

命令：`uv run python <tmp>/wp10_real_auth.py`

```text
active=acc_46223636 name=Ark:auth-5
activated=True
cookies_saved=True
```

随后 `auth.json` 的 mtime 更新，`meta.json` 的 `auth_saved_at` 更新为
`2026-10-19T07:25:11.424760+00:00`。

### 3.3 启动与 API 冒烟

启动：`uv run aistudio-api-server --port 18080 --browser-port 19223`
代理：`settings.proxy_url = http://127.0.0.1:10809`（已脱敏；浏览器启动使用同一代理）

```text
GET /health
{"status":"ok","busy":false}

GET /api/keepalive
available=true
enabled={auto_switch:false, auth_update:true, metadata_refresh:true, active_activity:false}
in_flight={chat:0, build:0, total:0}
```

启动日志显示 active 账号被真实激活并回写：

```text
已加载页面 Cookie 11 个 (auth_file=...acc_46223636/auth.json)
已保存 14 个 Cookie 至 ...acc_46223636/auth.json
会话蓝绿平滑热切成功
```

### 3.4 手动触发 Auth 回写

```text
POST /api/keepalive/run {"task":"auth_update"}
{"ok":true,"reason":"saved","account":"acc_46223636",...,"trigger":"api"}
```

状态接口随后显示：

```text
last_results.auth_update = {"ok":true,"reason":"saved","account":"acc_46223636","trigger":"api"}
meta.auth_saved_at = 2026-10-19T07:28:48.710509+00:00
```

### 3.5 真实自动切号与切回

```text
POST /api/keepalive/run {"task":"auto_switch"}
{"ok":true,"reason":"switched","from":"acc_46223636","to":"acc_b1a36ccf"}

registry.active_account_id = acc_b1a36ccf
auth_saved_at(acc_46223636) = 2026-10-19T07:29:27.411595+00:00
auth_saved_at(acc_b1a36ccf) = 2026-10-19T07:29:32.489662+00:00

POST /api/accounts/acc_46223636/activate
200 OK，恢复 active=acc_46223636
auth_saved_at(acc_46223636) = 2026-10-19T07:29:54.617159+00:00
```

说明：切号前保存旧账号 auth，切号后保存新账号 auth；全程无模型请求。

## 4. 新增测试

- `tests/unit/test_account_keepalive.py`
- `tests/unit/test_account_store_keepalive.py`
- `tests/unit/test_keepalive_config.py`
- `tests/unit/test_routes_keepalive.py`

测试按行为契约编写：Auth 落点、元数据筛选、在途跳过、单账号不切、LRU 候选、409 互斥、配置归一化、auth 文件字段保留。

## 5. 备注

- 当前 worktree 未提交；文档与代码改动均保留。
- 真实账号邮箱等敏感信息不在证据中展开。
- 未修改 `web/` 或任何前端静态产物。