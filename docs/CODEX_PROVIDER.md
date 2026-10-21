# 使用 Codex 进行分析与封面生成

AutoClip 可以调用已安装的官方 Codex CLI，使用其 ChatGPT 登录状态进行文本分析、视频抽帧图片分析，以及通过内置 GPT Image 生成封面。分析与封面分别选择和保存配置，均使用该账号的 Codex 额度；语音转写（ASR）仍需独立配置。

## 准备 CLI 和登录

本接入限用户自己的本机 CLI 工作流。Codex CLI 必须安装在 **AutoClip 后端实际运行的本机环境**中，并由运行后端的系统用户完成登录；只在浏览器所在环境安装和登录并不足够。不共享登录，不将此接入用作公共代理或转售服务；这些使用边界不构成绝对法律合规保证。

官方 [app-server 的 Auth endpoints 说明](https://learn.chatgpt.com/docs/app-server#auth-endpoints)表示，已有本地或开源应用可以继续使用 Codex app-server authentication，并推荐迁移到 Sign in with ChatGPT，以获得更好的用量可见性与控制；商业或托管服务从来不允许使用 app-server authentication。这是官方针对 app-server 认证的声明。本实现调用用户自行在本地已登录的官方 CLI，属于个人自动化工作流，并未接入 app-server 认证，也不代表获得官方特殊批准；不能据此为公共托管、商业代理或转售背书。

在对应环境的终端中运行：

```sh
codex --version
codex login
codex login status
```

按 CLI 提示使用 ChatGPT 账号登录。AutoClip 无需填写 API Key，不提取或复制 OAuth token，不读取 `auth.json`，不调用私有 HTTP 接口；登录凭据由 Codex CLI 管理。

CLI 需支持 `exec --ignore-user-config --ignore-rules --ephemeral` 和 `debug models`，封面还需支持内置 `image_generation` 工具及下述功能检测。旧版本缺少命令或参数时，请先更新 CLI。

如果后端找不到 CLI，可在启动后端前设置环境变量 `AUTOCLIP_CODEX_PATH`，值为 Codex 可执行文件的完整路径，然后重启 AutoClip。不要在该变量中附加命令参数。

## 配置分析

1. 打开「设置」中的 AI 模型设置，将分析服务的提供商选为 **Codex**。
2. 刷新模型列表，选择当前账号可用的模型。
3. 测试连接，成功后保存设置。
4. 需要画面分析时，选择支持图片输入的多模态模型，并按界面提示授权发送抽帧图片。

文本模型可用于字幕等文本分析；图片输入能力以刷新后的模型信息为准。分析与视觉推理请求显式禁用生图工具；封面生成使用单独的调用路径，ASR 继续使用其独立提供商。

## 配置封面

1. 完成上述 ChatGPT 登录，打开「设置 → 封面 → AI生成 → Codex → 内置GPT Image」。模型选择项的实际界面名称为 **GPT Image（Codex 内置）**。
2. 刷新模型列表并选择该项，保存封面设置。此项的虚拟模型 ID 为 `codex-image`，表示 CLI 内置工具；实际生图型号由 CLI 管理，不保证固定型号。
3. 需要以视频抽帧作为参考时，启用现有的帧发送授权（`allow_send_frame`）；只有该授权启用时才发送参考帧。
4. 在封面流程中生成并检查结果。模型项可见或设置保存成功，都不代表账号已具备生图权限或本次生成成功。

可在同一终端检查 CLI 功能：

```sh
codex features list
```

只有输出包含 `image_generation stable true` 时，模型列表才展示内置生图工具；每次启动生图也会重新检测。该输出只表示 CLI 功能已启用，不保证账号权限或剩余额度。官方说明内置 GPT Image 生图计入 Codex 额度，具体可用性仍以账号和实际调用结果为准，见 [官方图片生成说明](https://learn.chatgpt.com/docs/image-generation)。

封面由 `backend/core/codex_images.py` 通过官方 `codex exec` 和内置 `image_generation` 工具生成，单次生图调用最多等待 **300 秒**，取消会终止进程树。失败不会切换到 API 服务；现有封面流程可降级为本地截图或排版。原有标题 OCR 校验不符时仍最多重试一次，因此整个封面流程并非“无重试”，也不应把单次调用上限当作整个流程的总耗时上限。

`backend/core/codex_images.py` 从 `codex exec --json` 的 `thread.started.thread_id` 取得线程 ID，验证其符合 UUID 格式后，只读取 `CODEX_HOME/generated_images/<thread_id>` 下唯一的新图片，不采用模型回复文本中的路径。`CODEX_HOME` 默认是 `~/.codex`。候选图片须满足修改时间 `mtime >= 调用开始时间`，文件后缀和实际格式均属于 PNG/JPEG/WEBP，文件大小非零且不超过 32 MiB，尺寸的像素总数不超过 40,000,000，解析后的真实路径位于 `generated_images` 根目录内，且图片可验证。尺寸检查只限制像素总数，不保证严格符合请求的生成宽高。应用将有效图片另存为封面；CLI 原始生成图片保留，不删除用户文件。

该目录布局仅在官方 CLI **0.163.0** 的本地环境中验证过，并非稳定的官方 API 承诺。未来 CLI 的目录布局或事件变化，以及缺图、多图或无效图，均须明确报错，不能信任模型文本路径来绕过检查，也不会转向私有 HTTP 或 API 服务；现有本地截图/排版降级保留。

## 排查

- 找不到 CLI：检查后端运行环境、启动用户及 `AUTOCLIP_CODEX_PATH`，安装或调整后重启 AutoClip。
- 未登录或登录不可用：在同一环境重新运行 `codex login`，确认使用 ChatGPT 登录，再刷新模型并测试。
- 模型列表读取失败：检查登录状态、网络和 CLI 版本；确认支持 `codex debug models`。
- 封面列表没有 GPT Image：检查 `codex features list` 是否包含 `image_generation stable true`；更新 CLI 后刷新列表。分析模型可用不代表内置生图工具可用。
- 工具可见但生图失败：检查 ChatGPT 登录、网络、账号生图权限和 Codex 额度；功能检测不能代替真实调用。检查封面结果是否已降级为本地截图或排版，不能把降级结果认作 GPT Image 生图成功。
- 推理超时或失败：检查网络、账号额度和所选模型权限，再重试或更换模型；保存设置不代表模型推理已经成功。

## 当前验证范围

本机工具实际执行的本地源码集成样本：**Windows 本机、官方 Codex CLI 0.163.0**，在独立测试数据目录中完成 FFmpeg 合成 1 秒视频 → 保存 Codex 分析/封面配置 → 真实抽帧（`allow_send_frame=true`）→ 官方 `codex exec` 内置 `image_generation` → Codex `gpt-6.1-sol` 标题 OCR → 保存 JPEG。结果为 `method=model`、`warning=null`、**1146×717**、**44373 字节**；实现者已人工目视确认标题 **SUNRISE** 正确。`.venv/codex-cover-e2e.json` 记录真实链路，`.venv/codex-cover-final.jpg` 为本地图像。

本地实际运行的验证结果：Codex 相关 backend **45 passed**；`.venv/quality-codex-image-frontend.json` 记录 frontend quality gate **passed**，lint、types、tests、build **全部 passed**（**325 tests**）；`.venv/codex-cover-regressions.log`（UTF-16 日志）记录 cover + AI settings 回归 **120 passed、2 failed**，两项失败均为 Windows 上的 POSIX `0600` 权限断言，该回归未全部通过。此前全量结果 **2244 passed、25 failed、2 errors、8 skipped** 的失败与错误仍未解决。

上述证据文件位于 Git 已忽略的 `.venv` 目录，仅本地保留，不纳入版本控制；文档仅记录相对路径与脱敏摘要，不含个人绝对路径。真实链路证据仅证明本地源码集成封面生成与 JPEG 保存，不代表完整产品验收、全部源码检查、实际遥测收数或 Windows x64/macOS arm64 最终安装包验收通过。

验收要求见 [测试与发布验收工作流](TESTING_ACCEPTANCE.md)，遥测约束见 [LLM 遥测契约](analytics/LLM_TELEMETRY.md)。`codex exec` 的脚本调用方式见官方 [非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。
