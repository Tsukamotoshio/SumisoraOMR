// webui app.js — 正式 UI 逻辑（M2）：导航/主题、分视图文件托盘、转换流程、
// 进度浮层、结果弹层、模型下载。Python 是唯一状态源，本文件只渲染 + 转发操作。
'use strict';

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

// ═══ i18n ════════════════════════════════════════════════════════════════════
// 目录由 Python 下发（gui/strings.py + webui/i18n.py 合并，Python 为唯一事实源）。
// 静态文本：data-i18n / data-i18n-title 属性，retranslate() 统一刷；
// 动态文本：JS 里一律 t(key, params)。
const I18N = { lang: 'zh', strings: {} };
function t(key, params) {
  const entry = I18N.strings[key];
  let s = entry ? (entry[I18N.lang] || entry.zh || key) : key;
  if (params) for (const [k, v] of Object.entries(params)) s = s.split(`{${k}}`).join(String(v));
  return s;
}
function retranslate() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  document.documentElement.lang = I18N.lang === 'zh' ? 'zh-CN' : 'en';
}

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
const PAGES = ['score', 'audio', 'jianpu', 'staff', 'transpose', 'about', 'editor', 'notedigger'];
let activePage = 'score';
const pageEnterHooks = {};   // page → () => void（进入页面时触发，如刷新列表）

