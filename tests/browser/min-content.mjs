/* Catches the class of overflow that only shows up on iOS.
 *
 * A flex or grid child defaults to `min-width: auto`, which means it refuses to
 * shrink below its min-content width. For most elements that is harmless. For a
 * <select> it is not: a select's min-content width is its widest <option>, and
 * these options are whole product names. Chromium clamps a select's intrinsic
 * width and absorbs the problem; WebKit honours it, so the document grows past
 * the viewport and Safari zooms out to fit — which is what "there's still
 * scrollable space on mobile" looks like from the outside.
 *
 * widths.mjs cannot see this, because it only measures what Chromium renders.
 * This measures what each element *demands*, which is engine-independent.
 */
import { chromium } from 'playwright';

const URL = process.env.APP_URL || 'http://127.0.0.1:8899/';
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
let failures = 0;

for (const width of [320, 390, 430]) {
  const ctx = await browser.newContext({ viewport: { width, height: 844 }, deviceScaleFactor: 2, isMobile: true });
  const p = await ctx.newPage();
  await p.goto(URL, { waitUntil: 'networkidle' });

  const offenders = await p.evaluate(() => {
    const VIEW = document.documentElement.clientWidth;
    const bad = [];
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none') continue;
      // Out-of-flow boxes cannot widen an ancestor, whatever they contain.
      if (cs.position === 'absolute' || cs.position === 'fixed') continue;
      const parent = el.parentElement;
      if (!parent) continue;
      const pd = getComputedStyle(parent).display;
      const isFlexOrGridChild = /flex|grid/.test(pd);
      if (!isFlexOrGridChild) continue;
      // `auto` is the dangerous value: it floors the element at min-content.
      if (cs.minWidth !== 'auto') continue;

      // max-width would clamp the probe to the parent and hide the real
      // demand, so neutralise it while measuring and put it straight back.
      const prevWidth = el.style.width;
      const prevMax = el.style.maxWidth;
      el.style.width = 'min-content';
      el.style.maxWidth = 'none';
      const demand = el.getBoundingClientRect().width;
      el.style.width = prevWidth;
      el.style.maxWidth = prevMax;

      if (demand > VIEW) {
        bad.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().trim().split(/\s+/).slice(0, 2).join('.'),
          demand: Math.round(demand),
          viewport: VIEW,
          parentDisplay: pd,
        });
      }
    }
    return bad;
  });

  if (offenders.length === 0) {
    console.log(`PASS  ${String(width).padStart(3)}px  no flex/grid child floors wider than the viewport`);
  } else {
    failures += offenders.length;
    for (const o of offenders) {
      console.log(`FAIL  ${String(width).padStart(3)}px  <${o.tag}${o.cls ? '.' + o.cls : ''}> in a ${o.parentDisplay} parent cannot shrink below ${o.demand}px (viewport ${o.viewport}) — needs min-width: 0`);
    }
  }
  await ctx.close();
}

await browser.close();
console.log(failures === 0 ? '\nNothing demands more width than the viewport.' : `\n${failures} element(s) would overflow on a strict engine.`);
process.exit(failures === 0 ? 0 : 1);
