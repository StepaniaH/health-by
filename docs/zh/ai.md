# BYOK AI 解读

[English](../ai.md)

默认关闭。设置页的「AI 解读」填写 Base URL（含 `/v1`）、模型名称和 API key，再勾选启用并保存。
勾选时会显示目的服务与模型，以及失败修复会发送的内容。支持 OpenAI Chat Completions 兼容协议；
本地 Ollama 示例为 `http://127.0.0.1:11434/v1`、`qwen2.5:3b`，本地未要求认证时 key 可留空。
服务需支持 `/chat/completions`、非流式 JSON 输出及 `response_format: json_object`。
Base URL 末尾误带 `/chat/completions` 时会自动去掉，避免重复拼接；不会自动猜测或补上 `/v1`。

在日报「02 AI 解读」点击生成。读页面只取缓存，不会调用模型；已有结果可重新生成。
结果按日期区间、模型、提示词版本、输入协议版本及 Base URL 查找，切回原模型可读它的历史结果。
同步数据后不会自动重算，需点击「重新生成」更新解读。

## 时间窗口

AI 区块可选单日、近 7 / 30 / 90 天或自定义开始与结束日期。预设以日报所选日期为结束，
区间包含首尾，单次最多 366 天。改日期后点「应用区间」只读取已有解读；
只有点「生成解读」才向模型发送请求。日报所选单日没有存档时，仍可选择包含其它存档的区间。

旧请求 `{"date":"YYYY-MM-DD"}` 等价于单日 `start == end`。多日发送**在本机算好的窗口聚合**，
不是 N 天日记录；请求数据对象完全不含数组。单日保留原有统计，加 `window` 元信息，
相关性结果改为按指标对索引的对象。多日根对象仅 `window`、`metrics`、`goals`：

| 对象 | 内容与口径 |
|---|---|
| `window` | `days` 日历天数、`days_with_archive` 存档天数、`days_with_data` 有任一有效白名单测量的天数；`missing_days` 与 `max_missing_streak` 按完整日历算，`date_range` 只有相对日期 |
| `metrics` | 按单日白名单的分组/字段组织；每项 `mean` / `median` / `min` / `max`、有效 `coverage_days` / 缺测 `missing_days` / 最长连续缺测 `max_missing_streak` / 越界剔除 `excluded_days` |
| 极值日 | 各项只有一个 `min_day` / `max_day`，均为相对日期；同值取最早有效日，缺测时为 null，不提供排名 |
| `trend` | 只有 `direction` / `first` / `last`，比较窗口内最早和最晚有效观测；不是两个日历端点，也不是趋势显著性。少于两条时方向为 null |
| `goals` | 步数与睡眠目标的 `goal`、有效/缺测天数、`achieved_days` 与 `achievement_rate`；分母为有效测量天数，缺测不当失败；无有效样本或无有效目标时达成率为 null |
| 目标极值 | 各目标仅一个 `best_day` / `worst_day` 及对应比例；比例封顶为 1，同值取最早日；它只表示既定目标完成度，不表示健康最好或最差 |

实测零值保留，全部缺失的均值、中位数和极值为 null。已有分析层 `BOUNDS` 的指标沿用同一过滤范围，
不另设一套阈值；越界读数计入缺测，并单列剔除天数。时长均值可核对常见时分写法，覆盖天数不会被当成分钟换算。
逐日数组只用于本机计算，不进入提示词、重试或出口留痕；窗口天数增长不会线性增加发送字段数。
用量以服务返回的 token 记录为准，不用字符数猜测成本。

迁移 v6 给 `ai_analyses` 增加 `start_date` / `end_date`，旧记录均回填原 `date`，内容和用量不改写。
缓存及 OpenCode 会话按起止日期、模型与服务区分，同一个结束日的不同窗口互不复用。
System Prompt 使用 v4；多日窗口另在用户输入增加字段口径说明，标为 `v4-window-1`。
新版本不会命中 v3 的缓存；旧记录继续保留，读取不改判，重新生成需用户手动触发。

