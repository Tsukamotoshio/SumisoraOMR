// webui/static/js/transpose.js — 移调页。
// 三种模式（按调/按音程/按度数）分发到 core.notation.transposer；原调/移调后
// 双栏对比预览；导出 = 渲染五线谱 PDF 另存。从五线谱预览页的「移调」按钮进入。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, showPage } from './core.js';
import { PdfView } from './pdfview.js';

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

export async function tpLoad(path) {
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
