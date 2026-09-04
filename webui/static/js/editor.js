// webui/static/js/editor.js — 简谱编辑页。
// 左栏双模式（参考图 / 渲染预览），右栏行号文本编辑器；保存/渲染/导出走桥，
// 头部 # 注释块由 Python 侧保护。从简谱预览页「编辑」进入。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, showPage } from './core.js';
import { PdfView } from './pdfview.js';
import { lintJianpuText, isHeaderLine } from './jianpu-lint.js';
import { jianpuPlayer, activeNotesAt } from './jianpu-play.js';
import {
  EditHistory, KEY_TONICS, barQuarterLength, blankMeasureNotes, extractFragment,
  formatKeyHeader, insertConsumingCommands, noteRef, orderBatch, parseKeyHeader,
  pasteMeasuresCommands, pasteNotesCommands, selectionSpan, steppedDuration,
} from './jianpu-edit.js';

const edPvView = new PdfView($('ed-pv-canvas'), $('ed-pv-stage'), $('ed-pv-pageinfo'));
const edRefView = new PdfView($('ed-ref-canvas'), $('ed-ref-stage'), null);
let edDirty = false;
let edLoaded = false;

// ── 语法高亮 + 实时校验（阶段1，见修复计划2与简谱编辑器规划.md B5/B6） ──────────
// .ed-text 本身文字透明，可见的彩色文字来自 .ed-highlight 的镜像内容（见 app.css
// 的说明注释）；两者绝对定位重叠，靠 edSyncHighlightScroll() 同步滚动位置。
const CLASS_FOR_SEVERITY = { error: 'tok-err', warning: 'measure-warn', info: 'tok-info' };
let edDiagnostics = [];
let edLastToastKey = '';
let edToastTimer = null;

function edEscapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// 逐行标出注释（%…）/ 表头（title=、1=C、拍号…）行的字符区间，供高亮层染色；
// 纯视觉，不追求语法层面的绝对精度（精度要求见 jianpu-lint.js 的诊断逻辑）。
function edClassifyLines(text) {
  const ranges = [];
  let offset = 0;
  for (const rawLine of text.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    const trimmed = line.trim();
    let cls = null;
    if (trimmed.startsWith('%')) cls = 'tok-comment';
    else if (trimmed && isHeaderLine(trimmed)) cls = 'tok-header';
    if (cls) ranges.push({ start: offset, end: offset + rawLine.length, cls });
    offset += rawLine.length + 1;
  }
  return ranges;
}

function edBuildHighlightHtml(text) {
  const { diagnostics } = lintJianpuText(text);
  const n = text.length;
  const base = new Array(n).fill('');
  const overlay = new Array(n).fill('');
  for (const r of edClassifyLines(text)) {
    for (let i = r.start; i < r.end && i < n; i++) base[i] = r.cls;
  }
  for (let i = 0; i < n; i++) {
    if (text[i] === '|' && !base[i]) overlay[i] = 'tok-bar';
  }
  for (const d of diagnostics) {
    const cls = CLASS_FOR_SEVERITY[d.severity];
    for (let i = d.start; i < d.end && i < n; i++) {
      overlay[i] = overlay[i] ? `${overlay[i]} ${cls}` : cls;
    }
  }
  let html = '';
  let i = 0;
  while (i < n) {
    const cls = [base[i], overlay[i]].filter(Boolean).join(' ');
    let j = i + 1;
    while (j < n && [base[j], overlay[j]].filter(Boolean).join(' ') === cls) j++;
    const chunk = edEscapeHtml(text.slice(i, j));
    html += cls ? `<span class="${cls}">${chunk}</span>` : chunk;
    i = j;
  }
  return { html, diagnostics };
}

function edSyncHighlightScroll() {
  const hl = $('ed-highlight');
  const ta = $('ed-text');
  hl.scrollTop = ta.scrollTop;
  hl.scrollLeft = ta.scrollLeft;
}

function edJumpTo(start, end) {
  const ta = $('ed-text');
  if (ta.disabled) return;
  ta.focus();
  ta.setSelectionRange(start, end);
  const lineIdx = (ta.value.slice(0, start).match(/\n/g) || []).length;
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 21;
  ta.scrollTop = Math.max(0, lineIdx * lineHeight - ta.clientHeight / 2);
  edUpdateGutter();
  edSyncHighlightScroll();
}

// 汇总而非刷屏：同类问题合并计数一条 toast；诊断集合不变时不重复弹出。
function edShowLintToasts(diagnostics) {
  const errors = diagnostics.filter((d) => d.severity === 'error');
  const warnings = diagnostics.filter((d) => d.severity === 'warning');
  const key = `${errors.length}:${errors[0] ? errors[0].start : ''}:${warnings.length}:${warnings[0] ? warnings[0].start : ''}`;
  if (key === edLastToastKey) return;
  edLastToastKey = key;
  if (errors.length === 1) {
    const e = errors[0];
    toast(t('w.ed.lint.error_at', { line: e.line, token: e.params.token || '' }), {
      severity: 'error', onClick: () => edJumpTo(e.start, e.end),
    });
  } else if (errors.length > 1) {
    toast(t('w.ed.lint.errors_toast', { n: errors.length }), {
      severity: 'error', onClick: () => edJumpTo(errors[0].start, errors[0].end),
    });
  }
  if (warnings.length === 1) {
    const wDiag = warnings[0];
    toast(t('w.ed.lint.warning_at', { line: wDiag.line, got: wDiag.params.got, expected: wDiag.params.expected }), {
      severity: 'warning', onClick: () => edJumpTo(wDiag.start, wDiag.end),
    });
  } else if (warnings.length > 1) {
    toast(t('w.ed.lint.warnings_toast', { n: warnings.length }), {
      severity: 'warning', onClick: () => edJumpTo(warnings[0].start, warnings[0].end),
    });
  }
}

function edRunLint() {
  const { html, diagnostics } = edBuildHighlightHtml($('ed-text').value);
  $('ed-highlight').innerHTML = html;
  edSyncHighlightScroll();
  edDiagnostics = diagnostics;
  clearTimeout(edToastTimer);
  edToastTimer = setTimeout(() => edShowLintToasts(diagnostics), 300);
}

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
  $('ed-tab-gr').setAttribute('aria-selected', String(which === 'gr'));
  $('ed-ref-stage').classList.toggle('hidden', which !== 'ref');
  $('ed-pv-stage').classList.toggle('hidden', which !== 'pv');
  $('ed-gr-stage').classList.toggle('hidden', which !== 'gr');
  // 离开图形 tab 就停播：播放控件此刻已不可见，让声音在看不见的地方继续响
  // （而且用户找不到暂停按钮）是明显的坏交互。
  if (which !== 'gr') jianpuPlayer.stop();
}
$('ed-tab-ref').addEventListener('click', () => edShowTab('ref'));
$('ed-tab-pv').addEventListener('click', () => edShowTab('pv'));
$('ed-tab-gr').addEventListener('click', () => {
  edShowTab('gr');
  // 切到本 tab 时如果期间有过编辑（edGraphicalStale），或者干脆还没渲染过，就拉一次
  if (edGraphicalStale || !$('ed-gr-container').firstChild) edRenderGraphical();
});

// ── 图形化渲染（阶段3.2 只读渲染 + 阶段3.5 多声部 + 阶段3.6 实时更新）────────
// fork 自 flufy3d/JianpuRender（webui/static/vendor/jianpu-render/，见修复计划2
// 与简谱编辑器规划.md B9.3.1/B6 阶段3.2），构建产物 window.jr 走全局脚本注入，
// 与 tinysynth（midi.js）同一套懒加载手法。
let _jianpuRenderLibPromise = null;
function edLoadJianpuRenderLib() {
  if (_jianpuRenderLibPromise) return _jianpuRenderLibPromise;
  _jianpuRenderLibPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '../vendor/jianpu-render/dist/jianpurender.js';
    s.onload = () => resolve(window.jr);
    s.onerror = () => reject(new Error('jianpu-render load failed'));
    document.head.appendChild(s);
  });
  return _jianpuRenderLibPromise;
}