function showPage(name) {
  // 离开预览页时停止并收起内置 MIDI 播放器（避免切页后音频继续）
  if (typeof midiPlayer !== 'undefined' && name !== activePage) midiPlayer.leave();
  // 离开 noteDigger 页时停止其音频/MIDI 播放（CSS 隐藏 iframe 不触发它自带的
  // document.hidden 停止逻辑，否则跳走后声音还在放，用户会慌）
  if (activePage === 'notedigger' && name !== 'notedigger') ndStopPlayback();
  activePage = name;
  for (const p of PAGES) $(`page-${p}`).classList.toggle('hidden', name !== p);
  // 移调/编辑/高级修正是各自父页的子流程，导航栏保持父页高亮
  const navName = name === 'transpose' ? 'staff'
    : (name === 'editor' ? 'jianpu' : (name === 'notedigger' ? 'audio' : name));
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

// ═══ 内置 MIDI 播放器（WebAudioTinySynth，自包含 GM 合成器） ══════════════════
// MIDI 不能直接喂 <audio>，需 JS 合成器。tinySynth 懒加载（首次播放才拉），单实例
// 单合成器；播放时把播放器 DOM 移入当前预览页的 .midislot（内嵌，不重复）。
const MP_ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  vol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 9v6h4l5 4V5L8 9H4z" stroke-linejoin="round"/><path d="M16 8.5a5 5 0 0 1 0 7" stroke-linecap="round"/></svg>',
};
let _synthLibPromise = null;
function loadSynthLib() {
  if (_synthLibPromise) return _synthLibPromise;
  _synthLibPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = './vendor/tinysynth/webaudio-tinysynth.js';
    s.onload = () => resolve(window.WebAudioTinySynth);
    s.onerror = () => reject(new Error('tinysynth load failed'));
    document.head.appendChild(s);
  });
  return _synthLibPromise;
}
class MidiPlayer {
  constructor() {
    this.synth = null;
    this.maxTick = 0;
    this.seeking = false;
    this._timer = 0;
    this.el = this._build();
  }
  _build() {
    const el = document.createElement('div');
    el.className = 'midiplayer';
    el.innerHTML = `
      <button class="mp-btn" data-mp="play" aria-label="播放/暂停">${MP_ICON.play}</button>
      <button class="mp-btn" data-mp="stop" aria-label="停止">${MP_ICON.stop}</button>
      <span class="mp-time" data-mp="cur">0:00</span>
      <input class="mp-seek" data-mp="seek" type="range" min="0" max="1000" value="0" aria-label="进度">
      <span class="mp-time" data-mp="dur">0:00</span>
      <span class="mp-volico">${MP_ICON.vol}</span>
      <input class="mp-vol" data-mp="vol" type="range" min="0" max="100" value="80" aria-label="音量">
      <span class="mp-name" data-mp="name"></span>`;
    const q = (k) => el.querySelector(`[data-mp="${k}"]`);
    this.$play = q('play'); this.$stop = q('stop'); this.$cur = q('cur');
    this.$seek = q('seek'); this.$dur = q('dur'); this.$vol = q('vol'); this.$name = q('name');
    this.$play.addEventListener('click', () => this.toggle());
    this.$stop.addEventListener('click', () => this.stop());
    this.$seek.addEventListener('input', () => { this.seeking = true; });
    this.$seek.addEventListener('change', () => { this._applySeek(); this.seeking = false; });
    this.$vol.addEventListener('input', () => { if (this.synth) this.synth.setMasterVol(this.$vol.value / 100); });
    return el;
  }
  async play(slot, url, name) {
    let Lib;
    try { Lib = await loadSynthLib(); } catch (_e) { toast(t('w.midi.synth_fail')); return; }
    if (!this.synth) { this.synth = new Lib(); this.synth.setMasterVol(this.$vol.value / 100); }
    slot.appendChild(this.el);
    this.$name.textContent = name || '';
    let buf;
    try { buf = await (await fetch(url)).arrayBuffer(); } catch (_e) { toast(t('w.midi.load_fail')); return; }
    this.synth.stopMIDI();
    this.synth.loadMIDI(new Uint8Array(buf));
    this.maxTick = this.synth.getPlayStatus().maxTick || 0;
    this.$dur.textContent = this._fmt(this.maxTick * this._k());
    this.synth.locateMIDI(0);
    this.synth.playMIDI();
    this._setIcon(true);
    this._ensureTimer();
  }
  toggle() {
    if (!this.synth) return;
    if (this.synth.getPlayStatus().play) { this.synth.stopMIDI(); this._setIcon(false); }
    else { this.synth.playMIDI(); this._setIcon(true); this._ensureTimer(); }
  }
  stop() {
    if (!this.synth) return;
    this.synth.stopMIDI();
    this.synth.locateMIDI(0);
    this._setIcon(false);
    this._tick();
  }
  leave() {   // 离开预览页：停止并收起
    if (this.synth) { this.synth.stopMIDI(); this.synth.locateMIDI(0); }
    this._setIcon(false);
    clearInterval(this._timer); this._timer = 0;
    if (this.el.parentElement) this.el.remove();
  }
  _applySeek() {
    if (!this.synth || !this.maxTick) return;
    this.synth.locateMIDI(Math.round(this.$seek.value / 1000 * this.maxTick));
    this._tick();
  }
  _ensureTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), 200);
  }
  _tick() {
    if (!this.synth) return;
    const st = this.synth.getPlayStatus();
    if (!this.seeking && this.maxTick) this.$seek.value = Math.round(st.curTick / this.maxTick * 1000);
    const k = this._k();
    this.$cur.textContent = this._fmt(st.curTick * k);
    this.$dur.textContent = this._fmt(this.maxTick * k);   // tempo 事件在 tick0，首帧后即稳定
    if (!st.play) { this._setIcon(false); clearInterval(this._timer); this._timer = 0; }
  }
  // tick→秒换算系数：4 拍 * 60 / timebase / tempo（与 tinySynth 内部一致）
  _k() { const s = this.synth && this.synth.song; return (s && s.timebase && s.tempo) ? 4 * 60 / s.timebase / s.tempo : 0; }
  _setIcon(playing) { this.$play.innerHTML = playing ? MP_ICON.pause : MP_ICON.play; }
  _fmt(sec) { sec = Math.max(0, Math.round(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
}
const midiPlayer = new MidiPlayer();

// ═══ 窗口边缘 resize 把手（frameless 窗口任意调整大小） ═══════════════════════
// pointerdown 记录起点 → pointermove 用屏幕位移调 Python 直接 resize（rAF 节流）。
(function initResizeGrips() {
  const DIRS = {
    n: 'top', s: 'bottom', e: 'right', w: 'left',
    ne: 'topright', nw: 'topleft', se: 'bottomright', sw: 'bottomleft',
  };
  let drag = null;      // {dir, sx, sy, pid}
  let pending = null;   // 待发送的最新 delta（rAF 合并）
  let rafId = 0;
  function flush() {
    rafId = 0;
    if (drag && pending) {
      window.pywebview.api.window_resize_edge(drag.dir, pending.dx, pending.dy);
      pending = null;
    }
  }
  for (const [k, dir] of Object.entries(DIRS)) {
    const g = document.createElement('div');
    g.className = `resize-grip grip-${k}`;
    g.addEventListener('pointerdown', async (e) => {
      e.preventDefault();
      g.setPointerCapture(e.pointerId);
      drag = { dir, sx: e.screenX, sy: e.screenY, pid: e.pointerId };
      const r = await window.pywebview.api.window_resize_begin();
      if (r && r.maximized === false) updateMaxIcon(false);
    });
    g.addEventListener('pointermove', (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      pending = { dx: e.screenX - drag.sx, dy: e.screenY - drag.sy };
      if (!rafId) rafId = requestAnimationFrame(flush);
    });
    const end = (e) => {
      if (drag && e.pointerId === drag.pid) {
        try { g.releasePointerCapture(drag.pid); } catch (_e) {}
        drag = null;
      }
    };
    g.addEventListener('pointerup', end);
    g.addEventListener('pointercancel', end);
    document.body.appendChild(g);
  }
})();

// ═══ 标题栏 ══════════════════════════════════════════════════════════════════
const MAX_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/></svg>';
const RESTORE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8.5 8.5V7A1.5 1.5 0 0 1 10 5.5h7A1.5 1.5 0 0 1 18.5 7v7a1.5 1.5 0 0 1-1.5 1.5h-1.5" stroke-linecap="round"/><rect x="5.5" y="8.5" width="10" height="10" rx="1.5"/></svg>';
function updateMaxIcon(maxed) {
  $('btn-max').innerHTML = maxed ? RESTORE_ICON : MAX_ICON;
}
async function toggleMax() {
  const m = await api().window_toggle_maximize();
  updateMaxIcon(m);
}
$('btn-min').addEventListener('click', () => api().window_minimize());
$('btn-max').addEventListener('click', toggleMax);
$('btn-close').addEventListener('click', () => api().window_close());
document.querySelector('.titlebar').addEventListener('dblclick', (e) => {
  if (e.target.closest('.wc') || e.target.closest('.iconbtn')) return;
  toggleMax();
});

// ═══ 文件托盘（分视图渲染；Python 端共享一份托盘） ═══════════════════════════
const VIEW = {
  score: { list: 'score-files', count: 'score-count', sel: null,
           empty: 'w.score.empty' },
  audio: { list: 'audio-files', count: 'audio-count', sel: null,
           empty: 'w.audio.empty' },
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
    li.textContent = t(cfg.empty);
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
  $(cfg.count).textContent = t('w.list.selected_count', { n: checked, t: files.length });
}

function updatePreview(view, file) {
  const nameEl = $(view === 'score' ? 'score-preview-name' : 'audio-preview-name');
  nameEl.textContent = file ? file.name : '';
  const url = file ? `/file?path=${encodeURIComponent(file.path)}` : null;

  if (view === 'audio') {
    const stage = $('audio-stage');
    stage.replaceChildren();
    if (!file) {
      const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 12v0M8 8v8M12 5v14M16 9v6M20 12v0"/></svg>';
      stage.innerHTML = `<div class="placeholder">${icon}<span data-i18n="w.audio.listen_ph"></span></div>`;
      stage.querySelector('[data-i18n]').textContent = t('w.audio.listen_ph');
      return;
    }
    const au = document.createElement('audio');
    au.controls = true;
    au.src = url;
    stage.appendChild(au);
    return;
  }

  // view === 'score'：静态 placeholder + a4frame/canvas（PDF，PdfView 管理）常驻
  // 在 DOM 里；不能用 stage.replaceChildren()，那会把 PdfView 绑定的 canvas 也删掉。
  // 图片走独立动态创建的 .a4frame.imgframe，每次切换文件时先移除旧的。
  const stage = $('score-stage');
  const ph = stage.querySelector('.placeholder');
  const oldImgFrame = stage.querySelector('.a4frame.imgframe');
  if (oldImgFrame) oldImgFrame.remove();
  scoreImgZoom = null;

  if (!file) {
    scoreView.close();
    ph.classList.remove('hidden');
    return;
  }
  ph.classList.add('hidden');

  if (/\.(png|jpe?g)$/i.test(file.name)) {
    scoreView.close();
    const frame = document.createElement('div');
    frame.className = 'a4frame imgframe';
    const img = document.createElement('img');
    img.src = url;
    img.alt = file.name;
    frame.appendChild(img);
    stage.appendChild(frame);
    attachFrameDrag(frame);
    scoreImgZoom = makeImageZoom(frame, img);
  } else {
    scoreView.open(url).catch((e) => toast(t('w.pv.open_failed', { e })));
  }
}

window.addEventListener('files_changed', (e) => {
  trayCache = (e.detail && e.detail.files) || [];
  renderView('score');
  renderView('audio');
});

$('score-add').addEventListener('click', () => api().shell_pick_files('score'));
$('audio-add').addEventListener('click', () => api().shell_pick_files('audio'));

async function trayAddFolder(view) {
  const r = await api().shell_pick_folder_import(view);
  if (r.error === 'empty') toast(t('w.tray.folder_empty'));
}
$('score-addfolder').addEventListener('click', () => trayAddFolder('score'));
$('audio-addfolder').addEventListener('click', () => trayAddFolder('audio'));

for (const view of ['score', 'audio']) {
  $(`${view}-selall`).addEventListener('click', () => api().files_select_all(view));
}

async function trayDeleteChecked(view) {
  const info = await api().files_checked_count(view);
  if (!info.n) { toast(t('w.list.pick_first_delete')); return; }
  const msg = info.in_input === info.n
    ? t('w.tray.delete_confirm_all_input', { n: info.n })
    : info.in_input === 0
      ? t('w.tray.delete_confirm_list_only', { n: info.n })
      : t('w.tray.delete_confirm_mixed', { m: info.in_input, n: info.n - info.in_input });
  if (!confirm(msg)) return;
  await api().files_delete_checked(view);
}
$('score-delete').addEventListener('click', () => trayDeleteChecked('score'));
$('audio-delete').addEventListener('click', () => trayDeleteChecked('audio'));

// ═══ 转换流程 + 进度浮层 ═════════════════════════════════════════════════════
let cancelling = false;
let converting = false;

let progTimer = null;
let progStart = 0;
function _tickElapsed() {
  const s = Math.floor((Date.now() - progStart) / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  $('prog-elapsed').textContent = `${mm}:${ss}`;
}

function showOverlay(title) {
  $('prog-title').textContent = title;
  $('prog-msg').textContent = '';
  $('prog-submsg').textContent = '';
  $('prog-main').style.width = '0%';
  $('prog-sub').style.width = '0%';
  $('prog-log').replaceChildren();
  $('progress-overlay').classList.remove('hidden');
  progStart = Date.now();
  _tickElapsed();
  clearInterval(progTimer);
  progTimer = setInterval(_tickElapsed, 1000);
}
function hideOverlay() {
  $('progress-overlay').classList.add('hidden');
  clearInterval(progTimer);
  progTimer = null;
}

async function doStart(opts) {
  cancelling = false;
  const r = await api().convert_start(opts);
  if (!r.ok) {
    if (r.error === 'no_files') alert(t('w.conv.no_files'));
    else if (r.error === 'busy') alert(t('w.conv.busy'));
    else if (r.error === 'duplicates') showDupConfirm(opts, r.existing || []);
    else if (r.error === 'homr_missing') {
      toast(t('w.score.homr_missing_guard'));
      startModelDownload('homr', t('w.model.dl_title_homr'));
    }
    return;
  }
  converting = true;
  window.__uiFlags.busy = true;
  showOverlay(t(opts.view === 'score' ? 'w.conv.running_score' : 'w.conv.running_audio', { n: r.count }));
}

function startConvert(view) {
  const opts = { view };
  if (view === 'score') {
    opts.engine = $('score-engine').value;
    opts.sr_engine = $('score-sr').value;
    opts.parallel = $('score-parallel').value;   // '1'|'2'|'4'|'auto'（Python 侧解析）
  } else {
    opts.engine = 'auto';
    opts.melody_only = $('audio-melody').classList.contains('on');
  }
  return doStart(opts);
}

// ── 重复输出确认弹层（与 Flet landing 语义一致：默认勾选跳过） ──
let dupPendingOpts = null;
function showDupConfirm(opts, existing) {
  dupPendingOpts = opts;
  $('confirm-title').textContent = t('landing.convert_dialog_title', { n: existing.length });
  $('confirm-warn').textContent = t('landing.existing_outputs_warning', { n: existing.length });
  const list = $('confirm-list');
  list.replaceChildren();
  for (const name of existing.slice(0, 5)) {
    const item = document.createElement('div');
    item.className = 'result-item';
    const fn = document.createElement('span');
    fn.className = 'fn';
    fn.textContent = name;
    item.appendChild(fn);
    list.appendChild(item);
  }
  if (existing.length > 5) {
    const more = document.createElement('div');
    more.className = 'result-item';
    const w2 = document.createElement('span');
    w2.className = 'why';
    w2.textContent = t('landing.existing_outputs_more', { n: existing.length - 5 });
    more.appendChild(w2);
    list.appendChild(more);
  }
  $('confirm-skip').classList.add('on');
  $('confirm-skip').setAttribute('aria-checked', 'true');
  $('confirm-overlay').classList.remove('hidden');
}
$('confirm-skip').addEventListener('click', (e) => {
  e.preventDefault();
  const b = $('confirm-skip');
  b.classList.toggle('on');
  b.setAttribute('aria-checked', b.classList.contains('on') ? 'true' : 'false');
});
$('confirm-cancel').addEventListener('click', () => {
  $('confirm-overlay').classList.add('hidden');
  dupPendingOpts = null;
});
$('confirm-go').addEventListener('click', () => {
  const opts = dupPendingOpts;
  dupPendingOpts = null;
  $('confirm-overlay').classList.add('hidden');
  if (!opts) return;
  doStart({ ...opts, dup_resolved: true, skip_dup: $('confirm-skip').classList.contains('on') });
});
$('score-start').addEventListener('click', () => startConvert('score'));
$('audio-start').addEventListener('click', () => startConvert('audio'));
$('prog-cancel').addEventListener('click', async () => {
  cancelling = true;
  $('prog-msg').textContent = t('w.conv.cancelling');
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

// ═══ noteDigger 音频扒谱编辑器：iframe 懒加载 + MIDI 导出→简谱 桥接（③-3）══════
// 首次进入才设 src——noteDigger 会初始化 ONNX/GPU，避免拖慢主界面启动。
$('audio-notedigger').addEventListener('click', () => showPage('notedigger'));
$('nd-back').addEventListener('click', () => showPage('audio'));

let ndMidi = null;   // 捕获的最近一次导出 {b64, name}
// 劫持 noteDigger（同源 iframe）统一的保存出口 window.bSaver.saveArrayBuffer：
// 用户走 noteDigger 原生「导出 MIDI」流程时透明捕获字节（不改 noteDigger）。
function ndHook(frame) {
  try {
    const cw = frame.contentWindow;
    if (!cw || !cw.bSaver || cw.__ndHooked) return !!(cw && cw.__ndHooked);
    const orig = cw.bSaver.saveArrayBuffer.bind(cw.bSaver);
    cw.bSaver.saveArrayBuffer = (arrayBuffer, filename) => {
      if (/\.mid$/i.test(filename || '')) ndCapture(arrayBuffer, filename);
      return orig(arrayBuffer, filename);   // 仍触发原生下载，用户也能拿到 .mid
    };
    cw.__ndHooked = true;
    return true;
  } catch (_e) { return false; }
}
function ndCapture(arrayBuffer, filename) {
  const bytes = new Uint8Array(arrayBuffer);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  ndMidi = { b64: btoa(bin), name: filename };
  $('nd-gen').disabled = false;
  $('nd-status').textContent = t('w.nd.captured', { name: filename });
}
// 停止 noteDigger（同源 iframe）内的音频 + MIDI 合成器播放
function ndStopPlayback() {
  try {
    const cw = $('nd-frame').contentWindow;
    if (cw && cw.app && cw.app.AudioPlayer) cw.app.AudioPlayer.stop();
  } catch (_e) { /* iframe 未加载或结构变化时忽略 */ }
}
// 从我们这边注入覆盖样式隐藏 noteDigger 顶栏的作者推广链接（GitHub/Bilibili）。
// 用注入而非改 fork：合规上等价于删除，且 noteDigger 一个字节不改（零 GPL 修改义务）。
function ndInjectStyle(frame) {
  try {
    const d = frame.contentDocument;
    if (!d || !d.head) return false;
    if (d.getElementById('__omrStyle')) return true;
    const s = d.createElement('style');
    s.id = '__omrStyle';
    s.textContent = 'a.logo2,a.logo3{display:none !important;}';
    d.head.appendChild(s);
    return true;
  } catch (_e) { return false; }
}
// 屏蔽 noteDigger 里两个在本项目中失效的扒谱功能（我们外部已有音频识别），
// 并把它的 alert 弹窗接到我们的 toast（去掉 WebView2 "127.0.0.1 显示" 的 origin）。
function ndPatch(frame) {
  try {
    const cw = frame.contentWindow;
    const d = frame.contentDocument;
    if (!cw || !d) return false;
    // alert → 我们的 UI toast（幂等；alert 无返回值，非阻塞替换安全）
    if (!cw.__omrAlert) { cw.alert = (msg) => toast(String(msg)); cw.__omrAlert = true; }
    // 隐藏两个失效扒谱按钮（#analysePannel 里按文本精确匹配，稳过位置变化）
    const items = d.querySelectorAll('#analysePannel li');
    if (!items.length) return false;   // 分析面板尚未渲染 → 重试
    const dead = ['人工智障扒谱', '音色分离扒谱'];
    items.forEach((li) => { if (dead.includes(li.textContent.trim())) li.style.display = 'none'; });
    return true;
  } catch (_e) { return false; }
}
pageEnterHooks.notedigger = () => {
  const f = $('nd-frame');
  if (f.getAttribute('src')) return;
  f.addEventListener('load', () => {
    // bSaver / document.head 可能晚于 load 事件就绪，重试直到 hook + 样式都装上
    let tries = 0;
    const setup = () => {
      const done = ndHook(f) && ndInjectStyle(f) && ndPatch(f);
      if (done || ++tries > 40) return;
      setTimeout(setup, 150);
    };
    setup();
  });
  f.setAttribute('src', './vendor/notedigger/index.html');
};
$('nd-gen').addEventListener('click', async () => {
  if (!ndMidi) return;
  ndStopPlayback();   // 生成即停止 noteDigger 播放，避免处理/跳转期间声音仍在放
  $('nd-gen').disabled = true;
  $('nd-status').textContent = t('w.nd.generating');
  const r = await api().notedigger_generate_jianpu(ndMidi.name, ndMidi.b64);
  if (!r.started) {
    $('nd-status').textContent = t('w.nd.gen_failed', { e: r.error || '' });
    $('nd-gen').disabled = false;
  }
});
window.addEventListener('nd_jianpu_progress', (e) => {
  if (e.detail && e.detail.message) $('nd-status').textContent = e.detail.message;
});
window.addEventListener('nd_jianpu_done', (e) => {
  const d = e.detail || {};
  if (!d.ok) {
    $('nd-status').textContent = t('w.nd.gen_failed', { e: d.error || '' });
    $('nd-gen').disabled = false;
    return;
  }
  $('nd-status').textContent = t('w.nd.export_hint');
  $('nd-gen').disabled = false;
  toast(t('w.nd.gen_done', { name: d.name }));
  showPage('jianpu');   // pageEnterHooks.jianpu 会刷新列表 → 新 PDF 出现
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
  showResult({ error: (e.detail && e.detail.message) || t('w.result.unknown') });
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
  $('result-jianpu').classList.toggle('hidden', !(summary && summary.success_count > 0));
  if (error) {
    $('result-title').textContent = t('w.result.title_error');
    const item = document.createElement('div');
    item.className = 'result-item';
    item.innerHTML = '<span class="tag bad">✗</span>';
    const why = document.createElement('span');
    why.className = 'why';
    why.textContent = error;
    item.appendChild(why);
    list.appendChild(item);
  } else {
    $('result-title').textContent = t('w.result.title');
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
    mk('ok', t('w.result.success'), summary.success_count || 0);
    if (summary.fallback_count) mk('warn', t('w.result.fallback'), summary.fallback_count);
    mk(summary.failed_count ? 'bad' : '', t('w.result.failed'), summary.failed_count || 0);
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
    for (const f of summary.success_files || []) addItem('ok', '✓', f.file, f.engine_used ? t('w.result.engine', { name: f.engine_used }) : '');
    for (const f of summary.fallback_files || []) addItem('warn', '↻', f.file, t('w.result.fallback_engine', { name: f.engine_used || '' }));
    for (const f of summary.failed_files || []) addItem('bad', '✗', f.file, f.reason || '');
  }
  $('result-overlay').classList.remove('hidden');
}
$('result-close').addEventListener('click', () => $('result-overlay').classList.add('hidden'));
$('result-jianpu').addEventListener('click', () => {
  $('result-overlay').classList.add('hidden');
  showPage('jianpu');
});

// ═══ 模型状态 + 下载弹层 ═════════════════════════════════════════════════════
let modelKind = null;

function renderModels(st) {
  if (!st || !st.homr) return;
  const homr = $('homr-status');
  homr.classList.toggle('absent', !st.homr.available);
  $('homr-status-text').textContent = st.homr.available
    ? t('w.score.homr_ready')
    : t('w.score.homr_missing', { p: st.homr.files_present, t: st.homr.files_total });
  $('homr-download').disabled = st.homr.available;
  $('homr-delete').disabled = !st.homr.files_present;
  const piano = $('piano-status');
  piano.classList.toggle('absent', !st.piano.available);
  $('piano-status-text').textContent = st.piano.available ? t('w.audio.piano_ready') : t('w.audio.piano_missing');
  $('piano-download').disabled = st.piano.available;
  $('piano-delete').disabled = !st.piano.available;
}

async function refreshModels() { renderModels(await api().models_status()); }

function startModelDownload(kind, title) {
  modelKind = kind;
  $('model-title').textContent = title;
  $('model-msg').textContent = t('w.model.connecting');
  $('model-msg').classList.remove('err');
  $('model-bar').style.width = '0%';
  $('model-close').classList.add('hidden');
  $('model-retry').classList.add('hidden');
  $('model-cancel').classList.remove('hidden');
  $('model-overlay').classList.remove('hidden');
  api().models_download(kind);
}
$('homr-download').addEventListener('click', () => startModelDownload('homr', t('w.model.dl_title_homr')));
$('piano-download').addEventListener('click', () => startModelDownload('piano', t('w.model.dl_title_piano')));
$('model-cancel').addEventListener('click', () => { if (modelKind) api().models_cancel_download(modelKind); });
$('model-close').addEventListener('click', () => { $('model-overlay').classList.add('hidden'); modelKind = null; });
$('model-retry').addEventListener('click', () => { if (modelKind) startModelDownload(modelKind, $('model-title').textContent); });
$('homr-delete').addEventListener('click', async () => {
  if (confirm(t('w.model.del_homr_confirm'))) await api().models_delete('homr');
});
$('piano-delete').addEventListener('click', async () => {
  if (confirm(t('w.model.del_piano_confirm'))) await api().models_delete('piano');
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
  if (!d.ok && d.error !== 'cancelled') {
    // 保持浮层开着，切到"失败"态：提示 + 重试/关闭，而不是弹 alert 关掉了事
    $('model-msg').textContent = t('w.model.dl_failed', { e: d.error });
    $('model-msg').classList.add('err');
    $('model-cancel').classList.add('hidden');
    $('model-close').classList.remove('hidden');
    $('model-retry').classList.remove('hidden');
    return;
  }
  $('model-overlay').classList.add('hidden');
  modelKind = null;
});
window.addEventListener('models_changed', (e) => renderModels(e.detail && e.detail.status));

// ═══ pdf.js 查看器 ═══════════════════════════════════════════════════════════
// 本地捆绑 pdf.js（vendor/pdfjs，v6），worker 同源加载；渲染按 devicePixelRatio
// 放大保证清晰度。fit = 适应舞台宽度。
let _pdfjs = null;
// 注意：函数不能叫 pdfjsLib —— pdf.min.mjs 求值时会 `globalThis.pdfjsLib = 库对象`，
// 覆盖同名的全局函数声明，导致第二次调用起报 "pdfjsLib is not a function"。
async function loadPdfjs() {
  if (_pdfjs) return _pdfjs;
  _pdfjs = await import('./vendor/pdfjs/pdf.min.mjs');
  _pdfjs.GlobalWorkerOptions.workerSrc = './vendor/pdfjs/pdf.worker.min.mjs';
  return _pdfjs;
}

// 通用画框拖拽平移：原 Flet InteractiveViewer 的 pan_enabled 能力，pywebview 版
// 迁移时漏掉了，这里用鼠标按下+移动直接改画框 scrollLeft/scrollTop 补回来（对
// canvas/img 内容一视同仁，只操心画框本身的滚动位置，不关心里面渲染的是什么）。
function attachFrameDrag(frame) {
  let dragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;
  frame.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    startLeft = frame.scrollLeft; startTop = frame.scrollTop;
    frame.classList.add('dragging');
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    frame.scrollLeft = startLeft - (e.clientX - startX);
    frame.scrollTop = startTop - (e.clientY - startY);
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    frame.classList.remove('dragging');
  });
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
    // A4 画框随窗口尺寸变化（窗口化↔最大化、任意 resize）→ fit 模式下重渲当前页，
    // 让页面始终恰好填满新的画框（业界惯用做法：观察容器尺寸，防抖重排）。
    this._resizeTimer = 0;
    const frame = this.canvas.parentElement || this.stage;
    this._ro = new ResizeObserver(() => {
      if (!this.doc || this.scale !== null) return;   // 仅 fit 模式自动重排
      clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => { if (this.doc) this.render(); }, 120);
    });
    this._ro.observe(frame);
    // 滚轮缩放 + 拖拽平移
    frame.addEventListener('wheel', (e) => {
      if (!this.doc) return;
      e.preventDefault();
      this.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });
    attachFrameDrag(frame);
  }

  async open(url) {
    const lib = await loadPdfjs();
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
    // fit = 整页装入 A4 画框（宽高双约束取小 = contain）。A4 页面正好铺满；
    // 非 A4 页面也保证整页可见。-1px 抵消取整误差，避免冒出滚动条吃掉宽度。
    const frame = this.canvas.parentElement;
    const vp = page.getViewport({ scale: 1 });
    if (!frame) return Math.max(0.1, (this.stage.clientWidth - 36) / vp.width);
    const w = frame.clientWidth - 1;
    const h = frame.clientHeight - 1;
    if (w <= 0 || h <= 0) return 1;
    return Math.min(w / vp.width, h / vp.height);
  }

  async render() {
    if (!this.doc) return;
    const token = ++this._renderToken;
    const page = await this.doc.getPage(this.pageNo);
    if (token !== this._renderToken) return;
    // 先取消隐藏再测量：canvas.hidden 时 A4 画框 display:none，宽度为 0
    this.canvas.classList.remove('hidden');
    const scale = this.scale ?? this._fitScale(page);
    const dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr });
    this.canvas.width = vp.width;
    this.canvas.height = vp.height;
    this.canvas.style.width = `${vp.width / dpr}px`;
    this.canvas.style.height = `${vp.height / dpr}px`;
    await page.render({ canvas: this.canvas, viewport: vp }).promise;
    if (this.pageInfoEl) this.pageInfoEl.textContent = t('w.pv.page_info', { n: this.pageNo, t: this.doc.numPages });
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

// 图片画框的缩放控制器（乐谱识别页的 PNG/JPG 预览用——不经 PdfView，独立维护一个
// scale 状态，用 CSS transform 缩放；拖拽平移复用 attachFrameDrag）。返回的对象
// 供下方缩放按钮在图片模式下调用，滚轮事件也走同一套。
function makeImageZoom(frame, img) {
  let scale = 1;
  const apply = () => { img.style.transform = scale === 1 ? '' : `scale(${scale})`; };
  const controller = {
    zoom(f) { scale = Math.min(6, Math.max(1, scale * f)); apply(); },
    zoomFit() { scale = 1; apply(); },
  };
  frame.addEventListener('wheel', (e) => {
    e.preventDefault();
    controller.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });
  return controller;
}

// ═══ 乐谱识别页：预览（PDF 走 PdfView，PNG/JPG 走独立 .a4frame + <img>）═══════
const scoreView = new PdfView($('score-canvas'), $('score-stage'), $('score-pageinfo'));
let scoreImgZoom = null;   // 当前图片模式的缩放控制器；PDF 模式或无文件时为 null
$('score-prev').addEventListener('click', () => scoreView.prev());
$('score-next').addEventListener('click', () => scoreView.next());
// 缩放按钮：图片模式走 scoreImgZoom，PDF 模式走 scoreView——此前只接了 scoreView，
// 选中图片时点缩放按钮完全没反应。
$('score-zoomin').addEventListener('click', () => { if (scoreImgZoom) scoreImgZoom.zoom(1.2); else scoreView.zoom(1.2); });
$('score-zoomout').addEventListener('click', () => { if (scoreImgZoom) scoreImgZoom.zoom(1 / 1.2); else scoreView.zoom(1 / 1.2); });
$('score-zoomfit').addEventListener('click', () => { if (scoreImgZoom) scoreImgZoom.zoomFit(); else scoreView.zoomFit(); });

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
    li.textContent = t('w.jp.empty');
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
  $('jp-count').textContent = jpEntries.length ? t('w.list.checked_count', { n: jpChecked.size, t: jpEntries.length }) : '';
  const cur = jpEntries.find((e) => e.path === jpSel);
  $('jp-edit').disabled = !(cur && cur.has_txt);
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
  jpView.open(`/file?path=${encodeURIComponent(cur.path)}`).catch((e) => toast(t('w.pv.open_failed', { e })));
}

$('jp-refresh').addEventListener('click', jpRefresh);
$('jp-selall').addEventListener('click', () => {
  jpChecked = jpChecked.size === jpEntries.length
    ? new Set() : new Set(jpEntries.map((e) => e.path));
  jpRenderList();
});
$('jp-export').addEventListener('click', async () => {
  if (!jpChecked.size) { toast(t('w.list.pick_first_export')); return; }
  const r = await api().outputs_export([...jpChecked]);
  if (r.ok) toast(t('w.jp.export_done', { n: r.copied.length, dest: r.dest }));
  else if (r.error !== 'cancelled') toast(t('w.tp.export_failed', { e: r.error || (r.failed && r.failed.length) }));
});
$('jp-delete').addEventListener('click', async () => {
  if (!jpChecked.size) { toast(t('w.list.pick_first_delete')); return; }
  if (!confirm(t('w.jp.delete_confirm', { n: jpChecked.size }))) return;
  await api().outputs_delete([...jpChecked]);
  jpChecked.clear();
  jpRefresh();
});
$('jp-midi').addEventListener('click', async () => {
  if (!jpSel) return;
  const r = await api().outputs_play_midi(jpSel);
  if (!r.ok) { toast(r.error === 'not_found' ? t('w.jp.no_midi', { name: r.name }) : t('w.tp.open_failed', { e: r.error })); return; }
  midiPlayer.play($('jp-midislot'), `/file?path=${encodeURIComponent(r.path)}`, r.name);
});
$('jp-rerender').addEventListener('click', async () => {
  if (!jpSel) return;
  const r = await api().outputs_rerender(jpSel);
  if (r.started) toast(t('w.jp.rerender_started'));
  else toast(r.error === 'no_txt' ? t('w.jp.no_txt', { name: r.name }) : t('w.jp.rerender_failed', { e: r.error }));
});
window.addEventListener('rerender_done', (e) => {
  const d = e.detail || {};
  if (d.ok) {
    toast(t('w.jp.rerender_done'));
    if (d.path === jpSel) jpOpenSelected();
  } else {
    toast(t('w.jp.rerender_failed', { e: d.error }));
  }
});
$('jp-edit').addEventListener('click', () => { if (jpSel) edOpenForPdf(jpSel); });
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
    li.textContent = t('w.st.empty');
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
  $('st-count').textContent = stEntries.length ? t('w.list.checked_count', { n: stChecked.size, t: stEntries.length }) : '';
}

