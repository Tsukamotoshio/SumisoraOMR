// webui M1 — 主动脉测试台：批量事件接收 + 文件托盘 + 转换控制 + 日志流 + Gate 钩子。
// 自动检查结果写入 window.__m0state，供 main.py --selftest 轮询读取。
'use strict';

window.__m0state = null;

const $ = (id) => document.getElementById(id);

function mark(id, ok, detail) {
  const li = $(id);
  if (!li) return;
  const dot = li.querySelector('.dot');
  dot.classList.remove('pending');
  dot.classList.add(ok ? 'ok' : 'fail');
  if (detail !== undefined) {
    const code = li.querySelector('code');
    if (code) code.textContent = ' ' + detail;
  }
}

// ═══ 批量事件接收（EventPusher → 这里）═══════════════════════════════════════
// Python 每 ~50ms 推一批 [{name, payload}]；逐条转成 CustomEvent 派发。
window.__omrEvents = (events) => {
  for (const ev of events || []) {
    window.dispatchEvent(new CustomEvent(ev.name, { detail: ev.payload }));
  }
};

// ═══ 日志流 ══════════════════════════════════════════════════════════════════
const logView = $('log-view');
let logTotal = 0;         // 收到的 log_line 总数
let floodReceived = 0;    // 其中 [flood] 行数（Gate1 计数）
const MAX_LOG_NODES = 600;

window.addEventListener('log_line', (e) => {
  const line = (e.detail && e.detail.line) || '';
  logTotal += 1;
  if (line.startsWith('[flood]')) floodReceived += 1;
  const div = document.createElement('div');
  div.textContent = line;
  logView.appendChild(div);
  while (logView.childNodes.length > MAX_LOG_NODES) logView.removeChild(logView.firstChild);
  logView.scrollTop = logView.scrollHeight;
  $('log-count').textContent = `${logTotal} 行`;
});

// ═══ Gate1：日志压测 ═════════════════════════════════════════════════════════
function runFlood(n) {
  return new Promise((resolve) => {
    const before = floodReceived;
    const onDone = (e) => {
      // flood_done 与最后一批 log_line 同批到达；再等两个批次周期收尾
      setTimeout(() => {
        window.removeEventListener('flood_done', onDone);
        const received = floodReceived - before;
        resolve({ sent: e.detail.sent, received, ok: received === e.detail.sent });
      }, 200);
    };
    window.addEventListener('flood_done', onDone);
    window.pywebview.api.debug_flood(n);
  });
}

// ═══ 文件托盘 ════════════════════════════════════════════════════════════════
const fileList = $('file-list');
function renderFiles(files) {
  fileList.replaceChildren();
  if (!files.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = '（空 — 拖入 PDF/PNG/JPG 或音频）';
    fileList.appendChild(li);
    return;
  }
  for (const f of files) {
    const li = document.createElement('li');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = f.checked;
    cb.addEventListener('change', () => window.pywebview.api.files_toggle_check(f.path));
    const name = document.createElement('span');
    name.textContent = f.name;
    name.title = f.path;
    const rm = document.createElement('button');
    rm.textContent = '✕';
    rm.className = 'mini';
    rm.addEventListener('click', () => window.pywebview.api.files_remove(f.path));
    li.append(cb, name, rm);
    fileList.appendChild(li);
  }
}
window.addEventListener('files_changed', (e) => renderFiles((e.detail && e.detail.files) || []));

// ═══ 转换控制 ════════════════════════════════════════════════════════════════
const status = $('conv-status');
let cancelling = false;

// Gate 驱动（--gate2/3/4）从 Python 侧轮询的 UI 状态镜像
window.__uiFlags = { busy: false, lastError: null, statusText: '空闲', progressEvents: 0 };

function setBusy(busy) {
  window.__uiFlags.busy = busy;
  $('btn-start').disabled = busy;
  $('btn-cancel').disabled = !busy;
  $('btn-kill').disabled = !busy;
  if (!busy) {
    $('bar-sub').style.width = '0%';
    $('sub-msg').textContent = '';
  }
}
setBusy(false);

