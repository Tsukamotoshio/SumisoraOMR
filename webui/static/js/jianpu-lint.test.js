// webui/static/js/jianpu-lint.test.js — unit tests for the pure jianpu-ly
// body-text linter (Stage 1, see 修复计划2与简谱编辑器规划.md B6).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { lintJianpuText } from './jianpu-lint.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN_DIR = path.join(__dirname, '..', '..', '..', 'tests', 'fixtures', 'golden');

function errorsOf(diags) { return diags.filter((d) => d.severity === 'error'); }
function warningsOf(diags) { return diags.filter((d) => d.severity === 'warning'); }
function infosOf(diags) { return diags.filter((d) => d.severity === 'info'); }

test('empty text produces no diagnostics', () => {
  assert.deepEqual(lintJianpuText('').diagnostics, []);
});

test('a well-formed 4/4 measure produces no diagnostics', () => {
  const { diagnostics } = lintJianpuText('% jianpu-ly.py\ntitle=T\n1=C\n4/4\n\n1 2 3 4 |\n');
  assert.deepEqual(diagnostics, []);
});

test('accepts every 🟢 note-token shape from docs/jianpu-ly-syntax.md §1.1', () => {
  const body = "1=C\n4/4\n\n1 0 b2 1' 4'' 1, q2'. s3' d1' 6,. |\n";
  const { diagnostics } = lintJianpuText(body);
  assert.deepEqual(errorsOf(diagnostics), []);
});

test('accepts continuation dashes (§1.2) and does not miscount duration', () => {
  // base(16) + dash(16) + dash(16) + dash(16) = 64 = one full 4/4 bar.
  const { diagnostics } = lintJianpuText('4/4\n\n1 - - - |\n');
  assert.deepEqual(diagnostics, []);
});

test('flags an unrecognized token as a 🔴 error with correct position', () => {
  const { diagnostics } = lintJianpuText("4/4\n\n1 2 qi' 4 |\n");
  const errors = errorsOf(diagnostics);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'bad-token');
  assert.equal(errors[0].line, 3);
  assert.equal(errors[0].params.token, "qi'");
});

test('flags historical OCR-garbage tokens qo / ?, as errors', () => {
  const { diagnostics } = lintJianpuText('4/4\n\nqo 1 |\n?, 2 |\n');
  const errors = errorsOf(diagnostics);
  assert.equal(errors.length, 2);
  assert.deepEqual(errors.map((e) => e.params.token), ['qo', '?,']);
});

test('a measure-mismatch span never reaches back into the preceding header line', () => {
  // Regression: measureStartWord used to be set on the very first word seen
  // (the "4/4" header token itself) before the header-line check had a
  // chance to skip it, so the mismatch span for the first measure started
  // at the header line instead of at "1" — the highlighter then painted the
  // header line's background as if it were part of the offending measure.
  const body = "4/4\n\n1 2 qi' 4 |\n";
  const { diagnostics } = lintJianpuText(body);
  const warning = warningsOf(diagnostics)[0];
  assert.ok(warning, 'expected a measure-mismatch warning');
  assert.equal(warning.line, 3, 'the warning should start on the note line, not the "4/4" header line');
  assert.equal(body.slice(warning.start, warning.start + 1), '1');
});

test('flags an underfull measure as a 🟡 warning, not an error', () => {
  const { diagnostics } = lintJianpuText('4/4\n\n1 2 |\n'); // 2 quarters, bar wants 4
  assert.deepEqual(errorsOf(diagnostics), []);
  const warnings = warningsOf(diagnostics);
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].code, 'measure-mismatch');
  assert.equal(warnings[0].params.got, '2');
  assert.equal(warnings[0].params.expected, '4');
});

test('flags an overfull measure as a 🟡 warning', () => {
  const { diagnostics } = lintJianpuText('4/4\n\n1 2 3 4 5 |\n'); // 5 quarters
  const warnings = warningsOf(diagnostics);
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].params.got, '5');
});

test('does not warn on an empty measure (empty != underfull, per B5)', () => {
  const { diagnostics } = lintJianpuText('4/4\n\n| 1 2 3 4 |\n');
  assert.deepEqual(warningsOf(diagnostics), []);
});