## 实际发送什么

单日第一次请求是系统提示、窗口元信息和以下聚合对象。`date` 是相对当前民用日期的 `D-0` / `D-1` 等标签；
星期保留。不会发送绝对日期、profile、device、workout 名称、精确作息、睡眠阶段时间线、逐日时序、
逐分钟序列、热图、原始响应或健康数据的自由文本字段。

下面每行的路径加字段名构成出口契约；新字段默认不外发。独立期望表与值断言见
`tests/test_ai_payload.py`，唯一出口在 `health_by/ai/service.py::build_payload`。

| 对象路径 | 字段 |
|---|---|
| 根对象 | `date`, `weekday`, `rolling_window_days`, `window`, `day`, `goals`, `statistics` |
| `window` | `days`, `days_with_data`, `days_with_archive`, `missing_days`, `max_missing_streak`, `date_range` |
| `day` | `activity`, `heart`, `sleep`, `body`, `naps_count_today`, `naps_minutes_today`, `alerts` |
| `day.activity` | `steps_today`, `distance_km_today`, `calories_out_today`, `floors_today`, `total_active_minutes_today`, `light_active_minutes_today`, `moderate_active_minutes_today`, `vigorous_active_minutes_today`, `zone_minutes_today`, `sedentary_minutes_today` |
| `day.heart` | `resting_hr_today`, `resting_hr_7d`, `hrv_ms_today`, `hrv_ms_7d`, `spo2_today`, `spo2_7d`, `spo2_min_today`, `spo2_max_today`, `breathing_rate_today`, `breathing_rate_7d`, `vo2max_today`, `hr_min_today`, `hr_max_today`, `hr_avg_today`, `skin_temp_delta_c_today`, `skin_temp_delta_c_7d`, `skin_temp_nightly_c_today`, `skin_temp_baseline_c_today`, `skin_temp_stddev_30d_c_today`, `core_temp_c_today`, `non_rem_hr_today`, `hrv_deep_rmssd_ms_today`, `hrv_entropy_today` |
| `day.sleep` | `total_minutes_today`, `total_minutes_7d`, `efficiency_today`, `minutes_awake_today`, `minutes_to_fall_asleep_today`, `stages` |
| `day.sleep.stages` | `deep_today`, `light_today`, `rem_today`, `wake_today` |
| `day.body` | `weight_kg_today`, `body_fat_pct_today`, `water_ml_today`, `calories_in_today` |
| `day.alerts` | `irregular_rhythm_alerts_today` |
| `goals` | `steps`, `sleep_minutes` |
| `statistics` | `window_days`, `n_days`, `comparisons`, `period_deltas`, `goal_progress`, `bands_outside`, `recovery`, `load`, `rhythm`, `coverage`, `correlations` |
| `statistics.comparisons` | `steps`, `sleep_minutes`, `resting_hr`, `hrv_ms`, `breathing_rate`, `spo2`, `skin_temp_delta` |
| `statistics.comparisons.steps` | `current_today`, `baseline`, `difference_today`, `percent_change_today`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.sleep_minutes` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.resting_hr` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.hrv_ms` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.breathing_rate` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.spo2` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.comparisons.skin_temp_delta` | `current_today`, `current_7d`, `baseline`, `difference_today`, `difference_7d`, `percent_change_today`, `percent_change_7d`, `sample_count`, `window_days`, `min_samples` |
| `statistics.period_deltas` | `steps_7d`, `sleep_7d` |
| `statistics.goal_progress` | `steps`, `sleep_minutes` |
| `statistics.goal_progress.steps` | `ratio_today`, `remaining_today` |
| `statistics.goal_progress.sleep_minutes` | `ratio_today`, `remaining_today` |
| `statistics.bands_outside` | `steps`, `sleep_minutes`, `hrv_ms`, `resting_hr`, `active_minutes` |
| `statistics.bands_outside.steps` | `points`, `outside`, `percent` |
| `statistics.bands_outside.sleep_minutes` | `points`, `outside`, `percent` |
| `statistics.bands_outside.hrv_ms` | `points`, `outside`, `percent` |
| `statistics.bands_outside.resting_hr` | `points`, `outside`, `percent` |
| `statistics.bands_outside.active_minutes` | `points`, `outside`, `percent` |
| `statistics.recovery` | `need_minutes`, `hrv_delta_pct_today`, `rhr_delta_today`, `sleep_debt` |
| `statistics.recovery.sleep_debt` | `minutes`, `nights`, `window`, `avg_deficit` |
| `statistics.load` | `steps_avg_7`, `steps_avg_28`, `steps_ratio`, `active_avg_7`, `active_avg_28`, `active_ratio`, `who_weighted_minutes`, `who_weekly_minutes`, `who_pct`, `window_days` |
| `statistics.rhythm` | `sri`, `sri_pairs`, `midpoint_sd_min`, `bed_sd_min`, `wake_sd_min`, `social_jetlag_min`, `n_nights`, `rhythm_days`, `baseline_days` |
| `statistics.coverage` | `window_days`, `days_with_archive`, `partial_days`, `per_metric_days`, `excluded_out_of_range` |
| `statistics.coverage.per_metric_days` | `sleep_minutes`, `sleep_efficiency`, `resting_hr`, `hrv_ms`, `steps`, `active_minutes`, `sedentary_minutes` |
| `statistics.coverage.excluded_out_of_range` | `sleep_minutes`, `sleep_efficiency`, `resting_hr`, `hrv_ms`, `spo2`, `breathing_rate`, `weight_kg`, `body_fat_pct` |
| `statistics.correlations` | `sleep_minutes__resting_hr`, `sleep_minutes__hrv_ms`, `deep_minutes__resting_hr`, `sleep_efficiency__hrv_ms`, `sleep_mid__resting_hr`, `sleep_mid__hrv_ms`, `sleep_minutes__steps`, `steps__sleep_minutes`, `steps__deep_minutes`, `steps__resting_hr`, `active_minutes__sleep_minutes`, `sedentary_minutes__resting_hr` |
| `statistics.correlations.sleep_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_minutes__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.deep_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_efficiency__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_mid__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_mid__hrv_ms` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sleep_minutes__steps` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__sleep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__deep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.steps__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.active_minutes__sleep_minutes` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |
| `statistics.correlations.sedentary_minutes__resting_hr` | `x`, `y`, `offset_days`, `r`, `n`, `ci_low`, `ci_high` |

