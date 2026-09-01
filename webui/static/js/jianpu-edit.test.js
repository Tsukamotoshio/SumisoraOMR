// webui/static/js/jianpu-edit.test.js — unit tests for the 阶段5.1 edit model
// and undo/redo command stack. B7 risk 6 calls for exactly this: design the
// command stack independently of rendering, with command-pattern unit coverage.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EditHistory, applyCommand, barQuarterLength, blankMeasureNotes,
  extractFragment, getNote, insertConsumingCommands, nextRef, noteRef,
  orderBatch, pasteMeasuresCommands, pasteNotesCommands, prevRef, refEquals,
  selectionSpan, steppedDuration,
} from './jianpu-edit.js';

function note(symbol, extra = {}) {
  return {
    symbol,
    accidental: '',
    upper_dots: 0,
    lower_dots: 0,
    duration: 1.0,
    duration_dots: 0,
    midi: symbol === '0' ? null : 60,
    is_rest: symbol === '0',
    ...extra,
  };
}

/** Two measures in section 0, one measure in section 1. */
function makeDoc() {
  return {
    title: 'T',
    composer: '',
    key_header: '1=C',
    tempo: 120,
    sections: [
      { time_sig: '4/4', measures: [[note('1'), note('2')], [note('3'), note('0'), note('5')]] },
      { time_sig: '4/4', measures: [[note('6'), note('7')]] },
    ],
  };
}

const clone = (o) => JSON.parse(JSON.stringify(o));

// ── addressing ───────────────────────────────────────────────────────────────

test('getNote resolves a valid address and returns null out of bounds', () => {
  const doc = makeDoc();
  assert.equal(getNote(doc, noteRef(0, 1, 2)).symbol, '5');
  assert.equal(getNote(doc, noteRef(9, 0, 0)), null, 'section out of range');
  assert.equal(getNote(doc, noteRef(0, 9, 0)), null, 'measure out of range');
  assert.equal(getNote(doc, noteRef(0, 0, 9)), null, 'index out of range');
  assert.equal(getNote(doc, null), null);
});

test('refEquals compares all three coordinates', () => {
  assert.ok(refEquals(noteRef(1, 2, 3), noteRef(1, 2, 3)));
  assert.ok(!refEquals(noteRef(1, 2, 3), noteRef(1, 2, 4)));
  assert.ok(!refEquals(noteRef(1, 2, 3), null));
  assert.ok(refEquals(null, null));
});

test('nextRef walks within a measure, across measures, and across sections', () => {
  const doc = makeDoc();
  assert.deepEqual(nextRef(doc, noteRef(0, 0, 0)), noteRef(0, 0, 1), 'within a measure');
  assert.deepEqual(nextRef(doc, noteRef(0, 0, 1)), noteRef(0, 1, 0), 'into the next measure');
  assert.deepEqual(nextRef(doc, noteRef(0, 1, 2)), noteRef(1, 0, 0), 'into the next section');
});

test('prevRef walks back across measures and sections', () => {
  const doc = makeDoc();
  assert.deepEqual(prevRef(doc, noteRef(0, 0, 1)), noteRef(0, 0, 0), 'within a measure');
  assert.deepEqual(prevRef(doc, noteRef(0, 1, 0)), noteRef(0, 0, 1), 'back a measure');
  assert.deepEqual(prevRef(doc, noteRef(1, 0, 0)), noteRef(0, 1, 2), 'back a section');
});

test('nextRef/prevRef return null at the very end/start of the score', () => {
  const doc = makeDoc();
  assert.equal(nextRef(doc, noteRef(1, 0, 1)), null, 'past the last note');
  assert.equal(prevRef(doc, noteRef(0, 0, 0)), null, 'before the first note');
});

test('nextRef skips over empty measures rather than stopping in them', () => {
  const doc = makeDoc();
  doc.sections[0].measures.splice(1, 0, []);   // an empty measure in the middle
  assert.deepEqual(nextRef(doc, noteRef(0, 0, 1)), noteRef(0, 2, 0));
});

// ── each command: do -> undo restores the document exactly ───────────────────