test('honours a pickup (anacrusis) time signature for only the first measure', () => {
  // 3/4,8 => first bar expects an eighth note (8 units), later bars expect a full 3/4 bar (48 units).
  const { diagnostics } = lintJianpuText('3/4,8\n\nq1 | 1 2 3 |\n');
  assert.deepEqual(warningsOf(diagnostics), []);
});

test('a full bar after the anacrusis is still checked against the pickup, not the full meter', () => {
  const { diagnostics } = lintJianpuText('3/4,8\n\n1 2 3 |\n'); // first bar should be 8 units (eighth), not 48
  const warnings = warningsOf(diagnostics);
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].params.expected, '0.5');
});

test('NextPart resets the time signature and does not leak the previous voice\'s meter', () => {
  const body = '4/4\n\n1 2 3 4 |\nNextPart\n2/4\n\n1 2 |\n';
  assert.deepEqual(lintJianpuText(body).diagnostics, []);
});

test('classifies known 🟡-tier (jianpu-ly-supports-but-unproduced) tokens as info, not error', () => {
  const cases = ['~', 'Fine', 'DC', 'Segno', '\\p', 'R*8', 'letterA', 'instrument=Flute'];
  for (const tok of cases) {
    const { diagnostics } = lintJianpuText(`4/4\n\n1 ${tok} 2 |\n`);
    assert.deepEqual(errorsOf(diagnostics), [], `expected no error for ${tok}`);
    assert.equal(infosOf(diagnostics).length, 1, `expected one info diagnostic for ${tok}`);
  }
});

// The bundled LilyPond 2.24.4's AbsoluteDynamicEvent list. The linter used to
// know only 10 of these, and the other 12 fell through to bad-token: underlined
// as errors in the text view while the parser accepted them and they rendered.
const ALL_DYNAMIC_MARKS = [
  'ppppp', 'pppp', 'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff', 'ffff', 'fffff',
  'fp', 'sf', 'sfp', 'sff', 'sfz', 'fz', 'sp', 'spp', 'rfz', 'n',
];

test('every one of LilyPond\'s 22 dynamic marks is info, none is an error', () => {
  for (const mark of ALL_DYNAMIC_MARKS) {
    const { diagnostics } = lintJianpuText(`4/4\n\n1 \\${mark} 2 3 4 |\n`);
    assert.deepEqual(errorsOf(diagnostics), [], `\\${mark} should not be an error`);
    assert.deepEqual(warningsOf(diagnostics), [], 'a full measure: nothing else to report');
    assert.equal(infosOf(diagnostics).length, 1, `\\${mark} should be one info diagnostic`);
  }
});

test('a backslash word that is not a LilyPond dynamic is still an error', () => {
  // \PP: dynamics are case-sensitive, LilyPond rejects it. \foo: not a command.
  for (const tok of ['\\PP', '\\Mf', '\\foo', '\\pppppp']) {
    const { diagnostics } = lintJianpuText(`4/4\n\n1 ${tok} 2 3 4 |\n`);
    const errors = errorsOf(diagnostics);
    assert.equal(errors.length, 1, `expected exactly one error for ${tok}`);
    assert.equal(errors[0].code, 'bad-token');
  }
});

// ── dynamics and hairpins (stage 6.1a) ───────────────────────────────────────
// Same placements as tests/test_jianpu_dynamics.py's parser tests. Every rule was
// set by rendering the placement through the real jianpu-ly + LilyPond and
// comparing pages pixel for pixel, not by reading LilyPond's warnings. A mark
// with no note before it in its measure attaches to the NEXT note -- the page is
// identical to writing it after that note -- so it is fine. An earlier version of
// this linter took LilyPond's "缺少附属对象" warning for a loss and flagged it.
// The ones that really lose something are warnings, not errors: the marks are
// legal and export still succeeds, and B5 lets only illegal tokens block export.

function markDiags(body) {
  const { diagnostics } = lintJianpuText(body);
  return {
    errors: errorsOf(diagnostics),
    warnings: warningsOf(diagnostics),
    infos: infosOf(diagnostics),
  };
}
const codesOf = (d) => d.warnings.map((w) => w.code);
const textOf = (body, diag) => body.slice(diag.start, diag.end);

