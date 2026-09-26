import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(root, 'node_modules/.advisor-test-'));
after(() => rm(temporary, { recursive: true, force: true }));
const output = join(temporary, 'component.mjs');
await build({ entryPoints: [join(root, 'src/AdvisorMarkdown.tsx')], outfile: output,
  bundle: true, packages: 'external', platform: 'node', format: 'esm', jsx: 'automatic' });
const { AdvisorMarkdown } = await import(pathToFileURL(output).href);
const render = (text, props = {}) => renderToStaticMarkup(createElement(AdvisorMarkdown, props, text));

test('renders advisor emphasis, headings, aligned tables and lists as semantic elements', () => {
  const html = render('Value: **$1,000.00**.\n\n### Current allocation\n\n| Symbol | Value |\n|---|---:|\n| VTI | $600.00 |\n| CASH | $400.00 |\n\n- Indicative quantities\n- No trades placed');
  assert.match(html, /<strong>\$1,000\.00<\/strong>/);
  assert.match(html, /<h3>Current allocation<\/h3>/);
  assert.match(html, /role="region" aria-label="Advisor data table" tabindex="0"/);
  assert.match(html, /<th scope="col" style="text-align:right">Value<\/th>/);
  assert.match(html, /<td style="text-align:right">\$600\.00<\/td>/);
  assert.match(html, /<ul>\s*<li>Indicative quantities<\/li>/);
  assert.doesNotMatch(html, /###|\*\*|\|---/);
});

test('untrusted responses cannot render HTML, remote images or unsafe links', () => {
  const html = render('<script>alert(1)</script>\n\n<img src="https://example.com/track" onerror="alert(1)">\n\n![tracking](https://example.com/track)\n\n[unsafe](javascript:alert%281%29) [internal](/auth/logout) [source](https://example.com/quote)');
  assert.doesNotMatch(html, /<script|<img|onerror|href="javascript:|href="\/auth/);
  assert.match(html, /<span>unsafe<\/span>/);
  assert.match(html, /href="https:\/\/example.com\/quote" target="_blank" rel="noopener noreferrer"/);
});

test('plain text and formatted follow-up answers share the same renderer', () => {
  assert.match(render('Run an analysis to see an explanation.'), /<p>Run an analysis to see an explanation\.<\/p>/);
  const html = render('## Follow-up\n\n1. Keep **5% cash**.\n2. Review assumptions.', { className: 'answer', live: true });
  assert.match(html, /class="advisor-markdown answer" aria-live="polite"/);
  assert.match(html, /<h3>Follow-up<\/h3>/);
  assert.match(html, /<ol>\s*<li>Keep <strong>5% cash<\/strong>/);
});