const CASES = [
  ['set_pitch', { type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' }],
  ['set_pitch onto a rest', { type: 'set_pitch', ref: noteRef(0, 1, 1), symbol: '4' }],
  ['set_rest', { type: 'set_rest', ref: noteRef(0, 0, 0) }],
  ['set_accidental', { type: 'set_accidental', ref: noteRef(0, 0, 0), accidental: '#' }],
  ['set_octave up', { type: 'set_octave', ref: noteRef(0, 0, 0), delta: 1 }],
  ['set_octave down', { type: 'set_octave', ref: noteRef(0, 0, 0), delta: -2 }],
  ['set_duration', { type: 'set_duration', ref: noteRef(0, 0, 0), duration: 0.5 }],
  ['toggle_dot on', { type: 'toggle_dot', ref: noteRef(0, 0, 0) }],
  ['delete_note', { type: 'delete_note', ref: noteRef(0, 0, 0) }],
  ['delete_note (last in measure)', { type: 'delete_note', ref: noteRef(0, 1, 2) }],
  ['insert_note', { type: 'insert_note', ref: noteRef(0, 0, 1), note: note('7') }],
  ['insert_note at measure end', { type: 'insert_note', ref: noteRef(0, 0, 2), note: note('7') }],
];

for (const [name, cmd] of CASES) {
  test(`${name}: do then undo restores the document exactly`, () => {
    const doc = makeDoc();
    const before = clone(doc);
    const inverse = applyCommand(doc, cmd);
    assert.ok(inverse, 'command applied');
    assert.notDeepEqual(doc, before, 'the command actually changed something');
    applyCommand(doc, inverse);
    assert.deepEqual(doc, before, 'undo restored the document');
  });
}

// ── command semantics ────────────────────────────────────────────────────────

test('set_pitch to 0 turns the note into a rest and clears accidental/octave', () => {
  const doc = makeDoc();
  applyCommand(doc, { type: 'set_accidental', ref: noteRef(0, 0, 0), accidental: '#' });
  applyCommand(doc, { type: 'set_octave', ref: noteRef(0, 0, 0), delta: 2 });
  applyCommand(doc, { type: 'set_rest', ref: noteRef(0, 0, 0) });
  const n = getNote(doc, noteRef(0, 0, 0));
  assert.equal(n.symbol, '0');
  assert.equal(n.is_rest, true);
  assert.equal(n.accidental, '', 'a rest carries no accidental');
  assert.equal(n.upper_dots, 0, 'a rest carries no octave dots');
});

test('set_pitch off a rest clears the rest flag', () => {
  const doc = makeDoc();
  const ref = noteRef(0, 1, 1);
  assert.equal(getNote(doc, ref).is_rest, true);
  applyCommand(doc, { type: 'set_pitch', ref, symbol: '4' });
  assert.equal(getNote(doc, ref).is_rest, false);
  assert.equal(getNote(doc, ref).symbol, '4');
});

test('set_octave moves through zero rather than stacking dots on both sides', () => {
  const doc = makeDoc();
  const ref = noteRef(0, 0, 0);
  applyCommand(doc, { type: 'set_octave', ref, delta: -1 });
  assert.deepEqual(
    [getNote(doc, ref).upper_dots, getNote(doc, ref).lower_dots], [0, 1]);
  applyCommand(doc, { type: 'set_octave', ref, delta: 2 });
  assert.deepEqual(
    [getNote(doc, ref).upper_dots, getNote(doc, ref).lower_dots], [1, 0],
    'crossing zero leaves dots on one side only');
});

test('set_octave clamps at three dots in each direction', () => {
  const doc = makeDoc();
  const ref = noteRef(0, 0, 0);
  applyCommand(doc, { type: 'set_octave', ref, delta: 99 });
  assert.equal(getNote(doc, ref).upper_dots, 3);
  applyCommand(doc, { type: 'set_octave', ref, delta: -99 });
  assert.equal(getNote(doc, ref).lower_dots, 3);
});

test('toggle_dot changes the duration alongside the dot count', () => {
  const doc = makeDoc();
  const ref = noteRef(0, 0, 0);
  applyCommand(doc, { type: 'toggle_dot', ref });
  assert.equal(getNote(doc, ref).duration_dots, 1);
  assert.equal(getNote(doc, ref).duration, 1.5, 'a dot is 1.5x the duration');
  applyCommand(doc, { type: 'toggle_dot', ref });
  assert.equal(getNote(doc, ref).duration_dots, 0);
  assert.equal(getNote(doc, ref).duration, 1.0, 'removing the dot divides back out');
});

test('accidental and octave commands are refused on a rest', () => {
  const doc = makeDoc();
  const restRef = noteRef(0, 1, 1);
  assert.equal(applyCommand(doc, { type: 'set_accidental', ref: restRef, accidental: '#' }), null);
  assert.equal(applyCommand(doc, { type: 'set_octave', ref: restRef, delta: 1 }), null);
});

test('commands on an invalid address report failure instead of throwing', () => {
  const doc = makeDoc();
  const before = clone(doc);
  for (const cmd of [
    { type: 'set_pitch', ref: noteRef(9, 0, 0), symbol: '1' },
    { type: 'delete_note', ref: noteRef(0, 0, 9) },
    { type: 'insert_note', ref: noteRef(0, 0, 9), note: note('1') },
    { type: 'no_such_command', ref: noteRef(0, 0, 0) },
  ]) {
    assert.equal(applyCommand(doc, cmd), null, cmd.type);
  }
  assert.deepEqual(doc, before, 'a refused command leaves the document untouched');
});

test('inserting stores a copy, so later edits to the source object do not leak in', () => {
  const doc = makeDoc();
  const source = note('7');
  applyCommand(doc, { type: 'insert_note', ref: noteRef(0, 0, 0), note: source });
  source.symbol = '1';
  assert.equal(getNote(doc, noteRef(0, 0, 0)).symbol, '7');
});

// ── history ──────────────────────────────────────────────────────────────────

test('history: several edits then undo all the way back to the start', () => {
  const doc = makeDoc();
  const before = clone(doc);
  const h = new EditHistory(doc);
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' });
  h.do({ type: 'set_octave', ref: noteRef(0, 0, 1), delta: 1 });
  h.do({ type: 'delete_note', ref: noteRef(0, 1, 0) });
  assert.notDeepEqual(doc, before);
  while (h.canUndo) h.undo();
  assert.deepEqual(doc, before, 'undoing everything returns the original document');
});

test('history: redo re-applies undone edits step by step', () => {
  const doc = makeDoc();
  const h = new EditHistory(doc);
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' });
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 1), symbol: '6' });
  const after = clone(doc);
  h.undo(); h.undo();
  h.redo(); h.redo();
  assert.deepEqual(doc, after);
});

test('history: a new command invalidates the redo stack', () => {
  const doc = makeDoc();
  const h = new EditHistory(doc);
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' });
  h.undo();
  assert.ok(h.canRedo, 'redo is available right after an undo');
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 1), symbol: '4' });
  assert.ok(!h.canRedo, 'branching off discards the old future');
});

test('history: undo/redo on empty stacks are safe no-ops', () => {
  const doc = makeDoc();
  const before = clone(doc);
  const h = new EditHistory(doc);
  assert.equal(h.undo(), false);
  assert.equal(h.redo(), false);
  assert.equal(h.canUndo, false);
  assert.equal(h.canRedo, false);
  assert.deepEqual(doc, before);
});

test('history: a refused command is not pushed onto the undo stack', () => {
  const doc = makeDoc();
  const h = new EditHistory(doc);
  assert.equal(h.do({ type: 'set_pitch', ref: noteRef(9, 9, 9), symbol: '1' }), false);
  assert.equal(h.canUndo, false, 'nothing happened, so there is nothing to undo');
});

