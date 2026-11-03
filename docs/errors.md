# 错误码参考 (Error Codes)

VideoDownloader 不会把四级引擎的失败压成一句“下载失败”。每一次引擎尝试都会被归类为一个结构化的
[`ErrorCode`](../videodownloader/engines/base.py)，并写入 `EngineFailure` 与 `ResolutionTrace`。
任务卡片、`--explain` 命令行和诊断包看到的都是同一套错误码。

## 错误码一览

| 错误码 | 含义 | 自动重试 | 程序的行为 | 你可以做什么 |
| --- | --- | :---: | --- | --- |
| `network_timeout` | 连接超时、被重置、DNS 解析失败、代理不可达 | 是 | 指数退避后重试 | 境外平台：确认代理已开启，并在「设置 → 代理策略」中启用智能分流 |
| `risk_control` | HTTP 403/412/429、验证码页、“请求过于频繁” | 是 | 退避降速后重试；国内短视频会自动升级到 Stage 2 无头浏览器嗅探 | 稍等几分钟；频繁触发时在设置里导入 Cookie |
| `auth_required` | 需要登录、会员、年龄验证、私密视频 | **否** | 立即停止，不再空转后续引擎 | 点击卡片上的「打开 Cookie 设置」，选择浏览器 Cookie 或导入 `cookies.txt` |
| `geo_blocked` | 地区版权限制 | **否** | 立即停止 | 切换到对应地区的代理节点 |
| `unsupported` | DRM 加密（Widevine/PlayReady）或没有任何引擎能处理该链接 | **否** | 立即停止 | DRM 内容无法下载；其他情况请提交 [站点支持请求](../.github/ISSUE_TEMPLATE/site_request.yml) |
| `format_invalid` | 请求的清晰度/格式不存在 | **否** | 停止该次尝试 | 在 Quick Capture 中按 `Tab` 换一个清晰度档位 |
| `disk_full` | 目标磁盘空间不足（`ENOSPC` / errno 28） | **否** | **整条引擎链立即中止**，不再尝试其他引擎 | 清理磁盘或在设置里更换保存目录 |
| `cancelled` | 用户暂停或取消 | 否 | 保留进度，可随时恢复 | — |
| `unknown` | 未能归类的异常 | 是 | 有限次重试 | 导出诊断包并提交 [Bug 报告](../.github/ISSUE_TEMPLATE/bug_report.yml) |

## 主错误码是怎么选出来的

一条链接可能经过多个引擎、得到多个失败。`ResolutionError` 会按“对用户最有用”的优先级挑出**主错误码**：

```
disk_full > auth_required > geo_blocked > network_timeout > risk_control > unsupported > format_invalid > unknown
```

例如 Stage 3 (`yt-dlp`) 报 `auth_required`、Stage 4 (通用嗅探) 报 `unsupported`，界面显示的是
`auth_required` 并给出“打开 Cookie 设置”按钮，而不是一句没有帮助的“不支持”。

## 在命令行中查看

```powershell
VideoDownloader.exe --explain "https://www.bilibili.com/video/BV1Js411v7Y3/"
```

```text
├─ Stage 1  cn-native-sniffer    跳过  平台 bilibili 不在移动端直连注册表
├─ Stage 2  browser-cdp-sniffer  跳过  平台 bilibili 无需无头浏览器嗅探
├─ Stage 3  yt-dlp               失败  1.42s  [auth_required]  该视频需要大会员登录后观看
└─ Stage 4  web-media-sniffer    跳过  平台 bilibili 禁止通用 HTML/OG 兜底嗅探（防止误抓播放器框架）

解析结果 : 失败 [auth_required] (可自动重试: 否)
诊断建议 : 该视频需要账号登录或会员权限：请点击「设置 Cookie」选择浏览器或导入 cookies.txt 后重试
```

## 新增一条分类规则

分类逻辑集中在 [`classify_exception()`](../videodownloader/engines/base.py)。新增规则时：

1. 在对应分支的关键字元组里加入新的错误特征（小写匹配）。
2. 在 [`tests/test_errors_and_trace.py`](../tests/test_errors_and_trace.py) 的
   `test_classify_exception_taxonomy` 参数表中加一行用例。
3. 运行 `pytest tests/test_errors_and_trace.py -v`。
