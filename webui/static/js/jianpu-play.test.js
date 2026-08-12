// webui/static/js/jianpu-play.test.js — unit tests for the 阶段4.1 playback
// timeline builder. The acceptance criterion is that the scheduled note events
// match the source score note-for-note, so these assert the timeline itself
// rather than relying on "it sounded right".
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildTimeline } from './jianpu-play.js';

/** Minimal stand-in for one section of what editor_graphical_data returns. */
function section(notes, qpm = 120, ) {
  return { notes, tempos: [{ start: 0, qpm }], keySignatures: [], timeSignatures: [] };
}

test('converts quarter-note positions to seconds using the tempo', () => {
  // At 120 qpm a quarter note is exactly 0.5s.
  const { notes, duration } = buildTimeline([section([
    { start: 0, length: 1, pitch: 60, intensity: 80 },
    { start: 1, length: 1, pitch: 62, intensity: 80 },
    { start: 2, length: 2, pitch: 64, intensity: 80 },
  ])]);
  assert.deepEqual(notes.map((n) => [n.t, n.dur, n.pitch]), [
    [0, 0.5, 60],
    [0.5, 0.5, 62],
    [1.0, 1.0, 64],
  ]);
  assert.equal(duration, 2.0);
});

test('a different tempo scales every time proportionally', () => {
  // 60 qpm -> a quarter note is 1s, i.e. exactly twice the 120 qpm timings.
  const { notes, duration } = buildTimeline([section([
    { start: 0, length: 1, pitch: 60 },
    { start: 1, length: 1, pitch: 62 },
  ], 60)]);
  assert.deepEqual(notes.map((n) => [n.t, n.dur]), [[0, 1], [1, 1]]);
  assert.equal(duration, 2);
});

test('duration spans to the end of the last note, not its onset', () => {
  const { duration } = buildTimeline([section([
    { start: 0, length: 1, pitch: 60 },
    { start: 4, length: 4, pitch: 62 },   // ends at beat 8 -> 4.0s at 120qpm
  ])]);
  assert.equal(duration, 4.0);
});

test('each section gets its own MIDI channel so voices do not cut each other off', () => {
  const { notes } = buildTimeline([
    section([{ start: 0, length: 1, pitch: 60 }]),
    section([{ start: 0, length: 1, pitch: 64 }]),
    section([{ start: 0, length: 1, pitch: 67 }]),
  ]);
  const byPitch = Object.fromEntries(notes.map((n) => [n.pitch, n.ch]));
  assert.equal(byPitch[60], 0);
  assert.equal(byPitch[64], 1);
  assert.equal(byPitch[67], 2);
  assert.equal(new Set(notes.map((n) => n.ch)).size, 3, 'three distinct channels');
});

test('skips the GM percussion channel when assigning voices', () => {
  // 10 sections -> channels 0..9, but 9 is percussion in General MIDI, so the
  // 10th voice must land on 10 instead or it would play as drums.
  const sections = Array.from({ length: 10 }, (_v, i) =>
    section([{ start: 0, length: 1, pitch: 60 + i }]));
  const { notes } = buildTimeline(sections);
  const channels = notes.map((n) => n.ch);
  assert.ok(!channels.includes(9), `channel 9 must be skipped, got ${channels}`);
  assert.equal(channels[9], 10, 'the 10th voice lands on channel 10');
});

test('events come out sorted by time even when sections interleave', () => {
  const { notes } = buildTimeline([
    section([{ start: 0, length: 1, pitch: 60 }, { start: 2, length: 1, pitch: 62 }]),
    section([{ start: 1, length: 1, pitch: 64 }, { start: 3, length: 1, pitch: 65 }]),
  ]);
  const times = notes.map((n) => n.t);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
  assert.deepEqual(notes.map((n) => n.pitch), [60, 64, 62, 65]);
});

test('falls back to 120 qpm when a section carries no tempo', () => {
  const { notes } = buildTimeline([{ notes: [{ start: 2, length: 1, pitch: 60 }] }]);
  assert.equal(notes[0].t, 1.0, '2 quarters at 120qpm = 1.0s');
});

test('empty and missing input produce an empty timeline rather than throwing', () => {
  assert.deepEqual(buildTimeline([]), { notes: [], duration: 0 });
  assert.deepEqual(buildTimeline(undefined), { notes: [], duration: 0 });
  assert.deepEqual(buildTimeline([section([])]), { notes: [], duration: 0 });
});