/**
 * 把一组 renders 画到容器里。两条路都走它：文本改动后的重新解析（阶段3.6），
 * 以及图形编辑后由模型直接算出的新投影（阶段5.3b）。两者共用同一段绘制代码，
 * 才不会出现"从文本来的画法"和"从模型来的画法"两套逐渐走样的实现。
 */
async function edDrawRenders(renders) {
  const jr = await edLoadJianpuRenderLib();
  const container = $('ed-gr-container');
  // 重绘会重建全部 DOM，横向滚动位置会归零；先记下来再还原，否则用户每敲一个
  // 字都会被弹回谱面开头。JianpuRender 自己在容器里套了一层可滚动 div。
  const scrollLefts = [...container.querySelectorAll('.graphical-staff > div')].map((d) => d.scrollLeft);
  container.replaceChildren();
  // 阶段3.5：每个 NextPart 分段各自一个独立 renderer 实例，纵向堆叠成多行
  // 谱表——JianpuRender 本身没有"同一谱表多声部叠放"的概念（见 B9.3.1 spike
  // 结论），这是本项目自己在外层做的编排，不是改 fork 内部。
  edRenderers = renders.map((render) => {
    const staff = document.createElement('div');
    staff.className = 'graphical-staff';
    container.appendChild(staff);
    // 留住实例：阶段4.2 的播放高亮要对每个分段调它自己的 redraw(note, true)。
    return new jr.JianpuSVGRender(render, { noteHeight: 24 }, staff);
  });
  edHighlighted = [];   // 实例全换了，上一轮记的"已高亮音符"作废
  // 阶段5.2：建 data-id → 该分段音符下标 的索引，供点击命中测试反查。
  // id 一律用 fork 自己的 noteElementId() 现算，不在这边另写一份同样的公式——
  // 那样两处一旦漂移（比如哪天量化精度改了），命中测试会静默失灵。
  edSectionNotes = renders.map((render) => render.notes || []);
  // 阶段5.4：槽位（含休止符与延音横线）是录入光标的落点，音符投影里没有它们。
  edSectionSlots = renders.map((render) => render.slots || []);
  edSectionIdIndex = edSectionNotes.map((notes) => {
    const map = new Map();
    notes.forEach((n, i) => map.set(jr.noteElementId(n.start, n.pitch), i));
    return map;
  });
  const scrollables = container.querySelectorAll('.graphical-staff > div');
  scrollables.forEach((d, i) => { if (scrollLefts[i] !== undefined) d.scrollLeft = scrollLefts[i]; });
  // 阶段4.1：播放引擎吃的是同一份 renders 数据（声音与画面同源）。
  // load() 会停掉正在进行的播放——内容已经变了，继续按旧时间轴放会驴唇不对马嘴。
  jianpuPlayer.load(renders);
  edSyncTransport();
}

// 阶段3.6 实时更新：输入 → 防抖 → 重新解析并重绘。
// 防抖而不是逐键立即重绘，是因为解析在 Python 侧（阶段2 的解析器是唯一事实源，
// 不在前端重写一份），每次都要走一趟 pywebview 桥 IPC；逐键触发只会堆积一串
// 必然被下一次覆盖掉的往返。ED_GRAPHICAL_DEBOUNCE_MS 取 200ms：低于常人连续
// 打字的间隔（停手就更新），又足以把一个词的连续击键合并成一次渲染。
const ED_GRAPHICAL_DEBOUNCE_MS = 200;
let edGraphicalTimer = null;
let edGraphicalStale = false;   // 不在图形 tab 时发生过编辑 → 切回来要重拉
let edGraphicalSeq = 0;         // 作废迟到的响应（后发先至时不覆盖新结果）
let edGraphicalRendered = false; // 是否已有一份成功渲染的内容挂在容器里
let edRenderers = [];           // 每个分段一个 JianpuSVGRender 实例（阶段4.2 高亮要用）
let edSectionNotes = [];        // 每个分段的 render 音符数组（阶段5.2 命中测试要用）
let edSectionIdIndex = [];      // 每个分段：data-id → 该分段音符下标
let edSelection = null;         // { section, anchor, focus }（下标闭区间，anchor 可大可小）
let edDoc = null;               // 常驻模型（阶段5.3a 起由桥送来，事实源）
let edHistory = null;           // 该模型上的撤销/重做栈（阶段5.1）
// 阶段5.3b 的副作用：模型成为事实源之后，首次图形编辑会把整份文本按模型重新
// 生成，于是解析器没收进模型的东西（整行 % 注释、原有的换行版面）就没了。
// Python 侧拿真实序列化器实测这份文件会丢什么并回填 lossy；这里只负责在**第一次
// 真正动手改之前**问一次。为什么不在打开时就提示：只是切到图形页看一眼并不会
// 触发写回，那样问纯属打扰。
let edLossy = null;             // { comments?: number, layout?: true } | null
let edLossyAcked = false;       // 本文件已经问过并被确认，不再重复打断

function edScheduleGraphicalRender() {
  if (!edLoaded) return;
  if ($('ed-gr-stage').classList.contains('hidden')) {
    edGraphicalStale = true;  // 当前看不见，不浪费一次桥调用；切回来时再补
    return;
  }
  clearTimeout(edGraphicalTimer);
  edGraphicalTimer = setTimeout(() => edRenderGraphical({ quiet: true }), ED_GRAPHICAL_DEBOUNCE_MS);
}

// quiet=true 用于实时重绘：不闪"加载中"占位符、不因为中途的临时语法错误就把已经
// 画好的谱面清空——打字打到一半（比如刚敲下 "q" 还没敲数字）文本必然短暂非法，
// 这时把整张谱擦掉换成红字是最难受的交互。保留上一版可见内容，让阶段1 的实时校验
// 层（波浪线 + toast）去负责告诉用户哪里有错，各司其职。
async function edRenderGraphical({ quiet = false } = {}) {
  if (!edLoaded) return;
  const ph = $('ed-gr-ph');
  const seq = ++edGraphicalSeq;
  edGraphicalStale = false;
  if (!quiet || !edGraphicalRendered) {
    ph.parentElement.classList.remove('hidden');
    ph.textContent = t('w.ed.graphical_placeholder');
  }
  const r = await api().editor_graphical_data($('ed-text').value);
  if (seq !== edGraphicalSeq) return;  // 已有更新的一次渲染在路上，丢弃本次结果
  if (!r.ok) {
    if (quiet && edGraphicalRendered) return;  // 实时重绘遇到临时错误：保留旧画面
    edGraphicalRendered = false;
    ph.parentElement.classList.remove('hidden');
    ph.textContent = r.error === 'parse_error'
      ? t('w.ed.graphical_parse_error', { line: r.line, message: r.message || '' })
      : t('w.ed.graphical_failed', { e: r.error || '' });
    return;
  }
  try {
    if (seq !== edGraphicalSeq) return;
    await edDrawRenders(r.renders);
    // 阶段5.3a：常驻模型。文本侧发生改动 → 整份模型换新（文本是这一次编辑的
    // 源头）；图形侧编辑则反过来由模型生成文本。任一时刻只有一个方向在动，
    // 这样就不存在"两份表示互相同步"那种永远修不完的 bug（B4 第1条）。
    edDoc = r.doc || null;
    edHistory = edDoc ? new EditHistory(edDoc) : null;
    edSyncHeaderForm();
    edLossy = r.lossy || null;      // 本文件写回时会丢什么（Python 侧实测得出）
    // 文本重解析后音符下标可能已不指向原来那个音符，选中态作废。
    edSelection = null;
    edRenderSelection();
    edGraphicalRendered = true;
    ph.parentElement.classList.add('hidden');
  } catch (e) {
    edGraphicalRendered = false;
    ph.parentElement.classList.remove('hidden');
    ph.textContent = t('w.ed.graphical_failed', { e: String(e) });
  }
}

