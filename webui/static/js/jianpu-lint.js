// webui/static/js/jianpu-lint.js — pure jianpu-ly body-text linter (Stage 1,
// 修复计划2与简谱编辑器规划.md B5 校验层 / B6 阶段1).
//
// Input is exactly what the editor textarea holds: the .jianpu.txt BODY only
// (webui/editor.py:_split_header already strips the leading `#` comment
// header server-side before it ever reaches the front-end — see that
// function's docstring). Grammar reference: docs/jianpu-ly-syntax.md (P0-2),
// the shared source of truth for this linter, the future editor parser, and
// Architecture.md §5.17.
//
// No DOM access anywhere in this file — kept a pure function (text in,
// diagnostics out) so it is unit-testable under plain Node (see
// jianpu-lint.test.js) and reusable by a future incremental/AST parser
// without dragging along editor-specific rendering concerns.
'use strict';

// 64th-note units per duration prefix, undotted — docs §1.5/Appendix A.
const PREFIX_UNITS = { '': 16, q: 8, s: 4, d: 2 };

// 弱起码 → 64 分音符单位（docs §1.4 表；对齐 primitives.py:_QL_TO_ANACRUSIS_CODE）
const ANACRUSIS_UNITS = {
  '16': 4, '16.': 6, '8': 8, '8.': 12, '4': 16, '4.': 24,
  '2': 32, '2.': 48, '1': 64, '1.': 96,
};

