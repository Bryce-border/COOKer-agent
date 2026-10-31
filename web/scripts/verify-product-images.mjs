// 商品图片条的浏览器回归（麦当劳、瑞幸工具结果里的商品图）。
// 默认：预置会话（不调用模型）——工具消息带 artifact.images；检查商品图出现在提到它的表格单元格 / 段落里、每件只配一次、未提到的不显示。
// --real：真实模型 + 真实瑞幸只读查询；--real-mcd：真实模型 + 麦当劳抽奖奖品（只读）。会产生模型费用，不下单、不抽奖。
// 前置：本机已启动 langgraph dev(:2024) 与前端(:3000)。用法（web 目录）：node scripts/verify-product-images.mjs [--real|--real-mcd] [截图目录]
import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';
import { randomUUID } from 'node:crypto';

const realMcd = process.argv.includes('--real-mcd');
const real = realMcd || process.argv.includes('--real');
const shots = process.argv.filter(a => !a.startsWith('--'))[2] ?? '.';
const client = new Client({ apiUrl: 'http://127.0.0.1:2024' });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  if (real) {
    await page.goto('http://localhost:3000/');
    const box = page.getByPlaceholder('问我今天吃什么、某道菜怎么做、手头食材能做什么…');
    await box.fill(realMcd ? '麦当劳现在的积分抽奖有哪些奖品？只看看，不抽' : '我在上海人民广场附近，瑞幸上海来福士广场店有生椰拿铁吗？先不下单，看看就好');
    await box.press('Enter');
  } else {
    // 2026-10-31 实测的瑞幸官方图床地址（生椰拿铁商品图）
    const pic = 'https://img06.luckincoffeecdn.com/group5/M00/B2/CF/Ct1qP2hrsF-AEzVYAAGXq_WH8UM393.png';
    const call = { id: 'call_' + randomUUID().slice(0, 8), name: 'searchProductForMcp', args: { deptId: 390217, query: '拿铁' }, type: 'tool_call' };
    const t = await client.threads.create({ graphId: 'cooker' }); threadId = t.thread_id;
    await client.threads.updateState(threadId, { values: { messages: [
      { type: 'human', id: randomUUID(), content: '商品图片回归测试' },
      { type: 'ai', id: randomUUID(), content: '', tool_calls: [call] },
      { type: 'tool', id: randomUUID(), tool_call_id: call.id, name: call.name, content: '{"code":0}',
        artifact: { images: [{ name: '生椰拿铁（原创）', url: pic }, { name: '橙C美式', url: pic + '?b' }, { name: '未被提到的商品', url: pic + '?x' }] } },
      // verify 重答时插入的隐藏校验消息：取图时不能把它当成新一轮的开始
      { type: 'human', id: 'do-not-render-verify-test', content: '【系统校验】…' },
      { type: 'ai', id: randomUUID(), content: [
        '这家店有**生椰拿铁**，默认大杯冰。', '',
        '| 商品 | 价格 |', '|---|---|', '| 橙C美式 | ¥19 |', '',
        '再说一次生椰拿铁。'].join('\n') }] }, asNode: 'verify' });
    await page.goto(`http://localhost:3000/?threadId=${threadId}`);
  }
  if (real) await expect(page).toHaveURL(/threadId=/, { timeout: 60000 });
  threadId ??= new URL(page.url()).searchParams.get('threadId');
  const thumbs = page.getByTestId('inline-image');
  await expect(thumbs.first()).toBeVisible({ timeout: 180000 });
  if (!real) {
    await expect(thumbs).toHaveCount(2);  // 生椰拿铁提到两次只配一张；未提到的商品不显示
    // 段落提到的商品：图片行紧跟在这一段下方（括号后缀省略也能匹配）
    const fig = page.locator('p:has-text("这家店有") + .cooker-figures').getByRole('img', { name: '生椰拿铁（原创）' });
    await expect(fig).toBeVisible();
    const cell = page.locator('td', { hasText: '橙C美式' }).getByRole('img', { name: '橙C美式' });
    await expect(cell).toBeVisible();
    // 页面里直接看得清：图片行 ≥ 140px、表格 ≥ 60px
    for (const [loc, min, what] of [[fig, 140, '段落图片'], [cell, 60, '表格图片']]) {
      const w = (await loc.boundingBox())?.width ?? 0;
      if (w < min) throw new Error(`${what}太小：${w}px`);
    }
  }
  await expect.poll(() => thumbs.first().evaluate(el => el.naturalWidth), { timeout: 30000 }).toBeGreaterThan(0);
  const names = await thumbs.evaluateAll(els => els.map(e => e.getAttribute('alt')));
  // 尺寸足够看清（行内 ≥ 48px），点击放大、Esc 关闭
  const box = await thumbs.first().boundingBox();
  if (!box || box.width < 48) throw new Error(`商品图太小：${box?.width}px`);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${shots}/product-images${realMcd ? '-real-mcd' : real ? '-real' : ''}.png` });
  await thumbs.first().click();
  const lightbox = page.getByTestId('image-lightbox');
  await expect(lightbox).toBeVisible();
  await page.screenshot({ path: `${shots}/product-images-zoom.png` });
  await page.keyboard.press('Escape');
  await expect(lightbox).toHaveCount(0);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${shots}/product-images${realMcd ? '-real-mcd' : real ? '-real' : ''}.png` });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log(`OK${real ? ' [real]' : ''} 商品图：${names.join('、')}，图片已加载`);
} catch (e) {
  // 失败时打印本会话的消息结构，便于判断是模型措辞问题还是前端取图逻辑问题
  if (threadId) {
    const msgs = (await client.threads.getState(threadId)).values.messages ?? [];
    for (const m of msgs) console.log(m.type, String(m.id).slice(0, 28), m.name ?? '', m.artifact ? `artifact images=${m.artifact.images?.length}` : '',
      m.type === 'ai' ? String(m.content).slice(0, 80).replaceAll('\n', ' ') : '');
  }
  throw e;
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
