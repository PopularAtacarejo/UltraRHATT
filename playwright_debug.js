const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE LOG', msg.type(), msg.text()));
  page.on('pageerror', error => console.log('PAGE ERROR', error.stack));
  try {
    await page.goto('http://localhost:8007/funcionarios-dashboard.html', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    const canvasIds = await page.$$eval('canvas', els => els.map(el => el.id));
    console.log('Canvas IDs', canvasIds);
    const charts = await page.evaluate(() => ({
      company: window.chartCompany ? 'ok' : 'missing',
      parents: window.chartParents ? 'ok' : 'missing',
      warnings: window.chartWarnings ? 'ok' : 'missing',
      leaders: window.chartLeaders ? 'ok' : 'missing'
    }));
    console.log('Charts', charts);
  } catch (error) {
    console.error('Navigation error', error);
  } finally {
    await browser.close();
  }
})();
