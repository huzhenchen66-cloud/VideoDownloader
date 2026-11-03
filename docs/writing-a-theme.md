# 制作一个二次元自定义主题包 (Writing a Theme)

VideoDownloader 支持导入与分享 `.vdtheme` 格式的二次元个性化主题包。

一个 `.vdtheme` 文件本质上是一个普通的 `.zip` 压缩文件（修改后缀为 `.vdtheme`），包含配色方案、可选自定义壁纸和专属完成提示音。

---

## 1. 主题包目录结构

```text
my-custom-theme.vdtheme (ZIP)
├── theme.json         [必须] 主题元数据与配色定义
├── backdrop.png       [可选] 自定义全幅背景壁纸（建议 1920x1080 以上）
└── complete.wav       [可选] 专属三音完成提示音（44100Hz 16-bit PCM WAV）
```

---

## 2. 编写 `theme.json`

```json
{
  "id": "cyber_neko",
  "name": "赛博机甲猫 (Cyber Neko)",
  "badge": "赛博粉",
  "version": "1.0.0",
  "author": "YourName",
  "accent": "#FF2E93",
  "accent_light": "#FF70BA",
  "accent_bg_light": "#FFF0F7",
  "accent_bg_dark": "#360A1F",
  "gradient": [
    "#FF2E93",
    "#00F0FF"
  ]
}
```

### 字段说明：
- `id`: 唯一英数字符串（如 `cyber_neko`）；
- `name`: 显示在界面的主题中文名称；
- `badge`: 右上角风格小微标（如 `赛博粉`、`极光青`）；
- `accent`: 浅色模式下的核心交互强调色；
- `accent_light`: 深色模式下的微光强调色；
- `accent_bg_light` / `accent_bg_dark`: 选框底色与微透明遮罩底色；
- `gradient`: 渐变双色数组 `[色值1, 色值2]`，用于卡片缩略图、流光条及战报渲染。

---

## 3. 导入与使用

在 VideoDownloader 桌面客户端中：
1. 点击左下角 **「设置」** 页面；
2. 在「外观与主题」卡片中，点击 **「导入 .vdtheme 主题包」**；
3. 选择您制作好的 `.vdtheme` 文件，软件将自动校验并提取到 `%LOCALAPPDATA%\VideoDownloader\custom_themes\`；
4. 界面将立即刷新为您定制的专属二次元主题！