test('history: clear() drops both stacks', () => {
  const doc = makeDoc();
  const h = new EditHistory(doc);
  h.do({ type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' });
  h.undo();
  h.clear();
  assert.equal(h.canUndo, false);
  assert.equal(h.canRedo, false);
});

// ── randomised cross-check against a naive snapshot implementation ───────────

test('200 random do/undo/redo steps agree with a naive snapshot implementation', () => {
  // The inverse-command approach is the whole point of this module (it keeps
  // memory proportional to the size of an edit rather than the size of the
  // document), but it is also the part most likely to hide a subtle bug in a
  // command nobody exercised. So run it head-to-head against the dumbest
  // possible correct implementation -- full deep copies -- over a long random
  // sequence, and require the documents to stay identical throughout.
  let seed = 12345;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const pick = (arr) => arr[Math.floor(rnd() * arr.length) % arr.length];

  const doc = makeDoc();
  const h = new EditHistory(doc);

  // Reference model: snapshots only.
  let refDoc = clone(doc);
  const refUndo = [];
  const refRedo = [];

  const refs = [
    noteRef(0, 0, 0), noteRef(0, 0, 1), noteRef(0, 1, 0),
    noteRef(0, 1, 1), noteRef(0, 1, 2), noteRef(1, 0, 0), noteRef(1, 0, 1),
  ];

  for (let step = 0; step < 200; step++) {
    const roll = rnd();
    if (roll < 0.6) {
      const ref = pick(refs);
      const cmd = pick([
        { type: 'set_pitch', ref, symbol: pick(['1', '2', '3', '4', '5', '6', '7', '0']) },
        { type: 'set_accidental', ref, accidental: pick(['', '#', 'b']) },
        { type: 'set_octave', ref, delta: pick([1, -1, 2, -2]) },
        { type: 'set_duration', ref, duration: pick([0.25, 0.5, 1, 2]) },
        { type: 'toggle_dot', ref },
        { type: 'delete_note', ref },
        { type: 'insert_note', ref, note: note(pick(['1', '5'])) },
      ]);
      const snapshot = clone(refDoc);
      const ok = h.do(cmd);
      if (ok) {
        // Mirror the same command onto the reference doc via a throwaway
        // history whose document IS the reference doc.
        applyCommand(refDoc, cmd);
        refUndo.push(snapshot);
        refRedo.length = 0;
      }
    } else if (roll < 0.85) {
      const ok = h.undo();
      if (ok) {
        refRedo.push(clone(refDoc));
        refDoc = refUndo.pop();
      } else {
        assert.equal(refUndo.length, 0, 'both implementations run out of undo together');
      }
    } else {
      const ok = h.redo();
      if (ok) {
        refUndo.push(clone(refDoc));
        refDoc = refRedo.pop();
      } else {
        assert.equal(refRedo.length, 0, 'both implementations run out of redo together');
      }
    }
    assert.deepEqual(doc, refDoc, `documents diverged at step ${step}`);
  }
});

// ── steppedDuration: what +/- does to a note's value ────────────────────────

test('steppedDuration halves and doubles the note value', () => {
  const at = (duration) => ({ duration, duration_dots: 0 });
  assert.deepEqual(steppedDuration(at(1.0), +1), { duration: 2.0, duration_dots: 0 });
  assert.deepEqual(steppedDuration(at(1.0), -1), { duration: 0.5, duration_dots: 0 });
  assert.deepEqual(steppedDuration(at(0.25), -1), { duration: 0.125, duration_dots: 0 });
  assert.deepEqual(steppedDuration(at(2.0), +1), { duration: 4.0, duration_dots: 0 });
});

test('steppedDuration stops at the ends of the ladder instead of wrapping', () => {
  assert.equal(steppedDuration({ duration: 0.125, duration_dots: 0 }, -1), null);
  assert.equal(steppedDuration({ duration: 4.0, duration_dots: 0 }, +1), null);
});

test('steppedDuration keeps the dot, stepping the underlying value', () => {
  // A dotted quarter (1.5) doubled is a dotted half (3.0), not 3.0 undotted.
  assert.deepEqual(
    steppedDuration({ duration: 1.5, duration_dots: 1 }, +1),
    { duration: 3.0, duration_dots: 1 });
  assert.deepEqual(
    steppedDuration({ duration: 1.5, duration_dots: 1 }, -1),
    { duration: 0.75, duration_dots: 1 });
});

test('steppedDuration drops a dot that the notation cannot express', () => {
  // A dotted half (3.0) doubled would be a dotted whole (6.0), which is not a
  // value jianpu_note_token knows; emitting it would be silently rewritten to
  // something else, so the dot goes instead.
  assert.deepEqual(
    steppedDuration({ duration: 3.0, duration_dots: 1 }, +1),
    { duration: 4.0, duration_dots: 0 });
});

test('steppedDuration tolerates the float noise a dot introduces', () => {
  // 0.75/1.5 divide back to exact halves, but 0.375 and 0.1875 do not always
  // survive a round trip cleanly; the nearest-rung search must still land right.
  assert.deepEqual(
    steppedDuration({ duration: 0.375, duration_dots: 1 }, -1),
    { duration: 0.1875, duration_dots: 1 });
  assert.deepEqual(
    steppedDuration({ duration: 0.1875, duration_dots: 1 }, +1),
    { duration: 0.375, duration_dots: 1 });
});

test('steppedDuration handles every ladder rung round trip', () => {
  const ladder = [0.125, 0.25, 0.5, 1.0, 2.0, 4.0];
  for (let i = 0; i < ladder.length - 1; i++) {
    const up = steppedDuration({ duration: ladder[i], duration_dots: 0 }, +1);
    assert.equal(up.duration, ladder[i + 1], `up from ${ladder[i]}`);
    const back = steppedDuration(up, -1);
    assert.equal(back.duration, ladder[i], `back down to ${ladder[i]}`);
  }
});

// ── grouped commands + note entry (阶段5.4) ──────────────────────────────────

// A blank 4/4 measure is not one four-beat rest: jianpu-ly writes it `0 - - -`,
// which parses to a one-beat rest followed by three one-beat continuation
// dashes. Every note-entry test below starts from that real shape.
function blankDoc(beats = 4) {
  const measure = [note('0')];
  for (let i = 1; i < beats; i++) measure.push(note('-'));
  measure.forEach((n) => { n.midi = null; });
  measure[0].is_rest = true;
  return {
    title: 'T', composer: '', key_header: '1=C', tempo: 120,
    sections: [{ time_sig: beats + '/4', measures: [measure] }],
  };
}

const symbolsOf = (doc, m = 0) => doc.sections[0].measures[m].map((n) => n.symbol);
const beatsOf = (doc, m = 0) => doc.sections[0].measures[m]
  .reduce((sum, n) => sum + n.duration, 0);

test('doGroup applies every command and undoes them all as one step', () => {
  const doc = makeDoc();
  const history = new EditHistory(doc);
  const before = clone(doc);
  assert.ok(history.doGroup([
    { type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' },
    { type: 'set_octave', ref: noteRef(0, 0, 0), delta: 1 },
    { type: 'delete_note', ref: noteRef(0, 0, 1) },
  ]));
  assert.equal(getNote(doc, noteRef(0, 0, 0)).symbol, '5');
  assert.equal(getNote(doc, noteRef(0, 0, 0)).upper_dots, 1);
  assert.equal(doc.sections[0].measures[0].length, 1);

  // One Ctrl+Z, not three — the group is a single semantic operation (B5③).
  assert.ok(history.undo());
  assert.deepEqual(doc, before);
  assert.ok(!history.canUndo, 'three commands consumed exactly one undo slot');
  assert.ok(history.redo());
  assert.equal(getNote(doc, noteRef(0, 0, 0)).symbol, '5');
  assert.equal(doc.sections[0].measures[0].length, 1);
});

test('doGroup rolls back completely when a later command fails', () => {
  const doc = makeDoc();
  const history = new EditHistory(doc);
  const before = clone(doc);
  // Second command addresses a measure that does not exist.
  assert.ok(!history.doGroup([
    { type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '5' },
    { type: 'set_pitch', ref: noteRef(0, 9, 0), symbol: '5' },
  ]));
  assert.deepEqual(doc, before, 'half an operation must not survive');
  assert.ok(!history.canUndo, 'a failed group leaves nothing to undo');
});

test('doGroup rejects an empty command list', () => {
  const history = new EditHistory(makeDoc());
  assert.ok(!history.doGroup([]));
  assert.ok(!history.doGroup(null));
  assert.ok(!history.canUndo);
});

test('grouped and single-command history entries interleave correctly', () => {
  const doc = makeDoc();
  const history = new EditHistory(doc);
  history.do({ type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '4' });
  const afterSingle = clone(doc);
  history.doGroup([
    { type: 'set_pitch', ref: noteRef(0, 0, 1), symbol: '5' },
    { type: 'set_octave', ref: noteRef(0, 0, 1), delta: -1 },
  ]);
  assert.ok(history.undo());
  assert.deepEqual(doc, afterSingle, 'undoing the group leaves the single edit');
  assert.ok(history.undo());
  assert.equal(getNote(doc, noteRef(0, 0, 0)).symbol, '1');
});

test('entering a quarter note on a blank measure consumes the rest, not the length', () => {
  const doc = blankDoc();
  const cmds = insertConsumingCommands(doc, noteRef(0, 0, 0), note('1'));
  assert.ok(new EditHistory(doc).doGroup(cmds));
  assert.deepEqual(symbolsOf(doc), ['1', '-', '-', '-']);
  assert.equal(beatsOf(doc), 4, 'the measure is still four beats long');
});

test('four keypresses fill a blank 4/4 measure exactly', () => {
  const doc = blankDoc();
  const history = new EditHistory(doc);
  ['1', '2', '3', '4'].forEach((symbol, i) => {
    const cmds = insertConsumingCommands(doc, noteRef(0, 0, i), note(symbol));
    assert.ok(history.doGroup(cmds), 'keypress ' + i + ' applied');
  });
  assert.deepEqual(symbolsOf(doc), ['1', '2', '3', '4']);
  assert.equal(beatsOf(doc), 4);
});

test('a short note shrinks the slot it lands on instead of deleting it', () => {
  const doc = blankDoc();
  const eighth = note('1', { duration: 0.5 });
  assert.ok(new EditHistory(doc).doGroup(insertConsumingCommands(doc, noteRef(0, 0, 0), eighth)));
  assert.deepEqual(symbolsOf(doc), ['1', '0', '-', '-', '-']);
  const rest = doc.sections[0].measures[0][1];
  assert.equal(rest.duration, 0.5, 'the one-beat rest gave up half a beat');
  assert.equal(beatsOf(doc), 4, 'total unchanged — that is the whole point of 丙');
});

test('eighth notes fill a blank 4/4 measure halfway, still four beats', () => {
  const doc = blankDoc();
  const history = new EditHistory(doc);
  ['1', '2', '3', '4'].forEach((symbol, i) => {
    const cmds = insertConsumingCommands(doc, noteRef(0, 0, i), note(symbol, { duration: 0.5 }));
    assert.ok(history.doGroup(cmds));
  });
  assert.deepEqual(symbolsOf(doc).slice(0, 4), ['1', '2', '3', '4']);
  assert.equal(beatsOf(doc), 4, 'two beats of notes plus two beats of rest');
});

test('a long note swallows several slots at once', () => {
  const doc = blankDoc();
  const half = note('1', { duration: 2.0 });
  assert.ok(new EditHistory(doc).doGroup(insertConsumingCommands(doc, noteRef(0, 0, 0), half)));
  assert.deepEqual(symbolsOf(doc), ['1', '-', '-'], 'ate the rest and one dash');
  assert.equal(beatsOf(doc), 4);
});

test('entry stops eating at a real note rather than displacing it', () => {
  const doc = blankDoc();
  doc.sections[0].measures[0][2] = note('5');       // 0 - 5 -
  const half = note('1', { duration: 2.0 });
  assert.ok(new EditHistory(doc).doGroup(insertConsumingCommands(doc, noteRef(0, 0, 0), half)));
  // Only the rest and the dash before the '5' were consumed; the '5' survives,
  // and the measure runs over — B5② says warn, never silently adjust.
  assert.deepEqual(symbolsOf(doc), ['1', '5', '-']);
  assert.equal(beatsOf(doc), 4);
});

test('entry that cannot find enough to eat overfills rather than refusing', () => {
  const doc = blankDoc(2);                          // 0 -   (two beats)
  const whole = note('1', { duration: 4.0 });
  assert.ok(new EditHistory(doc).doGroup(insertConsumingCommands(doc, noteRef(0, 0, 0), whole)));
  assert.deepEqual(symbolsOf(doc), ['1']);
  assert.equal(beatsOf(doc), 4, 'a 2/4 measure now holds 4 beats — the linter warns');
});

test('one keypress is one undo, however many slots it touched', () => {
  const doc = blankDoc();
  const before = clone(doc);
  const history = new EditHistory(doc);
  history.doGroup(insertConsumingCommands(doc, noteRef(0, 0, 0), note('1', { duration: 2.0 })));
  assert.deepEqual(symbolsOf(doc), ['1', '-', '-']);
  assert.ok(history.undo());
  assert.deepEqual(doc, before, 'delete+delete+insert all came back together');
  assert.ok(history.redo());
  assert.deepEqual(symbolsOf(doc), ['1', '-', '-']);
});

test('insertConsumingCommands reports an invalid address instead of guessing', () => {
  const doc = blankDoc();
  assert.equal(insertConsumingCommands(doc, noteRef(0, 9, 0), note('1')), null);
  assert.equal(insertConsumingCommands(doc, noteRef(9, 0, 0), note('1')), null);
  assert.equal(insertConsumingCommands(doc, noteRef(0, 0, 99), note('1')), null);
});

test('appending at the end of a measure has nothing to consume and just inserts', () => {
  const doc = blankDoc();
  doc.sections[0].measures[0] = [note('1')];
  assert.ok(new EditHistory(doc).doGroup(insertConsumingCommands(doc, noteRef(0, 0, 1), note('2'))));
  assert.deepEqual(symbolsOf(doc), ['1', '2']);
});

// ── measure insert / delete (阶段5.5a) ───────────────────────────────────────

function measureAt(doc, section, measure) {
  return doc.sections[section].measures[measure];
}

test('barQuarterLength reads a plain time signature', () => {
  assert.equal(barQuarterLength('4/4'), 4.0);
  assert.equal(barQuarterLength('3/4'), 3.0);
  assert.equal(barQuarterLength('6/8'), 3.0);
  assert.equal(barQuarterLength('2/4'), 2.0);
});

test('barQuarterLength ignores an anacrusis suffix and falls back to 4/4 when unparseable', () => {
  assert.equal(barQuarterLength('3/4,8'), 3.0);
  assert.equal(barQuarterLength(''), 4.0);
  assert.equal(barQuarterLength(undefined), 4.0);
});

// The expected shapes below are not guessed -- they were read straight off
// the real Python parser (parse_jianpu_ly_text(_blank_measure_text(ql))),
// which is what any blank measure in this app actually looks like once
// loaded, whether created by the "new score" wizard or typed from scratch in
// input mode (stage 5.4). A 2.0/3.0/4.0-beat rest is NOT one big-duration
// note: jianpu_note_token writes it as `0 - - -`-style dash continuations,
// and the parser turns each token into its own model object -- so the
// "logical" 4-beat rest is actually 4 separate 1.0-duration objects. Every
// other duration (including dotted ones) stays a single token/object.
test('blankMeasureNotes expands a whole-bar rest into a rest plus dash continuations', () => {
  const notes = blankMeasureNotes(4.0);
  assert.deepEqual(notes.map((n) => [n.symbol, n.is_rest, n.duration]), [
    ['0', true, 1.0], ['-', false, 1.0], ['-', false, 1.0], ['-', false, 1.0],
  ]);
});

test('blankMeasureNotes matches the real parser for 3/4 and 2/4 bars', () => {
  assert.deepEqual(blankMeasureNotes(3.0).map((n) => n.symbol), ['0', '-', '-']);
  assert.deepEqual(blankMeasureNotes(2.0).map((n) => n.symbol), ['0', '-']);
});

test('blankMeasureNotes greedily chains rests for an irregular bar (5/4)', () => {
  const notes = blankMeasureNotes(5.0);
  // split_duration_chunks(5.0) = [4.0, 1.0]; the 4.0 chunk dash-expands, the
  // trailing 1.0 chunk is its own fresh rest token, not a fourth dash.
  assert.deepEqual(notes.map((n) => [n.symbol, n.duration]), [
    ['0', 1.0], ['-', 1.0], ['-', 1.0], ['-', 1.0], ['0', 1.0],
  ]);
  assert.equal(notes.reduce((sum, n) => sum + n.duration, 0), 5.0);
});

test('blankMeasureNotes handles 7/8 the same way the greedy chunker in Python does', () => {
  const notes = blankMeasureNotes(3.5);
  // split_duration_chunks(3.5) = [3.0, 0.5]: dash-expand the 3.0, then a
  // single eighth-rest token for the leftover 0.5 -- matches `0 - - q0`.
  assert.deepEqual(notes.map((n) => [n.symbol, n.duration]), [
    ['0', 1.0], ['-', 1.0], ['-', 1.0], ['0', 0.5],
  ]);
});

test('blankMeasureNotes keeps a dotted duration as one token, not dash-expanded', () => {
  const notes = blankMeasureNotes(1.5);
  assert.deepEqual(notes.map((n) => [n.duration, n.duration_dots]), [[1.5, 1]]);
});

test('blankMeasureNotes falls back to a quarter rest for a degenerate bar length', () => {
  assert.deepEqual(blankMeasureNotes(0), [{
    symbol: '0', accidental: '', upper_dots: 0, lower_dots: 0,
    duration: 1.0, duration_dots: 0, midi: null, is_rest: true, lyrics: {},
  }]);
});

test('insert_measure inserts a blank measure and delete_measure is its exact inverse', () => {
  const doc = makeDoc();
  const before = clone(doc);
  const notes = blankMeasureNotes(4.0);
  const inverse = applyCommand(doc, { type: 'insert_measure', at: { section: 0, measure: 1 }, notes });
  assert.deepEqual(inverse, { type: 'delete_measure', at: { section: 0, measure: 1 } });
  assert.equal(doc.sections[0].measures.length, 3);
  assert.deepEqual(measureAt(doc, 0, 1).map((n) => n.symbol), ['0', '-', '-', '-']);
  // Untouched measures keep their identity — only a new one was spliced in.
  assert.deepEqual(measureAt(doc, 0, 0).map((n) => n.symbol), ['1', '2']);
  assert.deepEqual(measureAt(doc, 0, 2).map((n) => n.symbol), ['3', '0', '5']);

  const reInverse = applyCommand(doc, inverse);
  assert.deepEqual(doc, before, 'delete undoes the insert exactly');
  assert.deepEqual(reInverse, { type: 'insert_measure', at: { section: 0, measure: 1 }, notes });
});

test('insert_measure appends when the index equals the measure count', () => {
  const doc = makeDoc();
  const notes = blankMeasureNotes(4.0);
  applyCommand(doc, { type: 'insert_measure', at: { section: 0, measure: 2 }, notes });
  assert.equal(doc.sections[0].measures.length, 3);
  assert.deepEqual(measureAt(doc, 0, 2).map((n) => n.symbol), ['0', '-', '-', '-']);
});

test('insert_measure rejects an out-of-range section or index', () => {
  const doc = makeDoc();
  assert.equal(applyCommand(doc, { type: 'insert_measure', at: { section: 9, measure: 0 }, notes: [] }), null);
  assert.equal(applyCommand(doc, { type: 'insert_measure', at: { section: 0, measure: 99 }, notes: [] }), null);
});

test('delete_measure removes exactly one measure and shifts the rest up', () => {
  const doc = makeDoc();
  doc.sections[0].measures.push([note('4')]);   // three measures, so one can safely go
  const removed = doc.sections[0].measures[1].map((n) => n.symbol);
  const inverse = applyCommand(doc, { type: 'delete_measure', at: { section: 0, measure: 1 } });
  assert.equal(doc.sections[0].measures.length, 2);
  assert.deepEqual(measureAt(doc, 0, 0).map((n) => n.symbol), ['1', '2']);
  assert.deepEqual(measureAt(doc, 0, 1).map((n) => n.symbol), ['4']);
  assert.equal(inverse.type, 'insert_measure');
  assert.deepEqual(inverse.notes.map((n) => n.symbol), removed);
});

test('delete_measure refuses to empty out a section entirely', () => {
  const doc = makeDoc();
  // Section 1 has exactly one measure -- deleting it would leave zero.
  assert.equal(applyCommand(doc, { type: 'delete_measure', at: { section: 1, measure: 0 } }), null);
  assert.equal(doc.sections[1].measures.length, 1, 'refused, so nothing changed');
});

test('delete_measure rejects an out-of-range address', () => {
  const doc = makeDoc();
  assert.equal(applyCommand(doc, { type: 'delete_measure', at: { section: 0, measure: 99 } }), null);
  assert.equal(applyCommand(doc, { type: 'delete_measure', at: { section: 9, measure: 0 } }), null);
});

test('a whole insert-then-delete-measure round trip is one undo step', () => {
  const doc = makeDoc();
  const before = clone(doc);
  const history = new EditHistory(doc);
  const notes = blankMeasureNotes(4.0);
  assert.ok(history.do({ type: 'insert_measure', at: { section: 0, measure: 1 }, notes }));
  assert.equal(doc.sections[0].measures.length, 3);
  assert.ok(history.undo());
  assert.deepEqual(doc, before);
  assert.ok(history.redo());
  assert.equal(doc.sections[0].measures.length, 3);
});

// ── malformed time signatures must not hang the page (5.5a review) ──────────
// The parser does not validate a time signature's numerator/denominator: a
// hand-typed `4/0` in the text pane reaches the model verbatim. It used to
// come out of barQuarterLength as Infinity and spin blankMeasureNotes forever
// (verified: the loop never terminates, taking the whole tab down with it).

test('barQuarterLength refuses a zero denominator instead of returning Infinity', () => {
  assert.equal(barQuarterLength('4/0'), 4.0, 'falls back to a plain 4/4 bar');
  assert.ok(Number.isFinite(barQuarterLength('4/0')));
});

test('barQuarterLength refuses a zero-length bar', () => {
  assert.equal(barQuarterLength('0/4'), 4.0);
});

test('barQuarterLength still accepts every well-formed signature', () => {
  assert.equal(barQuarterLength('4/4'), 4.0);
  assert.equal(barQuarterLength('3/4'), 3.0);
  assert.equal(barQuarterLength('6/8'), 3.0);
  assert.equal(barQuarterLength('7/8'), 3.5);
  assert.equal(barQuarterLength('5/4'), 5.0);
});

test('blankMeasureNotes terminates on a non-finite bar length', () => {
  // Would previously never return. Assert on the result, not on a timeout:
  // if this regresses, the test run hangs and that is loud enough.
  assert.deepEqual(blankMeasureNotes(Infinity).map((n) => n.duration), [1.0]);
  assert.deepEqual(blankMeasureNotes(NaN).map((n) => n.duration), [1.0]);
  assert.deepEqual(blankMeasureNotes(-5).map((n) => n.duration), [1.0]);
});

test('blankMeasureNotes stays bounded for an absurd but finite bar length', () => {
  const notes = blankMeasureNotes(100000);
  assert.ok(notes.length <= 512, `bounded, got ${notes.length}`);
});

// ── copy / paste (阶段5.5b) ─────────────────────────────────────────────────

/** A 4/4 score whose measures are distinguishable by content. */
function clipDoc() {
  const m = (...symbols) => symbols.map((s) => note(s));
  return {
    title: 'T', composer: '', key_header: '1=C', tempo: 120,
    sections: [{
      time_sig: '4/4',
      measures: [m('1', '2', '3', '4'), m('5', '6', '7', '1'), blankMeasureNotes(4.0)],
    }],
  };
}

const symbolsIn = (doc, m) => doc.sections[0].measures[m].map((n) => n.symbol);
const measureCount = (doc) => doc.sections[0].measures.length;
const beats = (doc, m) => doc.sections[0].measures[m].reduce((s, n) => s + n.duration, 0);

// -- selectionSpan --------------------------------------------------------

test('selectionSpan marks a whole measure as aligned', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 1, 0), noteRef(0, 1, 3));
  assert.deepEqual(span, {
    startMeasure: 1, startIndex: 0, endMeasure: 1, endIndex: 3, aligned: true,
  });
});

