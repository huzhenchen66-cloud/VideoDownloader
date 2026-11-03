"""Universal OpenAI-compatible LLM Video Copilot (DeepSeek / Qwen / SiliconFlow / Ollama / OpenAI)
with Temporal RAG Video Q&A and zero-key Extractive Timeline Fallback.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from typing import Any

import requests

from .transcript import format_mmss

LLM_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "dashscope": {
        "name": "阿里云百炼 (DeepSeek / 通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "deepseek-v4.1-flash-1017",
        "models": [
            "deepseek-v4.1-flash-1017",
            "deepseek-v4-pro-0813",
            "deepseek-v4-pro",
            "deepseek-r1",
            "deepseek-v3",
            "qwen-plus",
            "qwen-turbo",
            "qwen-max",
            "qwen-long",
        ],
    },
    "claude": {
        "name": "Anthropic Claude (官方 / 兼容中转)",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-3-7-sonnet-20250219",
        "models": [
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
    },
    "deepseek": {
        "name": "DeepSeek 官方开放平台",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "qwen": {
        "name": "通义千问 Qwen 独立端点",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"],
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow (含免费模型)",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2.5-7B-Instruct",
            "FunAudioLLM/SenseVoiceSmall",
        ],
    },
    "openai": {
        "name": "OpenAI 官方及标准兼容端点",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini", "gpt-4-turbo"],
    },
    "ollama": {
        "name": "本地 Ollama (免 API Key 离线)",
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5:7b",
        "models": ["qwen2.5:7b", "deepseek-r1:7b", "llama3.1:8b"],
    },
    "custom": {
        "name": "自定义第三方中转 / OneAPI / 网关",
        "base_url": "https://api.openai.com/v1",
        "default_model": "custom-model",
        "models": [
            "custom-model",
            "deepseek-v4.1-flash-1017",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "gpt-4o",
        ],
    },
}

TIME_ANCHOR_RE = re.compile(r"\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?")
WORD_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}")


def _parse_time_str_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.strip("[] ").split(":") if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0


def _condense_segments_for_prompt(segments: list[dict[str, Any]], max_chars: int = 14000) -> str:
    """Group fine-grained 2s subtitle lines into ~20s timestamped paragraphs to maximize LLM context quality."""
    if not segments:
        return ""

    blocks: list[str] = []
    bucket_start: float | None = None
    bucket_texts: list[str] = []

    for seg in segments:
        st = float(seg.get("start") or 0)
        txt = str(seg.get("text") or "").strip()
        if not txt:
            continue
        if bucket_start is None:
            bucket_start = st
        bucket_texts.append(txt)
        if (st - bucket_start) >= 22.0 or sum(len(x) for x in bucket_texts) >= 180:
            blocks.append(f"[{format_mmss(bucket_start)}] {' '.join(bucket_texts)}")
            bucket_start = None
            bucket_texts = []

    if bucket_start is not None and bucket_texts:
        blocks.append(f"[{format_mmss(bucket_start)}] {' '.join(bucket_texts)}")

    joined = "\n".join(blocks)
    if len(joined) <= max_chars:
        return joined

    # Uniformly sample blocks across the entire timeline so beginning, middle, and ending are preserved
    step = max(1, math.ceil(len(blocks) / max(10, int(max_chars / 180))))
    sampled = [blocks[i] for i in range(0, len(blocks), step)]
    return "\n".join(sampled)[:max_chars]


def _extractive_offline_summary(title: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Zero-API-key extractive TF-score timeline summarizer so users can test AI timeline UI immediately."""
    tokens_all: list[str] = []
    for s in segments:
        tokens_all.extend(w.lower() for w in WORD_TOKEN_RE.findall(s.get("text", "")))
    freq = Counter(tokens_all)

    scored: list[tuple[float, dict[str, Any]]] = []
    for s in segments:
        txt = s.get("text", "")
        words = [w.lower() for w in WORD_TOKEN_RE.findall(txt)]
        score = sum(freq[w] for w in set(words)) / (len(words) + 2.0) if words else 0.0
        scored.append((score, s))

    # Divide video into up to 5 chronological chapters
    n = len(segments)
    num_chapters = min(5, max(1, n // 6 or 1))
    chunk_size = max(1, math.ceil(n / num_chapters))
    chapters: list[dict[str, Any]] = []

    for i in range(0, n, chunk_size):
        window = segments[i : i + chunk_size]
        if not window:
            continue
        first_seg = window[0]
        best_seg = max(
            window,
            key=lambda seg: sum(freq[w.lower()] for w in set(WORD_TOKEN_RE.findall(seg.get("text", "")))),
        )
        chapters.append(
            {
                "time_str": first_seg["time_str"],
                "seconds": int(first_seg["start"]),
                "title": best_seg["text"][:28] + ("..." if len(best_seg["text"]) > 28 else ""),
                "summary": "；".join(x["text"] for x in window[:3])[:140],
            }
        )

    top_points = [
        f"[{seg['time_str']}] {seg['text']}"
        for _, seg in sorted(scored, key=lambda x: x[0], reverse=True)[:5]
    ]
    top_tags = [w for w, _ in freq.most_common(6) if len(w) >= 2]

    return {
        "mode": "extractive_offline",
        "model": "内置离线规则提炼引擎 (未配置 LLM API Key)",
        "overview": f"《{title}》共提取到 {len(segments)} 条时间轴字幕片段。当前处于免 Key 离线规则提炼模式，已按词频权重与时间窗口自动划分 {len(chapters)} 个章节锚点（点击时间戳即可跳转播放）。在【设置】中填入 DeepSeek / 通义千问 / Ollama 等模型即可开启深度大模型推理总结。",
        "chapters": chapters[:6],
        "key_points": top_points,
        "tags": top_tags or ["视频笔记", "时间轴精读"],
    }


def _call_chat_completion(
    settings: dict[str, Any],
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> tuple[str, str]:
    provider = settings.get("llm_provider", "dashscope")
    base_url = (settings.get("llm_base_url") or LLM_PROVIDER_PRESETS.get(provider, {}).get("base_url") or "").rstrip("/")
    api_key = (settings.get("llm_api_key") or "").strip()
    model = (settings.get("llm_model") or LLM_PROVIDER_PRESETS.get(provider, {}).get("default_model") or "deepseek-v4.1-flash-1017").strip()

    is_ollama = provider == "ollama" or "11434" in base_url
    if not api_key and not is_ollama:
        raise PermissionError("NO_API_KEY")

    # Native Anthropic Claude Endpoint support (x-api-key & /v1/messages)
    if "api.anthropic.com" in base_url:
        endpoint = f"{base_url}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        sys_parts = [m["content"] for m in messages if m.get("role") == "system"]
        non_sys = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]
        if not non_sys:
            non_sys = [{"role": "user", "content": "Hello"}]
        payload: dict[str, Any] = {
            "model": model,
            "messages": non_sys,
            "max_tokens": max_tokens,
        }
        if sys_parts:
            payload["system"] = "\n\n".join(sys_parts)
        if temperature is not None:
            payload["temperature"] = temperature
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=90)
        if resp.status_code >= 400:
            raise RuntimeError(f"Anthropic 接口请求失败 (HTTP {resp.status_code}): {resp.text[:240]}")
        data = resp.json()
        content = ""
        for block in data.get("content") or []:
            if block.get("type") == "text":
                content += block.get("text", "")
        if not content:
            raise RuntimeError("Anthropic 模型返回内容为空")
        return content.strip(), model

    # Universal OpenAI-compatible Endpoint (DashScope / DeepSeek / SiliconFlow / OpenAI / Ollama / Custom relays)
    endpoint = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=90)
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM 接口请求失败 (HTTP {resp.status_code}): {resp.text[:240]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content and msg.get("reasoning_content"):
        content = str(msg.get("reasoning_content")).strip()
    if not content:
        raise RuntimeError("大模型返回内容为空")
    # Clean thinking tags if present
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content, model


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            cleaned = cleaned[brace_start : brace_end + 1]
    return json.loads(cleaned)


def summarize_video_transcript(
    title: str,
    segments: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Generate structured overview, clickable timeline chapters, and key takeaways."""
    if not segments:
        raise ValueError("字幕内容为空，无法生成总结")

    condensed = _condense_segments_for_prompt(segments)
    system_prompt = (
        "你是一位专业的 AI 视频知识精读助手。请根据用户提供的带时间戳视频字幕，使用中文输出严格的 JSON 对象（不要输出多余解释），"
        "JSON 结构必须包含以下字段：\n"
        "{\n"
        '  "overview": "100~200字的视频核心内容精炼总结",\n'
        '  "chapters": [\n'
        '    {"time_str": "MM:SS", "title": "章节小标题", "summary": "该时间段讲了什么核心内容"}\n'
        "  ],\n"
        '  "key_points": ["核心要点1（可带 [MM:SS] 时间锚点）", "核心要点2"],\n'
        '  "tags": ["关键词1", "关键词2", "关键词3"]\n'
        "}"
    )
    user_prompt = f"视频标题：{title}\n\n带时间戳字幕原文：\n{condensed}"

    try:
        raw_reply, model_used = _call_chat_completion(
            settings,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.25,
        )
        parsed = _extract_json_block(raw_reply)
        chapters_out = []
        for ch in parsed.get("chapters") or []:
            ts = str(ch.get("time_str") or "00:00").strip("[] ")
            chapters_out.append(
                {
                    "time_str": ts,
                    "seconds": _parse_time_str_to_seconds(ts),
                    "title": str(ch.get("title") or "章节要点"),
                    "summary": str(ch.get("summary") or ""),
                }
            )
        return {
            "mode": "llm",
            "model": model_used,
            "overview": str(parsed.get("overview") or raw_reply[:300]),
            "chapters": chapters_out,
            "key_points": [str(x) for x in (parsed.get("key_points") or [])],
            "tags": [str(x) for x in (parsed.get("tags") or [])],
        }
    except PermissionError:
        return _extractive_offline_summary(title, segments)


def ask_video_question(
    title: str,
    segments: list[dict[str, Any]],
    question: str,
    settings: dict[str, Any],
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Temporal-RAG Q&A over timestamped video transcript with clickable `[MM:SS]` citations."""
    q = (question or "").strip()
    if not q:
        raise ValueError("请输入提问内容")
    if not segments:
        raise ValueError("尚未提取该视频的字幕，请先点击【提取字幕 / AI 精读】")

    # Retrieve most relevant timestamped passages + global context
    q_tokens = {w.lower() for w in WORD_TOKEN_RE.findall(q)}
    scored_indices: list[tuple[float, int]] = []
    for idx, seg in enumerate(segments):
        txt_lower = seg.get("text", "").lower()
        hits = sum(2.0 for tok in q_tokens if tok in txt_lower)
        # Also check character overlap for short Chinese queries
        char_hits = sum(0.3 for ch in set(q) if "\u4e00" <= ch <= "\u9fff" and ch in txt_lower)
        scored_indices.append((hits + char_hits, idx))

    scored_indices.sort(key=lambda x: x[0], reverse=True)
    picked_set: set[int] = set()
    for score, center_idx in scored_indices[:12]:
        if score <= 0 and len(picked_set) >= 8:
            break
        for neighbor in range(max(0, center_idx - 1), min(len(segments), center_idx + 2)):
            picked_set.add(neighbor)

    relevant_segs = [segments[i] for i in sorted(picked_set)] if picked_set else segments[:30]
    context_text = "\n".join(f"[{s['time_str']}] {s['text']}" for s in relevant_segs)
    if len(context_text) < 6000:
        global_condensed = _condense_segments_for_prompt(segments, max_chars=5000)
        context_text = f"【与问题最相关的时间轴片段】\n{context_text}\n\n【全片概览时间轴】\n{global_condensed}"

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是 AI 视频伴读问答助手。请严格依据提供的视频时间轴字幕回答用户问题，"
                "并在引用具体观点时标注对应的时间戳（格式如 [02:15]），方便用户点击跳转到视频对应位置。"
            ),
        },
        {
            "role": "user",
            "content": f"视频标题：《{title}》\n参考字幕上下文：\n{context_text}",
        },
    ]
    for msg in (history or [])[-6:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"][:1000]})
    messages.append({"role": "user", "content": q})

    try:
        answer, model_used = _call_chat_completion(settings, messages, temperature=0.3)
        return {
            "mode": "llm",
            "model": model_used,
            "answer": answer,
            "citations": [
                {"time_str": s["time_str"], "seconds": int(s["start"]), "text": s["text"]}
                for s in relevant_segs[:4]
            ],
        }
    except PermissionError:
        # Offline keyword retrieval fallback when no API key is configured
        lines = [f"- `[{s['time_str']}]` {s['text']}" for s in relevant_segs[:6]]
        return {
            "mode": "extractive_offline",
            "model": "离线字幕检索引擎",
            "answer": "当前尚未配置大模型 API Key，已为你从视频字幕中检索出与问题最相关的原片时间点（点击时间戳可直接跳转听原话）：\n\n"
            + "\n".join(lines),
            "citations": [
                {"time_str": s["time_str"], "seconds": int(s["start"]), "text": s["text"]}
                for s in relevant_segs[:4]
            ],
        }


def test_llm_connection(settings: dict[str, Any]) -> dict[str, Any]:
    """Ping OpenAI-compatible LLM endpoint and return latency or detailed error."""
    t0 = time.time()
    try:
        content, model = _call_chat_completion(
            settings,
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.1,
            max_tokens=15,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "ok": True,
            "latency_ms": elapsed_ms,
            "model": model,
            "message": f"连接正常！响应耗时 {elapsed_ms}ms · 校验模型 {model}",
        }
    except Exception as exc:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "latency_ms": elapsed_ms,
            "error": str(exc),
            "message": f"连接失败: {exc}",
        }


