// webui/static/js/editor.js — 简谱编辑页。
// 左栏双模式（参考图 / 渲染预览），右栏行号文本编辑器；保存/渲染/导出走桥，
// 头部 # 注释块由 Python 侧保护。从简谱预览页「编辑」进入。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, showPage } from './core.js';
import { PdfView } from './pdfview.js';

const edPvView = new PdfView($('ed-pv-canvas'), $('ed-pv-stage'), $('ed-pv-pageinfo'));
const edRefView = new PdfView($('ed-ref-canvas'), $('ed-ref-stage'), null);
let edDirty = false;
let edLoaded = false;

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

export async function edOpenForPdf(pdfPath) {
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
    $('ed-pv-ph').textContent = t('w.st.render_failed', { e: d.error || '' });
    return;
  }
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