test('selectionSpan marks a partial measure as not aligned', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 0, 1), noteRef(0, 0, 2));
  assert.equal(span.aligned, false);
  assert.deepEqual([span.startIndex, span.endIndex], [1, 2]);
});

test('selectionSpan spanning two full measures is aligned', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 1, 3));
  assert.equal(span.aligned, true);
  assert.deepEqual([span.startMeasure, span.endMeasure], [0, 1]);
});

test('selectionSpan swallows the dashes trailing the last selected note', () => {
  // A 4-beat note is one drawn note but four model objects (`1 - - -`).
  // Stopping at the drawn note alone would cut three beats off the copy.
  const doc = clipDoc();
  doc.sections[0].measures[0] = [note('1'), note('-'), note('-'), note('-')];
  const span = selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 0, 0));
  assert.equal(span.endIndex, 3, 'ran to the end of the sustain');
  assert.equal(span.aligned, true, 'and that makes it a whole measure');
});

test('selectionSpan reports an unresolvable address instead of guessing', () => {
  const doc = clipDoc();
  assert.equal(selectionSpan(doc, 9, noteRef(0, 0, 0), noteRef(0, 0, 0)), null);
  assert.equal(selectionSpan(doc, 0, noteRef(0, 9, 0), noteRef(0, 0, 0)), null);
  assert.equal(selectionSpan(doc, 0, null, noteRef(0, 0, 0)), null);
});

