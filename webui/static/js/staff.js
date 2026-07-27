// webui/static/js/staff.js — 五线谱预览页。
// 与简谱页同构；差异：预览需按需 LilyPond 渲染（scores_preview 可能异步，等
// score_preview_ready 事件），MIDI 缺失时确认后生成再播放，多一个「移调」入口。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, pageEnterHooks, showPage } from './core.js';
import { PdfView } from './pdfview.js';
import { midiPlayer } from './midi.js';
import { tpLoad } from './transpose.js';

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

export function stRenderList() {
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
    li.addEventListener('click', () => {
      if (e.path !== stSel) midiPlayer.leave();  // 切到其它文件 → 立即停止并收起当前 MIDI 播放
      stSel = e.path; stRenderList(); stOpenSelected();
    });
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
