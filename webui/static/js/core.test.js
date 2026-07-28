// webui/static/js/core.test.js — smoke test for core.js's pure i18n helper,
// run via node's built-in test runner (see P1-3 in the fix-plan doc: no jest).
import './test-dom-shim.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { I18N, t } from './core.js';

test('t() returns the string for the active language', () => {
  I18N.lang = 'zh';
  I18N.strings = { 'w.greet': { zh: '你好', en: 'hello' } };
  assert.equal(t('w.greet'), '你好');
});

test('t() falls back to zh when the active language is missing', () => {
  I18N.lang = 'en';
  I18N.strings = { 'w.greet': { zh: '你好' } };
  assert.equal(t('w.greet'), '你好');
});

test('t() falls back to the raw key when the key is not in the catalog', () => {
  I18N.lang = 'zh';
  I18N.strings = {};
  assert.equal(t('w.unknown_key'), 'w.unknown_key');
});

test('t() interpolates {param} placeholders', () => {
  I18N.lang = 'en';
  I18N.strings = { 'w.count': { en: '{n} of {t} selected' } };
  assert.equal(t('w.count', { n: 3, t: 10 }), '3 of 10 selected');
});

test('t() leaves unmatched placeholders untouched when no params are given', () => {
  I18N.lang = 'en';
  I18N.strings = { 'w.raw': { en: 'has {n} placeholder' } };
  assert.equal(t('w.raw'), 'has {n} placeholder');
});
