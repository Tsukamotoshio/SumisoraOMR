// webui app.js — 正式 UI 逻辑（M2）：导航/主题、分视图文件托盘、转换流程、
// 进度浮层、结果弹层、模型下载。Python 是唯一状态源，本文件只渲染 + 转发操作。
'use strict';

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

// gate/自动化驱动可读的 UI 状态镜像（与 harness 同名约定）
window.__uiFlags = { busy: false, lastError: null, statusText: '', progressEvents: 0, summary: null };

// ═══ 批量事件接收（EventPusher → CustomEvent） ═══════════════════════════════
window.__omrEvents = (events) => {
  for (const ev of events || []) {
    window.dispatchEvent(new CustomEvent(ev.name, { detail: ev.payload }));
  }
};

// ═══ 主题 ════════════════════════════════════════════════════════════════════
(function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
  $('themeBtn').addEventListener('click', () => {
    const root = document.documentElement;
    let cur = root.dataset.theme;
    if (!cur) cur = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const next = cur === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('theme', next);
  });
})();

// ═══ 导航 ════════════════════════════════════════════════════════════════════
let activePage = 'score';
document.querySelectorAll('.nav[data-page]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav').forEach((x) => x.removeAttribute('aria-current'));
    btn.setAttribute('aria-current', 'true');
    activePage = btn.dataset.page;
    $('page-score').classList.toggle('hidden', activePage !== 'score');
    $('page-audio').classList.toggle('hidden', activePage !== 'audio');
  });
});

// ═══ 标题栏 ══════════════════════════════════════════════════════════════════
$('btn-min').addEventListener('click', () => api().window_minimize());
$('btn-max').addEventListener('click', () => api().window_toggle_maximize());
$('btn-close').addEventListener('click', () => api().window_close());
document.querySelector('.titlebar').addEventListener('dblclick', (e) => {
  if (e.target.closest('.wc') || e.target.closest('.iconbtn')) return;
  api().window_toggle_maximize();
});

// ═══ 文件托盘（分视图渲染；Python 端共享一份托盘） ═══════════════════════════
const VIEW = {
  score: { list: 'score-files', count: 'score-count', sel: null,
           empty: '拖入 PDF / PNG / JPG，或点「添加文件」' },
  audio: { list: 'audio-files', count: 'audio-count', sel: null,
           empty: '拖入 MP3 / WAV / FLAC / OGG，或点「添加文件」' },
};

const ICONS = {
  pdf: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 13h6M9 17h4" stroke-linecap="round"/>',
  img: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10.5" r="1.6"/><path d="M4 17l5-4 4 3 3-2 4 3" stroke-linecap="round" stroke-linejoin="round"/>',
  audio: '<path d="M4 12v0M8 8v8M12 5v14M16 9v6M20 12v0" stroke-linecap="round"/>',
};
function iconFor(f) {
  if (f.kind === 'audio') return ICONS.audio;
  return f.name.toLowerCase().endsWith('.pdf') ? ICONS.pdf : ICONS.img;
}

let trayCache = [];