// ── 播放控件（阶段4.1）───────────────────────────────────────────────────────
function edFmtTime(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function edSyncTransport() {
  const bar = $('ed-gr-transport');
  bar.classList.toggle('hidden', !jianpuPlayer.duration);
  const playing = jianpuPlayer.isPlaying;
  const btn = $('ed-gr-play');
  btn.innerHTML = playing
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  btn.title = t(playing ? 'w.ed.pause' : 'w.ed.play');
  $('ed-gr-time').textContent = `${edFmtTime(jianpuPlayer.positionSec)} / ${edFmtTime(jianpuPlayer.duration)}`;
}

// ── 播放高亮 + 自动滚动（阶段4.2）─────────────────────────────────────────────
// 每帧读一次 AudioContext 时钟算出"此刻哪个音符在响"，再交给对应分段的
// renderer 高亮。**位置来自发声用的同一个时钟**，不是另起一套定时器自己数拍子——
// 这正是"误差 < 1 帧"能成立的原因：高亮误差的上界就是一帧的间隔本身，掉帧只会
// 让高亮少更新几次，不会让它跟声音越漂越远。
// `redraw(note, true)` 的第二个参数就是 fork 自带的按需自动滚动。
let edHighlighted = [];   // 每个分段上一帧高亮的音符（用来判断"变了没"，避免每帧重画）

function edUpdateHighlight() {
  if (!edRenderers.length) return;
  const active = activeNotesAt(jianpuPlayer.notes, jianpuPlayer.positionSec);
  for (let i = 0; i < edRenderers.length; i++) {
    const note = active.get(i);
    if (note === edHighlighted[i]) continue;   // 这一分段没换音符，不必重画
    edHighlighted[i] = note;
    if (note) edRenderers[i].redraw(note, true);
    else edRenderers[i].clearHighlight();
  }
}

// ── 鼠标选择（阶段5.2）────────────────────────────────────────────────────────
// 选中态**不能**复用 fork 的高亮机制：那套是直接改 `<text>`/`<path>` 的 fill
// （见 vendor 里的 highlightElement），播放高亮一染色就会把选中色盖掉，反之亦然。
// 所以选中用另一条视觉通道——在音符分组里插一个垫在最底下的圆角矩形。fork 的
// highlight/reset 只挑 text/path 处理，碰不到 rect，两者因此可以同时存在：
// 一个音符可以既"被选中"（蓝底）又"正在响"（红字）。
const SVGNS = 'http://www.w3.org/2000/svg';

/** 选区归一化成闭区间 [from, to]（anchor 在后、focus 在前时也要正确）。 */
function edSelectionRange() {
  if (!edSelection) return null;
  const { section, anchor, focus } = edSelection;
  return { section, from: Math.min(anchor, focus), to: Math.max(anchor, focus) };
}

function edRenderSelection() {
  const container = $('ed-gr-container');
  container.querySelectorAll('.jp-sel-rect').forEach((el) => el.remove());
  const range = edSelectionRange();
  if (!range) return;
  const staff = container.children[range.section];
  if (!staff) return;
  const notes = edSectionNotes[range.section] || [];
  for (let i = range.from; i <= range.to; i++) {
    const note = notes[i];
    if (!note) continue;
    const g = staff.querySelector(`g[data-id="${CSS.escape(window.jr.noteElementId(note.start, note.pitch))}"]`);
    if (!g) continue;
    let box;
    try {
      box = g.getBBox();
    } catch (_e) {
      continue;   // 未布局的元素取 bbox 会抛，跳过即可
    }
    if (!box || box.width <= 0) continue;
    const pad = 2;
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('class', 'jp-sel-rect');
    rect.setAttribute('x', `${box.x - pad}`);
    rect.setAttribute('y', `${box.y - pad}`);
    rect.setAttribute('width', `${box.width + pad * 2}`);
    rect.setAttribute('height', `${box.height + pad * 2}`);
    rect.setAttribute('rx', '3');
    // 垫到最底下，否则会盖住音符数字。
    g.insertBefore(rect, g.firstChild);
  }
}

function edSetSelection(section, index, extend) {
  if (extend && edSelection && edSelection.section === section) {
    edSelection = { section, anchor: edSelection.anchor, focus: index };
  } else {
    // Shift 点到了另一个声部：跨谱表选区在音乐上没有意义（那是两条独立声部），
    // 直接把锚点挪过去、当成在新声部里重新起选。
    edSelection = { section, anchor: index, focus: index };
  }
  edRenderSelection();
}

function edClearSelection() {
  if (!edSelection) return;
  edSelection = null;
  edRenderSelection();
}

$('ed-gr-container').addEventListener('click', (e) => {
  const g = e.target.closest ? e.target.closest('g[data-id]') : null;
  const staff = e.target.closest ? e.target.closest('.graphical-staff') : null;
  if (!g || !staff) { edClearSelection(); return; }   // 点空白处 = 取消选择
  const section = [...$('ed-gr-container').children].indexOf(staff);
  const index = section >= 0 && edSectionIdIndex[section]
    ? edSectionIdIndex[section].get(g.getAttribute('data-id'))
    : undefined;
  // 命中的可能是休止符块之类没有对应 render 音符的分组——那些不可选。
  if (index === undefined) { edClearSelection(); return; }
  edSetSelection(section, index, e.shiftKey);
});

// ── 键盘编辑（阶段5.3b）──────────────────────────────────────────────────────
// 一次按键 → 一条语义命令 → 命令栈（阶段5.1）改常驻模型 → 模型过桥序列化
// （阶段5.3a）→ 回来的文本灌进 textarea、回来的 renders 直接重画。
// 注意方向：**模型是事实源**，文本和图形都是它的投影。所以这里不去拼文本、
// 也不重新解析，只把模型算出来的结果铺到两个视图上。

/** 选中焦点音符在模型里的地址（渲染投影是有损的，靠 Python 带回的 ref 反查）。 */
function edFocusedRef() {
  if (!edSelection) return null;
  const notes = edSectionNotes[edSelection.section] || [];
  const note = notes[edSelection.focus];
  if (!note || !note.ref) return null;
  return noteRef(edSelection.section, note.ref.measure, note.ref.index);
}

/** 编辑后把选区落回同一个音符；音符已被删掉时退而选原位置上的那个。 */
function edReselect(ref, fallbackIndex, lastRef) {
  if (!ref) return;
  const notes = edSectionNotes[ref.section] || [];
  const locate = (r) => notes.findIndex(
    (n) => n.ref && n.ref.measure === r.measure && n.ref.index === r.index);
  let index = locate(ref);
  if (index < 0) index = Math.min(fallbackIndex, notes.length - 1);
  if (index < 0) { edSelection = null; edRenderSelection(); return; }
  // 批量变换之后整段应当仍然选中——否则用户想连着按两次（比如升八度再升一次）
  // 第二次就只作用在一个音符上了。lastRef 缺省时退化成原来的单音符行为。
  let focus = index;
  if (lastRef) {
    const last = locate(lastRef);
    if (last >= 0) focus = last;
  }
  edSelection = { section: ref.section, anchor: index, focus };
  edRenderSelection();
}

/** 把当前模型送去序列化，并用返回结果刷新两个视图。 */
async function edPushModel(keepRef, fallbackIndex, lastRef) {
  const r = await api().editor_apply_doc(edDoc);
  if (!r.ok) {
    toast(t('w.ed.edit_failed', { e: r.error || '' }), { severity: 'error' });
    return;
  }
  // 程序化赋值不会触发 'input' 事件，所以不会反过来又踢一次"文本改了→重解析"
  // 的防抖链路（那会把刚生成的模型再解析一遍，白跑且可能覆盖选区）。代价是
  // input 处理器顺带做的那几件事得在这里自己做一遍。
  const ta = $('ed-text');
  ta.value = r.text;
  edSetDirty(true);
  edUpdateGutter();
  edRunLint();
  clearTimeout(edGraphicalTimer);   // 作废可能还在等的那次文本触发重绘
  await edDrawRenders(r.renders);
  edReselect(keepRef, fallbackIndex, lastRef);
  edRenderCursor();
  edSyncHeaderForm();
}

/**
 * 首次图形编辑前的一次性确认。返回 false 表示用户选择了取消，调用方必须在
 * **改动模型之前**就此收手——所以每个会改模型的入口都在最前面 await 它，而不是
 * 等到 edPushModel 再拦（那时命令已经施加，还得再撤回去）。
 */
async function edConfirmFirstEdit() {
  if (!edLossy || edLossyAcked) return true;
  const parts = [];
  if (edLossy.comments) parts.push(t('w.ed.lossy_comments', { n: edLossy.comments }));
  if (edLossy.layout) parts.push(t('w.ed.lossy_layout'));
  if (!confirm(t('w.ed.lossy_confirm', { what: parts.join(t('w.ed.lossy_join')) }))) return false;
  edLossyAcked = true;
  return true;
}

/** 选区里每个音符的模型地址，按谱面顺序（阶段5.6a 批量变换的作用域）。 */
function edSelectedRefs() {
  const range = edSelectionRange();
  if (!range) return [];
  const notes = edSectionNotes[range.section] || [];
  const refs = [];
  for (let i = range.from; i <= range.to; i++) {
    const note = notes[i];
    if (note && note.ref) refs.push(noteRef(range.section, note.ref.measure, note.ref.index));
  }
  return refs;
}

/**
 * 对整个选区施加一批命令，作为**一次**撤销（阶段5.6a）。
 *
 * 选中什么就作用在什么上——选区在谱面上是看得见的蓝底，所以这不会造成"我以为
 * 只改一个音符"的意外，而且与 MuseScore 的行为一致。
 *
 * *cmds* 与 *refs* 一一对应；某个音符上这个键没有意义时（比如时值已到阶梯尽头）
 * 对应项是 null，orderBatch 会把它滤掉——其余音符照常生效，不因一个到顶的音符
 * 让整次操作作废。
 */
async function edRunSelectionCommands(cmds, refs) {
  if (!edDoc || !edHistory) return;
  if (!await edConfirmFirstEdit()) return;
  const ordered = orderBatch(cmds);
  if (!ordered.length) return;
  const structural = ordered.some((c) => c.type === 'delete_note');
  const fallbackIndex = edSelection ? Math.min(edSelection.anchor, edSelection.focus) : 0;
  if (!edHistory.doGroup(ordered)) return;
  if (structural) {
    // 选中的音符已经不存在了，没有"原位置"可留——显式清空，别让 edSelection
    // 带着旧下标继续指向别的音符（5.5a 删小节时踩过同一个坑）。
    edSelection = null;
    await edPushModel(null, 0);
    return;
  }
  await edPushModel(refs[0], fallbackIndex, refs[refs.length - 1]);
}

async function edUndoRedo(redo) {
  if (!edDoc || !edHistory) return;
  const keepRef = edFocusedRef();
  const fallbackIndex = edSelection ? edSelection.focus : 0;
  if (!(redo ? edHistory.redo() : edHistory.undo())) return;
  await edPushModel(keepRef, fallbackIndex);
}

/** 按键 → 命令。返回 null 表示这个键与编辑无关，交回给浏览器。 */
function edCommandForKey(e, ref) {
  if (e.key >= '1' && e.key <= '7') return { type: 'set_pitch', ref, symbol: e.key };
  if (e.key === '0') return { type: 'set_rest', ref };
  if (e.key === 'ArrowUp') return { type: 'set_octave', ref, delta: 1 };
  if (e.key === 'ArrowDown') return { type: 'set_octave', ref, delta: -1 };
  if (e.key === '.') return { type: 'toggle_dot', ref };
  if (e.key === '#') return { type: 'set_accidental', ref, accidental: '#' };
  if (e.key === 'b') return { type: 'set_accidental', ref, accidental: 'b' };
  if (e.key === 'n') return { type: 'set_accidental', ref, accidental: '' };
  if (e.key === 'Delete' || e.key === 'Backspace') return { type: 'delete_note', ref };
  if (e.key === '+' || e.key === '=' || e.key === '-') {
    // 时值靠模型里的当前值算下一档，而不是靠渲染投影里的 length——投影已经把
    // 延音横线折进前一个音符，读它会算错。批量时每个音符各算各的，所以这里必须
    // 按 ref 取，不能沿用焦点音符。
    const measure = (edDoc.sections[ref.section] || {}).measures;
    const model = measure && measure[ref.measure] && measure[ref.measure][ref.index];
    if (!model) return null;
    const next = steppedDuration(model, e.key === '-' ? -1 : 1);
    return next ? { type: 'set_duration', ref, ...next } : null;
  }
  return null;
}

// ── 录入模式（阶段5.4，B5① 已定的"甲：显式双模式"）──────────────────────────
// 两种模式泾渭分明，照抄 MuseScore 的心智模型：
//   选择模式（默认）：按 1–7 是**改**光标所在音符的音高；
//   录入模式（Shift+N 进出）：按 1–7 是**插入**一个新音符，光标随之前进。
// 同一个键在两种模式下做两件事，正是显式双模式的全部意义——不必为"插入"另找
// 一套组合键，代价是用户得知道自己在哪个模式里，所以模式必须一眼可见（工具条
// 上的胶囊标签 + 谱面上闪烁的插入光标）。
//
// 为什么是 Shift+N 而不是裸 n：小写 n 在选择模式下已经是"还原号"（本位音），
// 那是 B5④ 定的常用键。大写 N 与计划里写的"N 键"字面一致，又不与之冲突。
//
// 光标与选中是两个东西。选中指向**一个已存在的音符**，只能落在画得出来的音符
// 上；而录入光标指向**下一个音符会插进哪里**，必须能停在休止符和延音横线上——
// 空白谱通篇就只有这两样东西（4/4 的空小节是 `0 - - -`，解析成 1 个休止符 +
// 3 个延音横线），光标要是也只能停在音符上，在空白谱上就无处可去，而"从零录入
// 一个小节"恰恰是本阶段的验收目标。这就是 Python 侧除 notes 之外还要送一份
// slots（每个模型音符一个，含休止符与横线）的原因。

let edInputMode = false;
let edCursor = null;            // 模型地址 {section, measure, index}；index 可等于小节长度（末尾追加位）
let edSectionSlots = [];        // 每个分段的槽位数组（Python 送来，含休止符/横线）
// 录入时的"当前音符属性"，沿用 MuseScore 的黏滞语义：设一次，之后每个新音符都
// 用它，直到再改。比"每个音符敲完再修"少一半按键，也让 1234 连打的结果可预期。
let edInputAttrs = { duration: 1.0, duration_dots: 0, octave: 0, accidental: '' };

/** 取某个分段的槽位数组。 */
function edSlotsOf(section) {
  return edSectionSlots[section] || [];
}

/** 光标所在槽位的起始时刻（四分音符为单位）；末尾追加位取谱段总长。 */
function edCursorStart() {
  if (!edCursor) return null;
  const slots = edSlotsOf(edCursor.section);
  const hit = slots.find(
    (s) => s.ref.measure === edCursor.measure && s.ref.index === edCursor.index);
  if (hit) return hit.start;
  // 追加位：落在最后一个槽位的末尾。
  const before = slots.filter(
    (s) => s.ref.measure < edCursor.measure
      || (s.ref.measure === edCursor.measure && s.ref.index < edCursor.index));
  if (!before.length) return slots.length ? slots[0].start : 0;
  const last = before[before.length - 1];
  return last.start + (last.duration || 0);
}

/**
 * 把一个时刻换算成谱面上的 x 坐标。
 *
 * 靠 fork 给每个块留的 `data-block-start` 反查：找到起点不晚于该时刻的最后一
 * 个块，再按时刻在块内的比例插值。为什么要插值而不是直接用块的左边缘——
 * JianpuRender 会把一段连续的空隙**合并成一个休止块**，空白小节的四个槽位很
 * 可能只对应一个块；不插值的话四个光标位置会叠在同一个 x 上，看起来像没动。
 * 块长从下一个块的起点推出来，全程只依赖 DOM，不去猜 fork 内部怎么切块。
 */
function edCaretGeometry(staff, atStart) {
  const groups = [...staff.querySelectorAll('[data-block-start]')]
    .map((el) => ({ el, start: parseFloat(el.getAttribute('data-block-start')) }))
    .filter((b) => Number.isFinite(b.start))
    .sort((a, b) => a.start - b.start);
  if (!groups.length) return null;

  let minY = Infinity;
  let maxY = -Infinity;
  for (const b of groups) {
    try {
      b.box = b.el.getBBox();
    } catch (_e) {
      b.box = null;                 // 未布局的元素取 bbox 会抛
    }
    if (b.box && b.box.height > 0) {
      minY = Math.min(minY, b.box.y);
      maxY = Math.max(maxY, b.box.y + b.box.height);
    }
  }
  if (!Number.isFinite(minY)) return null;

  let i = 0;
  while (i + 1 < groups.length && groups[i + 1].start <= atStart + 1e-9) i++;
  const block = groups[i];
  if (!block.box) return null;
  const next = groups[i + 1];
  const span = next ? next.start - block.start : 0;
  const ratio = span > 1e-9 ? Math.min(1, Math.max(0, (atStart - block.start) / span)) : 0;
  const pad = 4;
  return {
    x: block.box.x + ratio * block.box.width,
    y: minY - pad,
    height: (maxY - minY) + pad * 2,
  };
}

function edRenderCursor() {
  const container = $('ed-gr-container');
  container.querySelectorAll('.jp-caret').forEach((el) => el.remove());
  if (!edInputMode || !edCursor) return;
  const staff = container.children[edCursor.section];
  if (!staff) return;
  const at = edCursorStart();
  if (at === null) return;
  const geom = edCaretGeometry(staff, at);
  if (!geom) return;
  const svg = staff.querySelector('svg');
  if (!svg) return;
  const rect = document.createElementNS(SVGNS, 'rect');
  rect.setAttribute('class', 'jp-caret');
  rect.setAttribute('x', `${geom.x - 1}`);
  rect.setAttribute('y', `${geom.y}`);
  rect.setAttribute('width', '2');
  rect.setAttribute('height', `${geom.height}`);
  svg.appendChild(rect);
}

/** 工具条上的模式标签：模式 + 当前时值/八度/临时记号，一眼看全录入状态。 */
function edRenderModeChip() {
  const chip = $('ed-gr-mode');
  chip.classList.toggle('hidden', !edInputMode);
  if (!edInputMode) return;
  const { duration, duration_dots: dots, octave, accidental } = edInputAttrs;
  const beats = dots > 0 ? `${duration / 1.5}.` : `${duration}`;
  const marks = (accidental || '')
    + (octave > 0 ? '↑'.repeat(octave) : '')
    + (octave < 0 ? '↓'.repeat(-octave) : '');
  chip.textContent = `${t('w.ed.input_mode')} ${beats}${marks ? ' ' + marks : ''}`;
}

/** 把光标挪到合法位置：越过小节末尾就进下一小节，全谱末尾停在追加位。 */
function edNormalizeCursor() {
  if (!edCursor || !edDoc) return;
  const section = edDoc.sections[edCursor.section];
  if (!section || !section.measures.length) { edCursor = null; return; }
  let { measure, index } = edCursor;
  // 先把小节号夹回有效范围。删掉末小节之后光标会正好指到"末小节+1"，而下面
  // 那个 while 只搬运 index 的溢出、从不动越界的 measure——不夹的话光标会停
  // 在一个不存在的小节上：谱面上照常画着，敲音符却什么都不发生（插入命令拿
  // 不到那个小节，直接被拒绝），而且没有任何提示。
  measure = Math.max(0, Math.min(measure, section.measures.length - 1));
  while (measure < section.measures.length - 1 && index >= section.measures[measure].length) {
    index -= section.measures[measure].length;
    measure += 1;
  }
  const last = section.measures[measure] || [];
  // 末小节允许 index === 长度（追加位），这样谱尾还能继续往后录。
  index = Math.max(0, Math.min(index, last.length));
  edCursor = { section: edCursor.section, measure, index };
}

function edMoveCursor(delta) {
  if (!edCursor || !edDoc) return;
  const section = edDoc.sections[edCursor.section];
  if (!section) return;
  let { measure, index } = edCursor;
  index += delta;
  while (index < 0 && measure > 0) {
    measure -= 1;
    index += section.measures[measure].length;
  }
  edCursor = { section: edCursor.section, measure, index: Math.max(0, index) };
  edNormalizeCursor();
  edRenderCursor();
}

function edSetInputMode(on) {
  edInputMode = !!on;
  if (edInputMode) {
    // 进入录入模式：从当前选中的音符起录；没有选中就从谱头起。
    const ref = edFocusedRef();
    edCursor = ref
      ? { section: ref.section, measure: ref.measure, index: ref.index }
      : { section: 0, measure: 0, index: 0 };
    edNormalizeCursor();
    toast(t('w.ed.input_mode_on'));
  } else {
    edCursor = null;
    toast(t('w.ed.input_mode_off'));
  }
  edRenderModeChip();
  edRenderCursor();
}

/** 用当前黏滞属性造一个待插入的音符。 */
function edNoteToInsert(symbol) {
  const isRest = symbol === '0';
  return {
    symbol,
    accidental: isRest ? '' : edInputAttrs.accidental,
    upper_dots: isRest ? 0 : Math.max(0, edInputAttrs.octave),
    lower_dots: isRest ? 0 : Math.max(0, -edInputAttrs.octave),
    duration: edInputAttrs.duration,
    duration_dots: edInputAttrs.duration_dots,
    // midi 是派生字段：Python 侧 jianpu_doc_from_dict 会按 key_header 重算，
    // 前端算一份只会多一处能与它不一致的地方。
    midi: null,
    is_rest: isRest,
    lyrics: {},
  };
}

/** 在光标处录入一个音符（决议"丙"：插入并从后面吃掉同样的时值），光标前进。 */
async function edInsertAtCursor(symbol) {
  if (!edDoc || !edHistory || !edCursor) return;
  if (!await edConfirmFirstEdit()) return;
  const at = noteRef(edCursor.section, edCursor.measure, edCursor.index);
  const cmds = insertConsumingCommands(edDoc, at, edNoteToInsert(symbol));
  if (!cmds || !edHistory.doGroup(cmds)) return;
  edCursor = { section: at.section, measure: at.measure, index: at.index + 1 };
  edNormalizeCursor();
  await edPushModel(null, 0);
}

/** 录入模式下的按键；返回 true 表示已处理（调用方据此 preventDefault）。 */
function edInputModeKey(e) {
  if (e.key >= '0' && e.key <= '7') { edInsertAtCursor(e.key); return true; }
  if (e.key === 'ArrowLeft') { edMoveCursor(-1); return true; }
  if (e.key === 'ArrowRight') { edMoveCursor(1); return true; }
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    // 八度是黏滞属性，不是改已录入的音符——与时值一致，整段都按同一个八度录。
    const delta = e.key === 'ArrowUp' ? 1 : -1;
    edInputAttrs.octave = Math.max(-3, Math.min(3, edInputAttrs.octave + delta));
    edRenderModeChip();
    return true;
  }
  if (e.key === '+' || e.key === '=' || e.key === '-') {
    const next = steppedDuration(edInputAttrs, e.key === '-' ? -1 : 1);
    if (next) { Object.assign(edInputAttrs, next); edRenderModeChip(); }
    return true;
  }
  if (e.key === '.') {
    const dotted = edInputAttrs.duration_dots > 0;
    edInputAttrs.duration_dots = dotted ? 0 : 1;
    edInputAttrs.duration = dotted ? edInputAttrs.duration / 1.5 : edInputAttrs.duration * 1.5;
    edRenderModeChip();
    return true;
  }
  if (e.key === '#' || e.key === 'b' || e.key === 'n') {
    edInputAttrs.accidental = e.key === 'n' ? '' : e.key;
    edRenderModeChip();
    return true;
  }
  if (e.key === 'Backspace') {
    // 打字时的"退格"就该是"收回我刚敲的那个"。撤销正好是这个意思，而且因为
    // 一次录入是一个命令组，被吃掉的休止符会连同插入一起还原——手写一个"删掉
    // 前一个音符"反而做不到这件事。
    edUndoRedo(false).then(() => edMoveCursor(-1));
    return true;
  }
  return false;
}

