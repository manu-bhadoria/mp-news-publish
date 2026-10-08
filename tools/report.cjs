// PDF report of one portal story: every site's link, and that site's home page with the story outlined.
//   node tools/report.cjs <story-id> [out.pdf]     env: PORTAL_URL, BUILD_KEY, PW (playwright path), CHROME (optional)
const fs = require('fs'), path = require('path'), os = require('os');
const { chromium } = require(process.env.PW || 'playwright');
const PORTAL = (process.env.PORTAL_URL || 'https://mp-news-dashboard.nihilycho.workers.dev').replace(/\/$/, '');
const [sid, outArg] = process.argv.slice(2);
const OUT = outArg || `${sid}.pdf`;
const PAR = +(process.env.PAR || 8);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(p) {
  for (let i = 0; i < 4; i++) {
    try { const r = await fetch(PORTAL + p, { headers: { 'X-Build-Key': process.env.BUILD_KEY || '', 'User-Agent': 'mp-news-report' } }); if (r.ok) return r.json(); throw new Error(r.status); }
    catch (e) { if (i === 3) throw e; await new Promise(r => setTimeout(r, 3000)); }
  }
}

// find the story on the page, bring it to the middle of the screen and outline it
const MARK = sid => {
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 20 && r.height > 8 && cs.visibility !== 'hidden' && el.offsetParent !== null; };
  const as = [...document.querySelectorAll(`a[href*="/khabar/${sid}/"]`)].filter(vis);
  if (!as.length) return false;
  const area = a => { const r = a.getBoundingClientRect(); return r.width * r.height; };
  const a = as.sort((x, y) => (y.querySelector('img') ? 1 : 0) - (x.querySelector('img') ? 1 : 0) || area(y) - area(x) || x.getBoundingClientRect().top - y.getBoundingClientRect().top)[0];
  let box = a.closest('article, li, figure, [class*="card"], [class*="lead"], [class*="story"], [class*="item"]') || a;
  const r0 = box.getBoundingClientRect();
  if (r0.height > innerHeight * 0.85 || r0.width > innerWidth * 0.95) box = a;      // a whole column: outline only the story
  scrollTo(0, 0);
  const R = box.getBoundingClientRect();                 // photo and headline together: every link to the story next to it
  let r = { left: R.left, top: R.top, right: R.right, bottom: R.bottom };
  for (const x of as) {
    const q = x.getBoundingClientRect();
    if (q.right > r.left - 40 && q.left < r.right + 40 && q.top < r.bottom + 260 && q.bottom > r.top - 260)
      r = { left: Math.min(r.left, q.left), top: Math.min(r.top, q.top), right: Math.max(r.right, q.right), bottom: Math.max(r.bottom, q.bottom) };
  }
  if (r.bottom - r.top > innerHeight - 60) r.bottom = r.top + innerHeight - 60;
  r.width = r.right - r.left; r.height = r.bottom - r.top;
  let head = 0;                                          // a fixed or sticky header covers the top of the screen
  for (const el of document.elementsFromPoint(innerWidth / 2, 5).concat(document.elementsFromPoint(40, 5))) {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'sticky') head = Math.max(head, el.getBoundingClientRect().bottom);
  }
  head = Math.min(head, innerHeight / 3);
  const atTop = r.top > 0 && r.bottom < innerHeight - 10;     // already on the first screen: keep the masthead in the picture
  if (!atTop) box.scrollIntoView({ block: 'center', inline: 'nearest' });
  if (!atTop) { const n = box.getBoundingClientRect(), d0 = R.top - n.top; r.top -= d0; r.bottom -= d0; }
  const want = Math.max(head + 50, (innerHeight - (r.bottom - r.top)) / 2);
  const before = box.getBoundingClientRect().top;
  if (!atTop) scrollBy(0, r.top - want);
  const d = before - box.getBoundingClientRect().top; r.top -= d; r.bottom -= d;
  if (r.bottom > innerHeight - 8) r.bottom = innerHeight - 8;
  r.width = r.right - r.left; r.height = r.bottom - r.top;
  const o = document.createElement('div');
  o.style.cssText = `position:fixed;left:${r.left - 8}px;top:${r.top - 8}px;width:${r.width + 16}px;height:${r.height + 16}px;border:5px solid #e0162b;border-radius:10px;box-shadow:0 0 0 9999px rgba(0,0,0,.28);z-index:2147483647;pointer-events:none`;
  const t = document.createElement('div');
  t.textContent = 'यह खबर';
  t.style.cssText = `position:fixed;left:${Math.max(4, r.left - 8)}px;top:${Math.max(4, r.top - 40)}px;background:#e0162b;color:#fff;font:700 18px/1 sans-serif;padding:8px 12px;border-radius:8px;z-index:2147483647`;
  document.body.append(o, t);
  return atTop ? { top: 0, bottom: Math.max(r.bottom + 14, 560) } : { top: Math.max(0, r.top - 50), bottom: r.bottom + 12 };
};

