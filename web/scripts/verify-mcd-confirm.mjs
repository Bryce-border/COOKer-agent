// 麦当劳确认卡的浏览器回归：真实模型 + 真实麦当劳服务（只读查询会执行），确认卡上只点「取消」，不会产生任何扣费。
// 前置：本机已启动 langgraph dev(:2024) 与前端(:3000)，并配置 config/mcd-mcp.local.env。
// 用法（web 目录）：node scripts/verify-mcd-confirm.mjs [截图目录]
import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';

const shots = process.argv[2] ?? '.';
const client = new Client({ apiUrl: 'http://127.0.0.1:2024' });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  await page.goto('http://localhost:3000/');
  const box = page.getByPlaceholder('问我今天吃什么、某道菜怎么做、手头食材能做什么…');
  await box.fill('帮我把麦当劳麦麦省能领的券一键都领了');
  await box.press('Enter');
  const card = page.getByTestId('mcd-confirm');
  await expect(card).toBeVisible({ timeout: 180000 });
  await expect(card.getByText('确认一键领券')).toBeVisible();
  threadId = new URL(page.url()).searchParams.get('threadId');
  await card.screenshot({ path: `${shots}/mcd-confirm-card.png` });

  await card.getByRole('button', { name: '取消' }).click();
  await expect(card).toHaveCount(0, { timeout: 120000 });
  // 卡片在提交恢复时就会消失；等这一轮跑完（线程回到 idle）再读状态
  for (let i = 0; i < 120 && (await client.threads.get(threadId)).status === 'busy'; i++) await page.waitForTimeout(1000);
  const state = await client.threads.getState(threadId);
  const tool = state.values.messages.filter(m => m.type === 'tool' && m.name === 'auto-bind-coupons').at(-1);
  const status = JSON.parse(tool.content).status;
  if (status !== 'CANCELLED') throw new Error(`期望 CANCELLED，实际 ${status}`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${shots}/mcd-after-cancel.png`, fullPage: false });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log('OK 确认卡出现 → 点取消 → 工具返回 CANCELLED，未执行领券');
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
