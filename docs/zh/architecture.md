# 架构 — health-by

[English](../architecture.md)

## 目标

- **自托管优先**：数据只存本机 SQLite；零遥测；默认只绑 `127.0.0.1`。
- **单一内部契约**：视图与分析不依赖任何远程 API 形状（数据源可替换）。
- **本地合成数据**：`devtools/` 生成与真实数据同形状的库，无凭据即可开发与评审 UI。
- **克制**：不做诊断、不做复合评分；每个结论可追溯到测量值、用户目标或可见个人基线。

## 数据流

```
[Fitbit/Pixel 设备] → 蓝牙 → Fitbit/Google 手机 App → 云同步
        │
        ▼  Google Health API v4（OAuth 2.0, REST） 或 Fitbit/Takeout 离线归档（`import-fitbit`）
 provider.fetch_raw(date)      30 个读取任务：14 天趋势窗口 + 90 天 ECG + 选中日日内序列
        │
        ▼  provider.translate(raw)（纯函数，可通过 `retranslate` 离线重译）
 DaySnapshot（单一契约；字段一律可空）
        │
        ▼  storage.store_day
 days + daily_metrics + raw_days（zlib BLOB 压缩存档，按顶层 job 键合并）
        │
        ▼  analysis.build_analysis + reliability.assess
 个人基线对比 / 周期差值 / 周内-周间方差比滚动展示 / 覆盖与新鲜度 / 规则化洞察
        │
        ▼  insights.build_insights（纯本地存档计算，零额外 API 调用）
  recovery（4 项信号 + 睡眠缺口）/ load（ACWR + MVPA）/ rhythm（SRI + 社交时差 + 余弦）
  / correlations（Pearson + Bartlett n_eff + Fisher CI）/ weekly / summary_table / bands / heatmap
        │
        ▼
 FastAPI 拆分路由（/api/v1/* + OAuth 回调）＋ 零构建 ES 模块前端（浅色 / 深色 / 跟随系统）
```

## 模块

| 模块 | 职责 |
|---|---|
| `health_by/config.py` | 数据目录 / 主机端口 / 出站限速参数 |
| `health_by/db.py` | SQLite(WAL) 连接（`0600`）、版本化迁移与在线备份 |
| `health_by/secrets.py` | Fernet 凭据加密（`HEALTH_BY_SECRET_KEY*` 或 `data/secret.key`，`0600`） |
| `health_by/models.py` | `DaySnapshot` 契约 + 指标提取 |
| `health_by/storage.py` | days / daily_metrics / zlib 压缩 `raw_days` / sync_runs / 流式导出读写 |
| `health_by/providers/base.py` | 数据源契约（fetch_raw / translate / OAuth 可选方法） |
| `health_by/providers/google_health/` | 真实数据源：OAuth（PKCE，10 个 scope）+ 30 类任务注册表 + 抓取 + 翻译 |
| `health_by/importers/fitbit_zip.py` | Fitbit / Google Takeout ZIP 与解压目录离线导入器 |
| `health_by/engine.py` | 同步引擎：共享限速、令牌轮换原子写回、队列 + 并发 worker、自动同步线程 |
| `health_by/analysis.py` | 基线对比 / 周期差值 / 覆盖与新鲜度 / 规则化洞察 |
| `health_by/reliability.py` | 周内/周间方差比（`rolling7` vs `daily`）与 `STABLE_WITHIN_SD` 噪声下限 |
| `health_by/metrics.py` | 统一 `BOUNDS`、SRI / 社交时差 / 余弦节律 / ACWR / Bartlett `n_eff` / Fisher CI |
| `health_by/insights/` | 分拆包（`common` / `series` / `correlations` / `recovery` / `rhythm` / `builder`） |
| `health_by/ai/` | 聚合出口白名单（`v4-input-3` / `v4-window-2`）、模型调用、校验 `v3` 与审计记录 |
| `health_by/api/` | FastAPI 工厂（`app.py`）、`security.py`、`common.py` 与拆分 `routes_*.py` 路由 |
| `health_by/web/` | 零构建 ES 模块前端（`theme.js`、`ui-controls.js`、拆分 `dashboard-*.js`、SVG 图表、浅色/深色主题变量） |
| `health_by/cli.py` | Click CLI：serve / sync / status / export / retranslate / import-fitbit / db（stats、check、vacuum、compress-raw） |
| `devtools/` | 本地开发用的合成生成器，不随包发布 |

## 安全边界

- 凭据（OAuth client_secret / tokens / AI API key）经 Fernet 加密后存入 SQLite；密钥支持
  `HEALTH_BY_SECRET_KEY`、`HEALTH_BY_SECRET_KEY_PATH` 或 `data/secret.key`（`0600`）；新建库文件与导出文件默认 `0600`，API 响应永不回显密钥。