// ── 小节插入/删除（阶段5.5a）─────────────────────────────────────────────────
// 计划文档明确写了键位：`Enter` 插入小节。删除没有明确写，照抄 MuseScore 的
// "Ctrl+Delete 删除选中小节"——与已经建立的"照抄 MuseScore 肌肉记忆"基调一致，
// 且不会跟已占用的 Delete（删音符）/Backspace（录入模式回退）冲突。
// 两个操作在选择模式和录入模式下语义相同：都是对"光标/选中音符所在的那个小节"
// 动手，所以先统一算出"当前在哪个小节"，两条路径共用。

/** 当前操作对象所在的小节地址；选择模式看选中音符，录入模式看光标。 */
function edCurrentMeasureRef() {
  if (edInputMode) return edCursor ? { section: edCursor.section, measure: edCursor.measure } : null;
  const ref = edFocusedRef();
  return ref ? { section: ref.section, measure: ref.measure } : null;
}

/** 在当前小节之后插入一个空白小节（内容按当前拍号贪心拆成休止符）。 */
async function edInsertMeasure() {
  if (!edDoc || !edHistory) return;
  if (!await edConfirmFirstEdit()) return;
  const at = edCurrentMeasureRef();
  if (!at) return;
  const section = edDoc.sections[at.section];
  if (!section) return;         // 地址失效（模型刚被换掉）时安静收手，不要抛
  const insertAt = { section: at.section, measure: at.measure + 1 };
  const notes = blankMeasureNotes(barQuarterLength(section.time_sig));
  const keepRef = edInputMode ? null : edFocusedRef();   // 选择模式下原音符位置不变
  if (!edHistory.do({ type: 'insert_measure', at: insertAt, notes })) return;
  if (edInputMode) edCursor = { section: insertAt.section, measure: insertAt.measure, index: 0 };
  await edPushModel(keepRef, edSelection ? edSelection.focus : 0);
}

