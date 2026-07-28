// eslint.config.js — lint config for webui/static/js/ only.
//
// Scope is deliberately narrow: this project ships plain ES modules straight
// to the browser (no bundler, no transpiler — see CLAUDE.md's webui/ section),
// so there's nothing to lint outside webui/static/js/. webui/static/vendor/
// (pdf.js, WebAudioTinySynth, noteDigger) is third-party/vendored and must
// never be linted or auto-fixed, mirroring how ruff.toml excludes vendored
// Python dirs.
import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    ignores: ['webui/static/vendor/**', 'node_modules/**'],
  },
  js.configs.recommended,
  {
    files: ['webui/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        // pywebview injects this bridge object at runtime; not a standard
        // browser global.
        pywebview: 'readonly',
      },
    },
    rules: {
      // Every module here follows the '_' prefix = intentionally-unused
      // convention already used throughout (catch (_e) {}, etc.).
      'no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      // catch (_e) {} — best-effort/ignore-failure is a deliberate, pervasive
      // pattern here (pointer-capture release, iframe-not-ready probes, etc.),
      // not an oversight.
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    files: ['*.config.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.node },
    },
  },
];
