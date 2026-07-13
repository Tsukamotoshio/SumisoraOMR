// webui M0 — 前端自检 + 标题栏 + 拖拽显示。
// 结果写入 window.__m0state，供 main.py --selftest 轮询读取。
'use strict';

window.__m0state = null;

function mark(id, ok, detail) {
  const li = document.getElementById(id);
  if (!li) return;
  const dot = li.querySelector('.dot');
  dot.classList.remove('pending');
  dot.classList.add(ok ? 'ok' : 'fail');
  if (detail !== undefined) {
    const code = li.querySelector('code');
    if (code) code.textContent = ' ' + detail;
  }
}

// ── Worker + SharedArrayBuffer 往返（noteDigger 所需能力的最小证明） ──
function testWorkerSab() {
  return new Promise((resolve) => {
    if (typeof SharedArrayBuffer === 'undefined') { resolve(false); return; }
    try {
      const sab = new SharedArrayBuffer(4);
      const src = 'onmessage = e => { const v = new Int32Array(e.data); Atomics.store(v, 0, 42); postMessage("done"); };';
      const worker = new Worker(URL.createObjectURL(new Blob([src], { type: 'application/javascript' })));
      const timer = setTimeout(() => { worker.terminate(); resolve(false); }, 3000);
      worker.onmessage = () => {
        clearTimeout(timer);
        worker.terminate();
        resolve(new Int32Array(sab)[0] === 42);
      };
      worker.postMessage(sab);
    } catch (_e) { resolve(false); }
  });
}

async function runChecks() {
  const state = {
    crossOriginIsolated: !!window.crossOriginIsolated,
    sharedArrayBuffer: typeof SharedArrayBuffer !== 'undefined',
    workerSab: false,
    bridgeEcho: false,
  };
  mark('chk-coi', state.crossOriginIsolated);
  mark('chk-sab', state.sharedArrayBuffer);

  state.workerSab = await testWorkerSab();
  mark('chk-worker', state.workerSab);

  try {
    const r = await window.pywebview.api.echo('ping');
    state.bridgeEcho = r && r.echo === 'ping';
    mark('chk-bridge', state.bridgeEcho, `Python ${r.python}`);
  } catch (e) {
    mark('chk-bridge', false, String(e));
  }

  window.__m0state = state;
}

// pywebview 就绪后才有 window.pywebview.api
window.addEventListener('pywebviewready', runChecks);

// ── 标题栏按钮 + 双击最大化 ──
document.getElementById('btn-min').addEventListener('click', () => window.pywebview.api.window_minimize());
document.getElementById('btn-max').addEventListener('click', () => window.pywebview.api.window_toggle_maximize());
document.getElementById('btn-close').addEventListener('click', () => window.pywebview.api.window_close());
document.getElementById('titlebar').addEventListener('dblclick', (e) => {
  if (e.target.closest('.win-controls')) return;
  window.pywebview.api.window_toggle_maximize();
});

// ── 拖拽结果显示（真实路径由 Python DOM handler 推回） ──
const dropList = document.getElementById('drop-list');
function setDropLines(lines, mutedText) {
  // 只用 textContent 构建，杜绝 XSS（文件名/异常文本均为外部可控输入）。
  dropList.replaceChildren();
  if (mutedText !== undefined) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = mutedText;
    dropList.appendChild(li);
    return;
  }
  for (const text of lines) {
    const li = document.createElement('li');
    li.textContent = text;
    dropList.appendChild(li);
  }
}
window.addEventListener('files-dropped', (e) => {
  const paths = e.detail || [];
  if (!paths.length) {
    setDropLines([], '（未取到路径 — pywebviewFullPath 为空）');
    return;
  }
  setDropLines(paths.map((p) => '✓ ' + p));
});
window.addEventListener('files-dropped-error', (e) => {
  setDropLines([], '拖拽处理异常：' + e.detail);
});

// 拖入视觉反馈（真正的 preventDefault 在 Python 侧 DOMEventHandler 做）
const dz = document.getElementById('dropzone');
window.addEventListener('dragover', () => dz.classList.add('dragging'));
window.addEventListener('dragleave', () => dz.classList.remove('dragging'));
window.addEventListener('drop', () => dz.classList.remove('dragging'));