/** 删除当前小节。拒绝删空整个分段的最后一个小节（命令层已经会拒绝，这里
 * 只负责删除成功后把选择/光标落到一个仍然存在的位置上）。 */
async function edDeleteMeasure() {
  if (!edDoc || !edHistory) return;
  if (!await edConfirmFirstEdit()) return;
  const at = edCurrentMeasureRef();
  if (!at) return;
  if (!edHistory.do({ type: 'delete_measure', at })) return;
  if (edInputMode) {
    edCursor = { section: at.section, measure: at.measure, index: 0 };
    edNormalizeCursor();
    await edPushModel(null, 0);
    return;
  }
  // 被选中的音符连同它所在的小节一起消失了，没有"原位置"可循——显式清空选择，
  // 而不是让 edSelection 带着删除前的下标继续留在内存里（那会在下一次按键时
  // 悄悄指向一个毫不相干的音符）。
  edSelection = null;
  await edPushModel(null, 0);
}


// ── 表头字段编辑（阶段5.6b-1）────────────────────────────────────────────────
// 只管标题/作曲/速度这三样：它们在 SVG 上**没有对应图元**，没得可点，只能给表单。
// 调号与拍号走另一条路——它们是画出来的，用户已定"点谱面就地编辑"（5.6b-2/b-3）。