// -- extractFragment ------------------------------------------------------

test('extractFragment keeps whole measures for an aligned span', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 1, 3));
  const frag = extractFragment(doc, 0, span);
  assert.equal(frag.aligned, true);
  assert.deepEqual(frag.measures.map((m) => m.map((n) => n.symbol)),
    [['1', '2', '3', '4'], ['5', '6', '7', '1']]);
});

test('extractFragment flattens a non-aligned span, barline and all', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 0, 2), noteRef(0, 1, 1));
  const frag = extractFragment(doc, 0, span);
  assert.equal(frag.aligned, false);
  assert.deepEqual(frag.notes.map((n) => n.symbol), ['3', '4', '5', '6']);
});

test('extractFragment deep-copies, so later edits cannot reach the clipboard', () => {
  const doc = clipDoc();
  const span = selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 0, 3));
  const frag = extractFragment(doc, 0, span);
  applyCommand(doc, { type: 'set_pitch', ref: noteRef(0, 0, 0), symbol: '7' });
  assert.equal(frag.measures[0][0].symbol, '1', 'clipboard is untouched');
});

// -- pasteMeasuresCommands ------------------------------------------------

test('pasting aligned measures inserts them before the target measure', () => {
  const doc = clipDoc();
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 1, 0), noteRef(0, 1, 3)));
  const history = new EditHistory(doc);
  assert.ok(history.doGroup(pasteMeasuresCommands(0, 2, frag.measures)));
  assert.equal(measureCount(doc), 4);
  assert.deepEqual(symbolsIn(doc, 2), ['5', '6', '7', '1'], 'the pasted measure');
  assert.deepEqual(symbolsIn(doc, 3), ['0', '-', '-', '-'], 'the old measure moved down');
});

