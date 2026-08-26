// webui/static/js/jianpu-edit.test.js — unit tests for the 阶段5.1 edit model
// and undo/redo command stack. B7 risk 6 calls for exactly this: design the
// command stack independently of rendering, with command-pattern unit coverage.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EditHistory, applyCommand, getNote, nextRef, noteRef, prevRef, refEquals,
  steppedDuration,
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
