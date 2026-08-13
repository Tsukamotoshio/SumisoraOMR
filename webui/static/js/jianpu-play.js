// webui/static/js/jianpu-play.js — 编辑器图形预览的播放引擎（阶段4.1）。
//
// 为什么不复用 midi.js 的 MidiPlayer：那套是"fetch 一个 .mid 文件 →
// tinysynth loadMIDI/playMIDI"，而编辑器里根本没有 MIDI 文件——用户正在编辑的
// 缓冲区可能还没保存过。这里直接用图形预览已经在用的那份 `renders` 数据发声：
// 它本来就是 {start(拍), length(拍), pitch(MIDI)} + tempos，正是发声所需的全部
// 信息，不需要为播放再往服务端跑一趟生成 MIDI。
//
// 附带的红利（阶段4.2 才兑现）：声音和高亮由同一份数据、同一个 AudioContext
// 时钟驱动，二者结构上就不可能对不齐。
'use strict';

// 刻意不静态 import './midi.js'：它经 core.js 会在模块顶层碰 document，静态引入
// 就没法在 node --test 里单测本模块的纯逻辑（buildTimeline）。改成真正播放时才
// 动态 import，顺带也让合成器那套东西不进初始模块图。
let _synthLibPromise = null;
function loadSynthLib() {
  if (!_synthLibPromise) {
    _synthLibPromise = import('./midi.js').then((m) => m.loadSynthLib());
  }
  return _synthLibPromise;
}

// 预调度窗口（秒）与调度器心跳（毫秒）——Web Audio 的经典"两个时钟"模式：
// setInterval 只负责"把未来一小段时间内的音符交给 AudioContext"，真正的发声
// 时刻由 AudioContext 自己按采样精度兑现，所以 setInterval 的抖动不会传导成
// 听得见的节奏抖动。窗口取得比心跳大若干倍，漏掉一两次心跳也不会断音。
const SCHEDULE_AHEAD_SEC = 0.15;
const SCHEDULER_TICK_MS = 25;
// 按下播放到第一个音符发声之间留的余量，给首次调度和 AudioContext 恢复留时间。
const START_LEAD_SEC = 0.06;

/** MIDI 通道 9（0 基）在 GM 里固定是打击乐，分配声部时必须跳过。 */
const DRUM_CHANNEL = 9;

/**
 * 把 `renders`（每个 NextPart 分段一项）摊平成一条按时间排序的音符事件表。
 * 每个分段占一个 MIDI 通道，这样多声部同时发声不会互相掐断。
 *
 * 每条事件除了发声要用的秒数信息，还带上 `section`（第几个分段）与 `note`
 * （原始的、以"拍"为单位的音符对象）——阶段4.2 的播放高亮要拿它去调对应
 * renderer 的 `redraw(note, true)`，那个 API 吃的正是拍单位的原始对象。
 * @param {Array<object>} renders
 * @returns {{notes: Array<{t:number, dur:number, pitch:number, ch:number, section:number, note:object}>, duration:number}}
 */
export function buildTimeline(renders) {
  const notes = [];
  let duration = 0;
  (renders || []).forEach((render, sectionIdx) => {
    const qpm = (render.tempos && render.tempos[0] && render.tempos[0].qpm) || 120;
    const secPerQuarter = 60 / qpm;
    // 通道 0..15，跳过打击乐通道；分段多于 15 个时回绕（真实文件最多 3-4 个分段，
    // 回绕只是别让下标越界，不是正经的声部管理）。
    let ch = sectionIdx % 15;
    if (ch >= DRUM_CHANNEL) ch += 1;
    for (const n of render.notes || []) {
      const t = n.start * secPerQuarter;
      const dur = n.length * secPerQuarter;
      notes.push({ t, dur, pitch: n.pitch, ch, section: sectionIdx, note: n });
      duration = Math.max(duration, t + dur);
    }
  });
  notes.sort((a, b) => a.t - b.t);
  return { notes, duration };
}

/**
 * 求某个时刻每个分段正在发声的音符（阶段4.2 播放高亮用）。
 * 同一分段同一时刻若有多个音符（和弦），取最后开始的那个——高亮只需要一个
 * 代表元素，而"最后开始的"与听感上最新的那次起音一致。
 * @param {Array<object>} timelineNotes buildTimeline() 的 notes
 * @param {number} posSec 位置（秒）
 * @returns {Map<number, object>} 分段下标 → 原始音符对象（拍单位）
 */
export function activeNotesAt(timelineNotes, posSec) {
  const active = new Map();
  const starts = new Map();
  for (const n of timelineNotes) {
    if (n.t > posSec) break;           // 已按时间排序，后面的都还没开始
    if (posSec >= n.t + n.dur) continue; // 已经放完
    const prev = starts.get(n.section);
    if (prev === undefined || n.t >= prev) {
      starts.set(n.section, n.t);
      active.set(n.section, n.note);
    }
  }
  return active;
}