function renderView(view) {
  const cfg = VIEW[view];
  const listEl = $(cfg.list);
  const files = trayCache.filter((f) => f.kind === view);
  listEl.replaceChildren();
  if (!files.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = cfg.empty;
    listEl.appendChild(li);
    cfg.sel = null;
    updatePreview(view, null);
  } else {
    if (!files.some((f) => f.path === cfg.sel)) cfg.sel = files[0].path;
    for (const f of files) {
      const li = document.createElement('li');
      li.className = 'file' + (f.path === cfg.sel ? ' sel' : '');
      const cb = document.createElement('button');
      cb.className = 'cbx' + (f.checked ? ' on' : '');
      cb.setAttribute('role', 'checkbox');
      cb.setAttribute('aria-checked', String(f.checked));
      cb.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l4 4 10-10" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      cb.addEventListener('click', (e) => { e.stopPropagation(); api().files_toggle_check(f.path); });
      const ic = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      ic.setAttribute('class', 'ic');
      ic.setAttribute('viewBox', '0 0 24 24');
      ic.setAttribute('fill', 'none');
      ic.setAttribute('stroke', 'currentColor');
      ic.setAttribute('stroke-width', '1.6');
      ic.innerHTML = iconFor(f);
      const nm = document.createElement('span');
      nm.className = 'nm';
      nm.textContent = f.name;
      nm.title = f.path;
      const x = document.createElement('button');
      x.className = 'x';
      x.setAttribute('aria-label', '移除');
      x.innerHTML = '<svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.6" fill="none"><path d="M7 7l10 10M17 7L7 17" stroke-linecap="round"/></svg>';
      x.addEventListener('click', (e) => { e.stopPropagation(); api().files_remove(f.path); });
      li.append(cb, ic, nm, x);
      li.addEventListener('click', () => { cfg.sel = f.path; renderView(view); });
      listEl.appendChild(li);
    }
    updatePreview(view, files.find((f) => f.path === cfg.sel) || null);
  }
  const checked = files.filter((f) => f.checked).length;
  $(cfg.count).textContent = `已选 ${checked} / ${files.length}`;
}

function updatePreview(view, file) {
  const stage = $(view === 'score' ? 'score-stage' : 'audio-stage');
  const nameEl = $(view === 'score' ? 'score-preview-name' : 'audio-preview-name');
  nameEl.textContent = file ? file.name : '';
  const url = file ? `/file?path=${encodeURIComponent(file.path)}` : null;
  stage.replaceChildren();
  if (!file) {
    stage.innerHTML = view === 'score'
      ? '<div class="placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10.5" r="1.6"/><path d="M4 17l5-4 4 3 3-2 4 3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>选中文件后在此预览<br>（PDF 预览将在 M3 接入 pdf.js）</span></div>'
      : '<div class="placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 12v0M8 8v8M12 5v14M16 9v6M20 12v0"/></svg><span>选中音频后在此试听<br>（识别引擎仅支持钢琴独奏）</span></div>';
    return;
  }
  if (view === 'audio') {
    const au = document.createElement('audio');
    au.controls = true;
    au.src = url;
    stage.appendChild(au);
  } else if (/\.(png|jpe?g)$/i.test(file.name)) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = file.name;
    stage.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.className = 'placeholder';
    ph.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 13h6M9 17h4" stroke-linecap="round"/></svg>';
    const span = document.createElement('span');
    span.textContent = `${file.name} — PDF 预览将在 M3 接入 pdf.js`;
    ph.appendChild(span);
    stage.appendChild(ph);
  }
}

window.addEventListener('files_changed', (e) => {
  trayCache = (e.detail && e.detail.files) || [];
  renderView('score');
  renderView('audio');
});

$('score-add').addEventListener('click', () => api().shell_pick_files());
$('audio-add').addEventListener('click', () => api().shell_pick_files());

// ═══ 转换流程 + 进度浮层 ═════════════════════════════════════════════════════
let cancelling = false;
let converting = false;

function showOverlay(title) {
  $('prog-title').textContent = title;
  $('prog-msg').textContent = '';
  $('prog-submsg').textContent = '';
  $('prog-main').style.width = '0%';
  $('prog-sub').style.width = '0%';
  $('prog-log').replaceChildren();
  $('progress-overlay').classList.remove('hidden');
}
function hideOverlay() { $('progress-overlay').classList.add('hidden'); }

