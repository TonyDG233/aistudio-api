# P9-pre Acceptance（装配 / API Key guard）

状态：planned（Gate 0.5 模板实例化；执行结果在 P9-pre 切片提交时回填）。
Feature IDs：`D01-001`..`D01-004`, `D01-014`, `D01-029`..`D01-033`, `D01-047`..`D01-049`, `D04-001`..`D04-004`, `D08-001`..`D08-005`, `D10-001`..`D10-010`, `D11-015`, `D11-017`, `D11-018`, `D11-035`, `D11-036`。

| ID | 测试项 | 预期结果 | 命令 | 工件 | 生命周期插入点 | 证据等级 | 状态 |
|---|---|---|---|---|---|---|---|
| P9-01 | lifespan 启动顺序：active 预热 → 实例 startup → 模型发现 → keepalive | 顺序固定、每步只执行一次；任一步失败按关键/非关键策略处理 | `pytest tests/unit/test_lifespan_wiring.py -q`（待创建） | `docs/dev/evidence/p9-pre-*/startup.log` | Account → Instance → Pool/Router → Executor → Event Sink | fake CDP | planned |
| P9-02 | 关闭顺序：keepalive → 实例 → pool → client | 无残留任务/页面；`active_requests` 全部归零后才关闭 | 同上 | 同上 | Instance/Executor | fake CDP | planned |
| P9-03 | config fail-fast：`count<=0`、未知 `surface`、未知 `min_tier` | 启动即失败并给出字段名；非关键键（logging.dump_*）缺失回退默认 | `pytest tests/unit/test_config_schema.py -q`（待创建） | `tests/fixtures/events/event.sample.json` 同目录配置用例 | Pool/Router 装配 | 离线 | planned |
| P9-04 | API Key 默认拒绝：`api_keys` 缺失或 `[]` | 所有 API 端点 401；loopback 无 Key 也 401 | `pytest tests/unit/test_api_auth.py -q`（rewrite） | `docs/dev/evidence/p9-pre-*/auth-401.txt` | API 接入层 | 离线 | planned |
| P9-05 | 显式空 Key 开放：`api_keys: [""]` | 无 Key 请求放行；不再区分来源 IP | 同上 | 同上 | API 接入层 | 离线 | planned |
| P9-06 | 有效 Key：配置 `["k"]`，缺 Key/错 Key/对 Key | 401/401/200；Key 只显示掩码、不落日志 | 同上 | 同上 | API 接入层 | 离线 | planned |
| P9-07 | Event Sink 基线：request 与 attempt 分别记账 | 每个 attempt 一条 attempt 事件；client 结果一条 request 事件；字段与 `event.sample.json` 一致 | `pytest tests/unit/test_event_sink.py -q`（待创建） | `tests/fixtures/events/event.sample.json` | Event Sink | 离线 | planned |
| P9-08 | 装配红线：控制面不计请求实例；pool 只按实例数计容量 | 控制面启动不占 `active_requests`；无 lease/reservation 字段 | `pytest tests/unit/test_instance_pool.py -q`（rewrite） | `docs/dev/evidence/p9-pre-*/pool.txt` | Pool/Router | fake CDP | planned |

回填规则：执行后把状态改为 `passed`/`failed`，追加日期与实际输出路径；失败不得删除原行。
