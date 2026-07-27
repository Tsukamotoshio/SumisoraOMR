// webui/static/js/models.js — 模型状态 + 下载弹层（HOMR / 钢琴转录权重）。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { $, api, t } from './core.js';

let modelKind = null;

function renderModels(st) {
  if (!st || !st.homr) return;
  const homr = $('homr-status');
  homr.classList.toggle('absent', !st.homr.available);
  $('homr-status-text').textContent = st.homr.available
    ? t('w.score.homr_ready', { ver: st.homr.version || '?' })
    : t('w.score.homr_missing', { p: st.homr.files_present, t: st.homr.files_total });
  $('homr-download').disabled = st.homr.available;
  $('homr-delete').disabled = !st.homr.files_present;
  const piano = $('piano-status');
  piano.classList.toggle('absent', !st.piano.available);
  $('piano-status-text').textContent = st.piano.available ? t('w.audio.piano_ready') : t('w.audio.piano_missing');
  $('piano-download').disabled = st.piano.available;
  $('piano-delete').disabled = !st.piano.available;
}

export async function refreshModels() { renderModels(await api().models_status()); }

export function startModelDownload(kind, title) {
  modelKind = kind;
  $('model-title').textContent = title;
  $('model-msg').textContent = t('w.model.connecting');
  $('model-msg').classList.remove('err');
  $('model-bar').style.width = '0%';
  $('model-close').classList.add('hidden');
  $('model-retry').classList.add('hidden');
  $('model-cancel').classList.remove('hidden');
  $('model-overlay').classList.remove('hidden');
  api().models_download(kind);
}
$('homr-download').addEventListener('click', () => startModelDownload('homr', t('w.model.dl_title_homr')));
$('piano-download').addEventListener('click', () => startModelDownload('piano', t('w.model.dl_title_piano')));
$('model-cancel').addEventListener('click', () => { if (modelKind) api().models_cancel_download(modelKind); });
$('model-close').addEventListener('click', () => { $('model-overlay').classList.add('hidden'); modelKind = null; });
$('model-retry').addEventListener('click', () => { if (modelKind) startModelDownload(modelKind, $('model-title').textContent); });
$('homr-delete').addEventListener('click', async () => {
  if (confirm(t('w.model.del_homr_confirm'))) await api().models_delete('homr');
});
$('piano-delete').addEventListener('click', async () => {
  if (confirm(t('w.model.del_piano_confirm'))) await api().models_delete('piano');
});

window.addEventListener('model_download_progress', (e) => {
  const d = e.detail || {};
  if (d.kind !== modelKind) return;
  $('model-bar').style.width = `${Math.round((d.value || 0) * 100)}%`;
  $('model-msg').textContent = d.message || '';
});
window.addEventListener('model_download_done', (e) => {
  const d = e.detail || {};
  if (d.kind !== modelKind) return;
  if (!d.ok && d.error !== 'cancelled') {
    // 保持浮层开着，切到"失败"态：提示 + 重试/关闭，而不是弹 alert 关掉了事
    $('model-msg').textContent = t('w.model.dl_failed', { e: d.error });
    $('model-msg').classList.add('err');
    $('model-cancel').classList.add('hidden');
    $('model-close').classList.remove('hidden');
    $('model-retry').classList.remove('hidden');
    return;
  }
  $('model-overlay').classList.add('hidden');
  modelKind = null;
});
window.addEventListener('models_changed', (e) => renderModels(e.detail && e.detail.status));