const NOTE_RE = /^([qsd]?)([#b]?)([0-7])('+|,+)?(\.?)$/;
const DASH_RE = /^([qsd]?)(-)(\.?)$/;
const TIMESIG_RE = /^(\d+)\/(\d+)(?:,(\S+))?$/;
const HEADER_LINE_RE = /^(title=.*|composer=.*|1=\S+|6=\S+|4=\d+)$/;

// LilyPond 的全部 22 个绝对力度记号——打包的 LilyPond 2.24.4 里
// ly/dynamic-scripts-init.ly 定义的 AbsoluteDynamicEvent，一个不多一个不少。
// 必须与 core/notation/jianpu/primitives.py 的 DYNAMIC_MARKS 逐字一致：
// tests/test_jianpu_dynamics.py 会把这里的清单读出来和 Python 那份对比。
//
// 这份清单以前是手写的 10 个（p pp ppp mp mf f ff fff sf sfz），另外 12 个合法记号
// 落进最后的 bad-token 分支被标成红色错误；6.1a 解析器开始接受全部 22 个之后，
// 同一个 \fp 就变成了"文本页标红、图形页正常打开、PDF 正常渲染"。
// 区分大小写：\PP 在 LilyPond 里是错误，这里也不认。
const DYNAMIC_MARKS = new Set([
  'ppppp', 'pppp', 'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff', 'ffff', 'fffff',
  'fp', 'sf', 'sfp', 'sff', 'sfz', 'fz', 'sp', 'spp', 'rfz', 'n',
]);

function isDynamicWord(word) {
  return word.length > 1 && word[0] === '\\' && DYNAMIC_MARKS.has(word.slice(1));
}

// 渐强 \<、渐弱 \>、结束 \!——LilyPond 里是跨音符的起止事件，不在力度记号表里。
function isHairpinWord(word) {
  return word === '\\<' || word === '\\>' || word === '\\!';
}

// 🟡 docs §2 — jianpu-ly 支持、管线未产出：整词即可识别的部分，归 info 级。
// 力度记号不在这张表里：它合不合法还取决于写在哪，由主循环里的力度分支单独判断。
const UNPRODUCED_WORD_RE = [
  /^~$/,
  /^(Fine|DC|DS|Segno|ToCoda)$/,
  /^R\*\d+$/,
  /^\d+\/{3,}$/,
  /^letter[A-Za-z0-9]+$/,
  /^instrument=.*$/,
  /^,[0-7#b',]+$/,
  /^(KeepLength|angka|OnePage|NoBarNums|NoIndent|RaggedLast|NextScore|Unicode|PartMidi|WithStaff|SeparateTimesig)$/,
];
// 需要“开括号词…闭括号词”整段透传的 🟡 语法（前/后倚音、连音组、反复跳跃）。
const BRACKET_ANY_RE = /[[{]/;                      // 词内含 [ 或 {
const BRACKET_CLOSE_RE = /[\]}]/;                   // 词内含 ] 或 }（自闭合词，如 g[#45]）
const BRACKET_OPEN_SUFFIX_RE = /[[{]$/;             // 词尾是 [ 或 { （如 R4{、3[、g[）
const BRACKET_CLOSE_SUFFIX_RE = /[\]}]$/;           // 词尾是 ] 或 }（如 ]g、}）

// ⚪ docs §3 — 明确排除，必须报错（不得静默透传）。
const EXCLUDED_WORD_RE = [/^LP:$/, /^:LP$/, /^chords=.*$/, /^frets=.*$/, /^ChordsRoman$/];

function isHeaderLine(word) {
  return HEADER_LINE_RE.test(word) || TIMESIG_RE.test(word);
}

function isUnproducedWord(word) {
  return UNPRODUCED_WORD_RE.some((re) => re.test(word));
}

function isExcludedWord(word) {
  return EXCLUDED_WORD_RE.some((re) => re.test(word));
}

// title=/composer= 的值允许含空格（如 "title=Scarborough Fair-Flauta"），
// 必须整行当一个 token，不能按空白继续拆分。
const WHOLE_LINE_VALUE_RE = /^(title=|composer=)/;

/** Split *text* into words with absolute offset + 1-based line/col, honouring
 * `%` full-line comments (docs: "jianpu-ly.py 将这些行视为注释并忽略"),
 * `L:`/`H:` lyric lines (rest of line is opaque syllable text, not tokens),
 * and `title=`/`composer=` (value may itself contain spaces — whole line is
 * one token, not whitespace-split). */
function tokenize(text) {
  const words = [];
  const lines = text.split('\n');
  let offset = 0;
  for (let li = 0; li < lines.length; li++) {
    // 保留原始行长度（含末尾 \r，若有）用于 offset 累加，避免 CRLF 文件里
    // 后续所有行的绝对偏移量漂移；仅在做词法匹配时用去掉 \r 的副本。
    const rawLine = lines[li];
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    const lineNo = li + 1;
    const trimmedStart = line.match(/^\s*/)[0].length;
    const trimmed = line.slice(trimmedStart);
    const firstWordMatch = trimmed.match(/^\S+/);
    const firstWord = firstWordMatch ? firstWordMatch[0] : '';
    if (firstWord.startsWith('%')) {
      // 整行注释 — 跳过（不产生 word）。
      offset += rawLine.length + 1;
      continue;
    }
    if (WHOLE_LINE_VALUE_RE.test(trimmed) && trimmed) {
      words.push({
        text: trimmed, offset: offset + trimmedStart, line: lineNo,
        col: trimmedStart + 1, endCol: trimmedStart + 1 + trimmed.length,
      });
      offset += rawLine.length + 1;
      continue;
    }
    const isLyricLine = firstWord === 'L:' || firstWord === 'H:';
    const re = /\S+/g;
    let m;
    let seenFirst = false;
    while ((m = re.exec(line))) {
      const w = m[0];
      const col = m.index + 1;
      if (isLyricLine && seenFirst) {
        // L:/H: 行首词之后整行视为歌词音节文本，不逐词校验。
        break;
      }
      words.push({ text: w, offset: offset + m.index, line: lineNo, col, endCol: col + w.length });
      seenFirst = true;
    }
    if (isLyricLine && words.length) {
      // 记录一个跨越整行剩余部分的 info 段（供调用方按需展示，主循环里处理）。
      words[words.length - 1].lyricLineRestEnd = offset + line.length;
    }
    offset += line.length + 1;
  }
  return words;
}

/**
 * Lint jianpu-ly body text. Returns `{ diagnostics }`; each diagnostic is
 * `{ severity: 'error'|'warning'|'info', code, start, end, line, col,
 *   messageKey, params }` — `messageKey`/`params` are resolved to human text
 * by the caller via webui/i18n.py's `t()`, keeping this module i18n-agnostic.
 */
function lintJianpuText(text) {
  const diagnostics = [];
  if (!text) return { diagnostics };

  const words = tokenize(text);
  let timesigUnits = 64;          // 默认 4/4（docs Appendix A：4/4 = 64 单位）
  let pendingAnacrusisUnits = null; // 仅对紧随其后的下一小节生效
  let measureUnits = 0;
  let measureHasNote = false;
  let measureStartWord = null;
  let bracketDepth = 0;
  let bracketStartWord = null;

  // 力度与渐强渐弱，规则与 core/notation/jianpu/parser.py 相同，每一条都是把写法
  // 拿真实 jianpu-ly + LilyPond 渲染、逐像素对比得出的，不是看警告文字推断的：
  // 记号挂在前一个音符上；小节里前面没有音符时（小节开头、全曲开头）它挂到**下一个**
  // 音符上，跨小节线也一样——渲染与写在那个音符后面完全相同。LilyPond 对这种写法
  // 会报"缺少附属对象"，但并没有丢任何东西（早先一版正是被这条警告误导，把它当成
  // 丢弃来报错）。真正丢掉的只有：分段/全曲末尾后面再没有音符的记号、同一个音上
  // 后写的那个同类记号、以及没收尾的渐强渐弱（连同它起点那个音上的力度记号）。
  let noteMarks = null;       // 最近一个音符已挂的记号 { dynamic, start, end, index }
  let noteCount = 0;          // 已出现的音符个数（含休止、延音线），用来给渐强起点排先后
  let waitingMarks = [];      // 前面没有音符、正等着下一个音符的记号
  let openHairpin = null;     // 尚未收尾的渐强/渐弱 { word, index }

  function pushAt(w, severity, code, messageKey, params) {
    diagnostics.push({
      severity, code,
      start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
      messageKey, params: params || {},
    });
  }

  // 把记号挂到 marks 代表的那个音符上。归 warning 而不是 error：B5 不阻断原则只让
  // **非法 token** 在导出时硬拦截，这里的记号本身合法、导出也照常成功，性质和
  // "小节拍数不符"一样。
  function attachMark(w, marks) {
    const word = w.text;
    if (word === '\\!') {
      // 重复的 \! 实测不改变任何渲染，所以措辞只说"没有作用"，不说"会丢"。
      if (marks.end) { pushAt(w, 'warning', 'hairpin-end-twice', 'w.ed.lint.hairpin_end_twice_at', { token: word }); return; }
      marks.end = true;
      // \! 只收尾更早的音符上开始的渐强：同一个音上既开始又 \!，实测照样"缺少结尾"。
      if (openHairpin && openHairpin.index < marks.index) openHairpin = null;
    } else if (word === '\\<' || word === '\\>') {
      // 两个开头：渲染与只写第一个完全相同，后写的那个被丢掉。
      if (marks.start) { pushAt(w, 'warning', 'hairpin-twice', 'w.ed.lint.hairpin_twice_at', { token: word }); return; }
      marks.start = true;
      openHairpin = { word: w, index: marks.index };   // 更早开始的那个就此收尾（换方向合法）
    } else {
      // 两个力度：渲染与只写第一个完全相同，后写的那个被丢掉。
      if (marks.dynamic) { pushAt(w, 'warning', 'dynamic-twice', 'w.ed.lint.dynamic_twice_at', { token: word }); return; }
      marks.dynamic = true;
      // 后面音符上的力度记号同样能给渐强收尾（实测跨小节线也行）。
      if (openHairpin && openHairpin.index < marks.index) openHairpin = null;
    }
    pushAt(w, 'info', 'unproduced-syntax', 'w.ed.lint.unproduced');
  }

  // 一个分段（或全文）结束：还在等音符的记号已经没有音符可挂——这是真的会丢的
  // 情况（实测渲染与不写它相同，LilyPond 还报程序错误）；没收尾的渐强也到此为止。
  function endOfPart() {
    for (const w of waitingMarks) {
      pushAt(w, 'warning', 'mark-no-note', 'w.ed.lint.mark_no_note_at', { token: w.text });
    }
    waitingMarks = [];
    if (openHairpin) {
      pushAt(openHairpin.word, 'warning', 'hairpin-unterminated', 'w.ed.lint.hairpin_unterminated_at',
        { token: openHairpin.word.text });
      openHairpin = null;
    }
  }

  function flushMeasure(endWord) {
    if (measureStartWord === null) return;
    if (measureHasNote) {
      const expected = pendingAnacrusisUnits !== null ? pendingAnacrusisUnits : timesigUnits;
      if (measureUnits !== expected) {
        diagnostics.push({
          severity: 'warning',
          code: 'measure-mismatch',
          start: measureStartWord.offset,
          end: endWord.offset + endWord.text.length,
          line: measureStartWord.line,
          col: measureStartWord.col,
          messageKey: 'w.ed.lint.measure_mismatch',
          params: { got: (measureUnits / 16).toString(), expected: (expected / 16).toString() },
        });
      }
    }
    pendingAnacrusisUnits = null;
    measureUnits = 0;
    measureHasNote = false;
    measureStartWord = null;
  }

  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const word = w.text;

    if (bracketDepth > 0) {
      if (BRACKET_CLOSE_SUFFIX_RE.test(word)) {
        bracketDepth--;
        if (bracketDepth === 0 && bracketStartWord) {
          diagnostics.push({
            severity: 'info',
            code: 'unproduced-syntax',
            start: bracketStartWord.offset,
            end: w.offset + w.text.length,
            line: bracketStartWord.line,
            col: bracketStartWord.col,
            messageKey: 'w.ed.lint.unproduced',
            params: {},
          });
          bracketStartWord = null;
        }
      }
      continue;
    }

    if (word === '|') {
      flushMeasure(w);
      measureStartWord = words[i + 1] || null;
      continue;
    }
    if (word === 'NextPart') {
      flushMeasure(w);
      endOfPart();
      timesigUnits = 64;
      pendingAnacrusisUnits = null;
      measureStartWord = words[i + 1] || null;
      continue;
    }
    if (word === 'L:' || word === 'H:') {
      diagnostics.push({
        severity: 'info', code: 'unproduced-syntax',
        start: w.offset, end: w.lyricLineRestEnd || (w.offset + w.text.length),
        line: w.line, col: w.col,
        messageKey: 'w.ed.lint.unproduced', params: {},
      });
      continue;
    }
    if (isHeaderLine(word)) {
      const tm = TIMESIG_RE.exec(word);
      if (tm) {
        endOfPart();   // 拍号行开启新分段，同 parser.py（开头或紧跟 NextPart 时是空操作）
        timesigUnits = 64 * Number(tm[1]) / Number(tm[2]);
        pendingAnacrusisUnits = tm[3] ? (ANACRUSIS_UNITS[tm[3]] ?? null) : null;
      }
      continue;
    }
    // 到这里才是真正的“小节内容”词（音符/延音/未知 token/🟡🔴 语法），measureStartWord
    // 只应锚定在这类词上——放在更前面会把表头/NextPart/歌词行也算进小节范围
    // （曾是真实 bug：4/4 表头行被连带标成 measure-warn 背景）。
    if (measureStartWord === null) measureStartWord = w;
    if (isExcludedWord(word)) {
      diagnostics.push({
        severity: 'error', code: 'excluded-token',
        start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
        messageKey: 'w.ed.lint.excluded', params: { token: word },
      });
      continue;
    }
    if (BRACKET_ANY_RE.test(word) && BRACKET_CLOSE_RE.test(word)) {
      // 自闭合括号词（如前倚音 g[#45]）—— 单词内已开合，无需跨词透传。
      diagnostics.push({
        severity: 'info', code: 'unproduced-syntax',
        start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
        messageKey: 'w.ed.lint.unproduced', params: {},
      });
      continue;
    }
    if (BRACKET_OPEN_SUFFIX_RE.test(word)) {
      bracketDepth = 1;
      bracketStartWord = w;
      continue;
    }
    if (isDynamicWord(word) || isHairpinWord(word)) {
      if (measureHasNote) attachMark(w, noteMarks);
      else waitingMarks.push(w);        // 前面没有音符：挂到下一个音符上
      continue;
    }
    if (isUnproducedWord(word)) {
      diagnostics.push({
        severity: 'info', code: 'unproduced-syntax',
        start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
        messageKey: 'w.ed.lint.unproduced', params: {},
      });
      continue;
    }

    const noteM = NOTE_RE.exec(word);
    const dashM = !noteM ? DASH_RE.exec(word) : null;
    if (noteM || dashM) {
      const prefix = (noteM || dashM)[1];
      const dotted = (noteM ? noteM[5] : dashM[3]) === '.';
      const base = PREFIX_UNITS[prefix] ?? 16;
      measureUnits += dotted ? base * 1.5 : base;
      measureHasNote = true;
      noteCount += 1;
      noteMarks = { dynamic: false, start: false, end: false, index: noteCount };
      for (const waiting of waitingMarks) attachMark(waiting, noteMarks);
      waitingMarks = [];
      continue;
    }

    diagnostics.push({
      severity: 'error', code: 'bad-token',
      start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
      messageKey: 'w.ed.lint.bad_token', params: { token: word },
    });
  }
  if (words.length) flushMeasure(words[words.length - 1]);
  endOfPart();

  return { diagnostics };
}

export { lintJianpuText, tokenize, isHeaderLine };
