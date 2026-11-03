# 编写一个新引擎 (Writing an Engine)

VideoDownloader 的核心是一条**可解释的四级引擎链**
([`MultiEngineChain`](../videodownloader/engines/base.py))。每个引擎只需要回答三个问题：

1. **我能不能处理这条链接？** → `can_handle()`
2. **不能的话，为什么？** → `skip_reason()`（会显示在解析轨迹里）
3. **能的话，怎么解析 / 下载？** → `parse()` / `download()`

链路编排、计时、错误分类、重试策略、`ResolutionTrace` 记录全部由框架完成，引擎本身不需要关心。

## 当前的四级链路

| Stage | 引擎 `name` | 实现 | 负责 |
| :---: | --- | --- | --- |
| 1 | `cn-native-sniffer` | [`NativeCNAndSnifferEngine`](../videodownloader/engines/cn_short_engine.py) | 抖音 / 小红书移动端分享页直连、M3U8/MP4 直链 |
| 2 | `browser-cdp-sniffer` | [`BrowserCDPSnifferEngine`](../videodownloader/engines/cdp_sniffer_engine.py) | 调用本机 Chrome/Edge 无头模式，经 CDP 监听真实媒体请求（快手、西瓜；抖音/小红书风控时兜底） |
| 3 | `yt-dlp` | [`YtdlpEngine`](../videodownloader/engines/ytdlp_engine.py) | YouTube、B 站等 1800+ 站点 |
| 4 | `web-media-sniffer` | [`FallbackWebSnifferEngine`](../videodownloader/core/task_queue.py) | 通用 HTML5 `<video>` / OpenGraph 兜底，校验文件头避免把网页存成 `.mp4` |

顺序就是优先级。前一级成功，后续阶段记为 `not_run`（“前序阶段已命中”）。

## 最小示例：约 50 行接入一个站点

下面为一个虚构站点 `example-tube.com` 写一个引擎，它提供 `/api/video/<id>` JSON 接口。

```python
# videodownloader/engines/example_tube_engine.py
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Callable

import requests

from .base import (
    BaseVideoEngine, DownloadCancelledError, DownloadOptions, DownloadResult,
    FormatChoice, ParsedMedia, ProgressCallback, ProgressEvent,
)

_ID_RE = re.compile(r"example-tube\.com/v/(\w+)")


class ExampleTubeEngine(BaseVideoEngine):
    name = "example-tube"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return bool(_ID_RE.search(url))

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        return "" if self.can_handle(url, platform_id) else "非 example-tube.com 链接"

    def _info(self, url: str) -> dict:
        vid = _ID_RE.search(url).group(1)
        resp = requests.get(f"https://example-tube.com/api/video/{vid}", timeout=10)
        resp.raise_for_status()          # 403/429 会被自动归类为 risk_control
        return resp.json()

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        info = self._info(url)
        return ParsedMedia(
            url=url, title=info["title"], platform_id=platform_id,
            uploader=info.get("author", ""), duration=float(info.get("duration", 0)),
            direct_video_url=info["mp4"],
            formats=[FormatChoice(format_id="mp4", label=info.get("quality", "原画"))],
        )

    def download(self, url: str, platform_id: str, options: DownloadOptions,
                 stop_event: threading.Event, stop_reason_getter: Callable[[], str],
                 on_progress: ProgressCallback | None = None) -> DownloadResult:
        media = self.parse(url, platform_id, options)
        out = Path(options.save_dir) / f"{media.title}.mp4"
        with requests.get(media.direct_video_url, stream=True, timeout=15) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(out, "wb") as fp:
                for chunk in r.iter_content(512 * 1024):
                    if stop_event.is_set():                     # 支持暂停 / 取消
                        raise DownloadCancelledError(stop_reason_getter() or "cancel")
                    fp.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(ProgressEvent("downloading", done * 100 / total))
        return DownloadResult(file_path=str(out), title=media.title, engine_used=self.name)
```

### 注册到引擎链

在 [`TaskQueueManager.__init__`](../videodownloader/core/task_queue.py) 中把它插到合适的位置：

```python
self.engine_chain = MultiEngineChain(
    [
        NativeCNAndSnifferEngine(),
        ExampleTubeEngine(),        # 想优先于 yt-dlp 就放在它前面
        BrowserCDPSnifferEngine(),
        YtdlpEngine(),
        FallbackWebSnifferEngine(),
    ]
)
```

如果希望 Quick Capture 和任务卡片显示站点名，在
[`PLATFORM_RULES`](../videodownloader/core/url_cleaner.py) 里加一行
`("example_tube", "Example Tube", ("example-tube.com",))`。

## 引擎作者需要遵守的约定

| 约定 | 原因 |
| --- | --- |
| **直接抛异常，不要自己吞掉** | 框架会用 [`classify_exception()`](../videodownloader/engines/base.py) 归类为 `ErrorCode`，决定是否重试、是否继续下一级（见 [errors.md](./errors.md)） |
| 异常消息尽量包含原始特征（HTTP 状态码、站点原文） | 分类靠关键字匹配，信息越原始越准确 |
| 循环中检查 `stop_event`，命中时抛 `DownloadCancelledError` | 暂停/取消依赖它；否则任务无法被中断 |
| 启动浏览器用 `BROWSER_SEM`，调用 FFmpeg 用 `FFMPEG_SEM` | 防止并发任务同时拉起多个 Chrome / FFmpeg 把内存打满 |
| 不要在日志里拼接 Cookie / Token | 日志会经过脱敏过滤器，但最好的脱敏是不写进去 |
| `skip_reason()` 写成人话 | 它会原样出现在用户看到的解析轨迹里 |

## 用离线测试验证

不需要联网。仿照 [`tests/test_errors_and_trace.py`](../tests/test_errors_and_trace.py)，用假引擎驱动整条链：

```python
from videodownloader.engines.base import MultiEngineChain, DownloadOptions

def test_example_tube_is_stage_one(tmp_path, monkeypatch):
    eng = ExampleTubeEngine()
    monkeypatch.setattr(eng, "_info", lambda url: {"title": "Demo", "mp4": "https://cdn/x.mp4"})
    chain = MultiEngineChain([eng])
    media = chain.parse("https://example-tube.com/v/abc", "example_tube", DownloadOptions(save_dir=str(tmp_path)))
    assert media.engine_name == "example-tube"
    assert media.trace["stages"][0]["status"] == "success"
```

然后用真实链接看轨迹：

```powershell
python -m videodownloader.launcher --explain "https://example-tube.com/v/abc"
```
