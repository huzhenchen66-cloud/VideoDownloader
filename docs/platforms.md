# 平台支持矩阵 (Platforms)

> [!NOTE]
> 视频站点的风控和接口会随时变化。本表如实记录**引擎路由**和**是否需要 Cookie**；
> “验证状态”一列只写真正测试过的结果。如果你发现某个站点失效，请提交
> [站点支持请求](../.github/ISSUE_TEMPLATE/site_request.yml) 并附上 `--explain` 的输出。

## 主要平台

| 平台 | 首选引擎 | 兜底引擎 | 最高画质 | 需要 Cookie | 验证状态 |
| --- | --- | --- | --- | --- | --- |
| YouTube（含 Shorts） | Stage 3 `yt-dlp` | — | 4K / 8K（需选 `best_4k`） | 年龄限制、会员视频需要 | 已验证（yt-dlp 2026.10.26 + EJS） |
| 哔哩哔哩 | Stage 3 `yt-dlp` | — | 未登录 480P/720P；登录 1080P；大会员 4K | 1080P 以上需要 | 已验证 |
| 抖音 | Stage 1 移动端直连 | Stage 2 无头浏览器 | 原画无水印 | 通常不需要 | 依赖风控状态 |
| 小红书 | Stage 1 移动端直连 | Stage 2 无头浏览器 | 原画无水印 | 部分笔记需要 | 依赖风控状态 |
| 快手 | Stage 2 无头浏览器 | Stage 3 `yt-dlp` | 原画 | 不需要 | 需本机安装 Chrome / Edge |
| 西瓜视频 | Stage 2 无头浏览器 | Stage 3 `yt-dlp` | 1080P | 不需要 | 需本机安装 Chrome / Edge |
| TikTok | Stage 3 `yt-dlp`（`curl_cffi` Chrome 指纹） | — | 1080P | 不需要 | 需要代理 |
| X / Twitter | Stage 3 `yt-dlp` | — | 原画 | 敏感内容需要 | 需要代理 |
| Instagram | Stage 3 `yt-dlp` | — | 原画 | 大多数情况需要 | 需要代理 |
| 微博 | Stage 3 `yt-dlp` | Stage 4 通用嗅探 | 1080P | 部分需要 | 未系统验证 |
| Vimeo / Twitch / Reddit / Dailymotion / Facebook | Stage 3 `yt-dlp` | — | 视站点而定 | 视内容而定 | 未系统验证 |
| AcFun / 知乎 / 优酷 / 腾讯视频 / 爱奇艺 | Stage 3 `yt-dlp` | Stage 4 通用嗅探 | 视站点而定 | 付费内容不支持 | 未系统验证 |
| MP4 / M3U8 直链 | Stage 1 直连 | — | 原始文件 | 不需要 | 已验证 |
| 其他 1800+ 站点 | Stage 3 `yt-dlp` | Stage 4 通用 HTML5 嗅探 | 视站点而定 | 视站点而定 | 取决于 yt-dlp 上游 |

## 明确不支持

| 情况 | 原因 | 错误码 |
| --- | --- | --- |
| Netflix、Disney+、爱奇艺/腾讯/优酷的 VIP 付费剧集等 DRM 内容 | Widevine / PlayReady 加密，本项目不做也不会做解密 | `unsupported` |
| 需要付费购买的课程 / 内容 | 版权保护 | `auth_required` |

## 代理与分流

在「设置 → 代理策略」中选择**智能分流**时：

- YouTube、TikTok、X、Instagram、Facebook、Vimeo、Twitch 走系统代理 / 自定义代理；
- B 站、抖音、快手、小红书等国内平台**强制直连**，避免经境外节点绕路导致 CDN 变慢或失败。

## 自行验证

```powershell
VideoDownloader.exe --explain "<视频链接>"
```

会打印该链接经过四级引擎的完整轨迹、每一级的耗时与失败原因。参见 [errors.md](./errors.md)。
