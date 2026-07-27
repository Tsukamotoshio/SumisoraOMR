// webui/static/js/convert.js — 转换流程 + 进度浮层 + 结果弹层。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
// 注：本模块不导出任何符号，仅注册事件监听；main.js 必须显式 side-effect import。
'use strict';

import { $, api, t, toast, showPage } from './core.js';
import { startModelDownload } from './models.js';

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
