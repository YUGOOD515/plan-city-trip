// 无头 Chromium 截图自检：node shot.js <html> [输出前缀]
// 需在 /data 下运行或能 require 到 playwright。
const path = require('path');
const { chromium } = require('playwright');
const file = path.resolve(process.argv[2] || 'sample/out.html');
const pre = process.argv[3] || file.replace(/\.html$/, '');
(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/local/bin/chromium',
    args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errs = [];
  const idle = async (page, ms = 45000) => {
    await page.waitForFunction(() => window.__mapIdle === true, null, { timeout: ms }).catch(() => errs.push('等待地图 idle 超时'));
    await page.waitForTimeout(600);
  };
  const reset = page => page.evaluate(() => { window.__mapIdle = false; });
  async function open(opts) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
    page.on('pageerror', e => errs.push('pageerror: ' + e.message));
    await page.goto('file://' + file);
    await idle(page);
    return { ctx, page };
  }
  // 1 桌面浅色
  let { ctx, page } = await open({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  await page.screenshot({ path: pre + '-1-desktop.png', fullPage: false });
  // 2 点时间线站点 → flyTo + 高亮
  await reset(page);
  const stops = await page.$$('.stp');
  if (stops.length > 3) await stops[3].click();
  await page.waitForTimeout(1800); await idle(page);
  const hot = await page.evaluate(() => ({ tl: !!document.querySelector('.stp.hot'), mk: !!document.querySelector('.mk.hot'), z: window.__trip.map && window.__trip.map.getZoom() }));
  await page.locator('#stage').screenshot({ path: pre + '-2-click-stop.png' });
  // 3 第 2 天 + 点一段交通
  await reset(page);
  const dayBtns = await page.$$('#days button');
  if (dayBtns.length > 2) await dayBtns[2].click();
  await page.waitForTimeout(1200); await idle(page);
  await page.locator('#stage').screenshot({ path: pre + '-3-day2.png' });
  // 4 全部
  await reset(page);
  await page.click('#days button[data-d="-1"]');
  await page.waitForTimeout(1200); await idle(page);
  await page.locator('#stage').screenshot({ path: pre + '-4-all.png' });
  // 5 切城市 + 4 人
  await reset(page);
  const cb = await page.$$('#citySeg button');
  if (cb.length > 1) await cb[1].click();
  await page.click('#paxSeg button[data-p="4"]');
  await page.waitForTimeout(1200); await idle(page);
  await page.locator('#stage').screenshot({ path: pre + '-5-city2-pax4.png' });
  await page.screenshot({ path: pre + '-6-fullpage.png', fullPage: true });
  const stats = await page.evaluate(() => ({ markers: document.querySelectorAll('.mk').length, legs: (()=>{try{return window.__trip.map.getStyle().sources.legs.data.features.length}catch(e){return -1}})(), pax: document.querySelector('#paxSeg [aria-pressed=true]').textContent }));
  await page.close(); await ctx.close();
  // 7 手机 375
  ({ ctx, page } = await open({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }));
  await page.screenshot({ path: pre + '-7-mobile.png', fullPage: true });
  await page.close(); await ctx.close();
  // 8 深色
  ({ ctx, page } = await open({ viewport: { width: 1280, height: 900 }, colorScheme: 'dark' }));
  await page.screenshot({ path: pre + '-8-dark.png' });
  await page.close(); await ctx.close();
  await browser.close();
  console.log(JSON.stringify({ hot, stats, errors: errs }, null, 1));
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
