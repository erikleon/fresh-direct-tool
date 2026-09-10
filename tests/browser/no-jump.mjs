import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
let failures = 0;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failures++;
};

// ---------- 1. JS path: toggling must not move the page at all ----------
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:8899/', { waitUntil: 'networkidle' });

const ids = await p.$$eval('#cart-body tr[id]', rows => rows.map(r => r.id));
const target = ids[8];               // deep enough that a scroll reset is obvious
await p.evaluate(id => document.getElementById(id).scrollIntoView({ block: 'center' }), target);
await p.waitForTimeout(150);

const before = await p.evaluate(id => {
  const r = document.getElementById(id).getBoundingClientRect();
  const doc = document.documentElement.scrollHeight;
  return { scrollY: Math.round(scrollY), top: Math.round(r.top), h: Math.round(r.height), doc };
}, target);

await p.click(`#${target} .toggle`);
await p.waitForFunction(id => !document.getElementById(id).classList.contains('is-busy'), target, { timeout: 5000 });
await p.waitForTimeout(400);   // let the budget bar transition settle

const after = await p.evaluate(id => {
  const el = document.getElementById(id);
  const r = el.getBoundingClientRect();
  return {
    scrollY: Math.round(scrollY), top: Math.round(r.top), h: Math.round(r.height),
    doc: document.documentElement.scrollHeight,
    dropped: el.classList.contains('dropped'),
    state: el.querySelector('.toggle').dataset.state,
  };
}, target);

check('scroll position unchanged', before.scrollY === after.scrollY, `${before.scrollY} -> ${after.scrollY}`);
check('row stays at same viewport y', Math.abs(before.top - after.top) <= 1, `${before.top} -> ${after.top}`);
check('row height unchanged', before.h === after.h, `${before.h}px -> ${after.h}px`);
check('document height unchanged', before.doc === after.doc, `${before.doc} -> ${after.doc}`);
check('row actually toggled off', after.dropped && after.state === 'out');
check('no navigation occurred', p.url() === 'http://127.0.0.1:8899/', p.url());

// toggle back on
await p.click(`#${target} .toggle`);
await p.waitForFunction(id => !document.getElementById(id).classList.contains('is-busy'), target, { timeout: 5000 });
const back = await p.evaluate(id => {
  const el = document.getElementById(id);
  return { scrollY: Math.round(scrollY), top: Math.round(el.getBoundingClientRect().top), h: Math.round(el.getBoundingClientRect().height), state: el.querySelector('.toggle').dataset.state };
}, target);
check('toggling back does not move page', back.scrollY === before.scrollY && Math.abs(back.top - before.top) <= 1, `y ${back.scrollY}, top ${back.top}`);
check('height identical in both states', back.h === before.h, `${before.h} / ${after.h} / ${back.h}`);
check('row restored to included', back.state === 'in');

// ---------- 2. worst case: crossing the budget cap ----------
// The label goes "· under budget" -> "· over by $X" and sits ABOVE the cart, so
// any rewrap there shifts every row below it.
const budgetBefore = await p.evaluate(() => Math.round(document.getElementById('budget-region').getBoundingClientRect().height));

// Type the quantity FIRST: playwright auto-scrolls to reach the field, and that
// scroll is not what we are measuring. Baseline is taken after it settles.
const qtyTarget = ids[7];
const anchorId = ids[7];
await p.fill(`#${qtyTarget} .qty-form input`, '40');
await p.waitForTimeout(200);
const anchorTopBefore = await p.evaluate(id => Math.round(document.getElementById(id).getBoundingClientRect().top), anchorId);
const scrollBefore = await p.evaluate(() => Math.round(scrollY));

// Only now does the edit apply and the label flip to "over by".
await p.dispatchEvent(`#${qtyTarget} .qty-form input`, 'change');
await p.waitForTimeout(900);
check('scroll held while cap was crossed', scrollBefore === await p.evaluate(() => Math.round(scrollY)), `y ${scrollBefore}`);

const budgetAfter = await p.evaluate(id => ({
  h: Math.round(document.getElementById('budget-region').getBoundingClientRect().height),
  over: !!document.querySelector('#budget-region .fill.over'),
  label: document.querySelector('.budget-label').textContent.replace(/\s+/g, ' ').trim(),
  anchorTop: Math.round(document.getElementById(id).getBoundingClientRect().top),
}), anchorId);

check('cap was actually crossed', budgetAfter.over, budgetAfter.label);
check('budget block height stable under/over cap', budgetBefore === budgetAfter.h, `${budgetBefore} -> ${budgetAfter.h}px`);
check('cart does not shift when label rewrites', Math.abs(anchorTopBefore - budgetAfter.anchorTop) <= 1, `row top ${anchorTopBefore} -> ${budgetAfter.anchorTop}`);

// ---------- 3. js-optional buttons gone, change-to-submit live ----------
const ui = await p.evaluate(() => ({
  jsLive: document.documentElement.classList.contains('js-live'),
  visibleOptional: [...document.querySelectorAll('.js-optional')].filter(b => b.offsetParent !== null).length,
  optionalCount: document.querySelectorAll('.js-optional').length,
}));
check('js-optional buttons hidden when JS is live', ui.jsLive && ui.visibleOptional === 0, `${ui.optionalCount} in DOM, ${ui.visibleOptional} visible`);

// quantity applies on change alone
const qtyRow = (await p.$$eval('#cart-body tr[id]', r => r.map(x => x.id)))[2];
await p.selectOption(`#${qtyRow} .swap-form select`, { index: 1 }).catch(() => {});
await p.waitForTimeout(700);
const swapped = await p.evaluate(id => document.querySelector(`#${id} .prod`).textContent.replace(/\s+/g,' ').trim(), qtyRow);
check('select applies on change with no confirm tap', /Store Brand/.test(swapped), swapped.slice(0, 60));

// ---------- 4. no-JS path: redirect must carry the row anchor ----------
const noJs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
const n = await noJs.newPage();
await n.goto('http://127.0.0.1:8899/', { waitUntil: 'domcontentloaded' });
const nIds = await n.$$eval('#cart-body tr[id]', rows => rows.map(r => r.id));
const nTarget = nIds[9];
const optionalVisible = await n.$$eval('.js-optional', b => b.filter(x => x.offsetParent !== null).length);
check('confirm buttons still present without JS', optionalVisible > 0, `${optionalVisible} visible`);

await Promise.all([n.waitForNavigation({ waitUntil: 'load' }), n.click(`#${nTarget} .toggle`)]);
check('no-JS redirect lands on the edited row', n.url().endsWith(`#${nTarget}`), n.url());
const rowTop = await n.evaluate(id => Math.round(document.getElementById(id).getBoundingClientRect().top), nTarget);
check('no-JS row is on screen after reload', rowTop >= 0 && rowTop < 844, `row top ${rowTop}px`);

await browser.close();
console.log(failures === 0 ? '\nAll no-jump checks passed.' : `\n${failures} check(s) FAILED.`);
process.exit(failures === 0 ? 0 : 1);