async function stOpenSelected() {
  const cur = stEntries.find((e) => e.path === stSel);
  $('st-preview-name').textContent = cur ? cur.name : '';
  const ph = $('st-stage').querySelector('.placeholder');
  if (!cur) {
    stView.close();
    if (ph) { ph.classList.remove('hidden'); $('st-placeholder-text').textContent = t('w.st.preview_ph'); }
    return;
  }
  const r = await api().scores_preview(cur.path);
  if (r.pdf) {
    if (ph) ph.classList.add('hidden');
    stPendingRender = null;
    stView.open(`/file?path=${encodeURIComponent(r.pdf)}`).catch((e2) => toast(t('w.pv.open_failed', { e: e2 })));
  } else if (r.started) {
    stPendingRender = cur.path;
    stView.close();
    if (ph) { ph.classList.remove('hidden'); $('st-placeholder-text').textContent = t('w.st.rendering', { name: cur.name }); }
  } else {
    toast(t('w.pv.cannot_preview', { e: r.error || t('w.result.unknown') }));
  }
}

window.addEventListener('score_preview_ready', (e) => {
  const d = e.detail || {};
  if (d.mxl !== stPendingRender) return;   // 已切换到其它文件，丢弃
  stPendingRender = null;
  if (!d.ok) {
    $('st-placeholder-text').textContent = t('w.st.render_failed', { e: d.error || '' });
    return;
  }
  const ph = $('st-stage').querySelector('.placeholder');
  if (ph) ph.classList.add('hidden');
  stView.open(`/file?path=${encodeURIComponent(d.pdf)}`).catch((e2) => toast(t('w.pv.open_failed', { e: e2 })));
});