/** 表单字段 → 模型字段。速度是数字，另外两个是字符串。 */
const ED_HEADER_FIELDS = [
  { input: 'ed-hdr-title', field: 'title', numeric: false },
  { input: 'ed-hdr-composer', field: 'composer', numeric: false },
  { input: 'ed-hdr-tempo', field: 'tempo', numeric: true },
];

/** 用模型的值刷新表单。**跳过正在编辑的那个框**，否则会把用户打了一半的字冲掉。 */
function edSyncHeaderForm() {
  $('ed-gr-header').classList.toggle('hidden', !edDoc);
  if (!edDoc) return;
  for (const { input, field } of ED_HEADER_FIELDS) {
    const el = $(input);
    if (el === document.activeElement) continue;
    const value = edDoc[field];
    el.value = value === null || value === undefined ? '' : String(value);
  }
}

/**
 * 提交一个表头字段。
 *
 * 绑的是 `change` 而不是 `input`：`input` 每敲一个字符都触发，撤销栈会被打字过程
 * 塞满——而 B5③ 要求撤销粒度是**一次语义操作**。`change` 在失焦或回车时才发出，
 * 正好一次编辑一条记录。值没变时 `set_doc_field` 会拒绝，所以点进点出不留痕迹。
 */
async function edCommitHeaderField(spec) {
  if (!edDoc || !edHistory) return;
  const el = $(spec.input);
  let value;
  if (spec.numeric) {
    const n = parseInt(el.value, 10);
    // 速度非法（空、0、负数、文字）就退回模型现值，不写进去——与其存一个
    // 序列化不出来的 tempo，不如让输入框弹回去，用户一眼看得见没生效。
    if (!Number.isFinite(n) || n <= 0) { edSyncHeaderForm(); return; }
    value = n;
  } else {
    value = el.value.trim();
  }
  if (!await edConfirmFirstEdit()) { edSyncHeaderForm(); return; }
  if (!edHistory.do({ type: 'set_doc_field', field: spec.field, value })) {
    edSyncHeaderForm();   // 被拒（多半是值没变）——让表单与模型保持一致
    return;
  }
  // 表头改动不牵涉任何音符地址，选区/光标原样留着即可。
  await edPushModel(edFocusedRef(), edSelection ? edSelection.focus : 0);
}

for (const spec of ED_HEADER_FIELDS) {
  $(spec.input).addEventListener('change', () => edCommitHeaderField(spec));
}

// ── 调号就地编辑（阶段5.6b-2）────────────────────────────────────────────────
// 用户已定：点谱面上画出来的调号直接改，用下拉栏而不是自由文本，且**不提供
// "升/降半音"按钮**——升降半音必须替用户决定同音异名怎么拼，而首调记谱下用户
// 本来就按"这首是什么调"在想。
//
// 首调记谱下改调号**就是整曲移调**：音符数字一个都不动，变的是每个数字对应的
// 绝对音高。midi 是派生字段，Python 侧过桥时按新调号统一重算，所以这里只改
// 一个字符串就够了，不需要遍历音符。

