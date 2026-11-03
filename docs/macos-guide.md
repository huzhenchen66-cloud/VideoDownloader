# VideoDownloader macOS 运行与打包指南

本项目参考了 `Manna` 等优秀开源项目的跨平台工程实践，原生支持 **macOS（Apple Silicon M1/M2/M3/M4 与 Intel x86_64）**。

---

## 一、一键打包 macOS `.app` 与 `.dmg`

在任意 macOS 设备上：

1. 克隆或下载本项目源码：
   ```bash
   git clone https://github.com/videodownloader/videodownloader.git
   cd videodownloader
   ```
2. 在访达（Finder）中直接**双击运行**：
   ```bash
   scripts/build_mac.command
   ```
   或者在终端中运行：
   ```bash
   chmod +x scripts/build_mac.command
   ./scripts/build_mac.command
   ```
3. 构建完成后，产物将生成在 `dist/` 目录：
   - **`dist/VideoDownloader-3.0.0-AppleSilicon.dmg`**（或 `Intel.dmg`）：经典 Mac 拖拽安装镜像（左侧应用图标，右侧 Applications 文件夹）；
   - **`dist/mac/VideoDownloader.app`**：免安装绿色应用包。

---

## 二、macOS 初次打开常见问题（Gatekeeper）

macOS 对未购买每年 \$99 苹果开发者证书签名的开源软件会默认弹出“无法打开，因为无法验证开发者”或“已损坏”提示。这是 macOS 的安全机制，通过以下任意一种方式即可正常打开：

### 方式 1：访达右键打开（推荐）
在 `访达` -> `应用程序` 中，**按住 Control 键并点击（或右键）** `VideoDownloader.app`，选择 **「打开」**，在弹出的提示框中点击 **「仍要打开」**，之后即可像常规应用一样直接双击启动。

### 方式 2：终端一键解除隔离属性
打开「终端 (Terminal)」，执行以下命令即可彻底解除拦截：
```bash
sudo xattr -cr /Applications/VideoDownloader.app
```

---

## 三、原生 macOS 体验特性
- **暗色模式自动跟随**：完美支持 macOS System Appearance（浅色 / 深色即时切换）；
- **视网膜高分屏支持**：开启 `NSHighResolutionCapable`，全套 UI 矢量图标与字体在 Retina 屏下细腻锐利；
- **纯 Python 本地和弦合成**：内置声音合成器使用标准库音频管道，在 Mac 上无需安装任何额外音频驱动。
