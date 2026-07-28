// webui/static/js/test-dom-shim.js — minimal fake DOM surface for node --test.
//
// core.js (and everything that transitively imports it) runs real DOM calls
// at module top level (creating the toast element, wiring nav buttons, etc.).
// That's normal for a page script, but it means importing core.js from plain
// Node — no browser, and deliberately no jsdom (see P1-3 in the fix-plan doc:
// "node --test 即可，不引入 jest") — throws immediately unless something
// answers those calls. This is that something: just enough of `document`/
// `window` to let module-top-level code run without crashing, not a real
// DOM implementation. Import this file FIRST, before importing anything
// from this directory, in any test that needs it.
function fakeElement() {
  const el = {
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    style: {},
    dataset: {},
    children: [],
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) { el.children.push(child); return child; },
    append() {},
    remove() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    querySelector() { return fakeElement(); },
    querySelectorAll() { return []; },
  };
  return el;
}

globalThis.document = {
  createElement: fakeElement,
  createElementNS: fakeElement,
  querySelector: () => fakeElement(),
  querySelectorAll: () => [],
  body: fakeElement(),
  head: fakeElement(),
  documentElement: fakeElement(),
  addEventListener() {},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = () => 0;
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.CustomEvent = class { constructor(name, opts) { this.type = name; this.detail = opts && opts.detail; } };
