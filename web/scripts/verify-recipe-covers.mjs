// 菜品图片条的浏览器回归：用预置会话（不调用模型），检查有图的菜显示封面、无图/库外菜不显示、点击打开菜谱抽屉。
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
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  const t = await client.threads.create({ graphId: 'cooker' }); threadId = t.thread_id;
  const answer = `推荐几道：\n- 【清蒸鲈鱼】鲜嫩\n- 【油焖大虾】下饭\n- 【凉拌黄瓜】清爽\n- 【不存在的菜】测试`;
  await client.threads.updateState(threadId, { values: { messages: [
    { type: 'human', id: randomUUID(), content: '图片回归测试' }, { type: 'ai', id: randomUUID(), content: answer }] }, asNode: 'verify' });  // 作为 verify 的输出写入：图视为已结束，不会停在待执行节点
  await page.goto(`http://localhost:3000/?threadId=${threadId}`);
  const strip = page.getByTestId('recipe-covers');
  await expect(strip).toBeVisible({ timeout: 60000 });
  const cards = strip.getByRole('button');
  await expect(cards).toHaveCount(covers.length);  // 只有接口返回有图的菜
  for (const c of covers) {
    const img = strip.getByRole('img', { name: c.name });
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate(el => el.naturalWidth)).toBeGreaterThan(0);  // 图片真的加载成功
  }
  await strip.screenshot({ path: `${shots}/recipe-covers.png` });
  await cards.first().click();
  await expect(page.getByTestId('recipe-sheet')).toBeVisible({ timeout: 30000 });
  await page.screenshot({ path: `${shots}/recipe-covers-sheet.png` });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log(`OK 封面 ${covers.map(c => c.name).join('、')}；无图与库外菜未显示；点击打开抽屉`);
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
