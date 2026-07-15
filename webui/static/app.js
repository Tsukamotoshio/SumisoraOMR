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
const PAGES = ['score', 'audio', 'jianpu', 'staff', 'transpose'];
let activePage = 'score';
const pageEnterHooks = {};   // page → () => void（进入页面时触发，如刷新列表）

function showPage(name) {
  activePage = name;
  for (const p of PAGES) $(`page-${p}`).classList.toggle('hidden', name !== p);
  // 移调页是五线谱预览的子流程，导航栏保持五线谱高亮
  const navName = name === 'transpose' ? 'staff' : name;
  document.querySelectorAll('.nav').forEach((x) => x.removeAttribute('aria-current'));
  const nav = document.querySelector(`.nav[data-page='${navName}']`);
  if (nav) nav.setAttribute('aria-current', 'true');
  if (pageEnterHooks[name]) pageEnterHooks[name]();
}
document.querySelectorAll('.nav[data-page]').forEach((btn) => {
  btn.addEventListener('click', () => showPage(btn.dataset.page));
});

// ═══ Toast ═══════════════════════════════════════════════════════════════════
const toastEl = document.createElement('div');
toastEl.className = 'toast';
document.body.appendChild(toastEl);
let toastTimer = null;
function toast(text) {
  toastEl.textContent = text;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2600);
}

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

// ═══ pdf.js 查看器 ═══════════════════════════════════════════════════════════
// 本地捆绑 pdf.js（vendor/pdfjs，v6），worker 同源加载；渲染按 devicePixelRatio
// 放大保证清晰度。fit = 适应舞台宽度。
let _pdfjs = null;
async function pdfjsLib() {
  if (_pdfjs) return _pdfjs;
  _pdfjs = await import('./vendor/pdfjs/pdf.min.mjs');
  _pdfjs.GlobalWorkerOptions.workerSrc = './vendor/pdfjs/pdf.worker.min.mjs';
  return _pdfjs;
}

class PdfView {
  constructor(canvas, stage, pageInfoEl) {
    this.canvas = canvas;
    this.stage = stage;
    this.pageInfoEl = pageInfoEl;
    this.doc = null;
    this.pageNo = 1;
    this.scale = null;      // null = fit-width
    this._renderToken = 0;
  }

  async open(url) {
    const lib = await pdfjsLib();
    if (this.doc) { try { this.doc.destroy(); } catch (_e) {} }
    this.doc = await lib.getDocument({ url }).promise;
    this.pageNo = 1;
    this.scale = null;
    await this.render();
  }

  close() {
    if (this.doc) { try { this.doc.destroy(); } catch (_e) {} this.doc = null; }
    this.canvas.classList.add('hidden');
    if (this.pageInfoEl) this.pageInfoEl.textContent = '';
  }

  _fitScale(page) {
    const avail = this.stage.clientWidth - 36;
    return avail > 0 ? avail / page.getViewport({ scale: 1 }).width : 1;
  }

  async render() {
    if (!this.doc) return;
    const token = ++this._renderToken;
    const page = await this.doc.getPage(this.pageNo);
    if (token !== this._renderToken) return;
    const scale = this.scale ?? this._fitScale(page);
    const dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr });
    this.canvas.width = vp.width;
    this.canvas.height = vp.height;
    this.canvas.style.width = `${vp.width / dpr}px`;
    this.canvas.style.height = `${vp.height / dpr}px`;
    this.canvas.classList.remove('hidden');
    await page.render({ canvas: this.canvas, viewport: vp }).promise;
    if (this.pageInfoEl) this.pageInfoEl.textContent = `第 ${this.pageNo} / ${this.doc.numPages} 页`;
  }

  prev() { if (this.doc && this.pageNo > 1) { this.pageNo -= 1; this.render(); } }
  next() { if (this.doc && this.pageNo < this.doc.numPages) { this.pageNo += 1; this.render(); } }
  async zoom(f) {
    if (!this.doc) return;
    if (this.scale === null) {
      const page = await this.doc.getPage(this.pageNo);
      this.scale = this._fitScale(page);
    }
    this.scale = Math.min(6, Math.max(0.2, this.scale * f));
    this.render();
  }
  zoomFit() { this.scale = null; this.render(); }
}

// ═══ 简谱预览页 ══════════════════════════════════════════════════════════════
const jpView = new PdfView($('jp-canvas'), $('jp-stage'), $('jp-pageinfo'));
let jpEntries = [];
let jpChecked = new Set();
let jpSel = null;