test('pasting several measures keeps their order', () => {
  const doc = clipDoc();
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 1, 3)));
  const history = new EditHistory(doc);
  assert.ok(history.doGroup(pasteMeasuresCommands(0, 2, frag.measures)));
  assert.equal(measureCount(doc), 5);
  assert.deepEqual(symbolsIn(doc, 2), ['1', '2', '3', '4']);
  assert.deepEqual(symbolsIn(doc, 3), ['5', '6', '7', '1']);
  assert.deepEqual(symbolsIn(doc, 4), ['0', '-', '-', '-']);
});

test('a measure paste is a single undo step however many measures it added', () => {
  const doc = clipDoc();
  const before = clone(doc);
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 0, 0), noteRef(0, 1, 3)));
  const history = new EditHistory(doc);
  history.doGroup(pasteMeasuresCommands(0, 2, frag.measures));
  assert.ok(history.undo());
  assert.deepEqual(doc, before);
  assert.ok(history.redo());
  assert.equal(measureCount(doc), 5);
});

test('pasteMeasuresCommands refuses an empty fragment', () => {
  assert.equal(pasteMeasuresCommands(0, 0, []), null);
  assert.equal(pasteMeasuresCommands(0, 0, null), null);
});

// -- pasteNotesCommands ---------------------------------------------------