async function startConvert(view) {
  cancelling = false;
  const opts = { view };
  if (view === 'score') {
    opts.engine = $('score-engine').value;
    opts.parallel = parseInt($('score-parallel').value, 10);
  } else {
    opts.engine = 'auto';
    opts.melody_only = $('audio-melody').classList.contains('on');
  }
  const r = await api().convert_start(opts);
  if (!r.ok) {
    if (r.error === 'no_files') alert('没有勾选的文件。');
    else if (r.error === 'busy') alert('已有转换在进行中。');
    return;
  }
  converting = true;
  window.__uiFlags.busy = true;
  showOverlay(view === 'score' ? `正在识别乐谱（${r.count} 个文件）` : `正在识别音频（${r.count} 个文件）`);
}
$('score-start').addEventListener('click', () => startConvert('score'));
$('audio-start').addEventListener('click', () => startConvert('audio'));
$('prog-cancel').addEventListener('click', async () => {
  cancelling = true;
  $('prog-msg').textContent = '取消中…（正在终止 worker）';
  await api().convert_cancel();
});

for (const id of ['score-outdir', 'audio-outdir', 'result-outdir']) {
  $(id).addEventListener('click', () => api().shell_open_output_dir());
}

// 仅主旋律复选
$('audio-melody').addEventListener('click', (e) => {
  e.preventDefault();
  const b = $('audio-melody');
  b.classList.toggle('on');
  b.setAttribute('aria-checked', b.classList.contains('on') ? 'true' : 'false');
});

window.addEventListener('progress_update', (e) => {
  window.__uiFlags.progressEvents += 1;
  $('prog-main').style.width = `${Math.round(((e.detail && e.detail.value) || 0) * 100)}%`;
  if (e.detail && e.detail.message) $('prog-msg').textContent = e.detail.message;
});
window.addEventListener('sub_progress', (e) => {
  window.__uiFlags.progressEvents += 1;
  $('prog-sub').style.width = `${Math.round(((e.detail && e.detail.value) || 0) * 100)}%`;
  $('prog-submsg').textContent = (e.detail && e.detail.message) || '';
});
window.addEventListener('log_line', (e) => {
  const el = $('prog-log');
  const div = document.createElement('div');
  div.textContent = (e.detail && e.detail.line) || '';
  el.appendChild(div);
  while (el.childNodes.length > 60) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
});

window.addEventListener('progress_error', (e) => {
  window.__uiFlags.lastError = (e.detail && e.detail.message) || '';
  window.__uiFlags.busy = false;
  if (!converting) return;
  converting = false;
  hideOverlay();
  if (cancelling) return; // 取消确认，不弹错误
  showResult({ error: (e.detail && e.detail.message) || '未知错误' });
});
window.addEventListener('conversion_finished', (e) => {
  window.__uiFlags.busy = false;
  const s = (e.detail && e.detail.summary) || {};
  window.__uiFlags.summary = s;
  if (!converting) return;
  converting = false;
  hideOverlay();
  if (cancelling) return;
  if (s.total !== undefined) showResult({ summary: s });
});

// ═══ 结果弹层 ════════════════════════════════════════════════════════════════
function showResult({ summary, error }) {
  const pills = $('result-pills');
  const list = $('result-list');
  pills.replaceChildren();
  list.replaceChildren();
  if (error) {
    $('result-title').textContent = '识别失败';
    const item = document.createElement('div');
    item.className = 'result-item';
    item.innerHTML = '<span class="tag bad">✗</span>';
    const why = document.createElement('span');
    why.className = 'why';
    why.textContent = error;
    item.appendChild(why);
    list.appendChild(item);
  } else {
    $('result-title').textContent = '识别结果';
    const mk = (cls, label, n) => {
      // 全 textContent 构建；n 强制数值化（summary 来自 Python，防御性处理）
      const p = document.createElement('span');
      p.className = `pill ${cls}`;
      p.append(label + ' ');
      const b = document.createElement('b');
      b.textContent = String(Number(n) || 0);
      p.appendChild(b);
      pills.appendChild(p);
    };
    mk('ok', '成功', summary.success_count || 0);
    if (summary.fallback_count) mk('warn', '引擎回退', summary.fallback_count);
    mk(summary.failed_count ? 'bad' : '', '失败', summary.failed_count || 0);
    const addItem = (tagCls, tagTxt, fileName, why) => {
      const item = document.createElement('div');
      item.className = 'result-item';
      const tag = document.createElement('span');
      tag.className = `tag ${tagCls}`;
      tag.textContent = tagTxt;
      const fn = document.createElement('span');
      fn.className = 'fn';
      fn.textContent = fileName;
      item.append(tag, fn);
      if (why) {
        const w = document.createElement('span');
        w.className = 'why';
        w.textContent = why;
        item.appendChild(w);
      }
      list.appendChild(item);
    };
    for (const f of summary.success_files || []) addItem('ok', '✓', f.file, f.engine_used ? `引擎 ${f.engine_used}` : '');
    for (const f of summary.fallback_files || []) addItem('warn', '↻', f.file, `引擎回退 ${f.engine_used || ''}`);
    for (const f of summary.failed_files || []) addItem('bad', '✗', f.file, f.reason || '');
  }
  $('result-overlay').classList.remove('hidden');
}
$('result-close').addEventListener('click', () => $('result-overlay').classList.add('hidden'));

