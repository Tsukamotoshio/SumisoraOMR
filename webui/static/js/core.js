// webui/static/js/core.js — shared primitives: DOM/api helpers, i18n, toast,
// navigation (showPage/pageEnterHooks), harness-facing globals.
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { midiPlayer } from './midi.js';

export const $ = (id) => document.getElementById(id);
export const api = () => window.pywebview.api;

// ═══ i18n ════════════════════════════════════════════════════════════════════
// 目录由 Python 下发（gui/strings.py + webui/i18n.py 合并，Python 为唯一事实源）。
// 静态文本：data-i18n / data-i18n-title 属性，retranslate() 统一刷；
// 动态文本：JS 里一律 t(key, params)。
export const I18N = { lang: 'zh', strings: {} };
export function t(key, params) {
  const entry = I18N.strings[key];
  let s = entry ? (entry[I18N.lang] || entry.zh || key) : key;
  if (params) for (const [k, v] of Object.entries(params)) s = s.split(`{${k}}`).join(String(v));
  return s;
}
export function retranslate() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  document.documentElement.lang = I18N.lang === 'zh' ? 'zh-CN' : 'en';
}

// gate/自动化驱动可读的 UI 状态镜像（与 harness 同名约定）
window.__uiFlags = { busy: false, lastError: null, statusText: '', progressEvents: 0, summary: null };

// ═══ 批量事件接收（EventPusher → CustomEvent） ═══════════════════════════════
window.__omrEvents = (events) => {
  for (const ev of events || []) {
    window.dispatchEvent(new CustomEvent(ev.name, { detail: ev.payload }));
  }
};

// ═══ Toast ═══════════════════════════════════════════════════════════════════
const toastEl = document.createElement('div');
toastEl.className = 'toast';
document.body.appendChild(toastEl);
let toastTimer = null;
export function toast(text) {
  toastEl.textContent = text;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2600);
}

// ═══ 导航 ════════════════════════════════════════════════════════════════════
const PAGES = ['score', 'audio', 'jianpu', 'staff', 'transpose', 'about', 'editor', 'notedigger'];
let activePage = 'score';
export const pageEnterHooks = {};   // page → () => void（进入页面时触发，如刷新列表）

// notedigger.js 的顶层代码里有立即执行的 $(...).addEventListener(...)（非函数体内
// 延迟调用），若本文件在其顶层静态 import { ndStopPlayback } from './notedigger.js'
// 会与 notedigger.js 反向 import 本文件的 $/api/t/... 形成循环——notedigger.js 求值
// 时本文件的 const 导出还处于暂时性死区，实测抛 "Cannot access '$' before
// initialization"。改用运行时注册：notedigger.js 加载完毕后把 ndStopPlayback 挂进
// 这个槽位，本文件不再对 notedigger.js 有任何静态 import，彻底断开该环。
let _ndStopPlayback = null;
export function _registerNdStopPlayback(fn) { _ndStopPlayback = fn; }

export function showPage(name) {
  // 离开预览页时停止并收起内置 MIDI 播放器（避免切页后音频继续）
  if (typeof midiPlayer !== 'undefined' && name !== activePage) midiPlayer.leave();
  // 离开 noteDigger 页时停止其音频/MIDI 播放（CSS 隐藏 iframe 不触发它自带的
  // document.hidden 停止逻辑，否则跳走后声音还在放，用户会慌）
  if (activePage === 'notedigger' && name !== 'notedigger' && _ndStopPlayback) _ndStopPlayback();
  activePage = name;
  for (const p of PAGES) $(`page-${p}`).classList.toggle('hidden', name !== p);
  // 移调/编辑/高级修正是各自父页的子流程，导航栏保持父页高亮
  const navName = name === 'transpose' ? 'staff'
    : (name === 'editor' ? 'jianpu' : (name === 'notedigger' ? 'audio' : name));
  document.querySelectorAll('.nav').forEach((x) => x.removeAttribute('aria-current'));
  const nav = document.querySelector(`.nav[data-page='${navName}']`);
  if (nav) nav.setAttribute('aria-current', 'true');
  if (pageEnterHooks[name]) pageEnterHooks[name]();
}
document.querySelectorAll('.nav[data-page]').forEach((btn) => {
  btn.addEventListener('click', () => showPage(btn.dataset.page));
});
