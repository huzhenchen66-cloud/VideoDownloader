<div align="center">

![VideoDownloader Banner](./docs/assets/banner.png)

# VideoDownloader

**可解释的四级多引擎媒体解析框架 · 沉浸式 Win11 Fluent 桌面客户端**

*An explainable, multi-engine video resolution framework & aesthetic Windows desktop application.*

[![Release](https://img.shields.io/badge/release-v3.0.0-38bdf8?style=flat-square)](https://github.com/YOUR_USERNAME/videodownloader/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-6366f1?style=flat-square)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-0ea5e9?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-10b981?style=flat-square)](#快速开始)
[![Desktop UI](https://img.shields.io/badge/UI-PyQt6%20Fluent%20Widgets-ec4899?style=flat-square)](#界面展示)
[![MCP Ready](https://img.shields.io/badge/MCP-JSON--RPC%202.0-8b5cf6?style=flat-square)](#mcp-agent-接入)

</div>

---

## 💡 为什么选择 VideoDownloader？

市面上的下载器大多是**不可解释的黑盒**：成功了皆大欢喜，失败了只有一句冰冷的“下载失败”，开发者改不动，用户不敢长期用。

**VideoDownloader** 将其重构为**白盒化的四级降级链路**，配合零打扰剪贴板预热与二次元极光美学，打造稳定、优雅、可扩展的现代桌面工具：

1. **四级多引擎可观测链路**：移动端直连 ➔ 无头 Chrome CDP 嗅探 ➔ `yt-dlp` (EJS + Chrome TLS 指纹) ➔ 通用 HTML5/OG 校验。每一步耗时与状态全程可视化（`ResolutionTrace`）。
2. **结构化错误分类体系**：抛弃几十行字符串正则匹配，原生输出 `auth_required`、`risk_control`、`geo_blocked` 等标准错误码，一键直达 Cookie 导入。
3. **零打扰桌面体验**：复制链接静默预热元数据（0ms 命中），全局热键 (`Ctrl+Shift+D`) 唤出 Raycast 级浮层，输入框回车秒下，托盘动态下载进度环。
4. **二次元美感多主题**：内置初音未来极光青、樱花粉、星空霓虹紫、元气金、琉璃蓝等二次元主题色系，支持深浅双色与 Win11 云母效果。
5. **AI 精读与原生 MCP Server**：字幕时间轴提取、Temporal-RAG 章节精读总结，支持作为标准 MCP stdio 服务器接入 Cursor / Claude Desktop。

---

## 🎬 界面展示

<div align="center">

![Workflow Demo](./docs/assets/demo.gif)

<p align="center"><em>工作流演示：复制链接 ➔ 全局热键 0ms 秒捕 ➔ 回车下载 ➔ 展开四级解析轨迹</em></p>

| 🌸 浅色模式（初音未来极光青） | 💜 深色模式（星空霓虹紫） |
| :---: | :---: |
| ![Light Mode](./docs/assets/screenshot-light.png) | ![Dark Mode](./docs/assets/screenshot-dark.png) |

| ⚡ Raycast 式 0ms Quick Capture 浮层 |
| :---: |
| ![Quick Capture](./docs/assets/screenshot-quick-capture.png) |

</div>

---

## 🏗️ 四级引擎降级链路

```text
用户输入 / 剪贴板链接
  │
  ├─ Stage 1: cn-native-sniffer    [移动端直连]   抖音 / 小红书去水印直链、M3U8 直链
  │     └─ 触发风控 / 失败 ──┐
  │                           ▼
  ├─ Stage 2: browser-cdp-sniffer [无头浏览器]   快手、西瓜视频；调用 Chrome/Edge CDP 捕获真实音视频流
  │     └─ 未匹配媒体流 ─────┐
  │                           ▼
  ├─ Stage 3: yt-dlp              [全球提取器]   YouTube (4K/8K)、B 站等 1800+ 站点，带 EJS 与 TLS 伪装
  │     └─ 不受支持 ─────────┐
  │                           ▼
  └─ Stage 4: web-media-sniffer   [通用 HTML5]   兜底抓取 <video src> / OpenGraph，防伪媒体头校验
```

支持通过 CLI 实时诊断任意链接：
```powershell
VideoDownloader.exe --explain "https://www.bilibili.com/video/BV1Js411v7Y3/"
```

---

## 🚀 快速开始

### 方式一：Windows 正式安装包（推荐 · 免管理员 UAC）
1. 前往 [Releases](https://github.com/YOUR_USERNAME/videodownloader/releases) 下载 `VideoDownloader-Setup-3.0.0-x64.exe`；
2. 双击安装（自动装入 `%LOCALAPPDATA%\Programs\VideoDownloader`，冷启动 < 1.5s）；
3. 绿色便携用户可下载 `VideoDownloader-3.0.0-Windows-portable.zip` 解压即用。

### 方式二：源码运行与多平台开发 (Windows / macOS / Linux)

```bash
# 1. 克隆并安装
git clone https://github.com/YOUR_USERNAME/videodownloader.git
cd videodownloader
pip install -e ".[dev]"

# 2. 启动原生 Fluent 桌面客户端
python -m videodownloader.launcher

# 3. 运行全套 35 项离线测试
pytest -v
```

---

## 🤖 MCP Agent 接入 (Cursor / Claude Desktop)

在 `claude_desktop_config.json` 或 `.cursor/mcp.json` 中配置：

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

---

## 📚 详细文档矩阵

- 📖 [平台支持矩阵与画质限制 (docs/platforms.md)](./docs/platforms.md)
- ⚠️ [结构化错误码与排障指南 (docs/errors.md)](./docs/errors.md)
- 🛠️ [如何编写一个新解析引擎 (docs/writing-an-engine.md)](./docs/writing-an-engine.md)
- ⚙️ [CLI 命令行、局域网 iPhone 互联与高级配置 (docs/advanced.md)](./docs/advanced.md)
- 🏛️ **架构决策记录 (ADR)**：
  - [ADR-001: 四级多引擎责任链设计](./docs/adr/001-multi-engine-chain.md)
  - [ADR-002: 秒开 onedir 与 Inno Setup 打包权衡](./docs/adr/002-onedir-inno-setup.md)
  - [ADR-003: PyQt6 原生桌面与可选 Web 服务协同架构](./docs/adr/003-dual-ui-tradeoff.md)
- 📝 [版本更新日志 (CHANGELOG.md)](./CHANGELOG.md)

---

## 📜 开源协议与免责声明

本项目基于 [MIT License](./LICENSE) 开源。仅供计算机网络与多媒体工程学术研究，请遵守各平台服务条款并尊重创作者版权。
致敬优秀开源先驱：[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)、[FastAPI](https://github.com/fastapi/fastapi)。