$('st-refresh').addEventListener('click', stRefresh);
$('st-selall').addEventListener('click', () => {
  stChecked = stChecked.size === stEntries.length
    ? new Set() : new Set(stEntries.map((e) => e.path));
  stRenderList();
});
$('st-export').addEventListener('click', async () => {
  if (!stChecked.size) { toast(t('w.list.pick_first_export')); return; }
  toast(t('w.st.export_started'));
  const r = await api().scores_export([...stChecked]);
  if (r.ok) toast(t('w.st.export_done', { n: r.copied.length, dest: r.dest }));
  else if (r.error !== 'cancelled') toast(t('w.tp.export_failed', { e: r.error || (r.failed ? r.failed.length : '') }));
});
$('st-delete').addEventListener('click', async () => {
  if (!stChecked.size) { toast(t('w.list.pick_first_delete')); return; }
  if (!confirm(t('w.st.delete_confirm', { n: stChecked.size }))) return;
  await api().scores_delete([...stChecked]);
  stChecked.clear();
  stRefresh();
});
$('st-midi').addEventListener('click', async () => {
  if (!stSel) return;
  const info = await api().scores_midi_for(stSel);
  if (!info.exists && !confirm(t('w.st.gen_midi_confirm', { name: info.name }))) return;
  const r = await api().scores_generate_play_midi(stSel);
  if (r.started && !info.exists) toast(t('w.st.gen_midi_started'));
});
window.addEventListener('score_midi_done', (e) => {
  const d = e.detail || {};
  if (!d.ok) { toast(t('w.st.gen_midi_failed', { e: d.error || '' })); return; }
  if (d.path) midiPlayer.play($('st-midislot'), `/file?path=${encodeURIComponent(d.path)}`, d.name);
});
$('st-transpose').addEventListener('click', () => {
  if (!stSel) { toast(t('w.st.pick_score_first')); return; }
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

let tpCurrentPath = null;

function tpResetPanes() {
  tpTransView.close();
  tpSetPlaceholder('trans', t('w.tp.trans_ph'));
  $('tp-export-trans').disabled = true;
  $('tp-progress').style.width = '0%';
}

function tpApplyLoad(r, path) {
  tpCurrentPath = path || tpCurrentPath;
  $('tp-name').textContent = r.name;
  $('tp-from').value = r.key;
  $('tp-status').textContent = t('w.tp.detected', { key: r.key_cn });
  tpRequestPreview('orig');
}

async function tpLoad(path) {
  await tpEnsureOptions();
  tpResetPanes();
  const r = await api().transpose_load(path);
  if (!r.ok) { toast(t('w.tp.open_failed', { e: r.error || '' })); return; }
  tpApplyLoad(r, path);
}

async function tpRequestPreview(which) {
  const r = await api().transpose_preview(which);
  if (r.pdf) tpShowPreview(which, r.pdf);
  else if (r.started) tpSetPlaceholder(which, t('w.tp.rendering'));
  else tpSetPlaceholder(which, t('w.pv.cannot_preview', { e: r.error || '' }));
}

function tpShowPreview(which, pdf) {
  tpSetPlaceholder(which, null);
  const view = which === 'orig' ? tpOrigView : tpTransView;
  view.open(`/file?path=${encodeURIComponent(pdf)}`).catch((e) => toast(t('w.pv.open_failed', { e })));
}

window.addEventListener('transpose_preview_ready', (e) => {
  const d = e.detail || {};
  if (d.ok) tpShowPreview(d.which, d.pdf);
  else tpSetPlaceholder(d.which, t('w.st.render_failed', { e: d.error || '' }));
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
  const opts = hasClosest
    ? [['closest', t('w.tp.dir_closest')], ['up', t('w.tp.dir_up')], ['down', t('w.tp.dir_down')]]
    : [['up', t('w.tp.dir_up')], ['down', t('w.tp.dir_down')]];
  for (const [v, label] of opts) {
    const op = document.createElement('option');
    op.value = v;
    op.textContent = label;
    dir.appendChild(op);
  }
  dir.value = opts.some(([v]) => v === cur) ? cur : opts[0][0];
  tpAutoRun();
});

$('tp-keysig').addEventListener('click', (e) => {
  e.preventDefault();
  const b = $('tp-keysig');
  b.classList.toggle('on');
  b.setAttribute('aria-checked', b.classList.contains('on') ? 'true' : 'false');
  tpAutoRun();
});

$('tp-detect').addEventListener('click', (e) => {
  e.preventDefault();
  // 重新 load 即重新检测（load 幂等）
  if (tpCurrentPath) tpLoad(tpCurrentPath);
});
$('tp-open').addEventListener('click', async () => {
  await tpEnsureOptions();
  const r = await api().transpose_pick_file();
  if (r.error === 'cancelled') return;
  if (!r.ok) { toast(t('w.tp.open_failed', { e: r.error || '' })); return; }
  tpResetPanes();
  tpApplyLoad(r, r.path);
});
$('tp-xmldir').addEventListener('click', () => api().shell_open_xml_dir());

async function tpDoRun() {
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
  if (!r.ok) { toast(r.error === 'no_file' ? t('w.tp.load_first') : t('w.tp.cannot', { e: r.error || '' })); return; }
  tpBusy = true;
  $('tp-run').disabled = true;
  $('tp-status').textContent = t('w.tp.running');
  $('tp-progress').style.width = '0%';
}
$('tp-run').addEventListener('click', () => { if (!tpBusy) tpDoRun(); });

// 参数变化自动预览（与旧 Flet 版一致）：debounce 250ms 避免连续调参时逐次触发；
// 不用 tpBusy 早退——后端 transpose.py 自带 run token，新调用会让旧任务的结果
// 作废，允许渲染中途再改参数、以最新一次为准，而不是把改动悄悄丢掉。
let tpAutoTimer = null;
function tpAutoRun() {
  if (!tpCurrentPath) return;  // 尚未加载文件：安静跳过，不弹提示（与旧版一致）
  clearTimeout(tpAutoTimer);
  tpAutoTimer = setTimeout(tpDoRun, 250);
}
for (const id of ['tp-dir', 'tp-from', 'tp-to', 'tp-interval', 'tp-degree']) {
  $(id).addEventListener('change', tpAutoRun);
}

window.addEventListener('transpose_progress', (e) => {
  $('tp-progress').style.width = `${Math.round(((e.detail && e.detail.value) || 0) * 100)}%`;
});
window.addEventListener('transpose_done', (e) => {
  const d = e.detail || {};
  tpBusy = false;
  $('tp-run').disabled = false;
  if (!d.ok) {
    $('tp-status').textContent = t('w.tp.failed', { e: d.error || '' });
    toast(t('w.tp.failed', { e: d.error || '' }));
    return;
  }
  $('tp-progress').style.width = '100%';
  $('tp-status').textContent = t('w.tp.done', { name: d.name });
  $('tp-export-trans').disabled = false;
  tpSetPlaceholder('trans', t('w.tp.rendering_trans'));
  tpRequestPreview('trans');
});

$('tp-back').addEventListener('click', () => showPage('staff'));
for (const [id, which] of [['tp-export-orig', 'orig'], ['tp-export-trans', 'trans']]) {
  $(id).addEventListener('click', async () => {
    const r = await api().transpose_export(which);
    if (r.ok) toast(t('w.tp.exported', { dest: r.dest }));
    else if (r.error !== 'cancelled') toast(t('w.tp.export_failed', { e: r.error || '' }));
  });
}
for (const [pfx, view] of [['tp-orig', tpOrigView], ['tp-trans', tpTransView]]) {
  $(`${pfx}-prev`).addEventListener('click', () => view.prev());
  $(`${pfx}-next`).addEventListener('click', () => view.next());
  $(`${pfx}-zoomin`).addEventListener('click', () => view.zoom(1.2));
  $(`${pfx}-zoomout`).addEventListener('click', () => view.zoom(1 / 1.2));
  $(`${pfx}-zoomfit`).addEventListener('click', () => view.zoomFit());
}

// ═══ 简谱编辑页 ══════════════════════════════════════════════════════════════
// 左栏双模式（参考图 / 渲染预览），右栏行号文本编辑器；保存/渲染/导出走桥，
// 头部 # 注释块由 Python 侧保护。从简谱预览页「编辑」进入。
const edPvView = new PdfView($('ed-pv-canvas'), $('ed-pv-stage'), $('ed-pv-pageinfo'));
const edRefView = new PdfView($('ed-ref-canvas'), $('ed-ref-stage'), null);
let edDirty = false;
let edLoaded = false;
let edPreviewOK = false;

function edSetDirty(v) {
  edDirty = v;
  $('ed-dirty').classList.toggle('hidden', !v);
}

function edUpdateGutter() {
  const ta = $('ed-text');
  const lines = (ta.value.match(/\n/g) || []).length + 1;
  const cur = ta.value.slice(0, ta.selectionStart).split('\n').length;
  const g = $('ed-gutter');
  g.replaceChildren();
  for (let i = 1; i <= lines; i++) {
    const d = document.createElement('div');
    d.textContent = String(i);
    if (i === cur) d.className = 'cur';
    g.appendChild(d);
  }
  g.scrollTop = ta.scrollTop;
  const col = ta.selectionStart - ta.value.lastIndexOf('\n', ta.selectionStart - 1);
  $('ed-linecol').textContent = t('w.ed.line_col', { l: cur, c: col });
}

function edShowTab(which) {
  $('ed-tab-ref').setAttribute('aria-selected', String(which === 'ref'));
  $('ed-tab-pv').setAttribute('aria-selected', String(which === 'pv'));
  $('ed-ref-stage').classList.toggle('hidden', which !== 'ref');
  $('ed-pv-stage').classList.toggle('hidden', which !== 'pv');
}
$('ed-tab-ref').addEventListener('click', () => edShowTab('ref'));
$('ed-tab-pv').addEventListener('click', () => edShowTab('pv'));

function edApplyLoad(r) {
  edLoaded = true;
  edPreviewOK = false;
  $('ed-name').textContent = r.name;
  $('ed-hint').textContent = '';
  const ta = $('ed-text');
  ta.disabled = false;
  ta.value = r.body || '';
  edSetDirty(false);
  edUpdateGutter();
  // 左栏参考
  const img = $('ed-ref-img');
  const ph = $('ed-ref-ph');
  edRefView.close();
  img.classList.add('hidden');
  if (r.source && r.source.kind === 'image') {
    img.src = `/file?path=${encodeURIComponent(r.source.path)}`;
    img.classList.remove('hidden');
    ph.parentElement.classList.add('hidden');
    $('ed-left-name').textContent = r.source.path.split(/[\\/]/).pop();
  } else if (r.source && r.source.kind === 'pdf') {
    ph.parentElement.classList.add('hidden');
    $('ed-left-name').textContent = r.source.path.split(/[\\/]/).pop();
    edRefView.open(`/file?path=${encodeURIComponent(r.source.path)}`).catch(() => {});
  } else {
    ph.parentElement.classList.remove('hidden');
    ph.textContent = t('w.ed.no_source');
    $('ed-left-name').textContent = '';
  }
  // 预览面板复位
  edPvView.close();
  $('ed-pv-ph').parentElement.classList.remove('hidden');
  $('ed-pv-ph').textContent = t('w.ed.no_preview_yet');
  edShowTab(r.source ? 'ref' : 'pv');
  showPage('editor');
}

async function edOpenForPdf(pdfPath) {
  const r = await api().editor_load_for_pdf(pdfPath);
  if (!r.ok) {
    toast(r.error === 'no_txt' ? t('w.jp.no_txt', { name: r.name }) : t('w.tp.open_failed', { e: r.error || '' }));
    return;
  }
  edApplyLoad(r);
}

$('ed-open').addEventListener('click', async () => {
  const r = await api().editor_pick_open();
  if (r.error === 'cancelled') return;
  if (!r.ok) { toast(t('w.tp.open_failed', { e: r.error || '' })); return; }
  edApplyLoad(r);
});

async function edSave() {
  if (!edLoaded) return false;
  const r = await api().editor_save($('ed-text').value);
  if (r.ok) {
    edSetDirty(false);
    toast(t('w.ed.saved', { name: r.name }));
    return true;
  }
  toast(t('w.ed.save_failed', { e: r.error || '' }));
  return false;
}
$('ed-save').addEventListener('click', edSave);

$('ed-render').addEventListener('click', async () => {
  if (!edLoaded) return;
  const r = await api().editor_render_preview($('ed-text').value);
  if (!r.ok) { toast(t('w.ed.save_failed', { e: r.error || '' })); return; }
  edSetDirty(false);        // render 内部已保存
  edShowTab('pv');
  edPvView.close();
  $('ed-pv-ph').parentElement.classList.remove('hidden');
  $('ed-pv-ph').textContent = t('w.tp.rendering');
});
window.addEventListener('editor_preview_ready', (e) => {
  const d = e.detail || {};
  if (!d.ok) {
    edPreviewOK = false;
    $('ed-pv-ph').textContent = t('w.st.render_failed', { e: d.error || '' });
    return;
  }
  edPreviewOK = true;
  $('ed-pv-ph').parentElement.classList.add('hidden');
  edShowTab('pv');
  edPvView.open(`/file?path=${encodeURIComponent(d.pdf)}`).catch((e2) => toast(t('w.pv.open_failed', { e: e2 })));
});

$('ed-export').addEventListener('click', async () => {
  if (!edLoaded) return;
  const r = await api().editor_export_to_output();
  if (r.ok) toast(t('w.ed.exported', { dest: r.dest }));
  else if (r.error === 'no_preview') toast(t('w.ed.export_need_preview'));
  else toast(t('w.tp.export_failed', { e: r.error || '' }));
});

$('ed-back').addEventListener('click', () => {
  if (edDirty && !confirm(t('w.ed.unsaved_confirm'))) return;
  showPage('jianpu');
});

// 编辑器输入行为：脏标记 + 行号同步 + Ctrl+S
const edTa = $('ed-text');
edTa.addEventListener('input', () => { edSetDirty(true); edUpdateGutter(); });
edTa.addEventListener('scroll', () => { $('ed-gutter').scrollTop = edTa.scrollTop; });
for (const ev of ['keyup', 'click']) edTa.addEventListener(ev, edUpdateGutter);
edTa.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    edSave();
  }
});

