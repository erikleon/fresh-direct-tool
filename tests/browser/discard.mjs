/* The discard flow's browser-side half: the <details> disclosure and the
 * `required` checkbox that guards it. The deletion logic itself is covered in
 * tests/test_web_dashboard.py; this is the part only a browser enforces.
 */
import { chromium } from 'playwright';
const out = process.env.SHOT_DIR || '/tmp/fdplanner-shots';
await (await import('node:fs/promises')).mkdir(out, { recursive: true });
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
let fail = 0;
const check = (n, ok, d) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`); if (!ok) fail++; };

const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const p = await ctx.newPage();
await p.goto(process.env.APP_URL || 'http://127.0.0.1:8899/', { waitUntil: 'networkidle' });

check('discard is folded shut by default', !(await p.$eval('.discard', d => d.open)));
check('confirm button hidden while folded', !(await p.isVisible('.discard .danger')));

// Opening it is user-initiated, so the height change is expected — but measure
// that it does not push the cart around above it.
const cartTopBefore = await p.evaluate(() => Math.round(document.querySelector('.cart-wrap').getBoundingClientRect().top));
await p.click('.discard > summary');
await p.waitForTimeout(200);
const cartTopAfter = await p.evaluate(() => Math.round(document.querySelector('.cart-wrap').getBoundingClientRect().top));
check('opening discard pushes content down, as a disclosure should', cartTopAfter > cartTopBefore, `${cartTopBefore} -> ${cartTopAfter}`);
await p.screenshot({ path: `${out}/discard-open.png` });

const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
check('no overflow with discard open', overflow === 0, `${overflow}px`);

// The checkbox is `required`, so the browser must refuse the bare submit.
await p.click('.discard .danger');
await p.waitForTimeout(400);
check('submitting without the checkbox is refused', p.url().endsWith('/') && (await p.$('.discard')) !== null);

await p.check('.discard input[name=confirm]');
await Promise.all([p.waitForNavigation({ waitUntil: 'load' }), p.click('.discard .danger')]);
const after = await p.evaluate(() => ({
  empty: document.body.textContent.includes('No draft plan yet'),
  rows: document.querySelectorAll('#cart-body tr[id]').length,
  discard: document.querySelectorAll('.discard').length,
  requests: [...document.querySelectorAll('.request-list li > span:first-child')].map(s => s.textContent.trim()),
}));
check('cart is gone', after.empty && after.rows === 0);
check('discard control gone with nothing to discard', after.discard === 0);
check('shopping-list asks survived', after.requests.length >= 3, after.requests.join(', '));
await p.screenshot({ path: `${out}/discard-after.png` });

await browser.close();
console.log(fail === 0 ? '\nDiscard flow works.' : `\n${fail} FAILED.`);
process.exit(fail ? 1 : 0);