async function jpRefresh() {
  jpEntries = await api().outputs_list_jianpu() || [];
  jpChecked = new Set([...jpChecked].filter((p) => jpEntries.some((e) => e.path === p)));
  if (!jpEntries.some((e) => e.path === jpSel)) jpSel = jpEntries.length ? jpEntries[0].path : null;
  jpRenderList();
  jpOpenSelected();
}
pageEnterHooks.jianpu = jpRefresh;

function jpRenderList() {
  const list = $('jp-files');
  list.replaceChildren();
  if (!jpEntries.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'Output/ 中还没有简谱 PDF';
    list.appendChild(li);
  }
  for (const e of jpEntries) {
    const li = document.createElement('li');
    li.className = 'file' + (e.path === jpSel ? ' sel' : '');
    const cb = document.createElement('button');
    cb.className = 'cbx' + (jpChecked.has(e.path) ? ' on' : '');
    cb.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l4 4 10-10" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    cb.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (jpChecked.has(e.path)) jpChecked.delete(e.path); else jpChecked.add(e.path);
      jpRenderList();
    });
    const nm = document.createElement('span');
    nm.className = 'nm';
    nm.textContent = e.name;
    nm.title = e.path;
    li.append(cb, nm);
    li.addEventListener('click', () => { jpSel = e.path; jpRenderList(); jpOpenSelected(); });
    list.appendChild(li);
  }
  $('jp-count').textContent = jpEntries.length ? `勾选 ${jpChecked.size} / ${jpEntries.length}` : '';
  const cur = jpEntries.find((e) => e.path === jpSel);
  $('jp-midi').disabled = !(cur && cur.has_midi);
  $('jp-rerender').disabled = !(cur && cur.has_txt);
}

function jpOpenSelected() {
  const cur = jpEntries.find((e) => e.path === jpSel);
  $('jp-preview-name').textContent = cur ? cur.name : '';
  const ph = $('jp-stage').querySelector('.placeholder');
  if (!cur) {
    jpView.close();
    if (ph) ph.classList.remove('hidden');
    return;
  }
  if (ph) ph.classList.add('hidden');
  jpView.open(`/file?path=${encodeURIComponent(cur.path)}`).catch((e) => toast(`PDF 打开失败：${e}`));
}

$('jp-refresh').addEventListener('click', jpRefresh);
$('jp-selall').addEventListener('click', () => {
  jpChecked = jpChecked.size === jpEntries.length
    ? new Set() : new Set(jpEntries.map((e) => e.path));
  jpRenderList();
});
$('jp-export').addEventListener('click', async () => {
  if (!jpChecked.size) { toast('先勾选要导出的文件'); return; }
  const r = await api().outputs_export([...jpChecked]);
  if (r.ok) toast(`已导出 ${r.copied.length} 个文件到 ${r.dest}`);
  else if (r.error !== 'cancelled') toast(`导出失败：${r.error || (r.failed && r.failed.length) + ' 个文件失败'}`);
});
$('jp-delete').addEventListener('click', async () => {
  if (!jpChecked.size) { toast('先勾选要删除的文件'); return; }
  if (!confirm(`删除勾选的 ${jpChecked.size} 个简谱（连同 MIDI 与编辑文本）？`)) return;
  await api().outputs_delete([...jpChecked]);
  jpChecked.clear();
  jpRefresh();
});
$('jp-midi').addEventListener('click', async () => {
  if (!jpSel) return;
  const r = await api().outputs_play_midi(jpSel);
  if (!r.ok) toast(r.error === 'not_found' ? `未找到 ${r.name}` : `打开失败：${r.error}`);
});
$('jp-rerender').addEventListener('click', async () => {
  if (!jpSel) return;
  const r = await api().outputs_rerender(jpSel);
  if (r.started) toast('正在从简谱文本重新渲染…');
  else toast(r.error === 'no_txt' ? `未找到 ${r.name}` : `无法重渲：${r.error}`);
});
window.addEventListener('rerender_done', (e) => {
  const d = e.detail || {};
  if (d.ok) {
    toast('重新渲染完成');
    if (d.path === jpSel) jpOpenSelected();
  } else {
    toast(`重新渲染失败：${d.error}`);
  }
});
$('jp-prev').addEventListener('click', () => jpView.prev());
$('jp-next').addEventListener('click', () => jpView.next());
$('jp-zoomin').addEventListener('click', () => jpView.zoom(1.2));
$('jp-zoomout').addEventListener('click', () => jpView.zoom(1 / 1.2));
$('jp-zoomfit').addEventListener('click', () => jpView.zoomFit());

