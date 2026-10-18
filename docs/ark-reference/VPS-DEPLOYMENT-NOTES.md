# 小鸡（VPS）部署情况与硬约束（移交版 · 脱敏）

> 来源：Ark 仓库 `.local_logs/vps-system-state.md`（2026-09-03 快照）+ 2026-10 会话补充。
> **完整未脱敏版仅存于 Ark 本机仓库**；本文已移除密钥、完整 IP、域名与节点端口。
> 用途：为 Spike D（内存测绘）、WP5（组配额/实例数默认值）、部署方案提供真实硬约束。

## 一、硬件与系统

- 套餐：DediRock "Promo VPS Special NY"（ColoCrossing 机房），$9.88/年。
- 配置：**1 vCore / 2 GB RAM / 30 GB SSD / 2 TB 流量 / 1 Gbps / 1 IPv4**。
- 系统：Debian 13 (trixie) x86_64；**物理 2G + Swap 4G**（`/etc/fstab` 持久化，swappiness=20）。
- 出口：`199.188.x.x`（Buffalo, NY，AS36352）——**该原生 IP 已被 Google 降权**（见三）。
- 磁盘使用：约 17G（含 Docker 镜像 ~11G），可用约 ~12G（扩容前需 `docker system prune` 习惯）。

## 二、同机共存服务（资源竞争是硬约束）

| 服务 | 目录 | 端口（均已绑 127.0.0.1） |
|---|---|---|
| AIStudioToAPI 反代（现役 Ark 方案） | `/opt/aistudio` | 7860 |
| AstrBot（QQ bot） | `/opt/astrbot` | 6185 |
| NapCat（QQ 协议端） | `/opt/astrbot` | 6099 |

- 反代 compose 环境（键名保留，敏感值略）：`MAX_CONTEXTS=2`、`IMMEDIATE_SWITCH_STATUS_CODES=429,503,403`、
  `ENABLE_AUTO_SWITCH=true`、`AUTO_SWITCH_INTERVAL_HOURS=1`（偏激进，后续建议调大）、`SWITCH_ON_USES=80`、
  `TZ=Asia/Shanghai`；挂载 `./auth:/app/configs/auth`（多账号池）与 `./data:/app/data`。
- **单核 CPU 长期负载 1.3+**；"2 账号（Ark 方案）+ bot"已接近满载。实测基线：`Mem: 1.9Gi total, 1.2G used`。
- 对移植方案的直接含义：
  - Python 版（单浏览器多标签页）取代 Ark 方案（2 × ~700MB context）后，内存压力应显著下降
    （对方文档值 250–380MB 含浏览器）；
  - **CPU 1 核是另一瓶颈**：CloakBrowser 单渲染进程串行化会造成排队，wp5 并发/实例配额默认值须保守；
  - Spike D 应在"2G 物理 + 4G swap，且 AstrBot 常驻"的真实预算下做；建议通过标准：
    反代总 PSS（含浏览器与 2 个页面实例）**≤ ~800MB**，且压测期间不触发持续 swap 抖动。

## 三、Google 出网链路（必须遵守）

- VPS 原生 IP 被 Google 标记（`available-regions` 降权，直连流出即被拦）→ **所有到 Google 的流量必须走代理链**：

```
容器 → 172.17.0.1:40001 (xray socks 转发) → 127.0.0.1:40000 (Cloudflare WARP 代理模式) → CF 出口 (AS13335)
```

- 反代容器内配置：`HTTP_PROXY/HTTPS_PROXY=socks5://172.17.0.1:40001`。
- 转发层：`warp-relay.service`（xray，配置 `/usr/local/etc/xray/warp-relay.json`）；
  **socat 方案已废弃**（TCP 转发遇 WARP 抖动挂死）；健康自愈 `warp-health.sh` + cron `*/10 * * * *`。
- 对移植方案的直接含义：
  - CloakBrowser 启动与页面出网、`curl_cffi` cookie 刷新、`GetAiStudioBenefitTier` 探活，
    **全部要挂 `socks5://172.17.0.1:40001`**（容器内地址；宿主机调试用 `127.0.0.1:40001`）；
  - Chromium 不读 `HTTP_PROXY` 环境变量，需要用其代理命令行参数（或 aistudio-api 现有
    `settings.proxy_url` 通道）显式指向该 socks5。

## 四、域名与入口（脱敏）

- 反向入口：Cloudflare Tunnel（`cloudflared` 系统服务，开机自启）→ 反代 Web/API 域、bot 面板域
  （具体域名见本机完整版）。
- 公网暴露面：仅 SSH 22 + 三个梯子节点端口（xray-cf-lite，具体端口见完整版；凭 UUID 防护）；
  7860/6185/6099 均已收紧为 127.0.0.1。

## 五、历史踩坑（设计约束来源，避免重蹈）

1. **IP 被 Google 降权**：一切 Google 直连不可用 → 统一 WARP 出口（承接上节）。
2. **CF Worker 曾被重试风暴打满**（10 万/日额度，urltest + 多账号连环重试）→ 重试必须节流；
   对方的黏性调度 + 按模型 429 隔离正契合此约束，勿引入"多账号并发冲锋"逻辑。
3. **socat 转发不稳** → xray socks 转发 + 健康自愈。
4. **端口裸露事故** → 全部服务改绑 127.0.0.1；新部署（Python 版）同样必须默认内网绑定。
5. **磁盘紧张**：镜像曾达 11GB+ → 部署注意体积（CloakBrowser 解压体积、旧镜像 prune）。

## 六、常用运维命令（脱敏保留）

```bash
# 反代（现役 Ark）
cd /opt/aistudio && docker compose logs -f
cd /opt/aistudio && docker compose pull && docker compose up -d

# WARP 链路
warp-cli status                                   # Connected = 正常
bash /usr/local/bin/warp-health.sh                # 手动健康检查
systemctl status warp-relay                       # 转发层

# 网络验证（链路通 = 204）
curl -x socks5://127.0.0.1:40001 -s https://www.gstatic.com/generate_204
ss -tlnp
```

## 七、对 P0–P5 的直接含义（清单）

- **Spike D**：以"1 核 / 2G+4G swap / AstrBot 常驻"为真实预算；记录反代总 PSS、单实例增量、
  swap 触碰情况；结论回填计划书 WP5 默认配额。
- **WP5 默认配额建议**：free 组 `max_page_instances` 初值 2（与现役 MAX_CONTEXTS=2 对齐）、
  pro 组 1；实例空闲回收沿用 900s（Termux 类 900/标准 1800）。
- **代理**：把 `socks5://172.17.0.1:40001` 作为部署默认值写入部署文档/示例（宿主机为 `127.0.0.1:40001`）。
- **部署形态**：Docker（沿用 `/opt/aistudio` 目录可平滑替换 compose；注意 Python 镜像 + CloakBrowser
  的磁盘预算与镜像源加速）。
- **风险提示**：机房 IP + WARP 出口信誉良好是当前账号安全前提；任何"从 VPS 原生 IP 直连 Google"
  的调试行为都会重现 §五.1（本机调试请同样挂代理）。