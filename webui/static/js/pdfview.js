// webui/static/js/pdfview.js — pdf.js 查看器 + 通用画框拖拽/图片缩放。
// 本地捆绑 pdf.js（vendor/pdfjs，v6），worker 同源加载；渲染按 devicePixelRatio
// 放大保证清晰度。fit = 适应舞台宽度。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { t } from './core.js';

let _pdfjs = null;
// 注意：函数不能叫 pdfjsLib —— pdf.min.mjs 求值时会 `globalThis.pdfjsLib = 库对象`，
// 覆盖同名的全局函数声明，导致第二次调用起报 "pdfjsLib is not a function"。
async function loadPdfjs() {
  if (_pdfjs) return _pdfjs;
  _pdfjs = await import('../vendor/pdfjs/pdf.min.mjs');
  _pdfjs.GlobalWorkerOptions.workerSrc = '../vendor/pdfjs/pdf.worker.min.mjs';
  return _pdfjs;
}

// 通用画框拖拽平移：原 Flet InteractiveViewer 的 pan_enabled 能力，pywebview 版
// 迁移时漏掉了，这里用鼠标按下+移动直接改画框 scrollLeft/scrollTop 补回来（对
// canvas/img 内容一视同仁，只操心画框本身的滚动位置，不关心里面渲染的是什么）。
export function attachFrameDrag(frame) {
  let dragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;
  frame.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    startLeft = frame.scrollLeft; startTop = frame.scrollTop;
    frame.classList.add('dragging');
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    frame.scrollLeft = startLeft - (e.clientX - startX);
    frame.scrollTop = startTop - (e.clientY - startY);
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    frame.classList.remove('dragging');
  });
}

export class PdfView {
  constructor(canvas, stage, pageInfoEl) {
    this.canvas = canvas;
    this.stage = stage;
    this.pageInfoEl = pageInfoEl;
    this.doc = null;
    this.pageNo = 1;
    this.scale = null;      // null = fit-width
    this._renderToken = 0;
    // A4 画框随窗口尺寸变化（窗口化↔最大化、任意 resize）→ fit 模式下重渲当前页，
    // 让页面始终恰好填满新的画框（业界惯用做法：观察容器尺寸，防抖重排）。
    this._resizeTimer = 0;
    const frame = this.canvas.parentElement || this.stage;
    this._ro = new ResizeObserver(() => {
      if (!this.doc || this.scale !== null) return;   // 仅 fit 模式自动重排
      clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => { if (this.doc) this.render(); }, 120);
    });
    this._ro.observe(frame);
    // 滚轮缩放 + 拖拽平移
    frame.addEventListener('wheel', (e) => {
      if (!this.doc) return;
      e.preventDefault();
      this.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });
    attachFrameDrag(frame);
  }

  async open(url) {
    const lib = await loadPdfjs();
    if (this.doc) { try { this.doc.destroy(); } catch (_e) {} }
    this.doc = await lib.getDocument({ url }).promise;
    this.pageNo = 1;
    this.scale = null;
    await this.render();
  }

  close() {
    if (this.doc) { try { this.doc.destroy(); } catch (_e) {} this.doc = null; }
    this.canvas.classList.add('hidden');
    if (this.pageInfoEl) this.pageInfoEl.textContent = '';
  }

  _fitScale(page) {
    // fit = 整页装入 A4 画框（宽高双约束取小 = contain）。A4 页面正好铺满；
    // 非 A4 页面也保证整页可见。-1px 抵消取整误差，避免冒出滚动条吃掉宽度。
    const frame = this.canvas.parentElement;
    const vp = page.getViewport({ scale: 1 });
    if (!frame) return Math.max(0.1, (this.stage.clientWidth - 36) / vp.width);
    const w = frame.clientWidth - 1;
    const h = frame.clientHeight - 1;
    if (w <= 0 || h <= 0) return 1;
    return Math.min(w / vp.width, h / vp.height);
  }

  async render() {
    if (!this.doc) return;
    const token = ++this._renderToken;
    const page = await this.doc.getPage(this.pageNo);
    if (token !== this._renderToken) return;
    // 先取消隐藏再测量：canvas.hidden 时 A4 画框 display:none，宽度为 0
    this.canvas.classList.remove('hidden');
    const scale = this.scale ?? this._fitScale(page);
    const dpr = window.devicePixelRatio || 1;
    const vp = page.getViewport({ scale: scale * dpr });
    this.canvas.width = vp.width;
    this.canvas.height = vp.height;
    this.canvas.style.width = `${vp.width / dpr}px`;
    this.canvas.style.height = `${vp.height / dpr}px`;
    await page.render({ canvas: this.canvas, viewport: vp }).promise;
    if (this.pageInfoEl) this.pageInfoEl.textContent = t('w.pv.page_info', { n: this.pageNo, t: this.doc.numPages });
  }

  prev() { if (this.doc && this.pageNo > 1) { this.pageNo -= 1; this.render(); } }
  next() { if (this.doc && this.pageNo < this.doc.numPages) { this.pageNo += 1; this.render(); } }
  async zoom(f) {
    if (!this.doc) return;
    if (this.scale === null) {
      const page = await this.doc.getPage(this.pageNo);
      this.scale = this._fitScale(page);
    }
    this.scale = Math.min(6, Math.max(0.2, this.scale * f));
    this.render();
  }
  zoomFit() { this.scale = null; this.render(); }
}

// 图片画框的缩放控制器（乐谱识别页的 PNG/JPG 预览用——不经 PdfView，独立维护一个
// scale 状态，用 CSS transform 缩放；拖拽平移复用 attachFrameDrag）。返回的对象
// 供下方缩放按钮在图片模式下调用，滚轮事件也走同一套。
export function makeImageZoom(frame, img) {
  let scale = 1;
  const apply = () => { img.style.transform = scale === 1 ? '' : `scale(${scale})`; };
  const controller = {
    zoom(f) { scale = Math.min(6, Math.max(1, scale * f)); apply(); },
    zoomFit() { scale = 1; apply(); },
  };
  frame.addEventListener('wheel', (e) => {
    e.preventDefault();
    controller.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });
  return controller;
}
