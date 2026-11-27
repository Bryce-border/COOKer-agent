// 执行过程面板的浏览器回归：真实模型问一个做饭问题（会产生少量模型费用），检查回答下方出现汇总行、展开后有模型 / 工具 / 校验三类步骤。
// 前置：本机已启动 langgraph dev(:2024) 与前端(:3000)。用法（web 目录）：node scripts/verify-trace.mjs [截图目录]
import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';

const shots = process.argv[2] ?? '.';
const client = new Client({ apiUrl: 'http://127.0.0.1:2024' });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  await page.goto('http://localhost:3000/');
  const box = page.getByPlaceholder('问我今天吃什么、某道菜怎么做、手头食材能做什么…');
  await box.fill('可乐鸡翅怎么做？简单说');
  await box.press('Enter');
  await expect(page).toHaveURL(/threadId=/, { timeout: 60000 });
  threadId = new URL(page.url()).searchParams.get('threadId');
  const panel = page.getByTestId('trace-panel');
  await expect(panel).toBeVisible({ timeout: 180000 });
  await expect(panel).toContainText(/输入 [\d,]+ \/ 输出 [\d,]+ tokens/);
  await panel.getByRole('button').first().click();
  for (const step of ['🧠 模型', '查看菜谱', '✅ 回答校验']) await expect(panel).toContainText(step);
  await page.waitForTimeout(800);
  await panel.screenshot({ path: `${shots}/trace-panel.png` });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log('OK', (await panel.getByRole('button').first().innerText()).replaceAll('\n', ' '));
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
