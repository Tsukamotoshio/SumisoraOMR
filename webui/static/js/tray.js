// webui/static/js/tray.js — 文件托盘（分视图渲染；Python 端共享一份托盘）+
// 乐谱识别页预览（PDF 走 PdfView，PNG/JPG 走独立 .a4frame + <img>）。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast } from './core.js';
import { PdfView, makeImageZoom, attachFrameDrag } from './pdfview.js';

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

export function renderView(view) {
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

// 启动初始化：拉一次文件列表并渲染两个视图。原为 app.js 末尾 pywebviewready
// 初始化块里的内联代码；trayCache 是本模块私有变量（ES module 具名导入是只读
// 绑定，外部模块无法直接对其赋值），故在此包一层导出函数供 main.js 调用——
// 是模块边界要求的必要调整，行为与原来完全一致。
export async function initFileTray() {
  const files = await api().files_list();
  trayCache = files || [];
  renderView('score');
  renderView('audio');
}

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
