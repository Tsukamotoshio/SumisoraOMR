// webui/static/js/notedigger.js — noteDigger 音频扒谱编辑器：iframe 懒加载 +
// MIDI 导出→简谱 桥接（③-3）。
// 首次进入才设 src——noteDigger 会初始化 ONNX/GPU，避免拖慢主界面启动。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t, toast, showPage, pageEnterHooks, I18N, _registerNdStopPlayback } from './core.js';

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
export function ndStopPlayback() {
  try {
    const cw = $('nd-frame').contentWindow;
    if (cw && cw.app && cw.app.AudioPlayer) cw.app.AudioPlayer.stop();
  } catch (_e) { /* iframe 未加载或结构变化时忽略 */ }
}
// core.js 的 showPage 需要在离开 notedigger 页时调用它，但不能静态 import 本文件
// （见 core.js 顶部注释），故在此运行时注册。
_registerNdStopPlayback(ndStopPlayback);
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
    // 隐藏 noteDigger 自带的两个导入项（用我们页头的宿主导入按钮替代，可默认到
    // Input/Output 目录）；「MIDI编辑器模式」等其余项保留。
    const fileItems = d.querySelectorAll('#filePannel li');
    const hiddenImports = ['导入音频', '导入midi'];
    fileItems.forEach((li) => { if (hiddenImports.includes(li.textContent.trim())) li.style.display = 'none'; });
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
  f.setAttribute('src', '../vendor/notedigger/index.html');
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

// ── noteDigger 操作指南浮层 ──────────────────────────────────────────────────
// 内容摘编自 noteDigger 项目 README（GPL-3.0，作者 madderscientist），并针对本集成
// 做了裁剪：AI 扒谱（人工智障/音色分离）在离线集成下已隐藏，故本指南不引导使用，
// 自动转录请走 SumisoraOMR 自身的「音频识别」。完整文档见其 GitHub 主页。
const ND_GUIDE = {
  zh: [
    ['基本流程', [
      '导入音频：拖拽音频文件进编辑区，或用左侧菜单「文件-上传」。',
      '选择声道并分析（推荐 CQT；有独显可勾选 GPU 加速）。',
      '在「分析」页面依次点「节奏分析」「去除谐波」「调性分析」（调性分析建议最后运行）。',
      '勾选「设置-播放节拍」校准节拍；用小节栏右键菜单「合并下一小节」等修正节奏型。',
      '参考频谱，在钢琴卷帘上手动绘制音符。',
      '建议把顶部吸附模式切到「节拍吸附」，让音符对齐小节线（三连音等仍需其他模式）。',
      '完成后在 noteDigger 里「导出 MIDI」，再回到本页点右上角「生成简谱」即可。',
    ]],
    ['鼠标操作', [
      '空格：播放 / 暂停。',
      '双击时间轴：从该处开始播放。',
      '在空白处按住拖动：在当前音轨绘制一个音符。',
      '按住音符左半边拖动：改位置；按住右半边拖动：改时长。',
      'Ctrl+点击音符：多选；Delete：删除选中音符。',
      'Ctrl+滚轮：横向缩放；按住中键拖拽 / 触摸板滑动：移动视野。',
      '在时间轴上拖拽：设置重复区间；拉动小节线：设置该小节 bpm（按住 Shift 只改本条线）。',
    ]],
    ['常用快捷键', [
      'Ctrl+Z / Ctrl+Y：撤销 / 重做（保留最近 16 步）。',
      'Ctrl+A：全选当前音轨；Ctrl+Shift+A：全选所有音轨；Ctrl+D：取消选中。',
      'Ctrl+C / Ctrl+X / Ctrl+V：复制 / 剪切 / 粘贴到选中音轨。',
      'Ctrl+B：呼出 / 收起音轨面板。',
      '←↑→↓：视野移动一格；PageUp / PageDown：翻页；Home：播放位置归零。',
    ]],
    ['小技巧', [
      '滑动条旁若有数字，点它可恢复初始值。',
      '多次点「笔」右侧的选择工具可切换选择模式（只作用于当前音轨）。',
      '点某个音符可选中其所在音轨。',
      '音轨的「闭眼」只是隐藏、仍可操作，一般搭配「锁定」使用。',
    ]],
  ],
  en: [
    ['Basic flow', [
      'Import audio: drag an audio file into the editor, or use the side menu (File → Upload).',
      'Pick channels and analyse (CQT recommended; tick GPU if you have a discrete card).',
      'On the "Analyse" page run Rhythm analysis, Remove harmonics, then Key analysis (run key analysis last).',
      'Tick Settings → "Play beat" to calibrate the beat; use the measure-bar right-click menu (e.g. "Merge next measure") to fix the rhythm.',
      'Draw notes by ear on the piano roll, using the spectrogram as a guide.',
      'Switch the top snap mode to "Beat snap" so notes align to the measure lines (triplets etc. still need other modes).',
      'When done, export MIDI inside noteDigger, then come back here and click "Generate jianpu" (top right).',
    ]],
    ['Mouse', [
      'Space: play / pause.',
      'Double-click the timeline: start playing from there.',
      'Drag on empty space: draw a note on the current track.',
      'Drag a note’s left half: move it; drag its right half: change its length.',
      'Ctrl+click a note: multi-select; Delete: remove selected notes.',
      'Ctrl+wheel: horizontal zoom; middle-drag / trackpad swipe: pan the view.',
      'Drag on the timeline: set a repeat region; drag a measure line: set that measure’s bpm (hold Shift to move only that line).',
    ]],
    ['Shortcuts', [
      'Ctrl+Z / Ctrl+Y: undo / redo (last 16 steps).',
      'Ctrl+A: select current track; Ctrl+Shift+A: select all tracks; Ctrl+D: deselect.',
      'Ctrl+C / Ctrl+X / Ctrl+V: copy / cut / paste onto the selected track.',
      'Ctrl+B: toggle the track panel.',
      'Arrow keys: nudge the view; PageUp / PageDown: page; Home: reset play position to 0.',
    ]],
    ['Tips', [
      'If a slider shows a number beside it, click the number to reset to the default.',
      'Click the select tool (right of the pen) repeatedly to cycle selection modes (current track only).',
      'Click a note to select its track.',
      'A track’s "closed eye" only hides it (still editable); usually pair it with "lock".',
    ]],
  ],
};

