# 架构 — health-by v0.0.1

[English](../architecture.md)

## 目标

- **自托管优先**：数据只存本机 SQLite；零遥测；默认只绑 `127.0.0.1`。
- **单一内部契约**：视图与分析不依赖任何远程 API 形状（数据源可替换）。
- **演示优先**：无凭据即可运行、测试、评审 UI。
- **克制**：不做诊断、不做复合评分；每个结论可追溯到测量值、用户目标或可见个人基线。

## 数据流

```
[Fitbit/Pixel 设备] → 蓝牙 → Fitbit/Google 手机 App → 云同步
        │
        ▼  Google Health API v4（OAuth 2.0, REST）
 provider.fetch_raw(date)      31 个读取任务：14 天趋势窗口 + 90 天 ECG + 选中日日内序列
        │
        ▼  provider.translate(raw)（纯函数，测试充分）
 DaySnapshot（单一契约；字段一律可空）
        │
        ▼  storage.store_day
 days + daily_metrics + raw_days（原始载荷按天留档，可重算）
        │
        ▼  analysis.build_analysis
 个人基线对比 / 周期差值 / 覆盖与新鲜度 / 规则化头条与洞察
        │
        ▼  insights.build_insights（纯本地存档计算，零额外 API 调用）
  recovery（4 项信号 + 睡眠缺口）/ load（ACWR + MVPA）/ rhythm（SRI + 社交时差 + 余弦）
  / correlations（r + 95% CI）/ weekly / summary_table / bands / heatmap
        │
        ▼
 FastAPI（/api/v1/* + OAuth 回调）＋ 零构建前端（看板 / 设置）
```

## 模块

| 模块 | 职责 |
|---|---|
| `health_by/config.py` | 数据目录 / 主机端口 / 出站限速参数 |
| `health_by/db.py` | SQLite(WAL) 连接与版本化迁移 |
| `health_by/secrets.py` | Fernet 凭据加密（`data/secret.key`，0600） |
| `health_by/models.py` | `DaySnapshot` 契约 + 指标提取 |
| `health_by/storage.py` | days / daily_metrics / raw_days / sync_runs / settings 读写 |
| `health_by/providers/base.py` | 数据源契约（fetch_raw / translate / OAuth 可选方法） |
| `health_by/providers/demo.py` | 演示数据：合成 GHA 形状载荷，走同一翻译管线 |
| `health_by/providers/google_health/` | 真实数据源：OAuth（PKCE）+ 任务注册表 + 抓取 + 翻译 |
| `health_by/engine.py` | 同步引擎：共享限速、令牌轮换原子写回、队列 + 并发 worker、自动同步线程 |
| `health_by/analysis.py` | 基线对比 / 周期差值 / 覆盖与新鲜度 / 规则化洞察 |
| `health_by/metrics.py` | SRI / 社交时差 / 余弦节律 / ACWR / SWC / Fisher CI（纯函数） |
| `health_by/insights.py` | 恢复、负荷、节律、关联、周报与汇总表（仅本地存档） |
| `health_by/api/app.py` | FastAPI 工厂：JSON API（含 `/api/v1/insights`）+ OAuth 路由 + 静态页 |
| `health_by/web/` | 零构建前端（原生 ES modules + 手写 SVG 图表） |
| `health_by/cli.py` | Click CLI：serve / sync / demo / status / export / db（stats、check、vacuum） |

## 安全边界

- 凭据（client_secret / tokens）经 Fernet 加密后存入 SQLite；密钥文件 `data/secret.key` 权限 0600；
  API 响应永不回显 secret（只回显 `has_client_secret: true/false`）。
- OAuth 流程：PKCE S256 + 一次性 state（数据库存储、15 分钟 TTL）；回调校验 state 后才兑换 code。
- 服务默认只绑 `127.0.0.1`；数据同步请求发往 Google 官方域名；启用可选 AI 时，白名单聚合字段也会发往用户配置的服务；
  管理类写操作（清空数据）需要原文确认。
- 凭据解密失败（密钥被替换）会得到明确报错，而不是静默丢数据。

## 数据库

表：`meta` / `settings` / `credentials` / `days` / `daily_metrics` / `raw_days` / `sync_runs` / `oauth_states` / `ai_analyses` / `ai_egress`。
Schema 变更 = `db.py` 的 `MIGRATIONS` 追加新版本块 + `SCHEMA_VERSION` 加一（禁止破坏性修改已发布字段）。

## 关键设计决策

以下几点决定了很多模块的形状，理由一并写在这里：

- **demo = 合成 GHA 载荷 → 真实翻译管线**：演示数据和真实数据走同一条代码路径，杜绝"两条腿"；
- **部分失败容忍**：31 个任务独立执行，单个失败不取消其余；覆盖情况进 UI；
- **401 语义统一**：任何 401 都视为"需要重新连接"（对应 Google 测试模式 7 天令牌政策）；
- **原始留档**：每天原始载荷留档，将来可重新翻译/补算新指标，无需重新拉取。

## 借鉴说明

架构思路参考了对 OpenFit（Electron 桌面项目，已弃坑）的公开调研
（调研存档在 `research-openfit/`，不纳入 git）；**全部代码为独立重写**——
上游仓库无 LICENSE（默认保留所有权利），本项目未复制其任何代码，只借用架构模式与公开 API 工艺。