$('btn-start').addEventListener('click', async () => {
  cancelling = false;
  const r = await window.pywebview.api.convert_start({ engine: 'auto' });
  if (!r.ok) { status.textContent = `无法启动：${r.error}`; return; }
  status.textContent = `转换中（${r.count} 个文件）…`;
  setBusy(true);
});
$('btn-cancel').addEventListener('click', async () => {
  cancelling = true;
  status.textContent = '取消中…';
  await window.pywebview.api.convert_cancel();
});
$('btn-kill').addEventListener('click', () => window.pywebview.api.debug_kill_worker());
$('btn-flood').addEventListener('click', async () => {
  const r = await runFlood(500);
  mark('chk-flood', r.ok, `${r.received}/${r.sent}`);
});

window.addEventListener('progress_update', (e) => {
  const v = (e.detail && e.detail.value) || 0;
  window.__uiFlags.progressEvents += 1;
  $('bar-main').style.width = `${Math.round(v * 100)}%`;
  if (e.detail && e.detail.message) status.textContent = e.detail.message;
});
window.addEventListener('sub_progress', (e) => {
  const v = (e.detail && e.detail.value) || 0;
  window.__uiFlags.progressEvents += 1;
  $('bar-sub').style.width = `${Math.round(v * 100)}%`;
  $('sub-msg').textContent = (e.detail && e.detail.message) || '';
});
window.addEventListener('progress_done', (e) => {
  status.textContent = `完成：${(e.detail && e.detail.message) || ''}`;
  window.__uiFlags.statusText = status.textContent;
  setBusy(false);
});
window.addEventListener('progress_error', (e) => {
  // Gate2 语义：取消中收到的错误＝取消确认，不弹失败
  window.__uiFlags.lastError = (e.detail && e.detail.message) || '';
  if (cancelling) {
    status.textContent = '已取消（worker 已终止）';
  } else {
    status.textContent = `错误：${(e.detail && e.detail.message) || ''}`;
    status.classList.add('error');
    setTimeout(() => status.classList.remove('error'), 4000);
  }
  window.__uiFlags.statusText = status.textContent;
  setBusy(false);
});
window.addEventListener('conversion_finished', (e) => {
  const s = (e.detail && e.detail.summary) || {};
  window.__uiFlags.summary = s;
  if (s.total !== undefined && !cancelling) {
    status.textContent += `　[成功 ${s.success_count} / 回退 ${s.fallback_count} / 失败 ${s.failed_count}]`;
  }
  setBusy(false);
});

// M0 拖拽验证事件仍然监听（托盘由 Python 侧 files_add 更新，这里只提示）
window.addEventListener('files-dropped-error', (e) => {
  status.textContent = '拖拽处理异常：' + e.detail;
});

// ═══ 自动检查（M0 四项 + Gate1）═══════════════════════════════════════════════
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
    gate1Flood: null,
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

  state.gate1Flood = await runFlood(500);
  mark('chk-flood', state.gate1Flood.ok, `${state.gate1Flood.received}/${state.gate1Flood.sent}`);

  window.__m0state = state;
}
window.addEventListener('pywebviewready', () => {
  runChecks();
  window.pywebview.api.files_list().then(renderFiles);
});

// ═══ 标题栏 ═════════════════════════════════════════════════════════════════
$('btn-min').addEventListener('click', () => window.pywebview.api.window_minimize());
$('btn-max').addEventListener('click', () => window.pywebview.api.window_toggle_maximize());
$('btn-close').addEventListener('click', () => window.pywebview.api.window_close());
$('titlebar').addEventListener('dblclick', (e) => {
  if (e.target.closest('.win-controls')) return;
  window.pywebview.api.window_toggle_maximize();
});

// 拖入视觉反馈（真正的 preventDefault 在 Python 侧 DOMEventHandler 做）
const dz = $('dropzone');
window.addEventListener('dragover', () => dz.classList.add('dragging'));
window.addEventListener('dragleave', () => dz.classList.remove('dragging'));
window.addEventListener('drop', () => dz.classList.remove('dragging'));
