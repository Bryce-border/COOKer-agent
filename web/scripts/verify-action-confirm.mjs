// 外部服务确认卡的浏览器回归：真实模型 + 真实服务（只读查询会执行），确认卡上只点「取消」，不会产生订单或扣费。
// 前置：本机已启动 langgraph dev(:2024) 与前端(:3000)，并配置 config/*.local.env。
// 用法（web 目录）：node scripts/verify-action-confirm.mjs [mcd|luckin] [截图目录]
import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';

const SCENARIOS = {
  // 领券场景已不可用：2026-10-31 已真实领过，模型查到「都已领取」后正确地不再调用领券。
  // 改用新增地址：卡片不依赖账户状态、每次都会出现；只点「取消」，不会真的创建地址（手机号是测试号）
  mcd: { ask: '帮我新增一个麦当劳收货地址：上海市，人民大道200号，门牌1号楼101，联系人张三先生，手机13800000000',
         title: '确认新增收货地址', tool: 'delivery-create-address' },
  luckin: { ask: '我在上海人民广场附近，在瑞幸上海来福士广场店帮我点一杯默认规格的生椰拿铁，到店自取，直接下单',
            title: '确认瑞幸下单', tool: 'createOrder' },
};
const key = process.argv[2] ?? 'mcd';
const sc = SCENARIOS[key];
const shots = process.argv[3] ?? '.';
const client = new Client({ apiUrl: 'http://127.0.0.1:2024' });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
let threadId;
try {
  await page.goto('http://localhost:3000/');
  const box = page.getByPlaceholder('问我今天吃什么、某道菜怎么做、手头食材能做什么…');
  await box.fill(sc.ask);
  await box.press('Enter');
  await expect(page).toHaveURL(/threadId=/, { timeout: 60000 });
  threadId = new URL(page.url()).searchParams.get('threadId');
  const card = page.getByTestId('action-confirm');
  await expect(card).toBeVisible({ timeout: 180000 });
  await expect(card.getByText(sc.title)).toBeVisible();
  await card.screenshot({ path: `${shots}/${key}-confirm-card.png` });

  await card.getByRole('button', { name: '取消' }).click();
  await expect(card).toHaveCount(0, { timeout: 120000 });
  // 卡片在提交恢复时就会消失；等这一轮跑完（线程回到 idle）再读状态
  for (let i = 0; i < 120 && (await client.threads.get(threadId)).status === 'busy'; i++) await page.waitForTimeout(1000);
  const state = await client.threads.getState(threadId);
  const tool = state.values.messages.filter(m => m.type === 'tool' && m.name === sc.tool).at(-1);
  const status = JSON.parse(tool.content).status;
  if (status !== 'CANCELLED') throw new Error(`期望 CANCELLED，实际 ${status}`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${shots}/${key}-after-cancel.png` });
  if (errors.length) throw new Error('页面错误：' + errors.join(' | '));
  console.log(`OK [${key}] 确认卡「${sc.title}」出现 → 点取消 → ${sc.tool} 返回 CANCELLED，未执行`);
} catch (e) {
  // 失败时打印本会话的消息与中断，区分「模型没调写工具」「卡片没渲染」「页面报错」
  if (threadId) {
    const st = await client.threads.getState(threadId);
    for (const m of st.values.messages ?? [])
      console.log(m.type, m.name ?? '', (m.tool_calls ?? []).map(c => c.name).join(','),
        String(typeof m.content === 'string' ? m.content : JSON.stringify(m.content)).slice(0, 120).replaceAll('\n', ' '));
    console.log('interrupts:', JSON.stringify(st.tasks?.flatMap(t => t.interrupts ?? []).map(i => i.value?.title ?? i.value)));
  }
  console.log('页面错误：', errors);
  throw e;
} finally {
  if (threadId) await client.threads.delete(threadId).catch(() => {});
  await browser.close();
}