// ═══ 五线谱预览页 ════════════════════════════════════════════════════════════
// 与简谱页同构；差异：预览需按需 LilyPond 渲染（scores_preview 可能异步，等
// score_preview_ready 事件），MIDI 缺失时确认后生成再播放，多一个「移调」入口。
const stView = new PdfView($('st-canvas'), $('st-stage'), $('st-pageinfo'));
let stEntries = [];
let stChecked = new Set();
let stSel = null;
let stPendingRender = null;   // 等待 score_preview_ready 的 mxl 路径

async function stRefresh() {
  stEntries = await api().scores_list() || [];
  stChecked = new Set([...stChecked].filter((p) => stEntries.some((e) => e.path === p)));
  if (!stEntries.some((e) => e.path === stSel)) stSel = stEntries.length ? stEntries[0].path : null;
  stRenderList();
  stOpenSelected();
}
pageEnterHooks.staff = stRefresh;

function stRenderList() {
  const list = $('st-files');
  list.replaceChildren();
  if (!stEntries.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'xml-scores/ 中还没有 MusicXML';
    list.appendChild(li);
  }
  for (const e of stEntries) {
    const li = document.createElement('li');
    li.className = 'file' + (e.path === stSel ? ' sel' : '');
    const cb = document.createElement('button');
    cb.className = 'cbx' + (stChecked.has(e.path) ? ' on' : '');
    cb.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l4 4 10-10" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    cb.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (stChecked.has(e.path)) stChecked.delete(e.path); else stChecked.add(e.path);
      stRenderList();
    });
    const nm = document.createElement('span');
    nm.className = 'nm';
    nm.textContent = e.name;
    nm.title = e.path;
    li.append(cb, nm);
    li.addEventListener('click', () => { stSel = e.path; stRenderList(); stOpenSelected(); });
    list.appendChild(li);
  }
  $('st-count').textContent = stEntries.length ? `勾选 ${stChecked.size} / ${stEntries.length}` : '';
}

async function stOpenSelected() {
  const cur = stEntries.find((e) => e.path === stSel);
  $('st-preview-name').textContent = cur ? cur.name : '';
  const ph = $('st-stage').querySelector('.placeholder');
  if (!cur) {
    stView.close();
    if (ph) { ph.classList.remove('hidden'); $('st-placeholder-text').textContent = '选中左侧文件预览五线谱'; }
    return;
  }
  const r = await api().scores_preview(cur.path);
  if (r.pdf) {
    if (ph) ph.classList.add('hidden');
    stPendingRender = null;
    stView.open(`/file?path=${encodeURIComponent(r.pdf)}`).catch((e2) => toast(`PDF 打开失败：${e2}`));
  } else if (r.started) {
    stPendingRender = cur.path;
    stView.close();
    if (ph) { ph.classList.remove('hidden'); $('st-placeholder-text').textContent = `正在渲染 ${cur.name} …（LilyPond）`; }
  } else {
    toast(`无法预览：${r.error || '未知错误'}`);
  }
}

window.addEventListener('score_preview_ready', (e) => {
  const d = e.detail || {};
  if (d.mxl !== stPendingRender) return;   // 已切换到其它文件，丢弃
  stPendingRender = null;
  if (!d.ok) {
    $('st-placeholder-text').textContent = `渲染失败：${d.error || ''}`;
    return;
  }
  const ph = $('st-stage').querySelector('.placeholder');
  if (ph) ph.classList.add('hidden');
  stView.open(`/file?path=${encodeURIComponent(d.pdf)}`).catch((e2) => toast(`PDF 打开失败：${e2}`));
});

