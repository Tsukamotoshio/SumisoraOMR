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

function sectionOf(doc, at) {
  return (doc && doc.sections ? doc.sections[at.section] : null) || null;
}

// ── 小节级操作（阶段5.5：小节插入/删除）────────────────────────────────────────

// 与 core/config.py 的 ALLOWED_JIANPU_DURATIONS 逐项一致（降序，供贪心填充用）。
// 之所以在这里也写一份而不是过桥问 Python：插入小节是模型层的同步编辑，跟
// jianpu-edit.js 全文件"零桥接"的设计一致（B7 风险6——把撤销/重做独立于渲染
// 设计，就是为了不依赖任何异步往返）。两处列表分别有测试兜底（Python 侧的
// split_duration_chunks 单测、这里对已知拍号的直接断言），漂移会被测试挡住。
const ALLOWED_DURATIONS_DESC = [4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125];
const DOTTED_DURATIONS = new Set([1.5, 0.75, 0.375, 0.1875]);

function blankRestNote(duration) {
  return {
    symbol: '0', accidental: '', upper_dots: 0, lower_dots: 0,
    duration, duration_dots: DOTTED_DURATIONS.has(duration) ? 1 : 0,
    midi: null, is_rest: true, lyrics: {},
  };
}

/** 延音横线续接对象——注意 is_rest 是 false：parse_jianpu_ly_text 读回 `-`
 * token 时就是这样标的（它不是"休止"，是"沿用上一个音/休止的时值"）。 */
function dashNote() {
  return {
    symbol: '-', accidental: '', upper_dots: 0, lower_dots: 0,
    duration: 1.0, duration_dots: 0, midi: null, is_rest: false, lyrics: {},
  };
}

/**
 * 一整小节的空白内容（全休止符），按 *barQuarterLength* 贪心拆分成可表达的
 * 时值链——与 core/notation/jianpu/primitives.py 的 split_duration_chunks /
 * webui/editor.py 的 _blank_measure_text 是同一个算法，非常规拍号（5/4、7/8）
 * 因此会拆成正确的多个休止符 token，而不是塞进一个表达不了的时值。
 *
 * 关键的一步在于**每个 chunk 不是直接变成一个音符对象**：`jianpu_note_token`
 * 序列化时只有 2.0/3.0/4.0 这三个整拍时值会展开成"起始休止符 + N 个延音横
 * 线"（文本形如 `0 - - -`），其余时值（含附点）都写成单个 token；而
 * parse_jianpu_ly_text 读文本时正是按 token 数量建模型对象的——所以一个
 * chunk=4.0 的休止符在**解析后的模型里**其实是 4 个对象（1 休止 + 3 延音横
 * 线），不是 1 个 duration=4.0 的对象。这里必须照抄这条展开规则，插入的空
 * 白小节才会跟"新建向导生成、经 create_blank→parse 过一轮"或"5.4 从零录
 * 入"的小节长成同一个形状——否则光标能停靠的槽位数量（阶段5.4 依据的正是
 * 模型音符对象，不是按时长连续切分）会因小节的来路不同而不一样。
 */
export function blankMeasureNotes(barQuarterLength) {
  const TOL = 0.01;
  // Number.isFinite 挡住 Infinity/NaN：一个非有限的拍长会让下面的循环永远
  // 减不完（Infinity - 4 还是 Infinity），把整个页面卡死。拍号文本是用户可以
  // 在文本页手打的，`4/0` 这种写法解析器并不拒绝，所以这不是假想输入。
  let remaining = Number.isFinite(barQuarterLength) ? Math.max(barQuarterLength, 0) : 0;
  // 再加一道硬上限：即使将来哪里又漏进一个畸形数值，最坏也只是产出一个长度
  // 离谱的小节，而不是让浏览器失去响应——死循环没有任何错误信息，最难排查。
  const MAX_NOTES = 512;
  const notes = [];
  while (remaining > TOL && notes.length < MAX_NOTES) {
    const piece = ALLOWED_DURATIONS_DESC.find((d) => remaining + TOL >= d) || 0.125;
    if (piece >= 2.0 - TOL) {
      notes.push(blankRestNote(1.0));
      for (let i = 1; i < Math.round(piece); i++) notes.push(dashNote());
    } else {
      notes.push(blankRestNote(piece));
    }
    remaining -= piece;
  }
  // 退化拍长（≈0）没有可分配的时值；一个四分休止符是最安全的兜底——与裸
  // token '0' 在文本格式里的默认时值语义一致（parse_jianpu_ly_text 把它读成
  // duration=1.0），不会凭空发明一个格式认不出的时值。
  return notes.length ? notes : [blankRestNote(1.0)];
}

