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

// 🟡 docs §2 — jianpu-ly 支持、管线未产出：整词即可识别的部分，归 info 级。
const UNPRODUCED_WORD_RE = [
  /^~$/,
  /^(Fine|DC|DS|Segno|ToCoda)$/,
  /^\\(p|pp|ppp|mp|mf|f|ff|fff|sf|sfz)$/,
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
      continue;
    }

    diagnostics.push({
      severity: 'error', code: 'bad-token',
      start: w.offset, end: w.offset + w.text.length, line: w.line, col: w.col,
      messageKey: 'w.ed.lint.bad_token', params: { token: word },
    });
  }
  if (words.length) flushMeasure(words[words.length - 1]);

  return { diagnostics };
}

export { lintJianpuText, tokenize, isHeaderLine };