async function shoot(ctx, x, dir, i) {
  const p = await ctx.newPage();
  const file = path.join(dir, `${String(i).padStart(3, '0')}.jpg`);
  let where = 'home';
  try {
    const home = x.url.replace(/\/khabar\/.*$/, '/');
    await p.goto(home, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await p.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
    await p.addStyleTag({ content: '*{animation:none!important;transition:none!important;scroll-behavior:auto!important} [class*="cookie"],[id*="cookie"]{display:none!important}' }).catch(() => {});
    let ok = await p.evaluate(MARK, sid).catch(() => false), clip = null;
    if (ok) { const top = Math.max(0, Math.min(ok.top, 800 - 560)); clip = { x: 0, y: top, width: 1280, height: Math.min(800 - top, Math.max(560, ok.bottom - top)) }; }
    if (!ok) {                                          // not on the home page's visible part: the story page itself
      where = 'story';
      await p.goto(x.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await p.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
    }
    await p.waitForTimeout(500);
    await p.screenshot({ path: file, type: 'jpeg', quality: 46, ...(clip ? { clip } : { clip: { x: 0, y: 0, width: 1280, height: 640 } }) });
  } catch (e) { where = 'error'; }
  await p.close();
  return { ...x, file: where === 'error' ? null : file, where };
}

(async () => {
  const { title, links } = await api(`/api/build/links/${sid}`);
  const live = links.filter(l => l.status === 'live').slice(0, +process.env.LIMIT || undefined);
  if (!live.length) { console.log('no live links'); process.exit(0); }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rep-'));
  const b = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 0.72, locale: 'hi-IN' });
  const res = new Array(live.length); let next = 0;
  await Promise.all(Array.from({ length: PAR }, async () => { while (next < live.length) { const i = next++; res[i] = await shoot(ctx, live[i], dir, i); } }));
  await ctx.close();
  const onHome = res.filter(r => r.where === 'home').length;
  const stamp = new Date().toLocaleString('hi-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const rows = res.map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.name)}</td><td><a href="${esc(r.url)}">${esc(r.url.replace(/^https?:\/\//, ''))}</a></td><td>${r.where === 'home' ? 'होम पेज पर' : r.where === 'story' ? 'खबर का पेज' : '—'}</td></tr>`).join('');
  const cards = res.map((r, i) => `<section class="card"><div class="hd"><b>${i + 1}. ${esc(r.name)}</b><span class="chip ${r.where}">${r.where === 'home' ? 'होम पेज पर दिख रही है' : r.where === 'story' ? 'होम पेज पर नहीं — खबर का पेज' : 'स्क्रीनशॉट नहीं बना'}</span></div><a href="${esc(r.url)}">${esc(r.url)}</a>${r.file ? `<img src="file://${r.file}">` : ''}</section>`).join('');
  const html = `<!doctype html><html lang="hi"><meta charset="utf-8"><link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;700&display=block" rel="stylesheet"><style>
@page{size:A4;margin:12mm}body{font-family:"Noto Sans Devanagari",sans-serif;color:#1b1d2a;font-size:11pt;margin:0}
h1{font-size:19pt;line-height:1.3;margin:0 0 6pt}.meta{color:#555;margin:0 0 10pt}.sum{display:flex;gap:18pt;margin:0 0 12pt}.sum div{border:1px solid #ddd;border-radius:6pt;padding:6pt 10pt}.sum b{font-size:16pt;display:block}
table{width:100%;border-collapse:collapse;font-size:9pt}td{border-bottom:1px solid #e5e5e5;padding:3pt 4pt;vertical-align:top}td:first-child{color:#888;width:22pt}td:nth-child(3) a{word-break:break-all}
a{color:#2f3a8f;text-decoration:none}.list{page-break-after:always}
.card{page-break-inside:avoid;margin:0 0 12pt}.card .hd{display:flex;justify-content:space-between;gap:8pt;align-items:baseline}.card b{font-size:12.5pt}.card a{font-size:9pt;word-break:break-all;display:block;margin:2pt 0 5pt}
.card img{width:100%;border:1px solid #ccc;border-radius:4pt;display:block}.chip{font-size:8.5pt;padding:2pt 7pt;border-radius:20pt;background:#e7f6ec;color:#17663a;white-space:nowrap}.chip.story{background:#fff4dc;color:#7a5200}.chip.error{background:#fde8e8;color:#9b1c1c}
</style><body><div class="list"><h1>${esc(title)}</h1><p class="meta">रिपोर्ट: ${esc(stamp)} · खबर ${esc(sid)}</p>
<div class="sum"><div><b>${res.length}</b>साइटों पर लाइव</div><div><b>${onHome}</b>होम पेज पर दिख रही है</div><div><b>${res.length - onHome}</b>खबर के पेज पर</div></div>
<table>${rows}</table></div>${cards}</body></html>`;
  const hf = path.join(dir, 'report.html'); fs.writeFileSync(hf, html);
  const pg = await b.newPage();
  await pg.goto('file://' + hf, { waitUntil: 'networkidle', timeout: 120000 });
  await pg.evaluate(() => document.fonts.ready);
  await pg.pdf({ path: OUT, format: 'A4', printBackground: true, margin: { top: '12mm', bottom: '12mm', left: '12mm', right: '12mm' } });
  await b.close();
  fs.rmSync(dir, { recursive: true, force: true });
  console.log(`${OUT}: ${res.length} sites, ${onHome} on the home page, ${(fs.statSync(OUT).size / 1e6).toFixed(1)} MB`);
})().catch(e => { console.error(e); process.exit(1); });
