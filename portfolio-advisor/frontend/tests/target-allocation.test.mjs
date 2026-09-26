import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(root, 'node_modules/.targets-test-'));
after(() => rm(temporary, { recursive: true, force: true }));
await build({ entryPoints: [join(root, 'src/targetAllocation.ts'), join(root, 'src/TargetAllocationEditor.tsx')], outdir: temporary,
  outExtension: { '.js': '.mjs' }, bundle: true, packages: 'external', platform: 'node', format: 'esm', jsx: 'automatic' });
const { addTargetSymbol, removeTargetSymbol, modelWeights, targetValidation, targetsChanged } = await import(pathToFileURL(join(temporary, 'targetAllocation.mjs')).href);
const { TargetAllocationEditor } = await import(pathToFileURL(join(temporary, 'TargetAllocationEditor.mjs')).href);

test('new symbols start at zero, normalize case and never alter existing targets', () => {
  const weights = Object.freeze({ AAPL: 60, BND: 35, CASH: 5 });
  assert.deepEqual(addTargetSymbol(weights, ' brk-b '), { ...weights, 'BRK-B': 0 });
  assert.deepEqual(weights, { AAPL: 60, BND: 35, CASH: 5 });
  assert.equal(targetValidation(addTargetSymbol(weights, 'AMZN')), null);
  for (const symbol of ['aapl', ' CASH ', '', 'A B', '<script>', '123', 'A'.repeat(16)]) {
    assert.throws(() => addTargetSymbol(weights, symbol));
  }
});

test('removal clears held targets but removes unowned symbols, with cash retained', () => {
  const weights = Object.freeze({ AAPL: 45, AMZN: 15, BND: 35, CASH: 5 });
  assert.deepEqual(removeTargetSymbol(weights, 'AAPL', ['AAPL']), { ...weights, AAPL: 0 });
  assert.deepEqual(removeTargetSymbol(weights, 'AMZN', ['AAPL']), { AAPL: 45, BND: 35, CASH: 5 });
  assert.deepEqual(removeTargetSymbol(weights, 'CASH', []), weights);
  assert.match(targetValidation(removeTargetSymbol(weights, 'AMZN', ['AAPL'])), /total 100/);
});

test('switching preset or portfolio discards custom targets and exposes current holdings', () => {
  const holdings = Object.freeze(['MSFT', 'AAPL']);
  const preset = Object.freeze({ VTI: 80, BND: 15, CASH: 5 });
  assert.deepEqual(modelWeights(holdings, preset), { MSFT: 0, AAPL: 0, VTI: 80, BND: 15, CASH: 5 });
  assert.deepEqual(holdings, ['MSFT', 'AAPL']);
});

test('invalid weights and security limits prevent analysis while legacy zero targets are supported', () => {
  for (const weights of [{ AAPL: NaN }, { AAPL: Infinity }, { AAPL: 101, CASH: -1 }, { AAPL: 90 }, {}]) assert.ok(targetValidation(weights));
  const hundred = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`A${i}`, 1]));
  assert.equal(targetValidation({ ...hundred, CASH: 0 }), null);
  assert.match(targetValidation({ ...hundred, EXTRA: 1 }), /At most 100/);
  const legacy = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`B${i}`, 0]));
  const maximum = { ...hundred, ...legacy, CASH: 0 };
  assert.equal(targetValidation(maximum), null);
  assert.throws(() => addTargetSymbol(maximum, 'AMZN'), /limit/);
});

test('saved proposal change detection includes symbols not present in the old report', () => {
  const saved = [{ symbol: 'AAPL', targetWeight: 100 }];
  assert.equal(targetsChanged({ AAPL: 100, AMZN: 0 }, saved), false);
  assert.equal(targetsChanged({ AAPL: 100, AMZN: 5 }, saved), true);
  assert.equal(targetsChanged({ CASH: 100 }, saved), true);
  assert.equal(targetsChanged({ AAPL: NaN }, saved), true);
});

test('editor labels distinguish held and target-only positions and expose keyboard controls', () => {
  const html = renderToStaticMarkup(createElement(TargetAllocationEditor, {
    weights: { AAPL: 60, AMZN: 35, CASH: 5 }, heldSymbols: ['AAPL'], onChange() {},
  }));
  assert.match(html, /<form class="target-add"/);
  assert.match(html, /for="target-symbol">New stock or ETF symbol/);
  assert.match(html, /aria-label="AMZN target percent"/);
  assert.match(html, /aria-label="Remove AMZN from target allocation"/);
  assert.match(html, /aria-label="Set AAPL target to zero"/);
  assert.match(html, /Current holding/);
  assert.match(html, /Target only/);
  assert.doesNotMatch(html, /Remove CASH/);
});