function ndRenderHelp() {
  const body = $('nd-help-body');
  body.replaceChildren();
  const sections = ND_GUIDE[I18N.lang] || ND_GUIDE.zh;
  for (const [title, items] of sections) {
    const h = document.createElement('h4');
    h.textContent = title;
    body.appendChild(h);
    const ol = document.createElement('ol');
    for (const it of items) {
      const li = document.createElement('li');
      li.textContent = it;
      ol.appendChild(li);
    }
    body.appendChild(ol);
  }
  $('nd-help-credit').textContent = t('w.nd.help_credit');
}
$('nd-help').addEventListener('click', () => {
  ndRenderHelp();               // 每次打开都按当前语言重渲染
  $('nd-help-overlay').classList.remove('hidden');
});

// ── 宿主 MIDI 导入（替代 noteDigger 内置的 midi 导入项）───────────────────────
// 走宿主 create_file_dialog（可默认到 Output 目录，取自动转录产出的 MIDI），取回白名单
// /file URL，fetch 成 File 后注入 noteDigger 的 app.io.onfile —— 绕过 iframe 内那个
// 无法设默认目录的 <input type=file>。midi 强制 type='audio/mid' 以命中 onfile 的
// MIDI 分支。（ndImport/bridge 仍保留 audio 通路参数，当前 UI 只暴露 MIDI 导入。）
async function ndImport(kind) {
  const f = $('nd-frame');
  const cw = f.contentWindow;
  if (!cw || !cw.app || !cw.app.io) { toast(t('w.nd.import_loading')); return; }
  const r = await api().notedigger_pick_import(kind);
  if (!r.ok) {
    if (r.error !== 'cancelled') toast(t('w.nd.import_failed', { e: r.error || '' }));
    return;
  }
  try {
    const resp = await fetch(r.url);
    const blob = await resp.blob();
    const type = kind === 'midi' ? 'audio/mid' : (blob.type || 'audio/mpeg');
    cw.app.io.onfile(new File([blob], r.name, { type }));
  } catch (e) {
    toast(t('w.nd.import_failed', { e: String(e) }));
  }
}
$('nd-import-midi').addEventListener('click', () => ndImport('midi'));
$('nd-help-close').addEventListener('click', () => $('nd-help-overlay').classList.add('hidden'));
$('nd-help-overlay').addEventListener('click', (e) => {
  if (e.target === $('nd-help-overlay')) $('nd-help-overlay').classList.add('hidden');
});