/**
 * 从 '4/4'、'3/4,8'（带切分符的弱起写法）等拍号文本算出一小节的四分音符数。
 *
 * 拍号是用户能在文本页直接手打的，而解析器**不校验**分子分母（`4/0`、`0/4`
 * 都会被原样收进 time_sig）。所以这里不能只判断"正则匹配上了没有"，还得确认
 * 算出来的确实是个正的有限数，否则 `4/0` 会算出 Infinity 一路传下去。算不出
 * 有意义的结果时退回 4/4——插入的小节拍数可能不合用户本意，但那是看得见、可
 * 修改的，比卡死或者产出一个畸形小节强。
 */
export function barQuarterLength(timeSig) {
  const m = /^(\d+)\/(\d+)/.exec(timeSig || '');
  if (!m) return 4.0;
  const ql = (4.0 * Number(m[1])) / Number(m[2]);
  return Number.isFinite(ql) && ql > 0 ? ql : 4.0;
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
    case 'insert_measure': {
      const section = sectionOf(doc, cmd.at);
      if (!section || cmd.at.measure > section.measures.length) return null;
      section.measures.splice(cmd.at.measure, 0, (cmd.notes || []).map(cloneNote));
      return { type: 'delete_measure', at: cmd.at };
    }
    case 'delete_measure': {
      const section = sectionOf(doc, cmd.at);
      if (!section || cmd.at.measure >= section.measures.length) return null;
      // 绝不让一个分段的小节数掉到 0——那不是"空小节"（空小节是合法内容，
      // 全是休止符），而是分段本身没有内容了，下游解析/渲染/播放都没为这种
      // 形状设计过、也没人测过。宁可拒绝这次删除，让用户看见"删不掉"，也不要
      // 造出一份没人验证过能不能正常工作的文档。
      if (section.measures.length <= 1) return null;
      const [removed] = section.measures.splice(cmd.at.measure, 1);
      return { type: 'insert_measure', at: cmd.at, notes: removed.map(cloneNote) };
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


// ── 复制 / 粘贴（阶段5.5b）────────────────────────────────────────────────────
// 决议：**整小节对齐的片段整小节粘贴，零碎片段按音符流插入**。
//
// 为什么要分两条路：验收要求"片段跨文件粘贴保真"。若一律按音符流，复制两个小
// 节再粘贴就会被倒进一个小节里变成 8 拍（黄警告）——而"复制一个乐句"本来就是
// 多小节，这种最常见的用法反而最不保真。反过来若一律新起小节，只想补两个漏掉
// 的音也会凭空多出一个欠拍小节，而校对 OMR 恰恰是本项目的主场景。按片段是否
// 对齐小节边界来分流，两种常见用法各自都符合直觉。

/**
 * 把选区两端的音符地址扩成一个**模型区间**，并判断它是否整小节对齐。
 *
 * *fromRef* / *toRef* 是选中的第一个和最后一个**画出来的**音符的地址。区间尾
 * 部要顺着延音横线往后吃：一个时值 ≥2 拍的音符在模型里是"音符 + N 个横线"多
 * 个对象，而画面上只是一个音符，只截到那个音符就会把它的时值削掉一大截。
 *
 * @returns {{startMeasure:number,startIndex:number,endMeasure:number,endIndex:number,aligned:boolean}|null}
 */
export function selectionSpan(doc, sectionIndex, fromRef, toRef) {
  const section = (doc && doc.sections) ? doc.sections[sectionIndex] : null;
  if (!section || !fromRef || !toRef) return null;
  const measures = section.measures || [];
  const startMeasure = fromRef.measure;
  const startIndex = fromRef.index;
  const endMeasure = toRef.measure;
  if (!measures[startMeasure] || !measures[endMeasure]) return null;
  let endIndex = toRef.index;
  const tail = measures[endMeasure];
  while (endIndex + 1 < tail.length && tail[endIndex + 1].symbol === '-') endIndex += 1;
  const aligned = startIndex === 0 && endIndex === tail.length - 1;
  return { startMeasure, startIndex, endMeasure, endIndex, aligned };
}

/**
 * 按 *span* 从文档里取出片段内容。
 *
 * 对齐的片段取成**若干个完整小节**（保住小节线），不对齐的取成一串音符。两种
 * 形状分别对应上面那两条粘贴路径，所以剪贴板里存的就是最终要用的形状，粘贴时
 * 不必再判断一次。
 *
 * @returns {{aligned:true, measures:Array<Array<object>>}|{aligned:false, notes:Array<object>}|null}
 */
export function extractFragment(doc, sectionIndex, span) {
  const section = (doc && doc.sections) ? doc.sections[sectionIndex] : null;
  if (!section || !span) return null;
  const measures = section.measures || [];
  if (span.aligned) {
    const picked = measures
      .slice(span.startMeasure, span.endMeasure + 1)
      .map((m) => m.map(cloneNote));
    return picked.length ? { aligned: true, measures: picked } : null;
  }
  const notes = [];
  for (let m = span.startMeasure; m <= span.endMeasure; m++) {
    const measure = measures[m];
    if (!measure) return null;
    const from = m === span.startMeasure ? span.startIndex : 0;
    const to = m === span.endMeasure ? span.endIndex : measure.length - 1;
    for (let i = from; i <= to; i++) if (measure[i]) notes.push(cloneNote(measure[i]));
  }
  return notes.length ? { aligned: false, notes } : null;
}

/**
 * 把一串音符依次录到 *ref* 处（每个都走 5.4 的"插入并吃掉后面的时值"），返回
 * 一整组命令交给 EditHistory.doGroup —— 一次粘贴是一次撤销。
 *
 * 必须在**副本**上推演：insertConsumingCommands 是照着当前文档算命令、并不施
 * 加，所以第二个音符要看到第一个已经插进去之后的样子才算得对。副本与真文档起
 * 点相同，同一串命令施加到真文档上结果一致。
 */
export function pasteNotesCommands(doc, ref, notes) {
  if (!notes || !notes.length) return null;
  const draft = JSON.parse(JSON.stringify(doc));
  const cmds = [];
  let index = ref.index;
  for (const note of notes) {
    const at = noteRef(ref.section, ref.measure, index);
    const part = insertConsumingCommands(draft, at, note);
    if (!part) return null;
    for (const cmd of part) {
      if (!applyCommand(draft, cmd)) return null;
      cmds.push(cmd);
    }
    index += 1;   // 每录一个音符就占住一格，下一个接着往后放
  }
  return cmds;
}

/**
 * 把若干个完整小节插到 *measureIndex* **之前**（粘贴点所在的那个小节前面），
 * 原有小节整体后移。返回一组命令，同样是一次撤销。
 */
export function pasteMeasuresCommands(sectionIndex, measureIndex, measures) {
  if (!measures || !measures.length) return null;
  return measures.map((notes, i) => ({
    type: 'insert_measure',
    at: { section: sectionIndex, measure: measureIndex + i },
    notes,
  }));
}
