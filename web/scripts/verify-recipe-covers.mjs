// 菜谱配图的浏览器回归：用预置会话（不调用模型），检查有图的菜在「自己的列表项」里显示封面（左图右文）、无图/库外菜不配图、点击图片打开菜谱抽屉。
// 前置：本机已启动 langgraph dev(:2024) 与前端(:3000)。用法（web 目录）：node scripts/verify-recipe-covers.mjs [截图目录]
import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';
import { randomUUID } from 'node:crypto';

const shots = process.argv[2] ?? '.';
const api = 'http://127.0.0.1:2024';
const client = new Client({ apiUrl: api });
// 先从接口确认测试用的菜：两道有图、一道无图
const pick = async names => (await (await fetch(`${api}/cooker/covers?${names.map(n => 'names=' + encodeURIComponent(n)).join('&')}`)).json()).covers;
const withImg = ['清蒸鲈鱼', '油焖大虾'];
const covers = await pick([...withImg, '凉拌黄瓜']);
const [shrimp] = await pick(['干煎阿根廷红虾']);  // 用于「列表项第一个菜没图、后面的菜有图」
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  const t = await client.threads.create({ graphId: 'cooker' }); threadId = t.thread_id;
  const answer = `推荐几道：\n- 【清蒸鲈鱼】鲜嫩\n- 【油焖大虾】下饭\n- 【凉拌黄瓜】清爽\n- 【不存在的菜】测试\n- 【凉拌黄瓜】也可以配【干煎阿根廷红虾】`;
  await client.threads.updateState(threadId, { values: { messages: [
    { type: 'human', id: randomUUID(), content: '图片回归测试' }, { type: 'ai', id: randomUUID(), content: answer }] }, asNode: 'verify' });  // 作为 verify 的输出写入：图视为已结束，不会停在待执行节点
  await page.goto(`http://localhost:3000/?threadId=${threadId}`);
  const thumbs = page.getByTestId('inline-image');
  await expect(thumbs).toHaveCount(covers.length + 1, { timeout: 60000 });  // 只有接口返回有图的菜（+ 红虾）
  for (const c of covers) {
    const item = page.locator('li.cooker-media', { hasText: c.name });  // 图片和菜名在同一个列表项里
    await expect(item.getByRole('img', { name: c.name })).toBeVisible();
    await expect.poll(() => item.getByRole('img', { name: c.name }).evaluate(el => el.naturalWidth)).toBeGreaterThan(0);
  }
  await expect(page.locator('li', { hasText: /^凉拌黄瓜\s*清爽$/ }).getByTestId('inline-image')).toHaveCount(0);
  // 第一个菜没图：这一项不能用左图右文（否则红虾的图看起来像凉拌黄瓜的），红虾小图插在红虾名称前
  const mixed = page.locator('li', { hasText: '也可以配' });
  await expect(mixed).not.toHaveClass(/cooker-media(\s|$)/);
  await expect(mixed.getByRole('img', { name: shrimp.name })).toBeVisible();
  await page.locator('.markdown-content').first().screenshot({ path: `${shots}/recipe-covers.png` });
  await page.getByRole('button', { name: `查看「${covers[0].name}」做法` }).click();
  await expect(page.getByTestId('recipe-sheet')).toBeVisible({ timeout: 30000 });
  await page.screenshot({ path: `${shots}/recipe-covers-sheet.png` });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log(`OK 封面 ${covers.map(c => c.name).join('、')} 显示在各自列表项；无图与库外菜未配图；点击图片打开抽屉`);
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
