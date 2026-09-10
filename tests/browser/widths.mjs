import { chromium } from 'playwright';
const out = process.env.SHOT_DIR || '/tmp/fdplanner-shots';
await (await import('node:fs/promises')).mkdir(out, { recursive: true });
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
let bad = 0;
// 320 = iPhone SE 1st gen, the narrowest phone still in the wild.
for (const w of [320, 360, 390, 430, 600, 768, 799, 800, 1024, 1280]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:8899/', { waitUntil: 'networkidle' });
  const m = await p.evaluate(() => {
    const de = document.documentElement;
    const off = [...document.querySelectorAll('*')]
      .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.right > de.clientWidth + 1; })
      .map(el => `${el.tagName.toLowerCase()}.${(el.className||'').toString().trim().split(/\s+/)[0]}`);
    return { over: de.scrollWidth - de.clientWidth, off: [...new Set(off)].slice(0, 5),
             mode: getComputedStyle(document.querySelector('table.cart')).display };
  });
  const ok = m.over === 0;
  if (!ok) bad++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${String(w).padStart(4)}px  overflow=${m.over}  table=${m.mode}  ${m.off.join(', ')}`);
  if ([390, 768, 1280].includes(w)) await p.screenshot({ path: `${out}/final-${w}.png`, fullPage: w !== 390 });
  await ctx.close();
}
await browser.close();
console.log(bad === 0 ? '\nNo horizontal overflow at any width.' : `\n${bad} width(s) overflow.`);
process.exit(bad ? 1 : 0);