test('a dynamic first in a measure attaches to the next note and is fine', () => {
  const d = markDiags('4/4\n\n1 2 3 4 | \\p 5 6 7 1 |\n');
  assert.deepEqual(d.errors, []);
  assert.deepEqual(d.warnings, [], 'renders exactly like "5 \\p": nothing to warn about');
  assert.equal(d.infos.length, 1);
});

test('a dynamic first in the piece attaches to the first note and is fine', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n\\p 1 2 3 4 |\n')), []);
});

test('a mark in an otherwise empty measure waits for the next measure', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 2 3 4 | \\p | 5 6 7 1 |\n')), []);
});

test('a mark after the last note of the piece is dropped', () => {
  const body = '4/4\n\n1 2 3 4 | 5 6 7 1 | \\p\n';
  const d = markDiags(body);
  assert.deepEqual(codesOf(d), ['mark-no-note']);
  assert.equal(textOf(body, d.warnings[0]), '\\p');
  assert.deepEqual(d.errors, [], 'a warning: export is not blocked');
});

test('a mark after the last note of a section is dropped', () => {
  // Both boundaries end a section: NextPart, and a time-signature line (which
  // is what normally follows NextPart, but also starts a section on its own).
  for (const body of [
    '4/4\n\n1 2 3 4 | \\f\nNextPart\n4/4\n5 6 7 1 |\n',
    '4/4\n\n1 2 3 4 | \\f\nNextPart\n5 6 7 1 |\n',
    '4/4\n\n1 2 3 4 | \\f\n3/4\n5 6 7 |\n',
  ]) {
    assert.deepEqual(codesOf(markDiags(body)), ['mark-no-note'], body);
  }
});

test('a second dynamic on the same note warns on the second one only', () => {
  const body = '4/4\n\n1 \\p \\f 2 3 4 |\n';
  const d = markDiags(body);
  assert.deepEqual(codesOf(d), ['dynamic-twice']);
  assert.equal(textOf(body, d.warnings[0]), '\\f', 'the page matches the first alone');
  assert.equal(d.infos.length, 1);
});

test('a waiting dynamic and one written after that same note clash', () => {
  const body = '4/4\n\n1 2 3 4 | \\p 5 \\f 6 7 1 |\n';
  const d = markDiags(body);
  assert.deepEqual(codesOf(d), ['dynamic-twice']);
  assert.equal(textOf(body, d.warnings[0]), '\\f');
});

test('dynamics on different notes are both fine', () => {
  const d = markDiags('4/4\n\n1 \\p 2 \\f 3 4 |\n');
  assert.deepEqual(d.warnings, []);
  assert.equal(d.infos.length, 2);
});

test('a rest or a continuation dash counts as the note a mark attaches to', () => {
  for (const body of ['4/4\n\n0 \\mf 2 3 4 |\n', '4/4\n\n1 - \\ff 3 4 |\n']) {
    const d = markDiags(body);
    assert.deepEqual(d.warnings, [], body);
    assert.deepEqual(d.errors, [], body);
  }
});

test('valid hairpins are info, no longer the bad-token error they used to be', () => {
  const d = markDiags('4/4\n\n1 \\< 2 3 4 \\! |\n');
  assert.deepEqual(d.errors, []);
  assert.deepEqual(d.warnings, []);
  assert.equal(d.infos.length, 2);
});

test('a hairpin closed across a barline is fine', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\> 2 3 4 | 5 6 7 1 \\! |\n')), []);
});

test('a dynamic on a later note closes a hairpin', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\< 2 3 4 | 5 6 7 1 \\f |\n')), []);
});

test('a new start on a later note closes the earlier one', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\< 2 3 \\> 4 | 5 6 7 1 \\! |\n')), []);
});

test('an end first in a measure waits for the next note and still closes', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\< 2 3 4 | \\! 5 6 7 1 |\n')), []);
});

test('an unfinished hairpin warns, pointing at where it starts', () => {
  const body = '4/4\n\n1 2 \\< 3 4 | 5 6 7 1 |\n';
  const d = markDiags(body);
  assert.deepEqual(codesOf(d), ['hairpin-unterminated']);
  assert.equal(textOf(body, d.warnings[0]), '\\<');
});

