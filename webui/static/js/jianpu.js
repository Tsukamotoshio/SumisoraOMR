// webui/static/js/jianpu.js — 简谱预览页。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, pageEnterHooks } from './core.js';
import { PdfView } from './pdfview.js';
import { midiPlayer } from './midi.js';
import { edOpenForPdf, edApplyLoad } from './editor.js';

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

export function jpRenderList() {
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
    li.addEventListener('click', () => {
      if (e.path !== jpSel) midiPlayer.leave();  // 切到其它文件 → 立即停止并收起当前 MIDI 播放
      jpSel = e.path; jpRenderList(); jpOpenSelected();
    });
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

// ── 新建空白简谱（B8.4 轻量版） ──────────────────────────────────────────────
// 主音下拉与 webui/transpose.py:KEYS 同一份 15 个大调名（简谱调号用同一套主音
// 拼写，无论大调/小调），静态常量不值得为它单开一趟桥调用。
const NS_TONICS = ['Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#'];
let nsTonicsPopulated = false;
function nsPopulateTonics() {
  if (nsTonicsPopulated) return;
  const sel = $('ns-tonic');
  for (const k of NS_TONICS) {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = k.replace('#', '♯').replace('b', '♭');
    if (k === 'C') opt.selected = true;
    sel.appendChild(opt);
  }
  nsTonicsPopulated = true;
}

function nsResetForm() {
  $('ns-title').value = '';
  $('ns-composer').value = '';
  $('ns-key-mode').value = 'major';
  $('ns-tonic').value = 'C';
  $('ns-time-num').value = '4';
  $('ns-time-den').value = '4';
  $('ns-tempo').value = '120';
  $('ns-anacrusis').value = '';
  $('ns-measures').value = '16';
  $('ns-voices').value = '1';
  $('ns-error').classList.add('hidden');
}

function nsOpen() {
  nsPopulateTonics();
  nsResetForm();
  $('newscore-overlay').classList.remove('hidden');
  $('ns-title').focus();
}
function nsClose() { $('newscore-overlay').classList.add('hidden'); }

$('jp-new').addEventListener('click', nsOpen);
$('ns-close').addEventListener('click', nsClose);
$('ns-cancel').addEventListener('click', nsClose);
$('newscore-overlay').addEventListener('click', (e) => { if (e.target.id === 'newscore-overlay') nsClose(); });

async function nsCreate() {
  const title = $('ns-title').value.trim();
  if (!title) {
    $('ns-error').textContent = t('w.ns.err_title_required');
    $('ns-error').classList.remove('hidden');
    $('ns-title').focus();
    return;
  }
  $('ns-create').disabled = true;
  try {
    const r = await api().editor_create_blank({
      title,
      composer: $('ns-composer').value.trim(),
      key_mode: $('ns-key-mode').value,
      tonic: $('ns-tonic').value,
      time_num: Number($('ns-time-num').value) || 4,
      time_den: Number($('ns-time-den').value) || 4,
      tempo: Number($('ns-tempo').value) || 120,
      anacrusis: $('ns-anacrusis').value,
      measure_count: Number($('ns-measures').value) || 16,
      voice_count: Number($('ns-voices').value) || 1,
    });
    if (!r.ok) {
      $('ns-error').textContent = t('w.ns.err_create_failed', { e: r.error || '' });
      $('ns-error').classList.remove('hidden');
      return;
    }
    nsClose();
    edApplyLoad(r);
  } finally {
    $('ns-create').disabled = false;
  }
}
$('ns-create').addEventListener('click', nsCreate);
