# WP5 第六轮证据（2026-10-21）

> 追溯说明（B 语义）：`max_contexts` 是总实例上限。2c3p = 3 个实例，在 `max_contexts>=3` 时成立；下面“禁止 2c3p”仅适用于当时 `max_contexts=2` 的验收场景。

## 本轮结论

- 实例 = 账号 + 渠道 = 一个独立用量窗口。
- 同一账号的 PG/Build 在总实例上限允许时是两个实例，共享一个浏览器上下文。
- 当时验收的 `max_contexts=2` 场景只覆盖 2c1p / 1c2p；2c3p 会超过 2 实例上限。
- 关闭一个实例不会关闭同账号的另一个渠道实例；关闭账号最后一个实例才回收上下文。

## 长期运行注意：实例内部 id

用户侧实例列表现在显示的是**当前列表序号 1..N**，不显示后端内部 id。

后端内部 id 当前格式为 `inst-N`，`N` 只在创建新的实例记录时递增：

- 启动新实例：+1
- 新建另一个渠道实例：+1
- 容量满时“替换并切换”：旧记录删除，新记录创建：+1
- 仅在两个已有实例之间切换：不增加

因此连续运行十几天甚至更长时间时，内部 `N` 可能持续变大，但：

- Python 整数不会溢出；
- 只是字符串位数增加，不影响功能；
- 后端重启后 `_next_number` 重置为 1；
- 用户界面只显示当前列表序号，不受影响；
- 日志和证据中的 `inst-N` 在同一进程内仍可用于追溯。

如果未来需要固定长度或非递增的内部 id，可以再改为 UUID；当前不改变，避免影响测试、日志和已有证据引用。

## 本目录截图

- `round6-p1-1024.png`：P1 同账号 PG+Build 两个实例、同账号标记
- `round6-p1-390.png`：P1 移动端
- `round6-p1-stats-total-1024.png`：P1 “总模型调用”统计卡
- `round6-p4-stats-final-1024.png`：P4 模型调用主指标与客户端请求次级信息
- `round6-p4-stats-final-390.png`：P4 移动端
- `round6-switch-modal-390.png`：切换面板移动端
- `round6-click-same-account-switch-390.png`：同账号渠道实例切换后的界面
- `p2-dashboard-*.png`、`p2-switch-modal-390.png`：P2 证据
- `p3-accounts-*.png`：P3 三档证据
- `click-*.png`：关闭/新建/PG 切换真实点击证据

## 验证记录

- 后端：`pytest -q` 885 passed；`ruff check` / `ruff format --check` / `pyright` 通过。
- 前端：`type-check` / `lint` / `build` 通过。
- 实机：
  - 2 账号启动：2 实例、2 context、chat2/build0（2c1p）。
  - 单账号 PG+Build：1 context、2 实例、chat1/build1（1c2p）。
  - 第三实例：409 `instance_capacity_exhausted`，无额外 context/page。
  - 容量满替换：A-PG + B-PG → A-Build + B-PG，仍为 2 context、chat1/build1。

## 内存采样（Private Working Set）

- 场景：2 context、2 实例，其中一个 Build 渠道；采样包含 Build 切换与一次生成。
- CloakBrowser 全部进程 `WorkingSetPrivate` 求和：
  - 样本 66 个
  - 平均 **453.8 MB**
  - 峰值 **523.2 MB**
  - 最低 **387.4 MB**
- 同一批 `WorkingSet` 求和仅作对比：平均 907.8 MB、峰值 1001.1 MB；该数值重复计算共享页，不作为主指标。
- 后端 Python 进程 Working Set：平均/峰值约 **80 MB**。
