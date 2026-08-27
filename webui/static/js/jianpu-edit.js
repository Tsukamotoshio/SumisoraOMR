// webui/static/js/jianpu-edit.js — 编辑模型 + 撤销/重做命令栈（阶段5.1）。
//
// 纯逻辑：不碰 DOM、不碰渲染、零静态 import，可直接 node --test。这不是偷懒，
// 是 B7 风险6 自己开的药方——"阶段5 的撤销/重做 + 光标模型：多数编辑器死在这
// 一步；独立于渲染先行设计；command pattern 单测覆盖"。
//
// 模型形状**逐字段镜像 Python 的 JianpuDoc/JianpuSection/JianpuNote**（core/config.py），
// 连字段名的 snake_case 都照搬。这样等 5.3 真要把 JianpuDoc 通过桥传过来时，
// JSON 两侧同形，不需要中间再加一层改名映射——少一层就少一处能出错的地方。
//
//   doc  = { title, composer, key_header, tempo, sections: [section, ...] }
//   sect = { time_sig, measures: [[note, ...], ...] }
//   note = { symbol, accidental, upper_dots, lower_dots, duration, duration_dots,
//            midi, is_rest }
//
// 本阶段刻意**不做**"把模型写回文本"——那属于 5.3，而且要先定计划文档里记的那条
// 甲/乙抉择（含 🟡 档语法的文件要不要开放图形编辑）。这里的设计对两条路都中立。
'use strict';

/** 八度点最多叠 3 层（与 B5 渲染层"可叠 2–3 层"一致）。 */
const MAX_OCTAVE_DOTS = 3;

// 时值阶梯：能被单个音符表达的**未附点**时值，从最短到最长。
// 上限 4.0 / 下限 0.125 来自 core/config.py 的 ALLOWED_JIANPU_DURATIONS——
// 序列化器 jianpu_note_token 只认这张表里的值，超出的会掉进兜底分支变成别的
// 时值。注意 2.0/3.0/4.0 在文本里是靠"音符 + 延音横线"表达的（`1 -`），
// 序列化器会自己展开，所以这里可以照常当成单个音符的时值来处理。
const DURATION_LADDER = [0.125, 0.25, 0.5, 1.0, 2.0, 4.0];
// 附点 = 基础时值的 1.5 倍。3.0（附点二分）在表内，6.0 不在——所以从 4.0
// 起步的附点无法表达，这种情况下只能把附点去掉。
const DOTTED_ALLOWED = new Set([0.1875, 0.375, 0.75, 1.5, 3.0]);

/**
 * 按 `+` / `-` 改时值时的下一档取值。
 *
 * 音乐上"加长/缩短一档"指的是**基础时值**翻倍或减半，附点是独立的修饰——
 * 所以先把附点除掉、在阶梯上挪一格、再把附点加回去。若加回去之后的值不在
 * 允许表内（例如 4.0 的附点是 6.0），就只能丢掉附点：宁可少一个附点，也不
 * 能写出一个序列化器认不出、会被悄悄换成别的时值的数。
 *
 * @param {{duration:number, duration_dots:number}} note
 * @param {number} direction +1 加长，-1 缩短
 * @returns {{duration:number, duration_dots:number}|null} 已在两端时返回 null
 */
export function steppedDuration(note, direction) {
  const dotted = (note.duration_dots || 0) > 0;
  const base = dotted ? (note.duration || 0) / 1.5 : (note.duration || 0);
  // 找最接近的一档，容忍浮点误差（时值是 1.5 除出来的，不会是精确的二进制小数）
  let index = 0;
  let best = Infinity;
  DURATION_LADDER.forEach((value, i) => {
    const distance = Math.abs(value - base);
    if (distance < best) { best = distance; index = i; }
  });
  const next = index + (direction > 0 ? 1 : -1);
  if (next < 0 || next >= DURATION_LADDER.length) return null;
  const nextBase = DURATION_LADDER[next];
  const nextDotted = dotted && DOTTED_ALLOWED.has(nextBase * 1.5);
  return {
    duration: nextDotted ? nextBase * 1.5 : nextBase,
    duration_dots: nextDotted ? 1 : 0,
  };
}