test('pasting a loose fragment consumes the rests it lands on', () => {
  const doc = clipDoc();
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 0, 2), noteRef(0, 0, 3)));
  assert.deepEqual(frag.notes.map((n) => n.symbol), ['3', '4']);
  const history = new EditHistory(doc);
  assert.ok(history.doGroup(pasteNotesCommands(doc, noteRef(0, 2, 0), frag.notes)));
  assert.deepEqual(symbolsIn(doc, 2), ['3', '4', '-', '-']);
  assert.equal(beats(doc, 2), 4, 'the measure is still four beats');
  assert.equal(measureCount(doc), 3, 'and no measure was created');
});

test('a loose paste that outruns the measure overfills it rather than spilling over', () => {
  // Decision 甲: loose fragments are a note stream poured into the target
  // measure. Running past the bar line is a lint warning (B5②), not a new bar.
  const doc = clipDoc();
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 0, 1), noteRef(0, 1, 3)));
  assert.equal(frag.notes.length, 7);
  const history = new EditHistory(doc);
  assert.ok(history.doGroup(pasteNotesCommands(doc, noteRef(0, 2, 0), frag.notes)));
  assert.equal(measureCount(doc), 3, 'still three measures');
  assert.equal(beats(doc, 2), 7, 'seven beats in a 4/4 bar -- the linter says so');
});

test('a note paste is a single undo step', () => {
  const doc = clipDoc();
  const before = clone(doc);
  const frag = extractFragment(doc, 0, selectionSpan(doc, 0, noteRef(0, 0, 2), noteRef(0, 0, 3)));
  const history = new EditHistory(doc);
  history.doGroup(pasteNotesCommands(doc, noteRef(0, 2, 0), frag.notes));
  assert.ok(history.undo());
  assert.deepEqual(doc, before, 'every consumed rest came back too');
  assert.ok(history.redo());
  assert.deepEqual(symbolsIn(doc, 2), ['3', '4', '-', '-']);
});

test('pasteNotesCommands leaves the document untouched while building commands', () => {
  const doc = clipDoc();
  const before = clone(doc);
  const cmds = pasteNotesCommands(doc, noteRef(0, 2, 0), [note('3'), note('4')]);
  assert.ok(cmds && cmds.length);
  assert.deepEqual(doc, before, 'planning happens on a copy, not in place');
});

