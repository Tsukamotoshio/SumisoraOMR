// webui/static/js/midi.js — 内置 MIDI 播放器（WebAudioTinySynth，自包含 GM 合成器）。
// MIDI 不能直接喂 <audio>，需 JS 合成器。tinySynth 懒加载（首次播放才拉），单实例
// 单合成器；播放时把播放器 DOM 移入当前预览页的 .midislot（内嵌，不重复）。
// Split out of the former monolithic app.js (P0-3) — pure move, no behavior change.
'use strict';

import { t, toast } from './core.js';

const MP_ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  vol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 9v6h4l5 4V5L8 9H4z" stroke-linejoin="round"/><path d="M16 8.5a5 5 0 0 1 0 7" stroke-linecap="round"/></svg>',
};
let _synthLibPromise = null;
function loadSynthLib() {
  if (_synthLibPromise) return _synthLibPromise;
  _synthLibPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '../vendor/tinysynth/webaudio-tinysynth.js';
    s.onload = () => resolve(window.WebAudioTinySynth);
    s.onerror = () => reject(new Error('tinysynth load failed'));
    document.head.appendChild(s);
  });
  return _synthLibPromise;
}
class MidiPlayer {
  constructor() {
    this.synth = null;
    this.maxTick = 0;
    this.seeking = false;
    this._timer = 0;
    this.el = this._build();
  }
  _build() {
    const el = document.createElement('div');
    el.className = 'midiplayer';
    el.innerHTML = `
      <button class="mp-btn" data-mp="play" aria-label="播放/暂停">${MP_ICON.play}</button>
      <button class="mp-btn" data-mp="stop" aria-label="停止">${MP_ICON.stop}</button>
      <span class="mp-time" data-mp="cur">0:00</span>
      <input class="mp-seek" data-mp="seek" type="range" min="0" max="1000" value="0" aria-label="进度">
      <span class="mp-time" data-mp="dur">0:00</span>
      <span class="mp-volico">${MP_ICON.vol}</span>
      <input class="mp-vol" data-mp="vol" type="range" min="0" max="100" value="80" aria-label="音量">
      <span class="mp-name" data-mp="name"></span>`;
    const q = (k) => el.querySelector(`[data-mp="${k}"]`);
    this.$play = q('play'); this.$stop = q('stop'); this.$cur = q('cur');
    this.$seek = q('seek'); this.$dur = q('dur'); this.$vol = q('vol'); this.$name = q('name');
    this.$play.addEventListener('click', () => this.toggle());
    this.$stop.addEventListener('click', () => this.stop());
    this.$seek.addEventListener('input', () => { this.seeking = true; });
    this.$seek.addEventListener('change', () => { this._applySeek(); this.seeking = false; });
    this.$vol.addEventListener('input', () => { if (this.synth) this.synth.setMasterVol(this.$vol.value / 100); });
    return el;
  }
  async play(slot, url, name) {
    let Lib;
    try { Lib = await loadSynthLib(); } catch (_e) { toast(t('w.midi.synth_fail')); return; }
    if (!this.synth) { this.synth = new Lib(); this.synth.setMasterVol(this.$vol.value / 100); }
    slot.appendChild(this.el);
    this.$name.textContent = name || '';
    let buf;
    try { buf = await (await fetch(url)).arrayBuffer(); } catch (_e) { toast(t('w.midi.load_fail')); return; }
    this.synth.stopMIDI();
    this.synth.loadMIDI(new Uint8Array(buf));
    this.maxTick = this.synth.getPlayStatus().maxTick || 0;
    this.$dur.textContent = this._fmt(this.maxTick * this._k());
    this.synth.locateMIDI(0);
    this.synth.playMIDI();
    this._setIcon(true);
    this._ensureTimer();
  }
  toggle() {
    if (!this.synth) return;
    if (this.synth.getPlayStatus().play) { this.synth.stopMIDI(); this._setIcon(false); }
    else { this.synth.playMIDI(); this._setIcon(true); this._ensureTimer(); }
  }
  stop() {
    if (!this.synth) return;
    this.synth.stopMIDI();
    this.synth.locateMIDI(0);
    this._setIcon(false);
    this._tick();
  }
  leave() {   // 离开预览页：停止并收起
    if (this.synth) { this.synth.stopMIDI(); this.synth.locateMIDI(0); }
    this._setIcon(false);
    clearInterval(this._timer); this._timer = 0;
    if (this.el.parentElement) this.el.remove();
  }
  _applySeek() {
    if (!this.synth || !this.maxTick) return;
    this.synth.locateMIDI(Math.round(this.$seek.value / 1000 * this.maxTick));
    this._tick();
  }
  _ensureTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), 200);
  }
  _tick() {
    if (!this.synth) return;
    const st = this.synth.getPlayStatus();
    if (!this.seeking && this.maxTick) this.$seek.value = Math.round(st.curTick / this.maxTick * 1000);
    const k = this._k();
    this.$cur.textContent = this._fmt(st.curTick * k);
    this.$dur.textContent = this._fmt(this.maxTick * k);   // tempo 事件在 tick0，首帧后即稳定
    if (!st.play) { this._setIcon(false); clearInterval(this._timer); this._timer = 0; }
  }
  // tick→秒换算系数：4 拍 * 60 / timebase / tempo（与 tinySynth 内部一致）
  _k() { const s = this.synth && this.synth.song; return (s && s.timebase && s.tempo) ? 4 * 60 / s.timebase / s.tempo : 0; }
  _setIcon(playing) { this.$play.innerHTML = playing ? MP_ICON.pause : MP_ICON.play; }
  _fmt(sec) { sec = Math.max(0, Math.round(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
}
export const midiPlayer = new MidiPlayer();