export class JianpuPlayer {
  constructor() {
    this.synth = null;
    this.notes = [];
    this.duration = 0;
    this._playing = false;
    this._timer = 0;
    this._nextIdx = 0;      // 下一个待调度的音符下标
    this._originCtxTime = 0; // 乐谱 0 秒对应的 AudioContext 时刻
    this._pausedAt = 0;      // 暂停时的乐谱位置（秒）
    this._channels = new Set();
    /** 状态变化回调（播放/暂停/停止/自然结束都会触发），供 UI 更新按钮。 */
    this.onstate = null;
  }

  /** 换谱：停掉当前播放并装载新的时间轴。 */
  load(renders) {
    this.stop();
    const { notes, duration } = buildTimeline(renders);
    this.notes = notes;
    this.duration = duration;
    this._channels = new Set(notes.map((n) => n.ch));
    this._pausedAt = 0;
  }

  get isPlaying() { return this._playing; }

  /** 当前播放位置（秒）。未播放时返回暂停/停止时的位置。 */
  get positionSec() {
    if (!this._playing || !this.synth) return this._pausedAt;
    const ctx = this.synth.getAudioContext();
    return Math.max(0, Math.min(this.duration, ctx.currentTime - this._originCtxTime));
  }

  async play() {
    if (this._playing || !this.notes.length) return;
    if (!this.synth) {
      const Lib = await loadSynthLib();
      this.synth = new Lib();
    }
    const ctx = this.synth.getAudioContext();
    // 浏览器自动播放策略：AudioContext 可能是 suspended，必须在用户手势里恢复。
    if (ctx.state === 'suspended') await ctx.resume();

    // 从暂停处继续：把"乐谱 0 秒"锚到过去，使得 now - origin == pausedAt。
    this._originCtxTime = ctx.currentTime + START_LEAD_SEC - this._pausedAt;
    this._nextIdx = this.notes.findIndex((n) => n.t >= this._pausedAt);
    if (this._nextIdx < 0) this._nextIdx = this.notes.length;
    this._playing = true;
    this._emit();
    this._schedule();
    this._timer = setInterval(() => this._schedule(), SCHEDULER_TICK_MS);
  }

  pause() {
    if (!this._playing) return;
    this._pausedAt = this.positionSec;
    this._teardown();
    this._emit();
  }

  stop() {
    const wasActive = this._playing || this._pausedAt > 0;
    this._pausedAt = 0;
    this._teardown();
    if (wasActive) this._emit();
  }

  toggle() {
    if (this._playing) this.pause(); else this.play();
  }

  /** 跳到指定位置（秒）；播放中跳转会重新锚定时钟并从该处继续。 */
  seek(sec) {
    const target = Math.max(0, Math.min(this.duration, sec || 0));
    const wasPlaying = this._playing;
    this._teardown();
    this._pausedAt = target;
    if (wasPlaying) this.play();
    else this._emit();
  }

  _teardown() {
    clearInterval(this._timer);
    this._timer = 0;
    this._playing = false;
    this._allNotesOff();
  }

  _allNotesOff() {
    if (!this.synth) return;
    for (const ch of this._channels) {
      this.synth.send([0xb0 | ch, 120, 0]);  // all sound off
      this.synth.send([0xb0 | ch, 123, 0]);  // all notes off
    }
  }

  _emit() {
    if (typeof this.onstate === 'function') this.onstate(this);
  }

  /** 把落在预调度窗口内的音符交给合成器（绝对 AudioContext 时刻）。 */
  _schedule() {
    if (!this._playing || !this.synth) return;
    const ctx = this.synth.getAudioContext();
    const horizon = ctx.currentTime + SCHEDULE_AHEAD_SEC;
    while (this._nextIdx < this.notes.length) {
      const n = this.notes[this._nextIdx];
      const onAt = this._originCtxTime + n.t;
      if (onAt > horizon) break;
      // tinysynth 的 send(msg, t)：tsmode 默认 0，t 即 AudioContext 绝对秒数
      // （见 vendor 里的 _tsConv），所以可以直接按采样精度预约。
      this.synth.send([0x90 | n.ch, n.pitch, 80], onAt);
      this.synth.send([0x80 | n.ch, n.pitch, 0], onAt + Math.max(0.02, n.dur * 0.98));
      this._nextIdx += 1;
    }
    // 全部音符都已调度且最后一个也放完了 → 自然结束，复位到开头。
    if (this._nextIdx >= this.notes.length
        && ctx.currentTime - this._originCtxTime >= this.duration) {
      this._pausedAt = 0;
      this._teardown();
      this._emit();
    }
  }
}

export const jianpuPlayer = new JianpuPlayer();