test('an end on the same note as the start does not close it', () => {
  // Rendered: "crescendo 缺少结尾" and no wedge, in either order.
  for (const body of ['4/4\n\n1 \\< \\! 2 3 4 |\n', '4/4\n\n1 \\! \\< 2 3 4 |\n']) {
    assert.deepEqual(codesOf(markDiags(body)), ['hairpin-unterminated'], body);
  }
});

test('a dynamic on the start note does not close it either', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\< \\p 2 3 4 |\n')), ['hairpin-unterminated']);
});

test('an unfinished hairpin ends with its section', () => {
  // The \! in the next section has nothing of its own to close: no second warning.
  for (const body of [
    '4/4\n\n1 \\< 2 3 4 |\nNextPart\n4/4\n5 6 7 1 \\! |\n',
    '4/4\n\n1 \\< 2 3 4 |\nNextPart\n5 6 7 1 \\! |\n',
    '4/4\n\n1 \\< 2 3 4 |\n3/4\n5 6 7 \\! |\n',
  ]) {
    assert.deepEqual(codesOf(markDiags(body)), ['hairpin-unterminated'], body);
  }
});

test('two starts on one note warn on the later one', () => {
  const body = '4/4\n\n1 \\< \\> 2 3 4 \\! |\n';
  const d = markDiags(body);
  assert.deepEqual(codesOf(d), ['hairpin-twice']);
  assert.equal(textOf(body, d.warnings[0]), '\\>');
});

test('a repeated end warns', () => {
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 \\< 2 3 4 \\! \\! |\n')), ['hairpin-end-twice']);
});

test('a stray end with nothing running is fine', () => {
  // Rendered: LilyPond ignores it without a word.
  assert.deepEqual(codesOf(markDiags('4/4\n\n1 2 3 4 \\! |\n')), []);
});

test('treats a bracketed grace-note/tuplet/repeat region as one opaque info span, not per-word errors', () => {
  const { diagnostics } = lintJianpuText("4/4\n\n1 g[#45] 1 |\n3[ q1 q1 q1 ] |\nR4{ 1 2 } |\n");
  assert.deepEqual(errorsOf(diagnostics), []);
});

test('treats L:/H: lyric lines as opaque, not note tokens', () => {
  const { diagnostics } = lintJianpuText('4/4\n\n1 2 3 4 |\nL: syl- la- ble here\n');
  assert.deepEqual(errorsOf(diagnostics), []);
});

test('flags the explicitly-excluded LP: raw-LilyPond block as an error (docs §3)', () => {
  const { diagnostics } = lintJianpuText('4/4\n\nLP:\nsome raw code\n:LP\n1 2 3 4 |\n');
  const errors = errorsOf(diagnostics);
  assert.ok(errors.some((e) => e.code === 'excluded-token' && e.params.token === 'LP:'));
});

test('flags chords=/frets=/ChordsRoman as errors (docs §3, guitar tab out of scope)', () => {
  for (const tok of ['chords=C', 'frets=x32010', 'ChordsRoman']) {
    const { diagnostics } = lintJianpuText(`4/4\n\n${tok}\n1 2 3 4 |\n`);
    assert.ok(errorsOf(diagnostics).some((e) => e.code === 'excluded-token'), `expected error for ${tok}`);
  }
});

test('48-file acceptance criterion (subset): zero false-positive errors or measure warnings on real pipeline-generated jianpu-ly text', () => {
  const files = fs.readdirSync(GOLDEN_DIR).filter((f) => f.endsWith('.jly.txt'));
  assert.ok(files.length >= 10, 'expected the golden fixture set to be present');
  for (const f of files) {
    const body = fs.readFileSync(path.join(GOLDEN_DIR, f), 'utf-8');
    const { diagnostics } = lintJianpuText(body);
    assert.deepEqual(errorsOf(diagnostics), [], `unexpected error(s) in ${f}`);
    assert.deepEqual(warningsOf(diagnostics), [], `unexpected measure warning(s) in ${f}`);
  }
});
