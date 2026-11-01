# 数据覆盖与已知坑 — Google Health API v4

[English](../data-coverage.md)

> 来源：对 OpenFit 源码的调研（`research-openfit/`）+ Google 官方文档 + 本项目实现的交叉验证。
> 本页是"接口工艺"的知识沉淀；代码中的相关处均有对应注释。

## 读取任务（31 个）

| 类别 | 任务 | 端点 | 窗口 |
|---|---|---|---|
| 元数据 | identity / profile / settings / pairedDevices / irnProfile / userinfo | GET `/users/me/*`、`oauth2/v3/userinfo` | — |
| 日汇总 ×12 | 步数 / 总卡路里 / 距离 / 楼层 / 活跃分钟 / 活动区分钟 / 久坐 / 体重 / 体脂 / 饮水 / 营养 / 核心体温 | POST `dataPoints:dailyRollUp`（civil 区间，`windowSizeDays=1`） | 14 天 |
| 每日记录 ×6 | 静息心率 / HRV / 血氧 / 呼吸率 / 睡眠皮温 / VO₂max | GET `dataPoints:reconcile` | 14 天 |
| 会话 | 睡眠（`google-wearables`）/ 运动 | GET `dataPoints:reconcile` | 14 天 |
| 独立 | ECG（`list`）/ 不规则心律通知（`list`）/ 血糖 | GET `dataPoints` | 90 天 / 14 天 / 14 天 |
| 日内 | 分时步数 / 分时心率（`google-wearables`，`list`） | GET `dataPoints` | 选中日 |

`reconcile` = 官方"对账流"：跨所有数据源（设备 + 第三方 App + 手动记录）自动去重合并，
与用户在 Fitbit App 看到的数据一致；不需要跨源合并时用 `list`。

## 已确认的坑（代码中已处理）

1. **`dailyRollUp` 区间结尾**：实测要求"当日 23:59:59"（`civilDateTime(end_of_day=True)`），
   官方文档的闭开区间语义会得到缺最后一天的结果。见 `client.py` 注释。
2. **reconcile 重复聚合**：同一阶段会被重复返回（不同 count）→ 按原始类型取 `max` 去重。
3. **`ASLEEP` 双计**：`stagesSummary` 里 `ASLEEP` 是 `LIGHT/DEEP/REM` 的合计；
   存在明细阶段时必须跳过它，否则睡眠分钟数翻倍。
4. **睡眠归属日**：按 `civil_end_time` 的日期归属（跨午夜会话记到醒来那天）；
   小睡由 `metadata.nap == true` 标记，单独统计。
5. **单位换算**：距离 `millimetersSum` → km（÷10⁶）；duration 形如 `"1920s"`；
   久坐 `durationSum` 秒 → 分钟；体重 `weightGramsAvg` 克 → kg。
6. **出站限速 <5 req/s**：全客户端统一节流（默认 225ms 间距）；429 按 `retry-after`
   或指数退避（上限 30 秒，最多重试 2 次）；分页守卫 100 页。
7. **401 = 授权失效**：统一抛"需要重新连接"（对应测试模式令牌政策，见下）。
8. **filter 语法**：按记录类型拼字段路径——daily：`{type}.date`；sleep：`sleep.interval.civil_end_time`；
   ecg：`electrocardiogram.interval.start_time`；sample：`{type}.sample_time.civil_time`；
   interval：`{type}.interval.civil_start_time`。类型名中的 `-` 要替换成 `_`。

## 原始载荷形状

**归一化处理**

1. **record 类接口没有 `civil*` 字段**：`sleep` / `activities`（运动）/ `ecg` 只给物理时间
   `2026-01-15T17:03:00Z` + `startUtcOffset: "28800s"`（合成示例）。直接按 UTC 处理会把跨夜睡眠记到前一天、
   界面时间显示偏 8 小时。翻译层统一经 `_civil_iso()` 转成带本地偏移的 ISO。
   （`dailyRollUp` 与 sample/interval 类端点——`stepsDaily`、`restingHeartRaw`、`stepsIntraday`、
   `heartIntraday`——本身带 `civil*`，无需转换。）