本表描述 `daily-v2`。相关性槽位保留固定 `x` / `y` 名称；只有通过校正的统计量才填入 `offset_days`、`r`、`n`、`ci_low`、`ci_high`，否则这些叶子为 null。`payload_schema_version` 记录在分析与出口审计中，不发送给模型。

`total_active_minutes_today` 使用现有权威函数按轻、中、高强度分量派生；缺轻活动时不退化为 MVPA。
`remaining_today` 为尚缺目标量，达标后为零。`null` 表示缺测，不能当零；越界统计缺项表示零次越界，
出口明确写 `0`。只发五指标的分项带外统计，不发四指标合并百分比。

### 请求头

除 `accept` 与 `authorization`（有 key 时才发）外，按目标地址决定：

```
host 为 opencode.ai 或其子域名 → 发 x-opencode-session 与 User-Agent: health-by/<version>
其它任何 host              → 只发 User-Agent
```

`x-opencode-session` 是 OpenCode 服务端的契约（缺少会返回 400 `MissingSessionID`），
对其它服务没有意义。**标识符只在需要它的地方出现**——这和"发给模型的内容要能逐项说清"
是同一条原则，只是这次说的是请求头。

会话标识的范围：**同一起止日期、模型与服务的多次分析及修复复用同一 UUID，重启后也能恢复。**
不同日期、模型或服务使用不同 UUID；这是 OpenCode 的缓存路由标识，会关联同组请求。
测试连接每次使用独立临时 UUID，不复用分析标识、不写分析留痕；非 OpenCode 服务不发送此头。