// ── 寻址（B5③ 光标模型的位置定义：声部/小节/音符索引）─────────────────────────

/** 构造一个音符地址。 */
export function noteRef(section, measure, index) {
  return { section, measure, index };
}

export function refEquals(a, b) {
  if (!a || !b) return a === b;
  return a.section === b.section && a.measure === b.measure && a.index === b.index;
}

/** 取地址处的音符；越界返回 null（不抛异常——光标移动天然会撞边界）。 */
export function getNote(doc, ref) {
  if (!doc || !ref) return null;
  const section = (doc.sections || [])[ref.section];
  if (!section) return null;
  const measure = (section.measures || [])[ref.measure];
  if (!measure) return null;
  return measure[ref.index] || null;
}

/**
 * 全谱所有音符地址，按演奏顺序。
 *
 * 光标前进/后退直接建在这个列表上，而不是就地做三层下标的加减：后者要同时照顾
 * 跨小节、跨分段、空小节、首尾越界四种情况，条件分支交织起来极易写错（第一版
 * 的 `prevRef` 就是在"索引减到负数"时错误地退回本小节末尾而不是上一小节，从谱
 * 首往前走会绕回自己）。列表法把这些情况全部化归成"在一维数组里挪一格"，正确
 * 性一眼可见。光标移动是按键触发、不是每帧调用，几百个音符的谱子这点开销可以
 * 忽略——这里换取的清晰度远比省下的微秒值钱。
 */
export function allRefs(doc) {
  const out = [];
  ((doc && doc.sections) || []).forEach((section, si) => {
    (section.measures || []).forEach((measure, mi) => {
      measure.forEach((_note, ni) => out.push(noteRef(si, mi, ni)));
    });
  });
  return out;
}

/** 下一个音符的地址，跨小节、跨分段；谱尾返回 null。 */
export function nextRef(doc, ref) {
  if (!doc || !ref) return null;
  const refs = allRefs(doc);
  const i = refs.findIndex((r) => refEquals(r, ref));
  return i >= 0 && i + 1 < refs.length ? refs[i + 1] : null;
}

/** 上一个音符的地址，跨小节、跨分段；谱首返回 null。 */
export function prevRef(doc, ref) {
  if (!doc || !ref) return null;
  const refs = allRefs(doc);
  const i = refs.findIndex((r) => refEquals(r, ref));
  return i > 0 ? refs[i - 1] : null;
}

// ── 命令 ─────────────────────────────────────────────────────────────────────
// 命令是**纯数据**（可序列化、可打日志、可直接在测试里比较），不是闭包。
// applyCommand() 施加它并**返回逆命令**；撤销就是施加那条逆命令。
//
// 值类命令的逆一律是 restore_note（原样放回改动前那一个音符）：与其为每条命令
// 手写一遍"怎么还原"（改音高会连带清掉休止标记、改休止会连带清掉升降号……漏一
// 个字段就是一个撤销 bug），不如现场把旧音符整个收起来。代价是一个音符的内存，
// 换来的是逆操作正确性**由构造保证**而不是靠我把每种连带影响都想全。
// 结构类命令（增删音符）无法这样表达，各自有明确的逆。

function cloneNote(note) {
  const copy = { ...note };
  if (note && note.lyrics) copy.lyrics = { ...note.lyrics };
  return copy;
}

function measureOf(doc, ref) {
  const section = (doc.sections || [])[ref.section];
  if (!section) return null;
  return (section.measures || [])[ref.measure] || null;
}

