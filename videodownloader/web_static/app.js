/* VideoDownloader AI Workspace - Frontend Controller (Desktop + iPhone PWA + Web Video Wall) */

const state = {
  tasks: new Map(), // id -> task
  systemInfo: null,
  settings: {},
  prefetched: null,
  currentStudioTaskId: null,
  chatHistories: new Map(), // taskId -> [{role, content}]
  wallTiles: [], // [{tileId, taskId, zoom, cover, solo}]
  wallLoopAll: true,
  ws: null,
};

// -------------------- Utilities --------------------

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDuration(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (!s) return '';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function formatSize(bytes) {
  const b = Number(bytes) || 0;
  if (b <= 0) return '';
  if (b >= 1024 * 1024 * 1024) return `${(b / (1024 ** 3)).toFixed(2)} GB`;
  if (b >= 1024 * 1024) return `${(b / (1024 ** 2)).toFixed(1)} MB`;
  return `${Math.round(b / 1024)} KB`;
}

function proxyImg(url) {
  if (!url) return '';
  if (url.startsWith('data:') || url.startsWith('/')) return url;
  return `/api/proxy/image?url=${encodeURIComponent(url)}`;
}

function toast(msg, duration = 3400) {
  const stack = $('toastStack');
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

async function apiFetch(path, options = {}, _retried = false) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === 'object') {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (netErr) {
    if (!_retried) {
      await new Promise((r) => setTimeout(r, 400));
      return apiFetch(path, options, true);
    }
    throw new Error('本地网页服务当前未开启或已关闭，请在桌面软件中开启【网页版互联服务】');
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `请求失败 (${resp.status})`);
  }
  return data;
}

function parseTimestampToSec(ts) {
  const parts = ts.replace(/[\[\]\s]/g, '').split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return 0;
}

function renderTextWithTimestamps(text) {
  const safe = escapeHtml(text);
  return safe.replace(/\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?/g, (match, ts) => {
    const sec = parseTimestampToSec(ts);
    return `<button class="ts-pill" onclick="seekStudioVideo(${sec})">[${ts}]</button>`;
  });
}

// -------------------- Navigation & Theme --------------------

function switchView(viewName) {
  document.querySelectorAll('.view-panel').forEach((p) => {
    p.classList.toggle('active', p.id === `view-${viewName}`);
  });
  document.querySelectorAll('[data-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === viewName);
  });
}

function initTheme() {
  const saved = localStorage.getItem('vd_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  $('btnToggleTheme').addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('vd_theme', next);
  });
}

