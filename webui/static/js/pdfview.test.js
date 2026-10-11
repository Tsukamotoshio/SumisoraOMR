// node --test: PdfView.render() when a second render arrives while the first is still drawing.
import './test-dom-shim.js';
import test from 'node:test';
import assert from 'node:assert/strict';

// What PdfView touches when it is built, beyond the shared shim.
globalThis.ResizeObserver = class { observe() {} };
globalThis.window.addEventListener ??= () => {};
globalThis.window.devicePixelRatio ??= 1;
const { PdfView } = await import('./pdfview.js');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fakeCanvas() {
  const frame = { clientWidth: 600, clientHeight: 800, addEventListener() {}, classList: { add() {}, remove() {} } };
  return { parentElement: frame, classList: { add() {}, remove() {} }, style: {}, width: 0, height: 0 };
}

// A page whose render() behaves like pdf.js's: it refuses a second render on a canvas
// whose first one is still drawing, and a cancelled render ends in a
// RenderingCancelledException.
function fakeDoc() {
  const state = { active: 0, completed: 0, cancelled: 0 };
  const page = {
    getViewport: ({ scale }) => ({ width: 595 * scale, height: 842 * scale }),
    render() {
      if (state.active) throw new Error('Cannot use the same canvas during multiple render() operations.');
      state.active += 1;
      let finish;
      let fail;
      const promise = new Promise((res, rej) => { finish = res; fail = rej; });
      const task = {
        promise,
        cancelled: false,
        cancel() {
          if (this.cancelled) return;
          this.cancelled = true;
          state.active -= 1;
          state.cancelled += 1;
          const e = new Error('Rendering cancelled');
          e.name = 'RenderingCancelledException';
          fail(e);
        },
      };
      setTimeout(() => {
        if (task.cancelled) return;
        state.active -= 1;
        state.completed += 1;
        finish();
      }, 30);
      return task;
    },
  };
  return { state, doc: { numPages: 1, getPage: async () => page } };
}

test('a render arriving while another draws cancels it instead of colliding on the canvas', async () => {
  const view = new PdfView(fakeCanvas(), {}, null);
  const { state, doc } = fakeDoc();
  view.doc = doc;
  const first = view.render();
  await sleep(5);                    // the first one is drawing now
  const second = view.render();      // e.g. the window was resized again
  await Promise.all([first, second]);
  assert.equal(state.active, 0);
  assert.equal(state.cancelled, 1);
  assert.equal(state.completed, 1);
});

test('back-to-back renders that start together still draw once', async () => {
  const view = new PdfView(fakeCanvas(), {}, null);
  const { state, doc } = fakeDoc();
  view.doc = doc;
  await Promise.all([view.render(), view.render(), view.render()]);
  assert.equal(state.active, 0);
  assert.equal(state.completed, 1);
});
