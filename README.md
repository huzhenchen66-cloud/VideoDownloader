<div align="center">

![VideoDownloader Banner](./docs/assets/banner.png)

# VideoDownloader

**全球多引擎可解释流媒体解析框架 · 影视级 AI 分镜工作台 · 沉浸式二次元 Fluent 桌面客户端**

*An explainable, multi-engine video resolution framework & aesthetic anime-themed desktop application with AI storyboarding.*

[![Release](https://img.shields.io/badge/release-v3.0.0-38bdf8?style=flat-square)](https://github.com/huzhenchen66-cloud/VideoDownloader/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-6366f1?style=flat-square)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-0ea5e9?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-10b981?style=flat-square)](#-快速开始)
[![Desktop UI](https://img.shields.io/badge/UI-PyQt6%20Fluent%20Widgets-ec4899?style=flat-square)](#-界面与二次元感官展示)
[![Tests Passing](https://img.shields.io/badge/tests-57%20passed-success?style=flat-square)](#-全量自动化测试)

</div>

---

## 💡 为什么选择 VideoDownloader？

市面上的下载器大多是**不可解释的黑盒**：成功了皆大欢喜，失败了只有一句冰冷的“下载失败”，开发者无法排障，用户更无法长期信任。

**VideoDownloader** 历经多次技术架构升级，从传统工具彻底跃升为**白盒化可解释的多引擎框架 + 影视级 AI 视频理解工作台 + 高颜值桌面客户端**：

1. **四级多引擎可观测降级链**：原生短视频逆向直连 ➔ 无头 Chromium CDP 嗅探 ➔ `yt-dlp` 全球核心 (带 EJS 求解器与 Chrome TLS 浏览器伪装) ➔ 通用 HTML5/OpenGraph 保底校验。每一步耗时与状态全程可视化输出（`ResolutionTrace`）。
2. **8 线程 Range 并发极速下载**：HTTP 分块并行拉取大文件，搭配 EMA 指数移动平均测速，彻底突破单连接限速瓶颈。
3. **AI 影视级分镜工作台**：
   - **6 轨自适应字幕管线**：完美解决短视频无 CC 字幕痛点，独家支持 B站官方 AI 字幕秒级直采与本地 `.srt` 伴随字幕落盘；
   - **导演级分镜脚本拆解**：输出包含景别（特写/近景/中景/全景）、运镜（推/拉/摇/移/固定）、构图细节、台词旁白与 BGM 节奏的专业分镜表；
   - **纯净文案提炼与金句归纳**：智能过滤口癖语气词，自动提炼传播金句；
   - **全模型服务商生态**：支持阿里云百炼 (DeepSeek / 通义千问)、Anthropic Claude、OpenAI、本地免 Key 离线 Ollama 及任意第三方兼容中转。
4. **二次元感官美学与零版权风险**：
   - 5 套程序化矢量柔光背景（初音绿、樱花粉、星空紫、元气橙、琉璃蓝），基于 `QPainter` 动态呼吸渐变，完全不内置商业受限立绘，零 DMCA 版权风险；
   - 44.1kHz 正弦波程序化自合成专属下载提示音效；
   - `Ctrl+Shift+K` 命令面板与成就战报海报生成。
5. **极简优雅的系统体检**：重构设置中心，彻底消除生硬的开发者代码与英文缩写，温和纯净中文徽标一目了然。

---

## 🎬 界面与二次元感官展示

<div align="center">

![Workflow Demo](./docs/assets/demo.gif)

<p align="center"><em>工作流演示：复制链接 ➔ 全局热键 0ms 快速捕获 ➔ 回车秒下 ➔ 可视化四级解析轨迹</em></p>

| 🌸 浅色模式（初音未来极光青） | 💜 深色模式（星空霓虹紫） |
| :---: | :---: |
| ![Light Mode](./docs/assets/screenshot-light.png) | ![Dark Mode](./docs/assets/screenshot-dark.png) |

| ⚡ Raycast 式 0ms Quick Capture 浮层 | 📊 个人成就与战报海报 (Ctrl+K) |
| :---: | :---: |
| ![Quick Capture](./docs/assets/screenshot-quick-capture.png) | ![Stats](./docs/assets/screenshot-stats.png) |

</div>

---

## 🏗️ 四级引擎降级链路架构

```text
用户输入 / 剪贴板链接
  │
  ├─ Stage 1: cn-native-sniffer    [原生短视频]   抖音 / 小红书 / 快手去水印直链、B站 SPI buvid3 自举
  │     └─ 触发风控 / 失败 ──┐
  │                           ▼
  ├─ Stage 2: browser-cdp-sniffer [无头浏览器]   快手、网页流媒体；无头 Chrome/Edge CDP 抓包捕获真实媒体流
  │     └─ 未捕获媒体流 ─────┐
  │                           ▼
  ├─ Stage 3: yt-dlp              [全球百大核心] YouTube (4K/8K)、B站等 1800+ 站点，注入 curl_cffi 浏览器指纹
  │     └─ 不受支持 ─────────┐
  │                           ▼
  └─ Stage 4: web-media-sniffer   [通用 HTML5]   兜底抓取 <video src> / OpenGraph 标签与媒体头校验
```

支持通过 CLI 命令行实时诊断任意音视频链接：
```powershell
VideoDownloader.exe --explain "https://www.bilibili.com/video/BV1nYx86pEeA"
```

---

## 🚀 快速开始

### 方式一：Windows 正式安装包（最推荐 · 免管理员 UAC）
1. 前往 [Releases](https://github.com/huzhenchen66-cloud/VideoDownloader/releases) 下载 `VideoDownloader-Setup-3.0.0-x64.exe`；
2. 双击安装（自动装入 `%LOCALAPPDATA%\Programs\VideoDownloader`，冷启动 < 1.5s）；
3. 绿色便携用户可直接下载 `VideoDownloader-3.0.0-Windows-portable.zip`，解压即用。

### 方式二：源码运行与多平台开发 (Windows / macOS / Linux)

```bash
# 1. 克隆代码仓库
git clone https://github.com/huzhenchen66-cloud/VideoDownloader.git
cd VideoDownloader

# 2. 安装项目依赖
pip install -e ".[dev]"

# 3. 启动 Fluent 桌面客户端
python run_desktop.py

# 4. 可选：启动局域网 Web / iPhone 互联模式
python run_desktop.py --web
```

---

## 🧪 全量自动化测试

本项目拥有严密的工业级自动化测试体系，覆盖错误状态码、引擎逐级降级、AI 六轨提取、程序化音效与 SQLite Schema 迁移：

```powershell
pytest tests/ -v
# ======================== 57 passed in 4.21s ========================
```

---

## 👥 贡献者与致谢 (Contributors & Credits)

本项目由以下开发者与智能体共同协作完成：

- **[@huzhenchen66-cloud](https://github.com/huzhenchen66-cloud)** - 项目发起人与核心维护者 (Creator & Lead Maintainer)
- **[@jiangqingheng0819](https://github.com/jiangqingheng0819)** (`jiangqingheng0819@gmail.com`) - 核心贡献者 (Core Contributor)
- **Antigravity** (Google DeepMind) - 智能结对编程与系统工程落地 (Agentic Pair Programmer)
- **DeepSeek** - 大模型推理架构设计与二次元视觉/音频安全感官建议 (Architecture & Sensory Guidance)

致敬优秀的开源先驱项目：
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 强大的流媒体解析基石
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) - 优雅现代的 Win11 Fluent Design 控件库
- [FastAPI](https://github.com/fastapi/fastapi) - 高性能异步 Web 核心

---

## 📜 开源协议

本项目基于 [MIT License](./LICENSE) 开源协议发布。仅供计算机网络与多媒体工程学术研究，请遵守各平台服务条款并尊重创作者版权。
