# 1.5.8 出片成功率埋点（线 A）

机读目录：[`event_catalog.json`](event_catalog.json)。未登记事件或缺必填字段，校验测试失败。

事件、查询和验收一起改。字段与枚举以第 0 步契约为准：[`docs/SUCCESS_RATE_CONTRACT.md`](../../docs/SUCCESS_RATE_CONTRACT.md)、`backend/core/upstream_errors.py`、`backend/core/llm_telemetry.py`、`frontend/src/analytics/successRateContract.ts`。

本线只发送事件、把 run 级字段写入 generation / production，并把允许字段并进 `safeStudioProperties`。不上报 key、提示词、字幕、模型原文或完整 URL；`base_url` 只留域名，本机地址记为 `local`。

## 事件

| 事件 | 谁发 | 何时 |
| --- | --- | --- |
| `llm_request_finished` | 后端 `core/llm_emit.py`（经 `llm_telemetry` 导出） | 每个 Provider.`call` 与 studio `vision_call` 的成功和失败 |
| `studio_generation_finished` | 前端观察 + 后端写入 run 字段 | 自动出片终态 |
| `studio_production_finished` | 前端观察 + 后端写入 run 字段 | 确认制作终态 |
| `asr_readiness_checked` | 前端导入探针/提交；后端 inspect 入口 | 生成前转写是否就绪 |
| `provider_validation_finished` | 后端挂钩（线 B 调用） | 设置页接入校验结束 |

`distinct_id` 与界面 PostHog 匿名 ID 相同（`PUT /settings/analytics-identity` → `analytics.json`）。Sentry 只设 `user.id`，不设 IP / email。

## 字段

`success_rate_schema_version = 1`。

### `llm_request_finished`

| 字段 | 规则 |
| --- | --- |
| `provider` | 闭集：dashscope / openai / codex / gemini / deepseek / seed / kimi / glm / grok / infistar / api88 / compatible / ollama / lmstudio / other |
| `model` | `[\w.\-:/]{1,64}`，不合规则省略 |
| `base_url_host` | 只域名；环回/内网/`.local` → `local` |
| `kind` | `text` / `vision` |
| `stage` | 管线阶段或 `other`（契约 `FAILURE_STAGES`） |
| `http_status` | 100–599 的整数，可空 |
| `upstream_error_type` | 契约闭集：`invalid_key` / `insufficient_quota` / `permission_denied` / `model_not_found` / `bad_endpoint` / `rate_limited` / `upstream_unavailable` / `timeout` / `connection` / `output_truncated` / `unknown` |
| `prompt_tokens` / `completion_tokens` / `reasoning_tokens` | 非负计数 |
| `retry_count` | 非负；SDK 内部重试记不到 |
| `duration_ms` | 非负耗时 |
| `outcome` | `success` / `error` |
| `finish_reason` | `stop` / `length` / `content_filter` / `unknown` |
| `failure_stage` | 失败必填，∈ `FAILURE_STAGES`（契约请求白名单之外的线 A 扩展，便于和 run 级对齐） |

### 生成 / 出片 run 级

挂在现有 `studio_generation_finished` / `studio_production_finished` 上，不改事件名：

| 字段 | 规则 |
| --- | --- |
| `provider` / `model` / `base_url_host` | 与上表相同；按本次 run 实际调用 |
| `http_status` | 失败时 400–599 |
| `upstream_error_type` | 上表闭集；站点拒绝（`source_blocked`）不标 |
| `failure_stage` | 失败必填 |
| `error_code` | 失败必填，∈ 现有 studio 错误码（缺失时后端写成 `unexpected`） |
| `billable_calls_before_failure` | 失败时必有；成功路径不发 |
| `retry_count` | 最后一次请求的重试次数 |
| `llm_calls` / `prompt_tokens` / `completion_tokens` | 本次 run 累计，可空 |

### `asr_readiness_checked`

| 字段 | 规则 |
| --- | --- |
| `provider` | `whisper_local` / `sensevoice_local` / `cloud` |
| `code` | readiness 闭集（`whisper_not_installed` 等） |
| `has_subtitle` | 布尔 |
| `blocked` | 转写未就绪且没有字幕 |

### `provider_validation_finished`（预留）

| 字段 | 规则 |
| --- | --- |
| `provider` | 与请求事件相同 |
| `base_url_host` | 只域名 |
| `result` | `success` / `failed` / `skipped` |
| `upstream_error_type` | 与请求事件相同闭集 |

## 查询

见 `docs/analytics/release_validation.sql` 末尾「1.5.8 成功率埋点」。失败事件缺 `error_code` 或 `failure_stage` 视为收数缺陷，不是失败原因。

## Codex 接入验收

Codex 的文本与抽帧图片请求使用 `provider=codex`，分别记录 `kind=text` / `vision`，沿用 `success_rate_schema_version=1` 及既有字段白名单。run 级归因和查询按实际调用保留 `codex`，不能归入 `openai` 或 `other`；ASR 提供商枚举不变。CLI 没有可报告的 API 地址或 HTTP 状态时省略相关字段，不将可执行文件路径作为 `base_url_host`。

Codex 内置 GPT Image 封面生成与上述文本/视觉分析请求不同，不能将生图调用冒充 `kind=text` / `vision`，也不能将本地截图/排版降级算作 GPT Image 生成成功。`codex-image` 是工具选择用的虚拟 ID，不能据此推断实际生图型号。本说明不新增事件、字段或 `kind` 枚举，也不宣称已有生图专用收数；既有分析事件或出片终态不能单独证明封面生图、保存和交付成功。

在隔离 validation 环境覆盖成功、未登录/登录不可用、超时和图片能力不匹配，核查事件与终态字段符合机读目录，错误类型仍使用既有闭集。核查 PostHog/Sentry 载荷不含 CLI 原始 stdout/stderr、模型回复、提示词、字幕、抽帧内容、临时文件路径或登录凭据；关闭采集时不发送。这里只定义验收标准，不表示已完成实际收数验收。

封面验收同样核查不上传参考帧、生成图片、个人文件路径或登录凭据到 PostHog/Sentry；向生图服务发送参考帧仅受现有 `allow_send_frame` 授权控制，与统计开关分别验收。CLI 0.163.0 的一次真实 PNG 生成不能代替应用集成、隐私检查或实际收数验收。
