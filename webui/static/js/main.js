// webui/static/js/main.js — 入口：主题、窗口 resize grips、标题栏、关于页、
// 语言切换、pywebviewready 初始化。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
//
// 依赖方向：本文件是唯一的应用入口（index.html 只 <script type="module"> 这一个
// 文件），可以向下导入任何功能模块；没有任何模块反向导入 main.js。
'use strict';

import { $, api, t, toast, retranslate, I18N } from './core.js';
import { renderView, initFileTray } from './tray.js';
import { refreshModels } from './models.js';
import { jpRenderList } from './jianpu.js';
import { stRenderList } from './staff.js';
// convert.js 不导出任何符号（只注册事件监听），必须显式 side-effect import，
// 否则它不会被任何其它模块的 import 图传递引入，其按钮/进度监听全部不会生效。
import './convert.js';
// notedigger.js 同理需要显式 side-effect import：core.js 曾经静态 import 它（用于
// showPage 里的 ndStopPlayback），但那会与 notedigger.js 反向 import core.js 的
// $/api/... 形成循环，导致核心导出在暂时性死区被访问而崩溃（见 core.js 顶部注释，
// 已改为运行时注册）。core.js 不再 import 本文件后，它就没有任何导入者了——
// 必须在此显式引入，否则其顶层的按钮事件监听（audio-notedigger 等）永远不会注册。
import './notedigger.js';

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
  initFileTray();
  refreshModels();
});
