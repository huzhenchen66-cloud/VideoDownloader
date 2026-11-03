# 高级使用与开发指南 (Advanced Guide)

本文档介绍 VideoDownloader 的命令行工具、局域网移动互联、AI 精读服务与 MCP Server 配置。

---

## 1. 命令行参数 (CLI)

VideoDownloader 提供了丰富而实用的 CLI 支持：

| 参数 | 描述 |
| --- | --- |
| `--explain <URL>` | 探查指定视频链接在四级引擎链上的真实执行轨迹，打印详细树形耗时与错误码，退出码反映成功状态 |
| `--minimized` | 启动时静默最小化至系统托盘，由 Windows 开机自启使用 |
| `--version`, `-V` | 打印当前软件版本号（如 `VideoDownloader 3.0.0`） |
| `--web` | 仅以纯网页服务模式运行（适合无桌面的 Linux 服务器、Docker 或 NAS 部署） |
| `--host <IP>` | Web 模式监听地址，默认 `0.0.0.0` |
| `--port <PORT>` | Web 模式监听端口，默认 `8765`（冲突时自动寻找可用端口） |
| `--mcp` | 启动标准 Model Context Protocol (MCP) stdio 服务器，供 AI 智能体协同调度 |

### 示例：使用 `--explain` 诊断链接

```powershell
VideoDownloader.exe --explain "https://www.bilibili.com/video/BV1Js411v7Y3/"
```

输出：
```text
VideoDownloader v3.0.0 · 四级多引擎解析轨迹诊断
目标地址 : https://www.bilibili.com/video/BV1Js411v7Y3/
识别平台 : 哔哩哔哩 Bilibili (bilibili)

├─ Stage 1  cn-native-sniffer    跳过  平台 bilibili 不在移动端直连注册表
├─ Stage 2  browser-cdp-sniffer  跳过  平台 bilibili 无需无头浏览器嗅探
├─ Stage 3  yt-dlp               成功  1.42s  解析到 14 个清晰度规格并完成 H.264+AAC 混流
└─ Stage 4  web-media-sniffer    未执行  前序阶段已命中

解析结果 : 成功 (1420ms)
视频标题 : 【4K60FPS】2026 经典动画巡礼 · 超高清视听盛宴
作者频道 : AnimeMaster
可选规格 : 1080P 高清 / 720P 极速 / 480P 省流 / 4K 原画
```

---

## 2. 局域网服务与 iPhone 手机互联

VideoDownloader 内置了基于 FastAPI 的微型局域网流媒体服务器：

1. 打开桌面端主界面，点击左侧导航栏的 **「多端互联」**；
2. 点击 **「启动局域网服务」**；
3. 界面将自动扫描本机所有网卡的内网 IP，并生成高清二维码；
4. 使用 iPhone / iPad 相机直接扫码即可在 Safari 中打开网页版播放墙：
   - 支持 **HTTP 206 Partial Content Range** 流式秒开播放；
   - 支持多屏对比播放；
   - 可以在手机网页端直接提交新的视频链接，桌面端将静默完成高速下载。

---

## 3. AI 精读与字幕时间轴分析

在左侧导航栏点击 **「AI 精读」**：
- 支持从本地或已下载视频中自动抽取内嵌字幕（SRT / VTT / ASS）；
- 支持接入主流大模型（DeepSeek、通义千问 Qwen、硅基流动 SiliconFlow、本地 Ollama 或 OpenAI 兼容端点）；
- 提供 **Temporal-RAG** 算法，自动划分视频章节并提取高光关键句，在对话框中点击时间戳即可直接跳转定位播放。

---

## 4. MCP Agent 配置 (Cursor / Claude Desktop)

VideoDownloader 原生集成了 **Model Context Protocol (MCP)** 标准，允许 AI 编码工具或桌面智能体直接控制视频下载和状态查询。

### 接入 Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "videodownloader": {
      "command": "C:/Users/YourName/AppData/Local/Programs/VideoDownloader/VideoDownloader.exe",
      "args": ["--mcp"]
    }
  }
}
```

### 接入 Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "videodownloader": {
      "command": "python",
      "args": ["-m", "videodownloader.launcher", "--mcp"]
    }
  }
}
```

提供给 AI 的标准工具包含：
- `parse_video_url(url)`: 探查视频元数据、时长与清晰度选项
- `download_video(url, quality)`: 发起视频下载并入队
- `list_tasks(status)`: 查询下载进度、状态与错误码
- `explain_resolution_chain(url)`: 获取完整的四级引擎降级解析树
