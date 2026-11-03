# 接入真实数据（GCP OAuth）

[English](../setup-google-health.md)

> 目标：接入 Google Cloud 项目，同步真实的 Fitbit / Google Health 数据。
> 模式：**BYO（自带 GCP 项目）**——每个部署者使用自己的 OAuth 客户端，规避 Google Restricted scope 的公开分发审查。

## 前置检查（S0）

- [ ] Fitbit 账号已迁移到 Google 账号：以 **Fitbit App**（手机）能正常登录并看到数据为准——不需要打开 fitbit.com 网页面板（该网页入口随迁移状态变化，容易误导）。
- [ ] 要接入的数据属于**这个** Google 账号（同一账号能在 Fitbit App 里看到手环数据）。
- [ ] 部署机器能出站访问 `oauth2.googleapis.com` 与 `health.googleapis.com`（token 兑换与数据抓取都在服务端发起）。

> 注意：本项目**不做「浏览器导出」**。网页只在两个环节出现：①一次性创建 GCP OAuth 客户端；②点一次「连接」完成授权。此后数据由服务端凭 refresh token 自动同步（默认每 6 小时），无需浏览器参与。

## 步骤（每步都有验证点）

### S1 · 建项目 + 启用 API

1. 打开 `console.cloud.google.com`，用存放 Fitbit 数据的 Google 账号登录。
2. 顶部项目选择器 → **新建项目**，名称任取（例如 `health-by`，项目 ID 自动生成，记下来）。
3. 左侧「API 和服务」→「库」→ 搜索 **Google Health API** → 启用。

验证：API 页面显示状态「已启用」。

### S2 · OAuth 同意屏幕

1. 「API 和服务」→「OAuth 同意屏幕」（新版界面为「Google Auth Platform」→「品牌 / Branding」）：
   - 应用名称：任取（例如 `health-by`）
   - 用户支持邮箱 / 开发者联系邮箱：你的邮箱
   - 受众（Audience）：**外部 / External**
2. 发布状态保持 **测试 / Testing**；在「测试用户」里添加你自己的 Google 账号。

验证：Audience 页显示 Testing，且测试用户 1 人。

### S3 · 数据访问范围（Scopes）

在「数据访问 / Data Access」的 Google Health API 范围里勾选 **9 个只读 scope**（不选任何 `*.writeonly`；`reproductive_health` / `logged_symptoms` / `mindfulness` 与本项目无关，不选）：

```
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
https://www.googleapis.com/auth/googlehealth.sleep.readonly
https://www.googleapis.com/auth/googlehealth.ecg.readonly
https://www.googleapis.com/auth/googlehealth.irn.readonly
https://www.googleapis.com/auth/googlehealth.location.readonly
https://www.googleapis.com/auth/googlehealth.nutrition.readonly
https://www.googleapis.com/auth/googlehealth.profile.readonly
https://www.googleapis.com/auth/googlehealth.settings.readonly
```

说明：代码 `SCOPES` 共 11 项 = 上述 9 个健康只读 + `openid` + `profile`（OIDC 非敏感 scope，运行时自动授权，**不需要**登记到同意屏幕）。

验证：数据访问页显示 9 个范围（`settings.readonly` 归"敏感"，其余 8 个归"受限"，分级差异不影响功能）；改完必须点 **Save**。

### S4 · 创建 OAuth 客户端

「凭据 / Clients」→ 创建客户端 → 类型 **Web 应用**：

- 名称：`health-by web`
- 已获授权的重定向 URI：`http://127.0.0.1:39441/oauth/callback`

> 常见坑：重定向 URI 必须填在「**已获授权的重定向 URI / Authorized redirect URIs**」，不要填到「已获授权的 JavaScript 来源 / JavaScript origins」——后者只接受 `scheme://host[:port]`（不能带路径），填错会报 `Invalid Origin: URIs must not contain a path`。本项目不用 JavaScript origins，留空即可。
>
> 原理备注：`127.0.0.1` 这个回调地址不需要公网可达——它只是「同意后浏览器跳回的落点」，而浏览器就在 部署机 本机；Google 从不主动连入 部署机，token 兑换与数据抓取都是 部署机 出站请求。

保存后得到 **Client ID / Client Secret**（Secret 只显示一次；**不要发到聊天里**）。

### S5 · 在 health-by 设置页填入凭据

1. 打开 `http://127.0.0.1:39441/settings`（服务需在运行）。
2. 填 Client ID / Client Secret，回调地址保持默认 `http://127.0.0.1:39441/oauth/callback`，点「保存凭据」。
3. 点「连接（跳转到 Google 授权）」→ 选择测试用户账号 → 同意（会出现「未验证应用」提示，属测试模式正常现象）。

验证：跳回设置页显示 `oauth=ok`，数据源为 Google Health 且状态「已连接」。

### S6 · 首次真实数据同步

1. 设置页点「立即同步（昨天 + 今天）」；或 CLI：`.venv/bin/health-by sync --days 2`。
2. 查看看板与「数据覆盖」区块。

验证：

- [ ] 看板出现真实步数 / 睡眠 / 心率（与 Fitbit App 对得上，允许 ≤1 天延迟）；
- [ ] 覆盖率尽量接近 31/31（部分 scope 未授权会掉项，记录具体失败项）；
- [ ] 无 401/403 报错。403 → 回查 S3 scope；401 → 回查连接与令牌。

### S7 · 记录与收尾

- [ ] 真实数据观察写入私有部署笔记（哪些端点有数据、哪些为空、字段差异）；
- [ ] 勾选本文件末尾的接入检查清单；
- [ ] 观察 7 天令牌过期行为（到期后设置页应提示「需要重新连接」，重连一次即可）；
- [ ] 若对外发布：补 `LICENSE` 文件，并在 `pyproject.toml` 填好作者与许可字段。

## 附 · 数据通道选项对比

| 通道 | 用处 | 好处 | 坏处 | 建议 |
|---|---|---|---|---|
| Google Health API v4（本项目） | 日常自动同步、长期历史 | 官方通道、增量、自动、可审计；写入本地 SQLite | 需一次性建 GCP 项目；测试模式 refresh token 7 天到期需点一次重连；Restricted scope 只能个人自用 | **主通道** |
| Fitbit 账户手动导出（ZIP） | 一次性历史回填 | 零配置、拿到全量原始文件 | 手动、非增量、格式与 API 不同（分钟级数据可能不同）、无法自动化 | 仅作补充（需要时再加导入器） |
| Google Takeout | 同上（随迁移状态包含 Fitbit 数据） | 同上 | 入口随账号迁移状态变化、体积大、延迟 | 同上 |
| Android Health Connect 桥 | Pixel 本机数据转存 | 不经 Google Cloud | 需手机端常驻桥梁 App、仅 Android、覆盖数据源有限 | 不建议作主通道 |
| 第三方聚合 API（Terra / Spike / Vital 等） | 省去 GCP 配置 | 接入快 | 健康数据交给第三方，违背自托管/隐私原则；收费 | 不推荐 |
| 旧 Fitbit Web API | — | — | 2026-10-30 关停 | 不可行 |

> 结论：主线用 Google Health API；手动导出不是"一直导出"，只可能作为一次性历史回填。

## 安全注意

- Client Secret、授权码、access/refresh token **一律不贴到聊天**；只填写在本地设置页（服务端 Fernet 加密存储）。
- 测试模式 refresh token **7 天过期**是 Google 政策，不是 bug；到期重连一次即可。
- 不要把 GCP 项目公开给他人使用；公开分发时每个部署者应自建项目（BYO）。