// ═══ 模型状态 + 下载弹层 ═════════════════════════════════════════════════════
let modelKind = null;

function renderModels(st) {
  if (!st || !st.homr) return;
  const homr = $('homr-status');
  homr.classList.toggle('absent', !st.homr.available);
  $('homr-status-text').textContent = st.homr.available
    ? 'OMR 引擎 “Homr” · 已就绪'
    : `未就绪（${st.homr.files_present}/${st.homr.files_total} 个权重）`;
  $('homr-download').disabled = st.homr.available;
  $('homr-delete').disabled = !st.homr.files_present;
  const piano = $('piano-status');
  piano.classList.toggle('absent', !st.piano.available);
  $('piano-status-text').textContent = st.piano.available ? '钢琴转录模型 · 已就绪' : '未下载（约 172 MB，按需）';
  $('piano-download').disabled = st.piano.available;
  $('piano-delete').disabled = !st.piano.available;
}

async function refreshModels() { renderModels(await api().models_status()); }

function startModelDownload(kind, title) {
  modelKind = kind;
  $('model-title').textContent = title;
  $('model-msg').textContent = '连接中…';
  $('model-bar').style.width = '0%';
  $('model-overlay').classList.remove('hidden');
  api().models_download(kind);
}
$('homr-download').addEventListener('click', () => startModelDownload('homr', '下载 HOMR 模型权重'));
$('piano-download').addEventListener('click', () => startModelDownload('piano', '下载钢琴转录模型（约 172 MB）'));
$('model-cancel').addEventListener('click', () => { if (modelKind) api().models_cancel_download(modelKind); });
$('homr-delete').addEventListener('click', async () => {
  if (confirm('删除 HOMR 模型权重？删除后图片识别将不可用，可随时重新下载。')) await api().models_delete('homr');
});
$('piano-delete').addEventListener('click', async () => {
  if (confirm('删除钢琴转录模型？可随时重新下载。')) await api().models_delete('piano');
});

window.addEventListener('model_download_progress', (e) => {
  const d = e.detail || {};
  if (d.kind !== modelKind) return;
  $('model-bar').style.width = `${Math.round((d.value || 0) * 100)}%`;
  $('model-msg').textContent = d.message || '';
});
window.addEventListener('model_download_done', (e) => {
  const d = e.detail || {};
  if (d.kind !== modelKind) return;
  $('model-overlay').classList.add('hidden');
  modelKind = null;
  if (!d.ok && d.error !== 'cancelled') alert(`模型下载失败：${d.error}`);
});
window.addEventListener('models_changed', (e) => renderModels(e.detail && e.detail.status));

// ═══ 初始化 ══════════════════════════════════════════════════════════════════
window.addEventListener('pywebviewready', async () => {
  api().app_info().then((info) => { $('ver').textContent = 'v' + (info.version || ''); });
  api().files_list().then((files) => {
    trayCache = files || [];
    renderView('score');
    renderView('audio');
  });
  refreshModels();
});
