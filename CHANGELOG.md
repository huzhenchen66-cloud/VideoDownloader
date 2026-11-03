# 更新日志 (Changelog)

所有关键架构变更均记录于此文件。本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范。

---

## [3.0.0] - 2026-11-01

### 🚀 重大架构重构 (Major Architecture Upgrade)
- **四级多引擎可观测解析链**：
  - 新增 `MultiEngineChain` 责任链编排器，支持 Stage 1 (国内移动直连) ➔ Stage 2 (无头 Chrome CDP) ➔ Stage 3 (`yt-dlp` + EJS + TLS 伪装) ➔ Stage 4 (通用媒体头校验嗅探)。
  - 新增 `ResolutionTrace` 结构化轨迹记录与 `--explain <url>` 命令行诊断工具，解析流程完全白盒化。
- **结构化错误分类体系 (Taxonomy)**：
  - 彻底淘汰旧版本几十行繁杂的中文错误字符串模糊匹配，重构为强类型 `ErrorCode` 枚举 (`auth_required`, `risk_control`, `geo_blocked`, `network_timeout`, `unsupported`, `disk_full` 等)。
  - `auth_required` 状态支持 UI 一键跳转唤出 Cookie 设置并提供浏览器自动读取；`disk_full` 异常立即中止整条链路防止空转。
- **并发与资源信号量管理**：
  - 引入资源级全局信号量：`_browser_sem=1`（防止多开无头 Chrome 导致内存暴涨）、`_ytdlp_sem=2`、`_ffmpeg_sem=1`。
- **SQLite 自动迁移与状态自愈 (PRAGMA user_version = 3)**：
  - 新增 `prefetch_cache`、`clipboard_suppressed` 表与 `error_code`、`trace_json` 字段。
  - 启动时自动将异常中断的任务标为 `interrupted` 状态，并支持一键恢复。
  - 历史记录清理时执行 `VACUUM`，同时完好保留用户已下载至本地的媒体文件。

### 🎨 桌面端必要件与二次元主题系统 (Desktop Essentials & Anime Themes)
- **二次元美感个性化多主题**：
  - 内置五套二次元主题色系：初音未来（极光青 · Miku Teal）、樱花粉（少女心 · Sakura Pink）、星空幻境（霓虹紫 · Neon Violet）、元气暖阳（暖金 · Amber Gold）、琉璃晴空（冰蓝 · Azure Sky）。
  - 支持深色（夜间模式）与浅色（清爽呼吸感）动态切换，与 Win11 云母（Mica）背景完美融合。
- **纯净品牌与零打扰交互**：
  - 软件标题栏与主界面统一为纯净的 `VideoDownloader`，版本号 `v3.0.0` 仅作为右下角低调微标展示。
  - 彻底淘汰所有界面 Emoji 图标，全面改用纯矢量 Fluent 图标库。
  - 主界面输入框支持「输入/粘贴链接后按回车直接秒下」。
- **Raycast 级 0ms Quick Capture 浮层与剪贴板哨兵**：
  - 实现三级剪贴板哨兵（L0 静默预热、L1 右下角无焦点滑入条、L2 Raycast 式极速浮层）。
  - 7 项防打扰规则（全屏游戏免打扰、24h 抑制、域名黑名单等），杜绝侵入式弹窗骚扰。
  - 复制视频链接后台静默完成预探测与封面缓存，按 `Ctrl+Shift+D` 呼出浮层实现 **0 等待秒捕**。
- **系统托盘与单实例守卫**：
  - 基于 `QLocalServer` 实现单实例锁，二次双击自动前置唤醒已有窗口。
  - 系统托盘图标支持 `QPainter` 动态绘制环形下载进度圈，并在下载完成时通知气泡。

### 📦 打包与交付
- **秒开 onedir + Inno Setup 正式安装包**：
  - 抛弃单文件 `onefile`（冷启动需 8~15 秒解压至 Temp），改用 `onedir`（`upx=False`），冷启动缩短至 < 1.5 秒。
  - 编译生成免管理员权限的正式安装包 `VideoDownloader-Setup-3.0.0-x64.exe`，安装至 `%LOCALAPPDATA%\Programs\VideoDownloader`。
  - 生成 `VideoDownloader-3.0.0-Windows-portable.zip` 绿色便携包，以及严格防篡改的 `SHA256SUMS.txt` 校验文件。

---

## [2.0.0] - 2026-10-30
- 引入 PyQt6 原生桌面界面与内置播放器；
- 引入 FastAPI 局域网服务与 iPhone 扫码播放墙；
- 初步引入 AI 字幕时间轴提取与 MCP Server 协议支持。

---

## [1.0.0] - 2026-10-25
- 初始多平台视频下载核心与 CLI 工具。
