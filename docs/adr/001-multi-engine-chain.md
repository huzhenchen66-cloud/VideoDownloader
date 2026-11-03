# ADR-001: 四级多引擎责任链与结构化错误体系设计

## 状态
已接受 (Accepted) - 2026-11-01

## 背景
早期版本的视频下载工具往往依赖单一内核（如仅使用 `yt-dlp` 或纯正则解析脚本）。当遇到以下情况时通常会面临严重困境：
1. 国内短视频平台（如抖音、小红书）对桌面版 UA 与 IP 进行频繁的反爬与滑动验证码风控，`yt-dlp` 极易报 HTTP 403 / 429 失败；
2. 西瓜视频与快手等站点需要前端 JS 逆向解密签名，常规 HTTP 请求无法直接获得无水印音视频直链；
3. 旧版代码在 `engines/base.py` 中将所有引擎的失败信息压缩为 `" | ".join(errors)`，并抛出通用的 `RuntimeError`，销毁了**哪一级失败、属于何种类别、能否自动重试**等关键元数据；
4. UI 侧只能靠长达 60 行的中文字符串正则模糊匹配（如 `humanize_download_error`）来猜测原因，每增加一个新站点的风控提示就需要往里面加 `if`，形成不可维护的死循环。

## 决策
1. **构建四级职责链 (`MultiEngineChain`)**：
   - **Stage 1 (`cn-native-sniffer`)**：移动端直连，利用手机端 UA 与原生 API 获取去水印直链；
   - **Stage 2 (`browser-cdp-sniffer`)**：基于 Chrome DevTools Protocol (CDP) 的无头浏览器嗅探，复用本机 Chrome/Edge 环境捕获真实媒体请求；
   - **Stage 3 (`yt-dlp`)**：搭载 `yt-dlp-ejs`（解算 YouTube JS 难题）与 `curl_cffi`（模拟 Chrome TLS 握手特征），处理全球 1800+ 站点；
   - **Stage 4 (`web-media-sniffer`)**：通用 HTML5 `<video src>` 与 OpenGraph 兜底嗅探，并带有严格的媒体文件头有效性校验。
2. **引入结构化错误分类体系 (`ErrorCode` / `EngineFailure` / `ResolutionError`)**：
   - 统一定义强类型枚举：`auth_required`、`risk_control`、`geo_blocked`、`network_timeout`、`unsupported`、`format_invalid`、`disk_full` 等；
   - 每一级引擎尝试均产出 `EngineFailure(stage, engine, code, retryable, elapsed_ms, raw, hint)`；
   - 框架通过权重优先级算法选出对用户最具有操作指导价值的主错误码（如优先暴露需要 Cookie 登录的 `auth_required`，而不是通用的嗅探失败）。
3. **暴露四级解析轨迹 (`ResolutionTrace`)**：
   - 无论在 GUI 任务卡片还是通过 `--explain <url>` 命令行工具，均向开发者与用户提供透明展开的 Stage 1~4 跳过/失败/成功时间轴。

## 后果
- **优势**：
  - 彻底解决了错误不可知与黑盒运行的问题，用户能在遇到登录墙时获得「打开 Cookie 设置」按钮，遇到风控时框架自动退避，遇到磁盘满时立即停止空转；
  - 架构从“单一功能工具”转变为“可解释、可测试、可由社区轻松扩展（约 50 行即可新增引擎）的多媒体解析框架”。
- **代价**：
  - 需要为每一级引擎设计健壮的 `can_handle()` 与 `skip_reason()` 判断逻辑。