- OAuth 流程：PKCE S256 + 一次性 state（存 `oauth_states`、15 分钟 TTL、写入时清理过期行）；uvicorn 访问日志对 `/oauth/callback` 与 `/settings` 的 query 参数脱敏。
- 本地 Web 安全中间件（`api/security.py`）：校验 `Host` 头（支持 `HEALTH_BY_ALLOWED_HOSTS` 白名单）、通过 `Origin` / `Sec-Fetch-Site` / `Referer` 拦截跨源状态变更与 `/oauth/start`、关闭 `/openapi.json` 与 `/docs`，并下发 `CSP`（`script-src 'self'`）、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer` 以及 HTML 页面与 API 的 `Cache-Control: no-store`（`/static/*` 为 `no-cache`）。
- BYOK AI 出口校验拦截链路本地/元数据/未指定地址（含 DNS 解析后检查），非回环地址发送密钥强制 HTTPS，切换 `ai_base_url` 源且未传新密钥时自动清除旧密钥，单次响应限制 2 MiB。
- `delete_all_data()` 启用 `PRAGMA secure_delete = ON`，清空全部 8 张健康/同步/AI/OAuth 表并执行 `VACUUM` 与 WAL checkpoint，界面操作需原文输入 `DELETE ALL DATA`。

## 数据库

表：`meta` / `settings` / `credentials` / `days` / `daily_metrics` / `raw_days` / `raw_measure_dates` / `sync_runs` / `oauth_states` / `ai_analyses` / `ai_egress`。

`raw_days.payload` 默认使用 zlib 压缩 JSON BLOB，同时透明兼容读取历史未压缩文本；存量库可通过 `health-by db compress-raw` 离线批量转换。同源重同步按顶层 job 键合并防局部失败覆盖，跨源写入时整体替换 `days` 与 `daily_metrics`。

Schema 变更 = `db.py` 的 `MIGRATIONS` 追加新版本块 + `SCHEMA_VERSION` 加一（禁止破坏性修改已发布字段）。

## 关键设计决策

以下几点决定了很多模块的形状，理由一并写在这里：

- **单一翻译管线**：开发生成器产出 GHA 形状载荷，与真实数据走同一套翻译代码，杜绝"两条腿"；
- **部分失败容忍**：30 个任务独立执行，单个失败不取消其余；覆盖情况进 UI；
- **401 语义统一**：任何 401 都视为"需要重新连接"（对应 Google 测试模式 7 天令牌政策）；
- **原始留档**：每天原始载荷留档，可用 `health-by retranslate` 离线重译或将来补算新指标，无需重新拉取。
- **测量日期单独索引**：载荷里带的测量日期在写入时记进 `raw_measure_dates`，覆盖度检查查表即可，
  不必每次重新解析整份载荷。

## 借鉴说明

架构思路参考了对 OpenFit（Electron 桌面项目，已弃坑）的公开调研
（调研存档在 `research-openfit/`，不纳入 git）；**全部代码为独立重写**——
上游仓库无 LICENSE（默认保留所有权利），本项目未复制其任何代码，只借用架构模式与公开 API 工艺。

## 已知权衡与后续可选项

Health By 面向**单用户、本地优先的自托管**场景。以下事项经过评估属于非阻塞的架构取舍，当前按意图暂不引入额外复杂度，统一记录为后续可选项（另见 [SECURITY.md](../../SECURITY.md#known-design-boundaries--non-goals-single-user-scope)）：

| 主题 | 当前边界与依据 | 未来可选演进 |
|---|---|---|
| AI 出站请求的 DNS Rebinding（TOCTOU） | 连接前校验解析 IP 并拒绝链路本地/元数据/未指定地址；`ai_base_url` 由本机所有者自行配置。 | 若未来扩展到多租户或不可信 URL 场景，再引入带 TLS SNI 校验的自定义传输层做 IP 绑定。 |
| 默认端口 `Origin` scheme 严格比对 | 现代浏览器已有 `Sec-Fetch-Site` 拦截跨协议跨站请求，本机默认场景可利用性极低。 | 可在 `api/security.py` 中顺手补上显式 scheme 相等判断作为纵深加固。 |
| 同一 `data/` 目录切换 Google 账号 | 架构假定单目录对应单人健康档案；更换账号应使用新目录或先清空数据。 | 在 `meta` 中记录脱敏后的远端账号指纹哈希，换号时提醒或阻止混档。 |
| AI 生成与并发修改设置的毫秒级读窗口 | 单用户同时点击生成分析与切换地址/密钥的概率极低。 | 在 `AIService.analyze()` 中将读取设置与凭据合并到同一个只读短事务快照。 |
| 间接依赖 `opentelemetry-api` | 由 FastAPI 间接引入，应用代码未启用任何 OpenTelemetry 追踪中间件。 | 通过 `requirements-lock.txt` 与 Dependabot 保持版本及时更新。 |
| 前端模拟 DOM 与真实浏览器视觉覆盖 | 已由 Node ES 模块测试 + `scripts/check_web_refs.py` 静态 ID 校验 + CI 真实 Chromium 冒烟测试（`tests/test_browser_e2e.py`）共同保障。 | 后续按需补充多视口截图回归比对与辅助功能自动化校验。 |