// 符号参考面板（键复用 gui/strings 的 jianpu_editor.symbol_*）
$('ed-symbols').addEventListener('click', () => {
  const panel = $('ed-symbols-panel');
  if (panel.childElementCount === 0) {
    const SECTIONS = [
      ['jianpu_editor.symbol_section_notes',
        ['notes', 'rest', 'accidental', 'high_octave', 'low_octave'].map((k) => [`jianpu_editor.symbol_${k}_row`, `jianpu_editor.symbol_${k}_desc`])],
      ['jianpu_editor.symbol_section_duration',
        [1, 2, 3, 4, 5, 6, 7, 8].map((i) => [`jianpu_editor.symbol_duration_row_${i}`, `jianpu_editor.symbol_duration_desc_${i}`])],
      ['jianpu_editor.symbol_section_structure',
        [1, 2, 3, 4, 5].map((i) => [`jianpu_editor.symbol_structure_row_${i}`, `jianpu_editor.symbol_structure_desc_${i}`])],
      ['jianpu_editor.symbol_section_polyphony',
        [1, 2, 3].map((i) => [`jianpu_editor.symbol_polyphony_row_${i}`, `jianpu_editor.symbol_polyphony_desc_${i}`])],
    ];
    for (const [secKey, rows] of SECTIONS) {
      const h = document.createElement('h4');
      h.textContent = t(secKey);
      panel.appendChild(h);
      for (const [rowKey, descKey] of rows) {
        const div = document.createElement('div');
        div.className = 'sym';
        const code = document.createElement('code');
        code.textContent = t(rowKey);
        const span = document.createElement('span');
        span.textContent = t(descKey);
        div.append(code, span);
        panel.appendChild(div);
      }
    }
  }
  panel.classList.toggle('hidden');
});
// 参考图点击 → 按纵向比例映射到文本行（与 Flet 版同为粗略映射），定位光标并滚动到该行
$('ed-ref-img').addEventListener('click', (e) => {
  const ta = $('ed-text');
  if (ta.disabled || !ta.value) return;
  const rect = e.target.getBoundingClientRect();
  const frac = Math.min(0.999, Math.max(0, (e.clientY - rect.top) / rect.height));
  const lines = ta.value.split('\n');
  const target = Math.floor(frac * lines.length);
  let pos = 0;
  for (let i = 0; i < target; i++) pos += lines[i].length + 1;
  ta.focus();
  ta.setSelectionRange(pos, pos + (lines[target] ? lines[target].length : 0));
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 21;
  ta.scrollTop = Math.max(0, target * lineHeight - ta.clientHeight / 2);
  edUpdateGutter();
});