// -------------------- WebSocket Real-Time Telemetry --------------------

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/tasks`);
  state.ws = ws;

  ws.onopen = () => {
    $('wsIndicator').classList.add('online');
    $('wsIndicator').title = '实时状态流已连接';
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'snapshot' && Array.isArray(msg.data)) {
        state.tasks.clear();
        msg.data.forEach((t) => state.tasks.set(t.id, t));
        renderAllTaskViews();
      } else if (msg.type === 'task_created' || msg.type === 'task_updated') {
        const prev = state.tasks.get(msg.data.id);
        state.tasks.set(msg.data.id, msg.data);
        if (prev && prev.status !== 'completed' && msg.data.status === 'completed') {
          toast(`✅ 下载完成：${msg.data.title}`);
        }
        renderAllTaskViews();
      } else if (msg.type === 'task_deleted') {
        state.tasks.delete(msg.data.id);
        renderAllTaskViews();
      }
    } catch (_) {}
  };

  ws.onclose = () => {
    $('wsIndicator').classList.remove('online');
    setTimeout(connectWebSocket, 2500);
  };
}

// -------------------- Render Queue & Trophy Library --------------------

function statusLabel(status) {
  const map = {
    queued: '⏳ 排队中',
    parsing: '🔍 解析中',
    downloading: '🚀 下载中',
    merging: '🎞️ 音视频合流中',
    retrying: '🛡️ 防风控重试中',
    paused: '⏸️ 已暂停',
    completed: '✅ 已完成',
    failed: '❌ 下载失败',
    canceled: '🛑 已取消',
  };
  return map[status] || status;
}

function renderAllTaskViews() {
  const all = Array.from(state.tasks.values()).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const active = all.filter((t) => t.status !== 'completed');
  const completed = all.filter((t) => t.status === 'completed');

  // Badges
  const bActive = $('badgeActiveCount');
  bActive.style.display = active.length ? 'inline-block' : 'none';
  bActive.textContent = active.length;

  const bDone = $('badgeDoneCount');
  bDone.style.display = completed.length ? 'inline-block' : 'none';
  bDone.textContent = completed.length;

  $('queueSummaryText').textContent = active.length
    ? `活跃队列 ${active.length} 项 · 已完成 ${completed.length} 项`
    : `全部就绪 · 库中共 ${completed.length} 部媒体`;

  renderActiveQueue(active);
  renderTrophyLibrary(completed);
  populateWallSelector(completed);
}

function renderActiveQueue(activeTasks) {
  const box = $('activeTaskList');
  if (!activeTasks.length) {
    box.innerHTML = '<div class="empty-state">暂无正在进行的下载任务，粘贴视频链接或手机口令即可开始 ⚡</div>';
    return;
  }

  box.innerHTML = activeTasks
    .map((t) => {
      const pct = Math.min(100, Math.max(0, Number(t.progress) || 0));
      const canPause = ['queued', 'parsing', 'downloading', 'retrying'].includes(t.status);
      const canResume = ['paused', 'failed', 'canceled'].includes(t.status);

      return `
      <div class="task-row">
        <div class="task-row-top">
          <div>
            <div class="task-title">${escapeHtml(t.title)}</div>
            <div class="task-sub">
              <span>${statusLabel(t.status)}</span>
              <span>🏷️ ${escapeHtml(t.platform_name)}</span>
              ${t.engine_used ? `<span>⚙️ ${escapeHtml(t.engine_used)}</span>` : ''}
              ${t.speed ? `<span style="color:var(--success); font-weight:600;">⚡ ${escapeHtml(t.speed)}</span>` : ''}
              ${t.eta ? `<span>剩余 ${escapeHtml(t.eta)}</span>` : ''}
              <span><strong>${pct.toFixed(1)}%</strong></span>
            </div>
            ${t.error ? `<div style="color:var(--danger); font-size:0.79rem; margin-top:4px;">${escapeHtml(t.error)}</div>` : ''}
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            ${canPause ? `<button class="btn btn-sm" onclick="taskAction('${t.id}', 'pause')">⏸️ 暂停</button>` : ''}
            ${canResume ? `<button class="btn btn-sm btn-primary" onclick="taskAction('${t.id}', 'resume')">▶️ 继续/重试</button>` : ''}
            <button class="btn btn-sm btn-danger" onclick="deleteTask('${t.id}', false)">✕ 移除</button>
          </div>
        </div>
        <div class="progress-track">
          <div class="progress-fill" style="width:${pct}%"></div>
        </div>
      </div>`;
    })
    .join('');
}

function renderTrophyLibrary(completedTasks) {
  const grid = $('libraryGrid');
  const keyword = ($('librarySearch').value || '').trim().toLowerCase();
  const filtered = keyword
    ? completedTasks.filter((t) => (t.title || '').toLowerCase().includes(keyword) || (t.platform_name || '').toLowerCase().includes(keyword))
    : completedTasks;

  if (!filtered.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;">战利品库暂无媒体。去【🚀 智能下载】粘贴一条 B站/抖音/YouTube 链接，或点右上角【🔄 扫描导入目录】吧！</div>';
    return;
  }

  grid.innerHTML = filtered
    .map((t) => {
      const thumbHtml = t.thumbnail
        ? `<img src="${proxyImg(t.thumbnail)}" alt="cover" loading="lazy" onerror="this.style.display='none'" />`
        : `<div class="media-thumb-placeholder">${t.only_audio ? '🎵' : '🎬'}</div>`;
      const dur = formatDuration(t.duration);
      const fsize = formatSize(t.file_size);
      const hasAi = Boolean(t.ai_summary);

      return `
      <div class="media-card">
        <div class="media-thumb-wrap" onclick="openStudio('${t.id}')">
          ${thumbHtml}
          <span class="media-platform-badge">${escapeHtml(t.platform_name)}</span>
          ${dur ? `<span class="media-duration-badge">${dur}</span>` : ''}
        </div>
        <div class="media-body">
          <div>
            <div style="font-weight:700; font-size:0.93rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;" title="${escapeHtml(t.title)}">
              ${escapeHtml(t.title)}
            </div>
            <div style="font-size:0.78rem; color:var(--text-secondary); margin-top:5px; display:flex; gap:8px; flex-wrap:wrap;">
              ${fsize ? `<span>📦 ${fsize}</span>` : ''}
              ${t.engine_used ? `<span>⚙️ ${escapeHtml(t.engine_used)}</span>` : ''}
              ${hasAi ? `<span style="color:var(--accent); font-weight:600;">✨ 已生成AI笔记</span>` : ''}
            </div>
          </div>

          <div class="media-actions">
            <button class="btn btn-sm btn-primary" onclick="openStudio('${t.id}')">▶️ 播放 & AI精读</button>
            <a class="btn btn-sm btn-success" href="/api/media/${t.id}/download" download>📲 存到手机/本机</a>
            <button class="btn btn-sm" onclick="addTaskToWall('${t.id}')" title="加入多屏对比播放墙">🧱 上墙</button>
            <button class="btn btn-sm" onclick="revealInFolder('${t.id}')" title="在电脑资源管理器定位">📂</button>
            <button class="btn btn-sm btn-danger" onclick="deleteTask('${t.id}', true)" title="删除记录与本地文件">🗑️</button>
          </div>
        </div>
      </div>`;
    })
    .join('');
}

// -------------------- Task Actions --------------------

window.taskAction = async function (taskId, action) {
  try {
    await apiFetch(`/api/tasks/${taskId}/${action}`, { method: 'POST' });
  } catch (err) {
    toast(`操作失败：${err.message}`);
  }
};

window.deleteTask = async function (taskId, askDeleteFile) {
  let deleteFile = false;
  if (askDeleteFile) {
    const choice = confirm('确定移除该媒体记录吗？\n（点击“确定”将同时清理记录；如需保留硬盘原文件请先备份）');
    if (!choice) return;
    deleteFile = confirm('是否同时删除电脑硬盘上的本地媒体文件？\n【确定】= 连同本地文件一起删\n【取消】= 仅移除列表记录，保留硬盘视频文件');
  }
  try {
    await apiFetch(`/api/tasks/${taskId}?delete_file=${deleteFile}`, { method: 'DELETE' });
    state.tasks.delete(taskId);
    renderAllTaskViews();
    toast('🗑️ 已移除');
  } catch (err) {
    toast(`移除失败：${err.message}`);
  }
};

window.revealInFolder = async function (taskId) {
  try {
    await apiFetch('/api/system/open-folder', { method: 'POST', body: { task_id: taskId, reveal_file: true } });
    toast('📂 已在电脑桌面打开文件所在目录');
  } catch (err) {
    toast(err.message);
  }
};

// -------------------- Parse & Submit Flow --------------------

async function handleProbeUrl() {
  const text = $('urlInput').value.trim();
  if (!text) {
    toast('请先粘贴视频链接或手机分享口令');
    return;
  }
  const btn = $('btnProbeUrl');
  btn.disabled = true;
  btn.textContent = '🔍 解析中...';

  try {
    const res = await apiFetch('/api/parse', { method: 'POST', body: { text } });
    state.prefetched = res.media;
    const box = $('parsePreviewBox');
    box.style.display = 'grid';
    $('prevThumb').src = res.media.thumbnail ? proxyImg(res.media.thumbnail) : '/static/icon.svg';
    $('prevTitle').textContent = res.media.title;

    const pills = [
      `平台: ${res.cleaned.platform_name}`,
      `驱动引擎: ${res.media.engine_name} (${res.media.elapsed_ms}ms)`,
    ];
    if (res.media.duration) pills.push(`时长: ${formatDuration(res.media.duration)}`);
    if (res.media.uploader) pills.push(`UP主/作者: ${res.media.uploader}`);
    if (res.cleaned.batch_count > 1) pills.push(`识别到批量链接: ${res.cleaned.batch_count} 条`);
    if (res.media.has_subtitles) pills.push('💬 含原生/CC字幕 (支持秒级AI总结)');

    $('prevPills').innerHTML = pills.map((p) => `<span class="pill">${escapeHtml(p)}</span>`).join('');
    const fmts = (res.media.formats || []).map((f) => f.label).join(' · ');
    $('prevFormats').textContent = fmts ? `可选流规格：${fmts}` : '已锁定最佳无水印直链流';
    toast(`✨ 解析成功 (${res.media.engine_name}, ${res.media.elapsed_ms}ms)`);
  } catch (err) {
    toast(`解析提示：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 解析预览';
  }
}