test('pasteNotesCommands refuses an empty fragment or a bad address', () => {
  const doc = clipDoc();
  assert.equal(pasteNotesCommands(doc, noteRef(0, 0, 0), []), null);
  assert.equal(pasteNotesCommands(doc, noteRef(0, 9, 0), [note('1')]), null);
});

test('a copied fragment pastes identically into a different document', () => {
  // "Fragment paste across files is faithful" -- the clipboard holds plain
  // model objects with no tie to the document it came from.
  const source = clipDoc();
  const frag = extractFragment(source, 0, selectionSpan(source, 0, noteRef(0, 1, 0), noteRef(0, 1, 3)));
  const target = {
    title: 'Other', composer: '', key_header: '6=A', tempo: 90,
    sections: [{ time_sig: '3/4', measures: [blankMeasureNotes(3.0)] }],
  };
  const history = new EditHistory(target);
  assert.ok(history.doGroup(pasteMeasuresCommands(0, 0, frag.measures)));
  assert.deepEqual(target.sections[0].measures[0].map((n) => n.symbol), ['5', '6', '7', '1']);
  assert.deepEqual(target.sections[0].measures[1].map((n) => n.symbol), ['0', '-', '-']);
});

// ── batch transforms over a selection (阶段5.6a) ─────────────────────────────

function batchDoc() {
  return {
    title: 'T', composer: '', key_header: '1=C', tempo: 120,
    sections: [{
      time_sig: '4/4',
      measures: [[note('1'), note('2'), note('3'), note('4')],
                 [note('5'), note('6'), note('7'), note('1')]],
    }],
  };
}

const syms = (doc, m) => doc.sections[0].measures[m].map((n) => n.symbol);

test('orderBatch leaves value-changing commands in the order given', () => {
  const cmds = [
    { type: 'set_octave', ref: noteRef(0, 0, 0), delta: 1 },
    { type: 'set_octave', ref: noteRef(0, 0, 1), delta: 1 },
    { type: 'set_octave', ref: noteRef(0, 0, 2), delta: 1 },
  ];
  assert.deepEqual(orderBatch(cmds), cmds);
});

test('orderBatch deletes from the back so addresses stay valid', () => {
  const cmds = [0, 1, 2].map((i) => ({ type: 'delete_note', ref: noteRef(0, 0, i) }));
  assert.deepEqual(orderBatch(cmds).map((c) => c.ref.index), [2, 1, 0]);
});

test('orderBatch sorts across measures and sections too', () => {
  const cmds = [
    { type: 'delete_note', ref: noteRef(0, 0, 1) },
    { type: 'delete_note', ref: noteRef(0, 1, 0) },
    { type: 'delete_note', ref: noteRef(1, 0, 0) },
  ];
  assert.deepEqual(
    orderBatch(cmds).map((c) => [c.ref.section, c.ref.measure, c.ref.index]),
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
});

test('orderBatch tolerates an empty or holey list', () => {
  assert.deepEqual(orderBatch([]), []);
  assert.deepEqual(orderBatch(null), []);
  assert.deepEqual(orderBatch([null, undefined]), []);
});

test('deleting a whole selection back-to-front removes exactly those notes', () => {
  const doc = batchDoc();
  const history = new EditHistory(doc);
  // Select notes 1..2 of measure 0 and delete both.
  const cmds = orderBatch([1, 2].map((i) => ({ type: 'delete_note', ref: noteRef(0, 0, i) })));
  assert.ok(history.doGroup(cmds));
  assert.deepEqual(syms(doc, 0), ['1', '4'], 'the notes between them are gone, nothing else');
});

test('a front-to-back delete would corrupt the measure -- proving the ordering matters', () => {
  const doc = batchDoc();
  // Deliberately NOT ordered: index 1 then index 2, applied in that order.
  const history = new EditHistory(doc);
  history.doGroup([
    { type: 'delete_note', ref: noteRef(0, 0, 1) },
    { type: 'delete_note', ref: noteRef(0, 0, 2) },
  ]);
  assert.deepEqual(syms(doc, 0), ['1', '3'],
    'the second delete hit the note that slid into index 2, not the one selected');
});

test('a batch delete is one undo step and restores every note', () => {
  const doc = batchDoc();
  const before = clone(doc);
  const history = new EditHistory(doc);
  history.doGroup(orderBatch([0, 1, 2, 3].map((i) => ({ type: 'delete_note', ref: noteRef(0, 0, i) }))));
  assert.deepEqual(syms(doc, 0), []);
  assert.ok(history.undo());
  assert.deepEqual(doc, before, 'all four came back, in order');
});

test('a batch octave shift is one undo step', () => {
  const doc = batchDoc();
  const before = clone(doc);
  const history = new EditHistory(doc);
  const cmds = orderBatch([0, 1, 2].map((i) => ({ type: 'set_octave', ref: noteRef(0, 0, i), delta: 1 })));
  assert.ok(history.doGroup(cmds));
  assert.deepEqual(doc.sections[0].measures[0].map((n) => n.upper_dots), [1, 1, 1, 0]);
  assert.ok(history.undo());
  assert.deepEqual(doc, before);
});

test('a batch spanning two measures reaches both', () => {
  const doc = batchDoc();
  const history = new EditHistory(doc);
  const refs = [noteRef(0, 0, 3), noteRef(0, 1, 0), noteRef(0, 1, 1)];
  assert.ok(history.doGroup(orderBatch(
    refs.map((ref) => ({ type: 'set_octave', ref, delta: -1 })))));
  assert.deepEqual(doc.sections[0].measures[0].map((n) => n.lower_dots), [0, 0, 0, 1]);
  assert.deepEqual(doc.sections[0].measures[1].map((n) => n.lower_dots), [1, 1, 0, 0]);
});

test('a batch that a command refuses rolls the whole thing back', () => {
  const doc = batchDoc();
  doc.sections[0].measures[0][1] = note('0');   // a rest: takes no accidental
  const before = clone(doc);
  const history = new EditHistory(doc);
  const cmds = [0, 1, 2].map((i) => (
    { type: 'set_accidental', ref: noteRef(0, 0, i), accidental: '#' }));
  assert.ok(!history.doGroup(cmds), 'the rest refuses, so the group fails');
  assert.deepEqual(doc, before, 'and the notes before it are put back');
});