let edKeyPopOpen = false;

function edPopulateTonics() {
  const sel = $('ed-keypop-tonic');
  if (sel.options.length) return;
  for (const k of KEY_TONICS) {
    const opt = document.createElement('option');
    opt.value = k;
    // 与新建向导一致，界面上用真正的乐理符号，值仍是 ASCII 的 '#'/'b'。
    opt.textContent = k.replace('#', '♯').replace('b', '♭');
    sel.appendChild(opt);
  }
}

function edCloseKeyPop() {
  edKeyPopOpen = false;
  $('ed-keypop').classList.add('hidden');
}

/** 把浮层摆到被点中的调号字样下方，位置相对 #ed-gr-stage。 */
function edOpenKeyPop(target) {
  if (!edDoc) return;
  edPopulateTonics();
  const { degree, tonic } = parseKeyHeader(edDoc.key_header);
  $('ed-keypop-mode').value = degree === '6' ? 'minor' : 'major';
  // 表头里的主音若不在下拉表内（手写文件可能有），先补一项，免得 select 悄悄
  // 落到第一项上、让用户以为调号本来就是那个。
  const sel = $('ed-keypop-tonic');
  if (!KEY_TONICS.includes(tonic)) {
    const opt = document.createElement('option');
    opt.value = tonic;
    opt.textContent = tonic;
    sel.appendChild(opt);
  }
  sel.value = tonic;

  const pop = $('ed-keypop');
  pop.classList.remove('hidden');
  const stage = $('ed-gr-stage').getBoundingClientRect();
  const box = target.getBoundingClientRect();
  pop.style.left = `${Math.max(4, box.left - stage.left)}px`;
  pop.style.top = `${box.bottom - stage.top + 6}px`;
  edKeyPopOpen = true;
}

async function edCommitKey() {
  if (!edDoc || !edHistory) return;
  const degree = $('ed-keypop-mode').value === 'minor' ? '6' : '1';
  const header = formatKeyHeader(degree, $('ed-keypop-tonic').value);
  edCloseKeyPop();
  if (!await edConfirmFirstEdit()) return;
  if (!edHistory.do({ type: 'set_doc_field', field: 'key_header', value: header })) return;
  toast(t('w.ed.key_changed', { k: header }));
  // 调号不牵涉音符地址，选区原样保留。
  await edPushModel(edFocusedRef(), edSelection ? edSelection.focus : 0);
}

$('ed-keypop-mode').addEventListener('change', edCommitKey);
$('ed-keypop-tonic').addEventListener('change', edCommitKey);

// fork 给调号字样打了 data-signature="key"（5.6b-2 加的，此前是匿名 <text>，
// 没有任何可命中的属性）。调号会同时画在固定 overlay 与可滚动层里，两处都带
// 这个标记，点哪个都算。
$('ed-gr-container').addEventListener('click', (e) => {
  const hit = e.target.closest ? e.target.closest('[data-signature="key"]') : null;
  if (!hit) return;
  e.stopPropagation();   // 别让它冒泡成"点空白处取消选择"
  if (edKeyPopOpen) edCloseKeyPop(); else edOpenKeyPop(hit);
});

document.addEventListener('click', (e) => {
  if (!edKeyPopOpen) return;
  if (e.target.closest && e.target.closest('#ed-keypop')) return;
  edCloseKeyPop();
});

// ── 复制 / 粘贴（阶段5.5b）────────────────────────────────────────────────────
// 剪贴板刻意**声明在换文件的重置之外**：验收要的就是"片段跨文件粘贴保真"，
// 复制完换个文件再粘贴是主用法，一重置就没了。它存的是纯模型对象，与来源文档
// 没有任何引用关系（extractFragment 做的是深拷贝），所以拿到别的文档里粘也不会
// 牵连原文件。
let edClipboard = null;   // { aligned:true, measures:[[note]] } | { aligned:false, notes:[note] }

/** 复制的落点：选区第一个和最后一个**画出来的**音符各自的模型地址。 */
function edSelectionRefs() {
  const range = edSelectionRange();
  if (!range) return null;
  const notes = edSectionNotes[range.section] || [];
  const first = notes[range.from];
  const last = notes[range.to];
  if (!first || !first.ref || !last || !last.ref) return null;
  return {
    section: range.section,
    from: noteRef(range.section, first.ref.measure, first.ref.index),
    to: noteRef(range.section, last.ref.measure, last.ref.index),
  };
}

/**
 * 把片段镜像到系统剪贴板。纯附赠功能：粘贴永远只读内部剪贴板，所以这里失败
 * 了也只是少一个便利，绝不能反过来让复制本身失败——因此全程吞掉异常。
 */
async function edMirrorToSystemClipboard(fragment, section) {
  try {
    const measures = fragment.aligned ? fragment.measures : [fragment.notes];
    const r = await api().editor_fragment_text({
      title: '', composer: '', key_header: edDoc.key_header, tempo: 0,
      sections: [{ time_sig: section.time_sig, measures }],
    });
    if (r && r.ok && r.text && navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(r.text);
    }
  } catch (_e) {
    // 系统剪贴板不可用（权限、非安全上下文……）——内部剪贴板已经存好了，静默即可
  }
}

/** Ctrl+C：把选中的音符区间收进剪贴板。 */
function edCopySelection() {
  if (!edDoc) return;
  const refs = edSelectionRefs();
  if (!refs) return;
  const span = selectionSpan(edDoc, refs.section, refs.from, refs.to);
  const fragment = span ? extractFragment(edDoc, refs.section, span) : null;
  if (!fragment) return;
  edClipboard = fragment;
  const count = fragment.aligned
    ? fragment.measures.reduce((sum, m) => sum + m.length, 0)
    : fragment.notes.length;
  toast(t(fragment.aligned ? 'w.ed.copied_measures' : 'w.ed.copied_notes',
    { n: fragment.aligned ? fragment.measures.length : count }));
  edMirrorToSystemClipboard(fragment, edDoc.sections[refs.section]);
}

/** Ctrl+V：整小节片段整小节插入；零碎片段按音符流录到光标处（决议甲）。 */
async function edPasteClipboard() {
  if (!edDoc || !edHistory) return;
  if (!edClipboard) { toast(t('w.ed.clipboard_empty')); return; }
  if (!await edConfirmFirstEdit()) return;
  const at = edCurrentMeasureRef();
  if (!at) { toast(t('w.ed.paste_no_target')); return; }
  const section = edDoc.sections[at.section];
  if (!section) return;

  if (edClipboard.aligned) {
    const cmds = pasteMeasuresCommands(at.section, at.measure, edClipboard.measures);
    if (!cmds || !edHistory.doGroup(cmds)) return;
    // 粘进来的小节占住了原来的位置，原内容整体后移；光标跟着落到第一个新小节。
    if (edInputMode) { edCursor = { section: at.section, measure: at.measure, index: 0 }; edNormalizeCursor(); }
    edSelection = null;
    await edPushModel(null, 0);
    return;
  }

  // 零碎片段：落点是光标（录入模式）或选中音符（选择模式）所在的那一格。
  const target = edInputMode
    ? (edCursor && noteRef(edCursor.section, edCursor.measure, edCursor.index))
    : edFocusedRef();
  if (!target) { toast(t('w.ed.paste_no_target')); return; }
  const cmds = pasteNotesCommands(edDoc, target, edClipboard.notes);
  if (!cmds || !edHistory.doGroup(cmds)) return;
  if (edInputMode) {
    edCursor = { section: target.section, measure: target.measure, index: target.index + edClipboard.notes.length };
    edNormalizeCursor();
  }
  await edPushModel(edInputMode ? null : target, edSelection ? edSelection.focus : 0);
}