$('ed-pv-prev').addEventListener('click', () => edPvView.prev());
$('ed-pv-next').addEventListener('click', () => edPvView.next());
$('ed-pv-zoomin').addEventListener('click', () => edPvView.zoom(1.2));
$('ed-pv-zoomout').addEventListener('click', () => edPvView.zoom(1 / 1.2));
$('ed-pv-zoomfit').addEventListener('click', () => edPvView.zoomFit());

// ═══ 关于页 ══════════════════════════════════════════════════════════════════
$('about-github').addEventListener('click', (e) => {
  e.preventDefault();
  api().shell_open_url('https://github.com/Tsukamotoshio/SumisoraOMR');
});
$('about-diag').addEventListener('click', async () => {
  $('about-diag').disabled = true;
  toast(t('w.about.diag_collecting'));
  try {
    const r = await api().about_copy_diagnostics();
    toast(r.ok ? t('w.about.diag_copied') : t('w.about.diag_failed', { e: r.error || '' }));
  } finally {
    $('about-diag').disabled = false;
  }
});

// ═══ 语言切换 ════════════════════════════════════════════════════════════════
function rerenderDynamic() {
  // 语言切换后重刷所有由 JS 渲染的动态区域（列表/计数/占位/模型状态）
  renderView('score');
  renderView('audio');
  jpRenderList();
  stRenderList();
  refreshModels();
}
$('langBtn').addEventListener('click', async () => {
  const next = I18N.lang === 'zh' ? 'en' : 'zh';
  const r = await api().i18n_set_language(next);
  if (!r.ok) return;
  I18N.lang = next;
  retranslate();
  rerenderDynamic();
});

// ═══ 初始化 ══════════════════════════════════════════════════════════════════
window.addEventListener('pywebviewready', async () => {
  // 先取文案目录（决定初始语言），再渲染其余部分
  try {
    const cat = await api().i18n_catalog();
    I18N.lang = cat.lang || 'zh';
    I18N.strings = cat.strings || {};
  } catch (_e) { /* 目录失败时回退键名显示，不阻断 */ }
  retranslate();
  api().app_info().then((info) => {
    $('ver').textContent = 'v' + (info.version || '');
    $('about-ver').textContent = 'v' + (info.version || '');
  });
  api().files_list().then((files) => {
    trayCache = files || [];
    renderView('score');
    renderView('audio');
  });
  refreshModels();
});