代价要写清楚：**同一天内的若干次请求在对方看来属于同一个会话，是可关联的**；不同日期之间不可关联。
如果哪天要把关联面压到更小，可以改成"每次分析一个"，代价是当天第二次生成不再命中对方的会话缓存。

> **已知限制**：判断依据是 host。如果把 OpenCode 挂在自有域名或反向代理后面，
> 这个头不会被发送，服务会返回 400。**错误提示需要能说明这一点**
> （「该地址可能需要会话头」），否则用户只会看到一个没有解释的 400。
>
> **不会自动补头重试**：收到 `400 MissingSessionID` 时只提示，不猜测性地再发一次。
> 因此一次分析最多两个请求（生成 + 失败修复一次），不存在"第三次请求"。

## 获取模型

先保存服务地址与密钥，再点「获取模型」。首次配置可保持 AI 关闭、模型名留空；
下拉显示服务返回的名称，缺名称时显示 id。选中后将 id 填入模型名，再保存才生效。
列表不会自动选第一个模型，也不会根据返回的元数据改 Base URL；手动填写始终可用。

只发送一次 `GET {base_url}/models`：有已保存的 key 时带认证与应用 User-Agent，
不带会话 UUID、不发送健康数据或提示词，不写分析结果、请求留痕或配置。
获取失败不阻止手填与保存：401 提示密钥无效、超时提示检查地址网络，
404 提示「该地址没有模型列表接口，请手动填写模型名」。不会猜测其它路径或自动重试。