def _extractive_offline_storyboard(title: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate structural offline storyboard when no LLM API key is supplied."""
    shots: list[dict[str, Any]] = []
    shot_types = ["特写 (CU)", "近景 (MCU)", "中景 (MS)", "全景 (LS)", "大特写 (ECU)"]
    movements = ["缓慢推近", "平稳横移", "固定镜头", "微仰摇镜", "轻微拉远"]
    step = max(1, math.ceil(len(segments) / 5)) if segments else 1

    for idx, i in enumerate(range(0, len(segments) or 1, step)):
        seg = segments[i] if i < len(segments) else {"time_str": "00:00", "text": title, "start": 0.0}
        t_start = seg.get("time_str", "00:00")
        t_end_sec = int(seg.get("start", 0)) + 4
        shots.append({
            "shot_id": idx + 1,
            "time_range": f"{t_start} - {format_mmss(t_end_sec)}",
            "scale": shot_types[idx % len(shot_types)],
            "movement": movements[idx % len(movements)],
            "visual": f"呈现画面核心主体：{seg.get('text', '')[:25]}...",
            "dialogue": seg.get("text", title),
            "bgm": "轻快节奏背景乐",
            "intent": "强化视觉停留与关键内容传达",
        })

    table_rows = [
        "| 镜号 | 预估时间 | 景别 | 运镜 | 画面细节与视觉构图 | 台词 / 旁白口播 | 音效 & BGM | 分镜意图 |",
        "|:---:|:---:|:---:|:---:|:---|:---|:---|:---|",
    ]
    for s in shots:
        table_rows.append(
            f"| {s['shot_id']} | {s['time_range']} | {s['scale']} | {s['movement']} | {s['visual']} | {s['dialogue']} | {s['bgm']} | {s['intent']} |"
        )

    md = (
        f"### 🎬 《{title}》影视与短视频分镜脚本拆解\n\n"
        f"> **提示**：当前处于内置离线规则分镜模式（未填入 API Key）。填入百炼/DeepSeek API Key 可激活深度 AI 编导分镜生成。\n\n"
        f"#### 一、视频基本信息与风格定位\n"
        f"- **识别语种**：中文（普通话）\n"
        f"- **建议受众**：短视频受众、创作者二次剪辑学习\n"
        f"- **总分镜数**：{len(shots)} 个镜头\n\n"
        f"#### 二、专业分镜脚本表格\n\n"
        + "\n".join(table_rows)
        + "\n\n#### 三、爆款拆解要点\n"
        "- **黄金前3秒**：首个特写镜头快速呈现核心反差，抓住用户注意力。\n"
        "- **节奏控制**：每 3~5 秒进行一次镜头切换或视觉变焦，避免视觉疲劳。\n"
        "- **互动设计**：视频尾段留有提问引导或行动呼吁，激发评论区讨论。"
    )

    return {
        "mode": "extractive_offline",
        "model": "内置离线规则分镜引擎",
        "markdown": md,
        "language": "中文 (普通话)",
        "shots_count": len(shots),
        "overview": f"《{title}》分镜脚本（离线规则拆解共 {len(shots)} 镜）",
    }


def generate_storyboard_script(
    title: str,
    segments: list[dict[str, Any]],
    settings: dict[str, Any],
    extra_context: str = "",
) -> dict[str, Any]:
    """Generate professional, broadcast-grade film & short-form video storyboard script with shot breakdown."""
    if not segments:
        segments = [{"time_str": "00:00", "start": 0.0, "end": 30.0, "text": title}]

    condensed = _condense_segments_for_prompt(segments, max_chars=8000)
    system_prompt = (
        "你是一位资深影视导演、短视频编导与千万爆款内容拆解专家。"
        "请根据用户提供的视频标题与时间轴文案/台词片段，输出专业、影视级别的【结构化分镜脚本与拆解报告】。\n\n"
        "输出必须遵循以下精美 Markdown 结构：\n"
        "### 🎬 《视频标题》影视与短视频分镜脚本拆解\n\n"
        "#### 一、视频基本信息与风格定位\n"
        "- **识别语种**：精准识别主语种及方言/外语混用（如 中文普通话 / 英语 / 粤语等）\n"
        "- **视频类型与风格**：如 知识口播 / 美食特写 / 剧情反转 / 沉浸式ASMR 等\n"
        "- **目标受众画像**：核心受众与观看动机\n"
        "- **节奏与视听风格**：色彩调性、BGM 选曲方向与剪辑快慢\n\n"
        "#### 二、专业影视分镜脚本表格\n"
        "严格使用标准 Markdown 表格输出每个镜头的详细拆解，表头格式必须如下：\n"
        "| 镜号 | 预估时间 | 景别 | 运镜 | 画面细节与视觉构图 | 台词 / 旁白口播 / 关键字幕 | 音效 & BGM 节奏 | 分镜设计意图 |\n"
        "|:---:|:---:|:---:|:---:|:---|:---|:---|:---|\n"
        "（景别如：特写/近景/中景/全景/大特写；运镜如：缓慢推近/平稳拉远/横向移动/微摇镜头/固定；画面细节需说明构图、光影及焦点变换）\n\n"
        "#### 三、爆款编导拆解与二次创作指南\n"
        "1. **黄金前3秒钩子（Hook）**：分析其如何抓住用户停留\n"
        "2. **情绪曲线与转折点**：情绪波形如何起伏、高潮爆发点在哪里\n"
        "3. **视觉停留与记忆符号**：有哪些亮眼镜头或道具符号\n"
        "4. **完播率与评论区互动策略**：尾部如何引导点赞、收藏与评论区热议"
    )
    user_prompt = f"视频标题：{title}\n\n时间轴文案/台词内容：\n{condensed}"
    if extra_context:
        user_prompt += f"\n\n创作者补充说明：\n{extra_context}"

    try:
        reply_md, model_used = _call_chat_completion(
            settings,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        return {
            "mode": "llm",
            "model": model_used,
            "markdown": reply_md,
            "language": "智能识别完成",
            "shots_count": reply_md.count("|") // 9 or 5,
            "overview": f"《{title}》影视级分镜脚本拆解报告（由 {model_used} 驱动生成）",
        }
    except PermissionError:
        return _extractive_offline_storyboard(title, segments)


def extract_clean_script(
    title: str,
    segments: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Extract clean voiceover script without vocal fillers, identify language, and extract golden quotes."""
    if not segments:
        segments = [{"time_str": "00:00", "start": 0.0, "end": 30.0, "text": title}]

    condensed = "\n".join(f"[{s.get('time_str', '00:00')}] {s.get('text', '')}" for s in segments)
    system_prompt = (
        "你是一位专业影视文学编剧与文案提炼大师。请根据提供的视频时间轴字幕，"
        "精准识别语种，剔除口癖语气词（如 '嗯'、'然后'、'那个'、重复词等），整理成清晰优雅的【纯净口播文案逐字稿】。\n\n"
        "请按以下 Markdown 格式输出：\n"
        "### 📝 《视频标题》纯净文案提炼与语种识别\n\n"
        "- **识别语种**：具体语言（如 中文普通话 / 英语 / 粤语等）\n"
        "- **文案总字数**：约 XX 字\n\n"
        "#### 一、核心文案金句（高传播力金句）\n"
        "- 提炼 3~5 句掷地有声的爆款金句\n\n"
        "#### 二、结构化纯净口播文案\n"
        "**【黄金引言 / 抛出痛点】**\n"
        "...\n\n"
        "**【主体展开 / 核心干货】**\n"
        "...\n\n"
        "**【升华总结 / 行动号召】**\n"
        "...\n\n"
        "#### 三、带时间戳的校对逐字稿\n"
        "整理后的时间轴对照..."
    )
    user_prompt = f"视频标题：{title}\n\n原始字幕内容：\n{condensed}"

    try:
        reply_md, model_used = _call_chat_completion(
            settings,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.25,
            max_tokens=4096,
        )
        return {
            "mode": "llm",
            "model": model_used,
            "markdown": reply_md,
            "overview": f"《{title}》纯净文案提取报告",
        }
    except PermissionError:
        full_text = " ".join(s.get("text", "") for s in segments)
        return {
            "mode": "extractive_offline",
            "model": "内置离线文案提取器",
            "markdown": (
                f"### 📝 《{title}》纯净文案提炼\n\n"
                f"> **提示**：当前处于离线免 Key 模式。在设置中填入 API Key 可开启智能去口癖与金句提炼。\n\n"
                f"#### 原文全文整理：\n\n{full_text}"
            ),
            "overview": f"《{title}》离线文案整理",
        }


ping_llm_connection = test_llm_connection