2. **同一指标多数据源重复**：同一日的 `stepsIntradayRaw` 可能包含多个来源，例如
   `FITBIT/Fitbit Air`、`FITBIT/MobileTrack` 和 `HEALTH_KIT`。直接累加会重复计数。
   归一化按“Fitbit 平台设备 > Fitbit 平台其它 > 无设备名”只取单一主导来源，心率同样处理。
3. **心率样本的分钟聚合**：按分钟求均值入库；`hr_min/hr_max` 仍取原始采样极值。
   原始载荷按天留档，可供重算。

**一次典型单日的字段面貌（演示数据）**

| 指标 | 示例值 |
|---|---|
| 步数 / 距离 / 热量 | 9,120 步 / 6.7 km / 2,240 kcal |
| 活跃分钟 / 久坐 / 爬楼 | 62 min / 540 min / 5 层 |
| 睡眠 | 432 min（23:40–07:12，效率 95%，深 76 / 浅 210 / REM 128 / 醒 18） |
| 静息心率 / 心率区间 | 58 bpm / 52–128 bpm（分钟聚合后） |
| HRV / SpO₂ / 呼吸率 | 42.0 ms / 97.0% / 15.0 次/分 |
| 运动 | 步行 30 min（3,100 步） |

> 上表是**演示数据**的形状示例，不是任何个人的测量值。演示模式（无需凭据）可复现同样的字段结构。

**可选数据**：体重、体脂、饮水、营养、血糖、ECG、不规则心律、核心体温的可用性取决于设备与账号。
UI 在缺少对应数据时隐藏区块。

**历史深度**：由账号和设备的存档范围决定。API 的 `dailyRollUp` 单次区间上限：心率/活动分钟/消耗热量/心率区间为 14 天，其余类型 90 天；
本项目按天抓取 14 天窗口，因此回填不受单次区间限制。超过账户最早数据的日期返回空 → 引擎不落库（`empty`）。

**分析层读数校验**：上游偶尔会给出无效读数（例如某天 HRV 为 0），设备未提供有效值时会这样返回。分析层对 HRV（5–250 ms）、
静息心率（25–220 bpm）、血氧（50–100%）、睡眠时长（0–16 小时）等做范围校验，越界值不参与基线/相关计算；
原始载荷仍完整保留在 `raw_days`，可追溯。极端的低值（例如某天睡眠远低于平常）如果是真实测量，会保留并如实展示，而不是当作异常丢弃。

## 硬限制（产品层面）

- **无蓝牙直连**：第三方应用的唯一通道是云端 API；数据新鲜度取决于用户手机端 App 同步。
- **可得性差异**：ECG / 不规则心律 / 血糖等取决于设备型号、地区、固件、授权范围——缺失是常态，
  不是错误；UI 对缺失区块整体隐藏。
- **测试模式 7 天令牌**：外部用户类型 + Testing 状态的应用，refresh token 每 7 天过期
  （Google 官方 OAuth 政策，无豁免）→ 设置页提供状态提示与一键重连。
- **API 演进中**：官方声明 2026 年内可能有需要跟进的变更 → 翻译层全容错 + 原始留档可重算。

## 官方参考

- 数据与端点：`developers.google.com/health/data-types`、`/health/endpoints`
- 迁移指南（旧 Fitbit Web API → GHA）：`developers.google.com/health/migration`
  （旧 API：2026-09-30 停止支持，**2026-10-30 彻底关停**）
- OAuth 令牌政策：`developers.google.com/identity/protocols/oauth2`
- 官方 CLI（取数参考）：`github.com/Google-Health-API/google-health-cli`（Apache-2.0）