当前经无凭据 GET 验证可用的 OpenCode 地址为 `https://opencode.ai/zen/go/v1`。
[官方 Go 文档](https://opencode.ai/v2/docs/console/go/#models)另列了独立的模型列表路径；
不同 Base URL 是否支持 `/models` 需分别判断，不能用列表 404 推断生成接口不可用。
列表成功也不证明模型额度、访问权限或生成质量；「测试连接」仍独立保留。

## 测试连接

保存配置后点「测试连接」，发一次最小调用（`max_tokens: 256`，只要求回复 OK），
使用与生成相同的已保存地址、模型和密钥，不带健康数据，也不带 `response_format`。
AI 解读关闭时仍可手动测试；修改表单后需先保存。测试成功只证明最小调用可用，
不保证正式生成所需的 JSON 模式、长输出或额度可用。结果显示为一行：

```
成功  连接正常 · 1.2s · deepseek-v4.1-flash
失败  <一句话原因> —— <下一步>
```

成功也显示，并带模型名与耗时：否则分不清"连上了"和"连上了但连的是别的模型"。

| 情况 | 提示 | 下一步 |
|---|---|---|
| 401 | 密钥无效或已过期 | 检查 key 是否复制完整（前后无空格）、是否属于这个服务 |
| 402 | 额度不足 | 这个 key 或模型没有可用额度；换 key 或补充额度 |
| 403 | 没有权限使用该模型 | 核对模型名或账号权限 |
| 404 | 接口地址不对 | 检查是否少填了 `/v1`，并核对模型名称 |
| 429 | 请求太频繁 | 稍后再试；本地模型可减少并发或换更小的模型 |
| 5xx | 对方服务出错 | 稍后重试；反复出现说明对方服务异常，不是配置问题 |
| 超时 | 连不上该地址 | 检查地址与网络；本地 Ollama 需先启动，稍后重试 |
| 域名解析失败 | 域名解析失败 | 检查地址拼写，或该域名是否需要代理 |
| 200 但内容不是 JSON | 返回内容不是 JSON | 确认地址指向 OpenAI Chat Completions 兼容服务 |
| 200 但缺少 `choices[0].message.content` | 返回结构不符合预期 | 确认用的是 OpenAI Chat Completions 兼容接口 |
| 内容为空 | 连接正常，但模型没有返回内容 | 不影响连接判定；生成分析时若仍为空，考虑换模型 |

### 上游返回的内容不原样展示

服务端出错时，很多厂商会在响应体里回显**请求内容或 API key**（"invalid key: sk-…"）。
所以：

```
✓ 显示：状态码 + 本项目的错误文案
✗ 不显示：上游响应正文原样
✓ 需要留档时：先脱敏再存——形如 sk-…、Bearer …、以及超长的字母数字串一律替换为 ***
```

**错误信息里出现 key，比不显示错误信息危害更大**：它会被截图、贴进 issue、写进日志。

**首选做法是整段丢弃上游正文**（只保留状态码）——没有正文就没有脱敏负担，三处天然干净。
确实需要留档排查时，再按上面的规则脱敏，并且**展示、日志、落库三处各做一次**：
只在界面上遮住、日志里照写，等于没遮，而日志往往比界面活得久。

**HTTP 200 一律算连接成功；空内容或响应不兼容另附 `warning`，不会报成连接失败。** 测试连接回答的是"地址通不通、认不认证"，
不是"模型答得好不好"——把两件事混在一起，会让一个完全可用的配置被判成失败
（`max_tokens` 太小时模型把预算花在别处，`content` 就是空字符串，而连接和认证都已通过）。

同一件事在别处含义不同：**生成分析时内容为空是失败**（见下一节），因为那里要的就是内容。
测试提示词为「请只回复 OK。」；空字符串、空白或 null 内容都不会把已通的连接判为失败。
HTTP 200 但正文不是 JSON 或缺少兼容响应结构时，也只提醒接口不兼容；连接成功不保证能生成有效分析。

```
测试连接   200 + 内容为空  → 成功（连接与认证都通过了）
生成分析   200 + 内容为空  → 失败（没有可用产出）
```

**这两处不要抽成同一个 `is_ok(response)`。** 它们看着一样、问的问题不一样；
两处共用请求发送逻辑，但连接判定与内容校验分别测试，不能将成功条件混用。
两个预算同理：测试连接要 256，是因为它只需要一个字；生成分析要完整输出，各有各的值。
测试连接用 `max_tokens: 256`（提示词只要求回复 OK），判据只有 HTTP 状态与是否连得上。

**这个值不能调小。** 实测同一端点、同一 key、同一句「请只回复 OK。」：

```
max_tokens=8    → 200 · finish=length · content 为空
max_tokens=32   → 200 · finish=length · content 为空
max_tokens=128  → 200 · finish=stop   · content "OK"
max_tokens=256  → 200 · finish=stop   · content "OK"
```

这类模型先把预算花在内部推理上，小预算时一个字都吐不出来。调小会更容易出现空内容；这时仍显示连接成功，并提醒用户正式生成时检查内容。

**下一步建议必须与失败类型匹配**：

```
401 / 402 / 403 / 404   配置问题 —— 重试一百次也不会好，不要建议重试
429 / 5xx / 超时          暂时性问题 —— 建议重试
超时 ≠ 域名解析失败       前者是连不上，后者是不知道往哪连（查网络 vs 改地址）
```

这些句子是**契约不是参考**：实现里应抽成一张表（状态码/异常 → 句子），代码只查表、不各写一份，
并让测试断言"给定状态码产出对应句子"。否则文档和界面会各说各话。

## 输出结束与截断

测试连接的上限仍为 256 tokens；正式分析及一次修复的上限为 2000 tokens。
结果保留 `finish_reason`（未提供时为未知），各次返回的原因也写入校验记录。
`length` 明确显示「输出被截断（本次上限 2000 tokens）」及更换简洁模型或提高预算的建议，
保留已收到的原文；不会把它仅说成 JSON 格式错误。第一次截断仍最多修复一次，
修复后正常结束则显示最终结果，两次截断则显示失败与原文。空内容也保留结束原因。
真正解析失败、且服务端没有报告截断时，仍按格式问题处理。
此预算是上限，不保证所有模型在每次请求中都能完整输出；真实连续生成由实例验收确认。

## 校验与一次修复

模型输出为 `phenomenon`、`causes`、`advice` 三字段 JSON。JSON 形状允许 null；rules v2 对缺失或空白原因段至少标为 `warn`。
字符串里重复编码的 JSON 对象只额外解析一层，不无限递归。
校验检查形状，以及每句是否**有可核对的出处**——具体数字，或输入里真实存在的键名
（「今天 hrv_ms 与 resting_hr 均为 null」这类句子依据的是键名，不是数值，算合格），
以及数字是否能匹配输入值。支持绝对值、相同精度四舍五入、
分钟的常见时分写法及比例的百分数写法。

每句有一个数字匹配、或引用真实字段时算有出处，额外未匹配数字仍记 `warn`。
没有出处的句子按整段占比分级：零句为 `ok`；有少数句、且占比不超过 30% 时为 `warn`，
仍显示全部内容并逐句标注。超过 30%，或完全没有命中依据时为 `failed`。
比例门槛优先：1/3 缺出处仍失败，1/4、2/7 和恰好 3/10 为提醒；格式错误或禁止套话仍直接失败。
`matched` 记录数字与字段出处合计，`matched_numbers` 单列数字命中，null 字段陈述不会因零数字被误杀。
字段引用只接受实际存在的叶子名、完整路径或真实后缀路径，按词边界匹配；
`hrv_msx`、`fake_hrv_ms`、`wrong.hrv_ms` 不会冒充 `hrv_ms`。
常用名称 HRV、静息心率、呼吸频率、血氧、睡眠时长、步数只在对应字段存在时作为同义出处。
会记录具体句子、数字匹配数量、引用字段与落空数字。
这项校验核对出处，不证明缺测陈述、自然语言推论或因果关系正确。

提示词 v4 要求现象最多三个事实、原因说明依据或具体缺失的数据、建议以动作开头，并使用中文指标名。
每句两三个数字及全文不超过 300 字是写作目标，不是新增校验门槛。
修复提示要求原因不可为 null；rules v2 对缺失原因段至少标为警告，历史判定不改写。
新结果的校验记录另存 `rules_version` 与 `validated_at`，分别表示校验判据版本和最终判定时间；
它们随结果保存、缓存与导出，旧记录不回填、不重算。校验判据版本独立于提示词版本。
新提示词只用于手动生成；旧版结果保留在数据库与导出中，不自动改写或重新调用模型。

失败时只自动修复一次：原始消息后追加上一轮完整模型输出和修复提示。
因此第二次请求包含**聚合指标 + 上一轮模型输出文本**。不会无限循环或在后台自动调用。
两次仍失败也会保存原回复，在上方明确标注「未通过校验」及具体问题；格式异常时先显示人话原因，
原始回复折叠在「查看原始输出」后，避免整段 JSON 铺在页面。重新生成由用户触发。
若修复请求发生网络错误，会保留第一次回复并说明修复未完成。

## 存储、用量与密钥

- `ai_analyses`：一次分析一行，保存模型、提示词版本、原回复、展示内容、校验状态、总用量与时间。
- `ai_egress`：一次实际请求一行，`attempt` 从 1 开始，最多为 2。保存结构化字段路径、排序路径的 SHA-256、
  payload 内容 SHA-256、类型/长度、完整消息体的哈希、时间与当次用量；不存 payload 内容。
  迁移 v5 增加 `session_id`（非 OpenCode 为 null）与 `user_agent`；历史记录保持 null，不补造标识。
  第二次另记 `assistant_echo` 的来源标记、字符数与哈希，不把它当数据字段，也不复制模型正文到留痕表。
- 页面显示两次请求的合计 token；服务没返回用量时明确表示未知，不冒充零。
- API key 经现有 `SecretStore` 加密，设置/API 只回传是否已保存；不会回传上游错误正文。
  key 留空保留旧值，删除使用单独按钮。也不将模型输出解释成 HTML。
- 导出包含 AI 结果和出口留痕；清空健康数据也会清空这两张表，保留设置及加密凭据。

## 接口

- `POST /api/v1/settings/ai`：`ai_enabled`、`ai_base_url`、`ai_model`、`api_key`（可选）；`clear_api_key=true` 单独删除 key。
- `POST /api/v1/ai/models`：按已保存配置获取列表，返回 `{ok, status, detail, models: [{id, name}]}`；
  不要求已启用 AI 或已选模型，不改设置。
- `POST /api/v1/ai/test`：按已保存配置发送最小测试，返回 `{ok, status, detail, model, latency_ms}`；
  `status` 为实际 HTTP 状态码，网络异常为 `timeout` / `dns_error` / `connection_error` 等字符串。
  内容为空时仍 `ok=true`，另有 `warning` 提醒，页面保留成功状态并显示提醒。
  不生成健康解读、不写分析或分析出口表。
- `POST /api/v1/ai/analyze`：`{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`；兼容旧 `date` 单日参数，不能混填。`force=true` 重新生成。
- `GET /api/v1/ai/analysis?start=YYYY-MM-DD&end=YYYY-MM-DD`：只读窗口缓存与非密配置，兼容旧 `date` 参数。
  结果包含 `start_date`、`end_date`；旧 `date` 字段仍为结束日期。

协议参考：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。


## 单日双口径输入与缓存版本

单日协议为 daily-v2，提示词输入说明版本为 v4-input-2；区间协议仍为 window-v1，
提示词仍为 v4-window-1。单日 day 中的测量叶子名带 *_today；
睡眠总时长、静息心率、HRV、呼吸率、血氧与皮温差同时提供 *_7d。
它们复用页面后端的日历 7 日计算与舍入，行为指标不计算 7 日值。
rolling_window_days=7 明示窗口；当日指所选日，分析历史日期时不改取服务器今天。
statistics.comparisons 分别给出 current_today/current_7d 及对应后缀的差额、百分比；
步数只给 today。所有差额都相对同一条基线，不把当日差额贴到均值上。

daily-v2 的键集合恒定，缺测填 null，不省略；合法零值保留。
相关性使用固定的 12 个允许配对槽位，只有通过校正的统计值才非 null。
空槽只是没有可引用的外发结论，不可据此断言没算、缺测、未显著或无关。
本地 insights 与图表的完整信息不受出口槽位影响。

迁移 v7 为 ai_analyses 与每次 ai_egress 增加 payload_schema_version：
旧单日标为 daily-v1，旧区间标为 window-v1，原回复、判定、哈希与时间不改写。
新记录同时保留人可读的版本名、机可读的 payload_keys_json 和摘要。
字段清单证明结构，内容哈希证明内容是否相同，两者都不能单独复原当时的数值。

缓存同时匹配起止日期、模型、服务地址、prompt_version 与 payload_schema_version。
旧单日记录不会命中新协议缓存，但仍保留在数据库与导出中；GET 不调用模型，
只有用户点击生成才创建新协议结果。区间缓存不因单日协议升级而失效。
两次实际请求各自记录相同输入协议及 payload 摘要，修复仍只允许一次。

模型应明确说“当日”“7 日均值”或“近 7 天平均”，避免含糊窗口。
这项语义要求采用人工抽查，不增加正则门槛，也不重判历史内容。