document.addEventListener('keydown', (e) => {
  if ($('ed-gr-stage').classList.contains('hidden')) return;
  // 文本框有它自己的键盘语义（也有自己的撤销栈），不抢。输入类控件同理，
  // 一个键都不能截。
  const target = e.target;
  const inControl = target && typeof target.closest === 'function';
  if (inControl && target.closest('input, textarea, select, [contenteditable="true"]')) return;
  // 按钮和链接则只让开 Enter / Space 这两个键——浏览器就是靠它们把键盘操作
  // 转成 click 的，一旦这里 preventDefault 掉，按钮看上去就是"按不动"，而且
  // 完全没有反馈（阶段5.5a 把 Enter 绑成"插入小节"时，播放按钮正是这样被按
  // 死的：实测 Enter 的 defaultPrevented=true、click 数 0，而未被绑定的
  // Space 是 defaultPrevented=false、click 数 1）。其余编辑键照常生效，所以
  // 用鼠标点过播放按钮之后仍然可以直接用数字键继续改谱。
  if ((e.key === 'Enter' || e.key === ' ') && inControl && target.closest('button, a[href]')) return;

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault();
    edUndoRedo(e.shiftKey);   // Ctrl+Shift+Z = 重做，与多数编辑器一致
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
    e.preventDefault();
    edUndoRedo(true);
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'Delete' || e.key === 'Backspace')) {
    e.preventDefault();
    edDeleteMeasure();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') {
    e.preventDefault();
    edCopySelection();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
    e.preventDefault();
    edPasteClipboard();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // 模式切换先于一切内容按键：Shift+N 进出录入模式，Esc 只退出。
  if (e.key === 'N') { e.preventDefault(); edSetInputMode(!edInputMode); return; }
  if (e.key === 'Escape' && edKeyPopOpen) { e.preventDefault(); edCloseKeyPop(); return; }
  if (e.key === 'Escape' && edInputMode) { e.preventDefault(); edSetInputMode(false); return; }

  // Enter 插入小节：选择模式、录入模式通用，所以放在两者分岔之前。
  if (e.key === 'Enter') { e.preventDefault(); edInsertMeasure(); return; }

  if (edInputMode) {
    if (edInputModeKey(e)) e.preventDefault();
    return;   // 录入模式下不再走下面那套"改选中音符"的键位
  }

  // 左右方向键移动选择，不改内容。
  if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && edSelection) {
    const notes = edSectionNotes[edSelection.section] || [];
    const next = edSelection.focus + (e.key === 'ArrowRight' ? 1 : -1);
    if (next >= 0 && next < notes.length) {
      e.preventDefault();
      edSetSelection(edSelection.section, next, e.shiftKey);
    }
    return;
  }

  // 编辑键作用于**整个选区**，不只是焦点音符（阶段5.6a）。
  const refs = edSelectedRefs();
  if (!refs.length) return;
  const cmds = refs.map((ref) => edCommandForKey(e, ref));
  if (!cmds.some(Boolean)) return;   // 这个键与编辑无关，交回给浏览器
  e.preventDefault();
  edRunSelectionCommands(cmds, refs);
});

function edClearHighlight() {
  for (const r of edRenderers) r.clearHighlight();
  edHighlighted = [];
}

// 播放期间用 rAF 刷新读数：位置直接读 AudioContext 时钟，不自己数拍子，
// 所以即使这个循环被掉帧拖慢，显示的位置也不会漂。
let edTransportRaf = 0;
function edTransportLoop() {
  edSyncTransport();
  edUpdateHighlight();
  edTransportRaf = jianpuPlayer.isPlaying ? requestAnimationFrame(edTransportLoop) : 0;
}
jianpuPlayer.onstate = () => {
  edSyncTransport();
  if (jianpuPlayer.isPlaying && !edTransportRaf) edTransportRaf = requestAnimationFrame(edTransportLoop);
  if (!jianpuPlayer.isPlaying) edClearHighlight();
};

$('ed-gr-play').addEventListener('click', async () => {
  // play() 是 async（要等合成器脚本加载 + AudioContext.resume），所以这里显式
  // 分支 await，而不是调同步的 toggle()——否则加载/恢复失败会变成无人接管的
  // unhandled rejection，用户只看到按钮没反应。
  try {
    if (jianpuPlayer.isPlaying) jianpuPlayer.pause();
    else await jianpuPlayer.play();
  } catch (e) {
    toast(t('w.ed.play_failed', { e: String(e) }));
  }
});
$('ed-gr-stop').addEventListener('click', () => jianpuPlayer.stop());

// Esc 取消选择——图形区不是输入框，没有别的"退出当前状态"的自然手势。
// 只在图形 tab 可见时接管，免得抢了别处的 Esc。
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('ed-gr-stage').classList.contains('hidden')) return;
  edClearSelection();
});

export function edApplyLoad(r) {
  edLoaded = true;
  $('ed-name').textContent = r.name;
  $('ed-hint').textContent = '';
  const ta = $('ed-text');
  ta.disabled = false;
  ta.value = r.body || '';
  edSetDirty(false);
  edUpdateGutter();
  edRunLint();
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
  // 图形预览复位（换文件后旧渲染作废，切到该 tab 时才重新拉取，见 edRenderGraphical）
  clearTimeout(edGraphicalTimer);
  edGraphicalSeq += 1;          // 作废上一个文件那次还在飞的渲染请求
  edGraphicalRendered = false;
  edGraphicalStale = false;
  edRenderers = [];             // 容器即将清空，实例引用一并作废
  edHighlighted = [];
  edSectionNotes = [];
  edSectionIdIndex = [];
  edSectionSlots = [];
  edSelection = null;
  edInputMode = false;          // 换文件不该带着上一份谱子的录入状态
  edCursor = null;
  edLossy = null;               // 新文件重新判定，确认也要重新问一次
  edLossyAcked = false;
  edCloseKeyPop();
  edDoc = null;                 // 换文件：旧模型与它的撤销历史一并作废
  edHistory = null;
  edSyncHeaderForm();
  $('ed-gr-container').replaceChildren();
  $('ed-gr-ph').parentElement.classList.remove('hidden');
  $('ed-gr-ph').textContent = t('w.ed.graphical_placeholder');
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
  // 不阻断原则：校验只警告，唯独 🔴 级别的非法 token 在导出这一步硬拦截（见 B5 校验层）。
  const firstError = edDiagnostics.find((d) => d.severity === 'error');
  if (firstError) {
    toast(t('w.ed.lint.export_blocked'), { severity: 'error', onClick: () => edJumpTo(firstError.start, firstError.end) });
    return;
  }
  const r = await api().editor_export_to_output();
  if (r.ok) toast(t('w.ed.exported', { dest: r.dest }));
  else if (r.error === 'no_preview') toast(t('w.ed.export_need_preview'));
  else toast(t('w.tp.export_failed', { e: r.error || '' }));
});

$('ed-back').addEventListener('click', () => {
  if (edDirty && !confirm(t('w.ed.unsaved_confirm'))) return;
  jianpuPlayer.stop();   // 别让声音跟着用户飘到别的页面去
  showPage('jianpu');
});

// 编辑器输入行为：脏标记 + 行号同步 + Ctrl+S
const edTa = $('ed-text');
edTa.addEventListener('input', () => {
  edSetDirty(true); edUpdateGutter(); edRunLint(); edScheduleGraphicalRender();
});
edTa.addEventListener('scroll', () => { $('ed-gutter').scrollTop = edTa.scrollTop; edSyncHighlightScroll(); });
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