async function handleStartDownload() {
  const text = $('urlInput').value.trim();
  if (!text) {
    toast('请先输入或粘贴视频链接');
    return;
  }
  const btn = $('btnStartDownload');
  btn.disabled = true;
  btn.textContent = '⚡ 投递中...';

  try {
    const pre = state.prefetched || {};
    const res = await apiFetch('/api/tasks', {
      method: 'POST',
      body: {
        text,
        quality: $('optQuality').value,
        only_audio: $('optOnlyAudio').checked,
        audio_format: $('optAudioFormat').value,
        subtitle: $('optSubtitle').checked,
        prefetched_title: pre.title || '',
        prefetched_thumbnail: pre.thumbnail || '',
        prefetched_duration: pre.duration || 0,
        prefetched_uploader: pre.uploader || '',
      },
    });
    (res.tasks || []).forEach((t) => state.tasks.set(t.id, t));
    renderAllTaskViews();
    $('urlInput').value = '';
    $('parsePreviewBox').style.display = 'none';
    state.prefetched = null;
    toast(`🚀 已加入下载队列 (${res.tasks.length} 个任务)`);
  } catch (err) {
    toast(`投递失败：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '⚡ 立即下载';
  }
}

// -------------------- AI Video Copilot & Player Studio --------------------

window.openStudio = function (taskId) {
  const task = state.tasks.get(taskId);
  if (!task) return;
  state.currentStudioTaskId = taskId;

  $('studioTitle').textContent = `🎬 ${task.title}`;
  $('studioDownloadPhoneBtn').href = `/api/media/${taskId}/download`;

  const video = $('studioVideo');
  video.src = `/api/media/${taskId}/stream`;
  video.play().catch(() => {});

  // Render cached AI summary / transcript if available
  if (task.ai_summary) {
    renderAiSummaryResult(task.ai_summary);
  } else {
    $('aiSummaryBody').innerHTML =
      '<div class="empty-state">点击上方【✨ 一键生成 AI 时间轴精读笔记】，自动抓取字幕并生成核心摘要与可点击跳转的章节卡片 🎯</div>';
  }

  if (task.transcript && task.transcript.segments) {
    renderTranscriptList(task.transcript);
  } else {
    $('aiTranscriptBody').innerHTML = '<div class="empty-state">点击【📥 提取内置字幕】或直接运行 AI 总结自动加载时间轴</div>';
  }

  renderChatMessages(taskId);
  $('studioModal').classList.add('open');
};

window.seekStudioVideo = function (seconds) {
  const video = $('studioVideo');
  if (!video) return;
  video.currentTime = Math.max(0, Number(seconds) || 0);
  video.play().catch(() => {});
};

function closeStudio() {
  const video = $('studioVideo');
  video.pause();
  video.removeAttribute('src');
  video.load();
  $('studioModal').classList.remove('open');
  state.currentStudioTaskId = null;
}

function renderAiSummaryResult(summary) {
  const tagsHtml = (summary.tags || []).map((t) => `<span class="pill">#${escapeHtml(t)}</span>`).join('');
  const chaptersHtml = (summary.chapters || [])
    .map(
      (ch) => `
      <div class="chapter-card" onclick="seekStudioVideo(${Number(ch.seconds) || 0})">
        <div style="font-weight:700; font-size:0.9rem; margin-bottom:3px;">
          <span class="ts-pill">[${escapeHtml(ch.time_str)}]</span>
          ${escapeHtml(ch.title)}
        </div>
        <div style="font-size:0.83rem; color:var(--text-secondary);">${escapeHtml(ch.summary)}</div>
      </div>`
    )
    .join('');

  const pointsHtml = (summary.key_points || [])
    .map((pt) => `<li style="margin-bottom:6px; font-size:0.87rem;">${renderTextWithTimestamps(pt)}</li>`)
    .join('');

  $('aiSummaryBody').innerHTML = `
    <div style="text-align:left;">
      <div style="font-size:0.76rem; color:var(--text-secondary); margin-bottom:8px;">
        驱动模型：<strong>${escapeHtml(summary.model || '')}</strong>
      </div>
      <div style="margin-bottom:10px; display:flex; gap:6px; flex-wrap:wrap;">${tagsHtml}</div>
      <div style="padding:12px; border-radius:10px; background:var(--bg-elevated); font-size:0.89rem; margin-bottom:14px; line-height:1.6;">
        <strong>📌 核心摘要：</strong>${escapeHtml(summary.overview)}
      </div>
      ${chaptersHtml ? `<div style="font-weight:700; font-size:0.9rem; margin-bottom:8px;">⏱️ 时间轴章节导读（点击秒级跳转画面）</div>${chaptersHtml}` : ''}
      ${pointsHtml ? `<div style="font-weight:700; font-size:0.9rem; margin:14px 0 8px;">💡 关键知识要点</div><ul style="padding-left:18px;">${pointsHtml}</ul>` : ''}
    </div>
  `;
}

function renderTranscriptList(transcript) {
  const segs = transcript.segments || [];
  if (!segs.length) {
    $('aiTranscriptBody').innerHTML = '<div class="empty-state">无字幕内容</div>';
    return;
  }
  $('aiTranscriptBody').innerHTML = `
    <div style="text-align:left; font-size:0.84rem;">
      <div style="color:var(--text-secondary); margin-bottom:8px;">字幕来源: ${escapeHtml(transcript.source)} · 共 ${segs.length} 句（点击任意行跳转视频）</div>
      ${segs
        .map(
          (s) => `
        <div style="padding:6px 8px; border-bottom:1px solid var(--border); cursor:pointer;" onclick="seekStudioVideo(${s.start})">
          <span class="ts-pill">[${escapeHtml(s.time_str)}]</span>
          <span>${escapeHtml(s.text)}</span>
        </div>`
        )
        .join('')}
    </div>
  `;
}

async function runStudioAiSummary(force = false) {
  const taskId = state.currentStudioTaskId;
  if (!taskId) return;
  const btn = $('btnRunAiSummary');
  btn.disabled = true;
  btn.textContent = '⏳ 正在提取字幕与AI推理...';
  $('aiSummaryBody').innerHTML = '<div class="empty-state">正在提取时间轴字幕并进行智能归纳，请稍候 (约 3~15 秒)...</div>';

  try {
    const res = await apiFetch(`/api/tasks/${taskId}/ai/summarize?force=${force}`, { method: 'POST' });
    renderAiSummaryResult(res.summary);
    if (res.transcript) renderTranscriptList(res.transcript);
    const t = state.tasks.get(taskId);
    if (t) {
      t.ai_summary = res.summary;
      t.transcript = res.transcript;
      renderAllTaskViews();
    }
    toast('✨ AI 时间轴笔记生成完毕');
  } catch (err) {
    $('aiSummaryBody').innerHTML = `<div class="empty-state" style="color:var(--danger);">${escapeHtml(err.message)}</div>`;
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ 一键生成 AI 时间轴精读笔记';
  }
}

function renderChatMessages(taskId) {
  const box = $('aiChatMessages');
  const hist = state.chatHistories.get(taskId) || [];
  const intro = `<div class="chat-bubble assistant">你好！我可以基于这部视频的时间轴字幕回答任何细节问题，并自动标注原片时间点。点击回复中的时间戳即可跳转画面 🎯</div>`;
  box.innerHTML =
    intro +
    hist
      .map(
        (m) =>
          `<div class="chat-bubble ${m.role}">${
            m.role === 'assistant' ? renderTextWithTimestamps(m.content) : escapeHtml(m.content)
          }</div>`
      )
      .join('');
  box.scrollTop = box.scrollHeight;
}

async function sendStudioAiQuestion() {
  const taskId = state.currentStudioTaskId;
  if (!taskId) return;
  const input = $('aiChatInput');
  const question = input.value.trim();
  if (!question) return;

  const hist = state.chatHistories.get(taskId) || [];
  hist.push({ role: 'user', content: question });
  state.chatHistories.set(taskId, hist);
  input.value = '';
  renderChatMessages(taskId);

  const btn = $('btnSendAiChat');
  btn.disabled = true;
  btn.textContent = '思考中...';

  try {
    const res = await apiFetch(`/api/tasks/${taskId}/ai/ask`, {
      method: 'POST',
      body: { question, history: hist.slice(0, -1) },
    });
    hist.push({ role: 'assistant', content: res.answer });
    renderChatMessages(taskId);
  } catch (err) {
    hist.push({ role: 'assistant', content: `⚠️ ${err.message}` });
    renderChatMessages(taskId);
  } finally {
    btn.disabled = false;
    btn.textContent = '发送';
  }
}

// -------------------- Web Multi-Screen Video Wall --------------------

function populateWallSelector(completedTasks) {
  const sel = $('wallAddSelect');
  const currentVal = sel.value;
  sel.innerHTML =
    '<option value="">+ 选择已下载媒体上墙播放...</option>' +
    completedTasks.map((t) => `<option value="${t.id}">${escapeHtml(t.title.slice(0, 42))}</option>`).join('');
  sel.value = currentVal;
}

window.addTaskToWall = function (taskId) {
  const task = state.tasks.get(taskId);
  if (!task) return;
  if (state.wallTiles.length >= 9) {
    toast('为保证浏览器硬件解码流畅，同屏最多放置 9 路视频');
    return;
  }
  const isFirst = state.wallTiles.length === 0;
  state.wallTiles.push({
    tileId: `tile_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    taskId,
    zoom: 1.0,
    cover: false,
    solo: isFirst,
  });
  switchView('wall');
  renderWallStage();
  toast(`🧱 已加入播放墙：${task.title.slice(0, 22)}`);
};

function renderWallStage() {
  const stage = $('wallStage');
  const count = state.wallTiles.length;
  const colMode = $('wallColsSelect').value;

  let cols = 1;
  if (colMode !== 'auto') {
    cols = Number(colMode) || 2;
  } else {
    cols = count <= 1 ? 1 : count <= 4 ? 2 : 3;
  }
  stage.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;

  if (!count) {
    stage.innerHTML =
      '<div class="empty-state" style="grid-column:1/-1; align-self:center;">从上方下拉框选择视频，或点击【⚡ 一键铺满最近 4 部】开启多路同屏对比播放（支持主声道独奏、滚轮缩放、循环与全屏）</div>';
    return;
  }

  stage.innerHTML = state.wallTiles
    .map((tile) => {
      const task = state.tasks.get(tile.taskId);
      const title = task ? task.title : '媒体流';
      return `
      <div class="wall-tile ${tile.solo ? 'solo-active' : ''}" id="${tile.tileId}">
        <div class="wall-video-viewport" onwheel="onWallTileWheel(event, '${tile.tileId}')">
          <video
            src="/api/media/${tile.taskId}/stream"
            autoplay
            playsinline
            controls
            ${state.wallLoopAll ? 'loop' : ''}
            ${tile.solo ? '' : 'muted'}
            class="${tile.cover ? 'fill-cover' : ''}"
            style="transform: scale(${tile.zoom});"
          ></video>
        </div>
        <div class="wall-tile-bar">
          <span class="wall-tile-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
          <div style="display:flex; gap:4px;">
            <button class="btn btn-sm" onclick="soloWallTile('${tile.tileId}')" title="设为主声道并静音其他窗口">
              ${tile.solo ? '🔊 主声道✓' : '🔇 独奏'}
            </button>
            <button class="btn btn-sm" onclick="toggleWallTileCover('${tile.tileId}')">${tile.cover ? '填充' : '适配'}</button>
            <button class="btn btn-sm" onclick="zoomWallTile('${tile.tileId}', 0.2)">＋</button>
            <button class="btn btn-sm" onclick="zoomWallTile('${tile.tileId}', -0.2)">－</button>
            <button class="btn btn-sm btn-danger" onclick="removeWallTile('${tile.tileId}')">✕</button>
          </div>
        </div>
      </div>`;
    })
    .join('');

  // Enforce muted property via DOM API (HTML `muted` attribute only sets defaultMuted)
  state.wallTiles.forEach((tile) => {
    const vid = document.querySelector(`#${tile.tileId} video`);
    if (vid) vid.muted = !tile.solo;
  });
}

window.soloWallTile = function (tileId) {
  state.wallTiles.forEach((t) => {
    t.solo = t.tileId === tileId;
    const el = $(t.tileId);
    if (el) {
      el.classList.toggle('solo-active', t.solo);
      const v = el.querySelector('video');
      if (v) v.muted = !t.solo;
    }
  });
  renderWallStageSnapshotButtons();
};

function renderWallStageSnapshotButtons() {
  state.wallTiles.forEach((t) => {
    const el = $(t.tileId);
    if (!el) return;
    const btn = el.querySelector('.wall-tile-bar button');
    if (btn) btn.textContent = t.solo ? '🔊 主声道✓' : '🔇 独奏';
  });
}

window.toggleWallTileCover = function (tileId) {
  const tile = state.wallTiles.find((t) => t.tileId === tileId);
  if (!tile) return;
  tile.cover = !tile.cover;
  const vid = document.querySelector(`#${tileId} video`);
  if (vid) vid.classList.toggle('fill-cover', tile.cover);
};

window.zoomWallTile = function (tileId, delta) {
  const tile = state.wallTiles.find((t) => t.tileId === tileId);
  if (!tile) return;
  tile.zoom = Math.min(2.6, Math.max(1.0, Number((tile.zoom + delta).toFixed(2))));
  const vid = document.querySelector(`#${tileId} video`);
  if (vid) vid.style.transform = `scale(${tile.zoom})`;
};

window.onWallTileWheel = function (event, tileId) {
  event.preventDefault();
  zoomWallTile(tileId, event.deltaY < 0 ? 0.15 : -0.15);
};

window.removeWallTile = function (tileId) {
  state.wallTiles = state.wallTiles.filter((t) => t.tileId !== tileId);
  if (state.wallTiles.length && !state.wallTiles.some((t) => t.solo)) {
    state.wallTiles[0].solo = true;
  }
  renderWallStage();
};

// -------------------- System Info, LAN QR & Settings --------------------

async function loadSystemAndSettings() {
  try {
    const [sys, cfg] = await Promise.all([apiFetch('/api/system/info'), apiFetch('/api/settings')]);
    state.systemInfo = sys;
    state.settings = cfg;

    // Populate Fetch defaults from saved settings
    if (cfg.quality) $('optQuality').value = cfg.quality;
    $('optOnlyAudio').checked = Boolean(cfg.only_audio);
    $('optAudioFormat').style.display = cfg.only_audio ? 'inline-block' : 'none';
    if (cfg.audio_format) $('optAudioFormat').value = cfg.audio_format;
    $('optSubtitle').checked = Boolean(cfg.subtitle);

    // Populate LAN QR
    const lan = sys.lan;
    if (lan && lan.endpoints && lan.endpoints.length) {
      const sel = $('settingsLanSelect');
      sel.innerHTML = lan.endpoints
        .map((ep, i) => `<option value="${i}">${escapeHtml(ep.url)}</option>`)
        .join('');
      applyLanEndpoint(lan.endpoints[0]);
      sel.onchange = () => applyLanEndpoint(lan.endpoints[Number(sel.value) || 0]);
    }

    // Populate AI settings
    $('cfgLlmProvider').value = cfg.llm_provider || 'deepseek';
    $('cfgLlmBaseUrl').value = cfg.llm_base_url || '';
    $('cfgLlmApiKey').value = cfg.llm_api_key || '';
    $('cfgLlmModel').value = cfg.llm_model || '';
    $('cfgAsrApiKey').value = cfg.asr_api_key || '';
    $('cfgAsrBaseUrl').value = cfg.asr_base_url || 'https://api.siliconflow.cn/v1';
    $('cfgAsrModel').value = cfg.asr_model || 'FunAudioLLM/SenseVoiceSmall';

    // Populate Engine settings
    $('cfgSavePath').value = cfg.save_path || '';
    $('cfgConcurrent').value = cfg.concurrent_tasks || 3;
    $('cfgFragments').value = String(cfg.fragment_threads || 8);
    $('cfgBrowserCookie').value = cfg.browser_cookie || 'none';
    $('cookieFileStatus').textContent = cfg.cookie_file ? '✓ 已加载 cookies.txt' : '';

    renderToolchainInfo(sys.toolchain);
  } catch (err) {
    toast(`初始化配置读取异常：${err.message}`);
  }
}

function applyLanEndpoint(ep) {
  if (!ep) return;
  $('settingsQrImg').src = ep.qr_data_uri;
  $('settingsLanUrl').textContent = ep.url;
  $('shortcutApiDisplay').textContent = ep.shortcut_api;
  $('modalQrImg').src = ep.qr_data_uri;
  $('modalLanUrl').textContent = ep.url;
}

function renderToolchainInfo(tc) {
  if (!tc) return;
  $('toolchainStatusBox').innerHTML = `
    <div>• 宿主系统: <code>${escapeHtml(tc.platform)}</code></div>
    <div>• yt-dlp 内核: <strong>${escapeHtml(tc.ytdlp_python_version)}</strong> ${tc.ytdlp_binary_version ? `(独立热更包: ${escapeHtml(tc.ytdlp_binary_version)})` : ''}</div>
    <div>• FFmpeg 混流器: <strong>${escapeHtml(tc.ffmpeg_version)}</strong> ${tc.ffmpeg_ready ? '✅' : '⚠️'}</div>
    <div>• 多引擎链路: <code>口令清洗 → 国内无水印快解 → yt-dlp 4K → 网页媒体嗅探</code></div>
  `;
}

// -------------------- Event Bindings --------------------

document.addEventListener('DOMContentLoaded', async () => {
  initTheme();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // Nav tabs (desktop + mobile)
  document.querySelectorAll('[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  // Fetch panel
  $('optOnlyAudio').addEventListener('change', (e) => {
    $('optAudioFormat').style.display = e.target.checked ? 'inline-block' : 'none';
  });

  $('btnPasteClipboard').addEventListener('click', async () => {
    try {
      const txt = await navigator.clipboard.readText();
      if (txt) {
        $('urlInput').value = txt;
        toast('📋 已从剪贴板粘贴');
      }
    } catch (_) {
      $('urlInput').focus();
      toast('请长按输入框点击粘贴（非 HTTPS 局域网下浏览器限制自动读剪贴板）');
    }
  });

  $('btnClearInput').addEventListener('click', () => {
    $('urlInput').value = '';
    $('parsePreviewBox').style.display = 'none';
    state.prefetched = null;
  });

  $('btnProbeUrl').addEventListener('click', handleProbeUrl);
  $('btnStartDownload').addEventListener('click', handleStartDownload);

  // Library actions
  $('librarySearch').addEventListener('input', () => renderAllTaskViews());
  $('btnScanFolder').addEventListener('click', async () => {
    try {
      const res = await apiFetch('/api/tasks/scan-local', { method: 'POST' });
      const snap = await apiFetch('/api/tasks');
      (snap.tasks || []).forEach((t) => state.tasks.set(t.id, t));
      renderAllTaskViews();
      toast(`🔄 扫描完成，新增导入 ${res.added} 个媒体文件`);
    } catch (err) {
      toast(err.message);
    }
  });
  $('btnOpenHostFolder').addEventListener('click', async () => {
    try {
      await apiFetch('/api/system/open-folder', { method: 'POST', body: { reveal_file: false } });
      toast('📂 已在电脑上打开下载保存目录');
    } catch (err) {
      toast(err.message);
    }
  });

  // Studio Modal & AI Tabs
  $('btnCloseStudio').addEventListener('click', closeStudio);
  $('studioModal').addEventListener('click', (e) => {
    if (e.target.id === 'studioModal') closeStudio();
  });

  document.querySelectorAll('[data-rate]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const v = $('studioVideo');
      v.playbackRate = Number(btn.dataset.rate) || 1.0;
      toast(`⏩ 播放倍速: ${v.playbackRate}x`);
    });
  });

  document.querySelectorAll('[data-aitab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-aitab]').forEach((b) => b.classList.toggle('active', b === btn));
      ['summary', 'chat', 'transcript'].forEach((name) => {
        const pane = $(`aitab-${name}`);
        pane.style.display = name === btn.dataset.aitab ? (name === 'chat' ? 'flex' : 'block') : 'none';
      });
    });
  });

  $('btnRunAiSummary').addEventListener('click', () => runStudioAiSummary(false));
  $('btnRefreshAiSummary').addEventListener('click', () => runStudioAiSummary(true));
  $('btnSendAiChat').addEventListener('click', sendStudioAiQuestion);
  $('aiChatInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendStudioAiQuestion();
  });

  $('btnExtractSubOnly').addEventListener('click', async () => {
    const taskId = state.currentStudioTaskId;
    if (!taskId) return;
    try {
      toast('📥 正在提取视频字幕...');
      const res = await apiFetch(`/api/tasks/${taskId}/ai/transcript?force=true`, { method: 'POST' });
      renderTranscriptList(res.transcript);
      toast(`✅ 成功提取 ${res.transcript.segments.length} 条时间轴字幕`);
    } catch (err) {
      toast(err.message);
    }
  });

  $('uploadSubInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    const taskId = state.currentStudioTaskId;
    if (!file || !taskId) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await apiFetch(`/api/tasks/${taskId}/ai/upload-subtitle`, { method: 'POST', body: fd });
      renderTranscriptList(res.transcript);
      toast(`✅ 已导入外部字幕 (${res.transcript.segments.length} 句)`);
    } catch (err) {
      toast(err.message);
    } finally {
      e.target.value = '';
    }
  });

  // Web Video Wall controls
  $('wallAddSelect').addEventListener('change', (e) => {
    if (e.target.value) {
      addTaskToWall(e.target.value);
      e.target.value = '';
    }
  });

  $('btnWallLoadRecent').addEventListener('click', () => {
    const completed = Array.from(state.tasks.values())
      .filter((t) => t.status === 'completed' && !t.only_audio)
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
      .slice(0, 4);
    if (!completed.length) {
      toast('战利品库暂无已完成的视频文件');
      return;
    }
    state.wallTiles = completed.map((t, idx) => ({
      tileId: `tile_${Date.now()}_${idx}`,
      taskId: t.id,
      zoom: 1.0,
      cover: false,
      solo: idx === 0,
    }));
    renderWallStage();
  });

  $('wallColsSelect').addEventListener('change', renderWallStage);

  $('btnWallPlayAll').addEventListener('click', () => {
    const vids = Array.from(document.querySelectorAll('#wallStage video'));
    if (!vids.length) return;
    const anyPlaying = vids.some((v) => !v.paused);
    vids.forEach((v) => (anyPlaying ? v.pause() : v.play().catch(() => {})));
  });

  $('btnWallLoopAll').addEventListener('click', (e) => {
    state.wallLoopAll = !state.wallLoopAll;
    e.target.textContent = `🔁 循环: ${state.wallLoopAll ? '开' : '关'}`;
    document.querySelectorAll('#wallStage video').forEach((v) => {
      v.loop = state.wallLoopAll;
    });
  });

  $('btnWallFullscreen').addEventListener('click', () => {
    const stage = $('wallStage');
    if (!document.fullscreenElement) {
      stage.requestFullscreen?.();
    } else {
      document.exitFullscreen?.();
    }
  });

  $('btnWallClear').addEventListener('click', () => {
    state.wallTiles = [];
    renderWallStage();
  });

  // QR Modal
  $('btnOpenQrModal').addEventListener('click', () => $('qrModal').classList.add('open'));
  $('btnCloseQrModal').addEventListener('click', () => $('qrModal').classList.remove('open'));
  $('qrModal').addEventListener('click', (e) => {
    if (e.target.id === 'qrModal') $('qrModal').classList.remove('open');
  });

  // Settings LLM provider quick-fill
  $('cfgLlmProvider').addEventListener('change', (e) => {
    const presets = (state.systemInfo && state.systemInfo.llm_presets) || {};
    const p = presets[e.target.value];
    if (p) {
      $('cfgLlmBaseUrl').value = p.base_url;
      $('cfgLlmModel').value = p.default_model;
    }
  });

  $('btnSaveAiSettings').addEventListener('click', async () => {
    try {
      await apiFetch('/api/settings', {
        method: 'PUT',
        body: {
          llm_provider: $('cfgLlmProvider').value,
          llm_base_url: $('cfgLlmBaseUrl').value.trim(),
          llm_api_key: $('cfgLlmApiKey').value.trim(),
          llm_model: $('cfgLlmModel').value.trim(),
          asr_api_key: $('cfgAsrApiKey').value.trim(),
          asr_base_url: $('cfgAsrBaseUrl').value.trim(),
          asr_model: $('cfgAsrModel').value.trim(),
        },
      });
      toast('✅ AI 工作台配置已保存');
    } catch (err) {
      toast(err.message);
    }
  });

  $('btnSaveEngineSettings').addEventListener('click', async () => {
    try {
      await apiFetch('/api/settings', {
        method: 'PUT',
        body: {
          save_path: $('cfgSavePath').value.trim(),
          concurrent_tasks: Number($('cfgConcurrent').value) || 3,
          fragment_threads: Number($('cfgFragments').value) || 8,
          browser_cookie: $('cfgBrowserCookie').value,
          quality: $('optQuality').value,
          only_audio: $('optOnlyAudio').checked,
          audio_format: $('optAudioFormat').value,
          subtitle: $('optSubtitle').checked,
        },
      });
      toast('✅ 下载引擎配置已保存');
    } catch (err) {
      toast(err.message);
    }
  });

  $('cookieFileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      await apiFetch('/api/settings/upload-cookie', { method: 'POST', body: fd });
      $('cookieFileStatus').textContent = '✓ 已加载 cookies.txt';
      toast('🍪 cookies.txt 导入成功！');
    } catch (err) {
      toast(err.message);
    }
  });

  $('btnUpdateYtdlp').addEventListener('click', async (e) => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = '⏳ 更新中...';
    try {
      const res = await apiFetch('/api/system/update-ytdlp', { method: 'POST' });
      renderToolchainInfo(res.toolchain);
      toast('✅ yt-dlp 独立内核已热更新至最新版');
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '🔄 热更新 yt-dlp';
    }
  });

  // Boot load
  await loadSystemAndSettings();
  const initialTasks = await apiFetch('/api/tasks').catch(() => ({ tasks: [] }));
  (initialTasks.tasks || []).forEach((t) => state.tasks.set(t.id, t));
  renderAllTaskViews();
  connectWebSocket();
});