$('st-refresh').addEventListener('click', stRefresh);
$('st-selall').addEventListener('click', () => {
  stChecked = stChecked.size === stEntries.length
    ? new Set() : new Set(stEntries.map((e) => e.path));
  stRenderList();
});
$('st-export').addEventListener('click', async () => {
  if (!stChecked.size) { toast('先勾选要导出的文件'); return; }
  toast('正在导出（未缓存的将逐个渲染，请稍候）…');
  const r = await api().scores_export([...stChecked]);
  if (r.ok) toast(`已导出 ${r.copied.length} 个五线谱 PDF 到 ${r.dest}`);
  else if (r.error !== 'cancelled') toast(`导出失败：${r.error || (r.failed ? r.failed.length + ' 个文件失败' : '')}`);
});
$('st-delete').addEventListener('click', async () => {
  if (!stChecked.size) { toast('先勾选要删除的文件'); return; }
  if (!confirm(`删除勾选的 ${stChecked.size} 个 MusicXML？`)) return;
  await api().scores_delete([...stChecked]);
  stChecked.clear();
  stRefresh();
});
$('st-midi').addEventListener('click', async () => {
  if (!stSel) return;
  const info = await api().scores_midi_for(stSel);
  if (!info.exists && !confirm(`${info.name} 不存在。从当前乐谱生成 MIDI 并播放？`)) return;
  const r = await api().scores_generate_play_midi(stSel);
  if (r.started && !info.exists) toast('正在生成 MIDI…');
});
window.addEventListener('score_midi_done', (e) => {
  const d = e.detail || {};
  if (!d.ok) toast(`MIDI 生成/播放失败：${d.error || ''}`);
});
$('st-transpose').addEventListener('click', () => {
  if (!stSel) { toast('先选中一个乐谱'); return; }
  showPage('transpose');
  tpLoad(stSel);
});
$('st-prev').addEventListener('click', () => stView.prev());
$('st-next').addEventListener('click', () => stView.next());
$('st-zoomin').addEventListener('click', () => stView.zoom(1.2));
$('st-zoomout').addEventListener('click', () => stView.zoom(1 / 1.2));
$('st-zoomfit').addEventListener('click', () => stView.zoomFit());

// ═══ 移调页 ══════════════════════════════════════════════════════════════════
// 三种模式（按调/按音程/按度数）分发到 core.notation.transposer；原调/移调后
// 双栏对比预览；导出 = 渲染五线谱 PDF 另存。从五线谱预览页的「移调」按钮进入。
const tpOrigView = new PdfView($('tp-orig-canvas'), $('tp-orig-stage'), $('tp-orig-pageinfo'));
const tpTransView = new PdfView($('tp-trans-canvas'), $('tp-trans-stage'), $('tp-trans-pageinfo'));
let tpOptionsLoaded = false;
let tpBusy = false;

async function tpEnsureOptions() {
  if (tpOptionsLoaded) return;
  const o = await api().transpose_options();
  const fill = (sel, items, useObj) => {
    sel.replaceChildren();
    for (const it of items || []) {
      const op = document.createElement('option');
      op.value = useObj ? it.value : it;
      op.textContent = useObj ? it.label : it;
      sel.appendChild(op);
    }
  };
  fill($('tp-from'), o.keys, true);
  fill($('tp-to'), o.keys, true);
  fill($('tp-interval'), o.intervals, false);
  fill($('tp-degree'), o.degrees, false);
  $('tp-from').value = 'C';
  $('tp-to').value = 'C';
  $('tp-interval').value = '大二度';
  tpOptionsLoaded = true;
}

function tpSetPlaceholder(which, text) {
  const ph = $(`tp-${which}-stage`).querySelector('.placeholder');
  if (text === null) { if (ph) ph.classList.add('hidden'); return; }
  if (ph) { ph.classList.remove('hidden'); $(`tp-${which}-ph`).textContent = text; }
}

async function tpLoad(path) {
  await tpEnsureOptions();
  tpTransView.close();
  tpSetPlaceholder('trans', '移调后在此预览');
  $('tp-export-trans').disabled = true;
  $('tp-progress').style.width = '0%';
  const r = await api().transpose_load(path);
  if (!r.ok) { toast(`打开失败：${r.error || ''}`); return; }
  $('tp-name').textContent = r.name;
  $('tp-from').value = r.key;
  $('tp-status').textContent = `检测到原调：${r.key_cn}`;
  tpRequestPreview('orig');
}

async function tpRequestPreview(which) {
  const r = await api().transpose_preview(which);
  if (r.pdf) tpShowPreview(which, r.pdf);
  else if (r.started) tpSetPlaceholder(which, '正在渲染…（LilyPond）');
  else tpSetPlaceholder(which, `无法预览：${r.error || ''}`);
}

function tpShowPreview(which, pdf) {
  tpSetPlaceholder(which, null);
  const view = which === 'orig' ? tpOrigView : tpTransView;
  view.open(`/file?path=${encodeURIComponent(pdf)}`).catch((e) => toast(`PDF 打开失败：${e}`));
}