/** 施加命令，返回逆命令。地址无效时返回 null（调用方据此判断"什么也没发生"）。 */
export function applyCommand(doc, cmd) {
  switch (cmd.type) {
    case 'restore_note': {
      const measure = measureOf(doc, cmd.ref);
      if (!measure || !measure[cmd.ref.index]) return null;
      const before = cloneNote(measure[cmd.ref.index]);
      measure[cmd.ref.index] = cloneNote(cmd.note);
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'set_pitch': {
      const note = getNote(doc, cmd.ref);
      if (!note) return null;
      const before = cloneNote(note);
      note.symbol = cmd.symbol;
      note.is_rest = cmd.symbol === '0';
      if (note.is_rest) {           // 休止符没有升降号与八度，改过去就得清干净
        note.accidental = '';
        note.upper_dots = 0;
        note.lower_dots = 0;
      }
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'set_rest':
      return applyCommand(doc, { type: 'set_pitch', ref: cmd.ref, symbol: '0' });
    case 'set_accidental': {
      const note = getNote(doc, cmd.ref);
      if (!note || note.is_rest) return null;   // 休止符没有升降号可言
      const before = cloneNote(note);
      note.accidental = cmd.accidental;
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'set_octave': {
      const note = getNote(doc, cmd.ref);
      if (!note || note.is_rest) return null;
      const before = cloneNote(note);
      // 八度用"上点数 - 下点数"表示，中间没有同时有上下点的状态。
      let octave = (note.upper_dots || 0) - (note.lower_dots || 0) + cmd.delta;
      octave = Math.max(-MAX_OCTAVE_DOTS, Math.min(MAX_OCTAVE_DOTS, octave));
      note.upper_dots = Math.max(0, octave);
      note.lower_dots = Math.max(0, -octave);
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'set_duration': {
      const note = getNote(doc, cmd.ref);
      if (!note) return null;
      const before = cloneNote(note);
      note.duration = cmd.duration;
      if (cmd.duration_dots !== undefined) note.duration_dots = cmd.duration_dots;
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'toggle_dot': {
      const note = getNote(doc, cmd.ref);
      if (!note) return null;
      const before = cloneNote(note);
      const dotted = (note.duration_dots || 0) > 0;
      // 附点 = 时值的 1.5 倍；取消附点就除回去。时值与附点标记必须一起动，
      // 否则序列化出的 token 与它自称的时值对不上。
      note.duration_dots = dotted ? 0 : 1;
      note.duration = dotted ? note.duration / 1.5 : note.duration * 1.5;
      return { type: 'restore_note', ref: cmd.ref, note: before };
    }
    case 'delete_note': {
      const measure = measureOf(doc, cmd.ref);
      if (!measure || cmd.ref.index >= measure.length) return null;
      const [removed] = measure.splice(cmd.ref.index, 1);
      return { type: 'insert_note', ref: cmd.ref, note: cloneNote(removed) };
    }
    case 'insert_note': {
      const measure = measureOf(doc, cmd.ref);
      if (!measure || cmd.ref.index > measure.length) return null;
      measure.splice(cmd.ref.index, 0, cloneNote(cmd.note));
      return { type: 'delete_note', ref: cmd.ref };
    }
    default:
      return null;
  }
}

// ── 历史（撤销 / 重做）────────────────────────────────────────────────────────

// 两个栈里存的都是**命令**，且都靠同一条性质运转：applyCommand() 总是返回一条
// 精确的逆命令，所以"逆的逆"就是原改动本身。撤销/重做因此不需要额外记住原始
// 命令，只要把 apply 吐回来的东西压到另一个栈上即可，两个方向对称。
export class EditHistory {
  constructor(doc) {
    this.doc = doc;
    // 两个栈里存的都是**命令数组**，不是单条命令。一次语义操作可能由好几条
    // 原始命令组成（比如"插入一个音符并从后面吃掉同样的时值"要先删/缩后插），
    // 而 B5③ 要求撤销的粒度是**一次语义操作**而不是一次原始命令——用户按一次
    // 键，按一次 Ctrl+Z 就该整个退回去。单条命令只是长度为 1 的数组。
    this._undo = [];
    this._redo = [];
  }

  get canUndo() { return this._undo.length > 0; }
  get canRedo() { return this._redo.length > 0; }

  /** 执行一条命令；成功返回 true。执行新命令会让重做栈立即失效。 */
  do(cmd) {
    return this.doGroup([cmd]);
  }

  /**
   * 把若干条命令作为**一个**撤销单位执行。
   * 中途有任何一条失败就把已经做掉的部分原样退回，不留半截状态——半个操作
   * 落在文档里比操作没发生更糟。
   */
  doGroup(cmds) {
    if (!Array.isArray(cmds) || !cmds.length) return false;
    const inverses = [];
    for (const cmd of cmds) {
      const inverse = applyCommand(this.doc, cmd);
      if (!inverse) {
        for (let i = inverses.length - 1; i >= 0; i--) applyCommand(this.doc, inverses[i]);
        return false;
      }
      inverses.push(inverse);
    }
    this._undo.push(inverses);
    // 撤销之后又做了新改动，原来的"未来"已经不成立了——保留它会让重做重放到
    // 一个不存在的分支上。这是编辑器的通用语义。
    this._redo.length = 0;
    return true;
  }

  undo() { return this._step(this._undo, this._redo); }

  redo() { return this._step(this._redo, this._undo); }

  /**
   * 撤销与重做是同一件事：把栈顶那组命令**倒序**施加回去，再把这一趟得到的
   * 逆压到另一个栈上。倒序是必须的——组内命令有先后依赖（先删后插），退回去
   * 自然要从最后一步开始。两个方向完全对称，所以共用一个实现。
   */
  _step(from, to) {
    const group = from.pop();
    if (!group) return false;      // 空栈时是安全的 no-op，不抛异常
    const inverses = [];
    for (let i = group.length - 1; i >= 0; i--) {
      const inverse = applyCommand(this.doc, group[i]);
      if (!inverse) return false;
      inverses.push(inverse);
    }
    to.push(inverses);
    return true;
  }

  clear() {
    this._undo.length = 0;
    this._redo.length = 0;
  }
}

/**
 * 在 *ref* 处插入 *note*，并从紧随其后的休止符/延音横线里吃掉同样多的时值，
 * 使小节总长不变（阶段5.4 决议"丙"）。返回一组命令，交给 EditHistory.doGroup
 * 作为一次撤销单位执行；地址无效时返回 null。
 *
 * 为什么要"吃掉"：空白谱是由整小节休止符构成的（`0 - - -` 解析成 1 个休止符
 * + 3 个延音横线），纯插入会让原有休止符原封不动地留着，小节越填越胀；而按
 * 槽位覆写又只能写出槽位自带的时值，八分音符根本录不进去。
 *
 * 只吃休止符和延音横线，**遇到真实音符就停**——那属于"补一个漏掉的音"，把后
 * 面的音符挤掉不是用户的意思。吃不够时也照插不误，让小节超拍，由校验层报黄
 * 警告（B5② 不阻断原则）。
 */
export function insertConsumingCommands(doc, ref, note) {
  const section = doc && doc.sections ? doc.sections[ref.section] : null;
  const measure = section && section.measures ? section.measures[ref.measure] : null;
  if (!measure || ref.index > measure.length) return null;

  const TOL = 1e-9;
  const cmds = [];
  let remaining = note.duration || 0;
  // 读指针在原数组上前进；而删除命令始终指向同一个下标——每删掉一个，后面的
  // 元素就顶上来，所以下标不动才是对的。
  let read = ref.index;
  const at = noteRef(ref.section, ref.measure, ref.index);
  while (remaining > TOL && read < measure.length) {
    const item = measure[read];
    if (!item.is_rest && item.symbol !== '-') break;
    if ((item.duration || 0) <= remaining + TOL) {
      cmds.push({ type: 'delete_note', ref: at });
      remaining -= item.duration || 0;
      read += 1;
    } else {
      cmds.push({
        type: 'set_duration', ref: at,
        duration: item.duration - remaining, duration_dots: 0,
      });
      remaining = 0;
    }
  }
  cmds.push({ type: 'insert_note', ref: at, note });
  return cmds;
}
