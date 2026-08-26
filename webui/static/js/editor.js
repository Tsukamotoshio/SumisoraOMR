// webui/static/js/editor.js — 简谱编辑页。
// 左栏双模式（参考图 / 渲染预览），右栏行号文本编辑器；保存/渲染/导出走桥，
// 头部 # 注释块由 Python 侧保护。从简谱预览页「编辑」进入。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, showPage } from './core.js';
import { PdfView } from './pdfview.js';
import { lintJianpuText, isHeaderLine } from './jianpu-lint.js';
import { jianpuPlayer, activeNotesAt } from './jianpu-play.js';
import { EditHistory, noteRef, steppedDuration } from './jianpu-edit.js';

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
function edReselect(ref, fallbackIndex) {
  if (!ref) return;
  const notes = edSectionNotes[ref.section] || [];
  let index = notes.findIndex(
    (n) => n.ref && n.ref.measure === ref.measure && n.ref.index === ref.index);
  if (index < 0) index = Math.min(fallbackIndex, notes.length - 1);
  if (index < 0) { edSelection = null; edRenderSelection(); return; }
  edSelection = { section: ref.section, anchor: index, focus: index };
  edRenderSelection();
}

/** 把当前模型送去序列化，并用返回结果刷新两个视图。 */
async function edPushModel(keepRef, fallbackIndex) {
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
  edReselect(keepRef, fallbackIndex);
}

/** 执行一条命令：命令被拒绝（地址无效、休止符不能加升降号等）就什么都不做。 */
async function edRunCommand(cmd) {
  if (!edDoc || !edHistory || !cmd) return;
  const keepRef = cmd.ref;
  const fallbackIndex = edSelection ? edSelection.focus : 0;
  if (!edHistory.do(cmd)) return;
  await edPushModel(keepRef, fallbackIndex);
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
  const note = edSectionNotes[edSelection.section][edSelection.focus];
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
    // 延音横线折进前一个音符，读它会算错。
    const model = edDoc.sections[ref.section].measures[ref.measure][ref.index];
    const next = steppedDuration(model || note, e.key === '-' ? -1 : 1);
    return next ? { type: 'set_duration', ref, ...next } : null;
  }
  return null;
}

document.addEventListener('keydown', (e) => {
  if ($('ed-gr-stage').classList.contains('hidden')) return;
  // 文本框有它自己的键盘语义（也有自己的撤销栈），不抢。
  if (e.target === $('ed-text')) return;

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
  if (e.ctrlKey || e.metaKey || e.altKey) return;

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

  const ref = edFocusedRef();
  if (!ref) return;
  const cmd = edCommandForKey(e, ref);
  if (!cmd) return;
  e.preventDefault();
  edRunCommand(cmd);
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
  edSelection = null;
  edDoc = null;                 // 换文件：旧模型与它的撤销历史一并作废
  edHistory = null;
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