window.addEventListener('transpose_preview_ready', (e) => {
  const d = e.detail || {};
  if (d.ok) tpShowPreview(d.which, d.pdf);
  else tpSetPlaceholder(d.which, `渲染失败：${d.error || ''}`);
});

// 模式切换：显示对应字段组；方向选项按模式调整（按调支持「就近」）
$('tp-mode').addEventListener('change', () => {
  const m = $('tp-mode').value;
  document.querySelectorAll('.tp-key').forEach((el) => el.classList.toggle('hidden', m !== 'key'));
  document.querySelector('.tp-interval').classList.toggle('hidden', m !== 'interval');
  document.querySelector('.tp-diatonic').classList.toggle('hidden', m !== 'diatonic');
  document.querySelector('.tp-keysig').classList.toggle('hidden', m === 'diatonic');
  const dir = $('tp-dir');
  const hasClosest = m === 'key';
  const cur = dir.value;
  dir.replaceChildren();
  const opts = hasClosest ? [['closest', '就近'], ['up', '向上'], ['down', '向下']]
                          : [['up', '向上'], ['down', '向下']];
  for (const [v, label] of opts) {
    const op = document.createElement('option');
    op.value = v;
    op.textContent = label;
    dir.appendChild(op);
  }
  dir.value = opts.some(([v]) => v === cur) ? cur : opts[0][0];
});

$('tp-keysig').addEventListener('click', (e) => {
  e.preventDefault();
  const b = $('tp-keysig');
  b.classList.toggle('on');
  b.setAttribute('aria-checked', b.classList.contains('on') ? 'true' : 'false');
});

$('tp-detect').addEventListener('click', async (e) => {
  e.preventDefault();
  if (!$('tp-name').textContent) return;
  // 重新 load 即重新检测（load 幂等，且会刷新原谱预览缓存判断）
  const cur = stEntries.find((x) => x.name === $('tp-name').textContent);
  if (cur) tpLoad(cur.path);
});

$('tp-run').addEventListener('click', async () => {
  if (tpBusy) return;
  const mode = $('tp-mode').value;
  const params = {
    direction: $('tp-dir').value,
    keysig: $('tp-keysig').classList.contains('on'),
    from_key: $('tp-from').value,
    to_key: $('tp-to').value,
    interval: $('tp-interval').value,
    degree: $('tp-degree').value,
  };
  const r = await api().transpose_run(mode, params);
  if (!r.ok) { toast(r.error === 'no_file' ? '先载入乐谱' : `无法移调：${r.error || ''}`); return; }
  tpBusy = true;
  $('tp-run').disabled = true;
  $('tp-status').textContent = '移调中…';
  $('tp-progress').style.width = '0%';
});

window.addEventListener('transpose_progress', (e) => {
  $('tp-progress').style.width = `${Math.round(((e.detail && e.detail.value) || 0) * 100)}%`;
});
window.addEventListener('transpose_done', (e) => {
  const d = e.detail || {};
  tpBusy = false;
  $('tp-run').disabled = false;
  if (!d.ok) {
    $('tp-status').textContent = `移调失败：${d.error || ''}`;
    toast(`移调失败：${d.error || ''}`);
    return;
  }
  $('tp-progress').style.width = '100%';
  $('tp-status').textContent = `完成：${d.name}`;
  $('tp-export-trans').disabled = false;
  tpSetPlaceholder('trans', '正在渲染移调结果…（LilyPond）');
  tpRequestPreview('trans');
});

$('tp-back').addEventListener('click', () => showPage('staff'));
for (const [id, which] of [['tp-export-orig', 'orig'], ['tp-export-trans', 'trans']]) {
  $(id).addEventListener('click', async () => {
    const r = await api().transpose_export(which);
    if (r.ok) toast(`已导出：${r.dest}`);
    else if (r.error !== 'cancelled') toast(`导出失败：${r.error || ''}`);
  });
}
for (const [pfx, view] of [['tp-orig', tpOrigView], ['tp-trans', tpTransView]]) {
  $(`${pfx}-prev`).addEventListener('click', () => view.prev());
  $(`${pfx}-next`).addEventListener('click', () => view.next());
  $(`${pfx}-zoomin`).addEventListener('click', () => view.zoom(1.2));
  $(`${pfx}-zoomout`).addEventListener('click', () => view.zoom(1 / 1.2));
  $(`${pfx}-zoomfit`).addEventListener('click', () => view.zoomFit());
}

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
