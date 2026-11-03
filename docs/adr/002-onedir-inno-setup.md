# ADR-002: 秒开 onedir 结合 Inno Setup 6 制作标准 Windows 安装包

## 状态
已接受 (Accepted) - 2026-11-01

## 背景
许多 Python 开源桌面工具为了追求所谓的“单一文件”，习惯使用 PyInstaller `--onefile` 打包。在本项目实测中暴露了致命问题：
1. **冷启动极其缓慢**：单文件体积超过 100MB，每次双击运行，PyInstaller 必须将整个 Python 运行时、数十个 DLL 以及 Qt 静态资源解压到 `%TEMP%\_MEIxxxxx` 临时目录，冷启动耗时高达 8 ~ 15 秒，严重损害用户体验；
2. **杀毒软件误报严重**：向系统 Temp 目录释放执行文件和动态库是恶意软件的典型行为，频繁触发 Windows Defender 和各类杀软的误拦截；
3. **缺少桌面安装体验**：没有开始菜单项、没有桌面快捷方式、无法设置开机自启、且卸载时容易误删用户已下载的视频文件。

## 决策
1. **重构 PyInstaller 为目录打包模式 (`--onedir`, `upx=False`)**：
   - 保留完整的 `_internal` 依赖树与可执行主程序，无需运行时解压，在 Windows 机器上实现 **< 1.5 秒冷启动秒开**；
   - 禁用 UPX 压缩，避免杀毒软件因解壳异常而发生误杀，同时提升 Windows 系统内存共享与页面换入效率。
2. **使用 Inno Setup 6 编译标准安装程序 (`Setup.exe`)**：
   - 编写 `installer/videodownloader.iss` 脚本；
   - 设定 `PrivilegesRequired=lowest`，无需管理员 UAC 权限即可免提权安装至当前用户的 `%LOCALAPPDATA%\Programs\VideoDownloader`；
   - 自动生成桌面快捷方式与开始菜单项，并提供开机自启（带 `--minimized` 托盘驻留参数）选项；
   - 编写 Pascal 卸载保护逻辑：卸载时即使清理软件本体和运行缓存，也默认完好保留用户保存在本地磁盘的音视频文件。
3. **同步分发便携版与 SHA-256 清单**：
   - 排除开发调试生成的本地运行时文件（`.db`, `logs/` 等），自动封装 `VideoDownloader-<ver>-Windows-portable.zip`（内含 `portable.flag`）；
   - 自动计算并输出 `SHA256SUMS.txt` 校验清单，供自更新模块与用户进行防篡改校验。

## 后果
- **优势**：
  - 启动速度提升近 10 倍，真正具备了商业级桌面客户端的品质感；
  - 彻底摆脱了 Temp 释放所带来的杀软误报问题；
  - 用户安装卸载流程规范友好，不污染系统环境。
- **代价**：
  - 构建阶段需要安装并调用 Inno Setup 编译器 (`ISCC.exe`)。
