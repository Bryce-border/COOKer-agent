import { chromium, expect } from '@playwright/test';
import { Client } from '@langchain/langgraph-sdk';
import { randomUUID } from 'node:crypto';
const client = new Client({apiUrl:'http://127.0.0.1:2024'});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const ids=[];
async function fixture(title,answer){
 const t=await client.threads.create({graphId:'cooker'});ids.push(t.thread_id);
 await client.threads.updateState(t.thread_id,{values:{messages:[{type:'human',id:randomUUID(),content:title},{type:'ai',id:randomUUID(),content:answer}]},asNode:'agent'});
 return t.thread_id;
}
try {
 const a=await fixture('回归测试甲-'+Date.now(),'甲的历史正文');
 const b=await fixture('回归测试乙-'+Date.now(),'乙的历史正文');
 const url=id=>`http://localhost:3000/?threadId=${id}&chatHistoryOpen=true`;
 await page.goto(url(a));await expect(page.getByText('甲的历史正文',{exact:true})).toBeVisible({timeout:30000});
 await page.reload();await expect(page.getByText('甲的历史正文',{exact:true})).toBeVisible();
 let failures=0;
 await page.route(`**/threads/${a}/history`,r=>{failures++;return r.fulfill({status:503,contentType:'application/json',body:'{"detail":"test transient outage"}'});});
 await page.reload();await expect(page.getByRole('button',{name:'重新加载对话'})).toBeVisible({timeout:30000});
 await page.unroute(`**/threads/${a}/history`);
 await page.getByRole('button',{name:'重新加载对话'}).click();await expect(page.getByText('甲的历史正文',{exact:true})).toBeVisible();
 // Switch with a delayed response for the previous thread.
 await page.route(`**/threads/${b}/history`,async r=>{const response=await r.fetch();await new Promise(resolve=>setTimeout(resolve,1200));await r.fulfill({response});});
 await page.getByRole('button',{name:/^回归测试乙-/}).click();
 await page.getByRole('button',{name:/^回归测试甲-/}).click();
 await expect(page.getByText('甲的历史正文',{exact:true})).toBeVisible();
 await page.waitForTimeout(1500);await expect(page.getByText('乙的历史正文',{exact:true})).toHaveCount(0);
 await page.unroute(`**/threads/${b}/history`);
 // Delete cancellation and server error must retain the conversation.
 page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:/^删除对话：回归测试乙-/}).click();
 await expect(page.getByRole('button',{name:/^回归测试乙-/})).toBeVisible();
 await page.route(`**/threads/${b}`,r=>r.request().method()==='DELETE'?r.fulfill({status:503,body:'test'}):r.continue());
 page.once('dialog',d=>d.accept());await page.getByRole('button',{name:/^删除对话：回归测试乙-/}).click();
 await expect(page.getByText('删除失败，对话已保留，请稍后重试。')).toBeVisible({timeout:30000});
 await page.unroute(`**/threads/${b}`);
 page.once('dialog',d=>d.accept());await page.getByRole('button',{name:/^删除对话：回归测试乙-/}).click();
 await expect(page.getByRole('button',{name:/^回归测试乙-/})).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});await page.goto(url(a).replace('true','false'));
 await expect(page.getByText('甲的历史正文',{exact:true})).toBeVisible();
 await page.screenshot({path:'../.cache/conversation-mobile.png',fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
 if(overflow)throw new Error('mobile conversation overflow');
 await page.setViewportSize({width:1280,height:900});await page.goto(url(a));
 page.once('dialog',d=>d.accept());await page.getByRole('button',{name:/^删除对话：回归测试甲-/}).click();
 await expect(page).not.toHaveURL(new RegExp(a));
 await page.reload();await expect(page.getByRole('button',{name:/^回归测试甲-/})).toHaveCount(0);
 // A fresh stream must survive the new thread ID being written into the URL.
 await page.goto('http://localhost:3000/');
 const input=page.getByPlaceholder('问我今天吃什么、某道菜怎么做、手头食材能做什么…');
 await input.fill('你好，这是界面回归测试，请只回复“连接正常”');
 await page.getByRole('button',{name:'发送',exact:true}).click();
 await expect(page).toHaveURL(/threadId=/,{timeout:30000});
 const streamedId=new URL(page.url()).searchParams.get('threadId');if(streamedId)ids.push(streamedId);
 await expect(page.getByText('连接正常',{exact:true})).toBeVisible({timeout:90000});
 await page.reload();await expect(page.getByText('连接正常',{exact:true})).toBeVisible({timeout:30000});
 if(errors.length)throw new Error(JSON.stringify(errors));
 console.log(JSON.stringify({historyReload:true,historyFailureRequests:failures,retry:true,staleResponseIsolation:true,deletionCancelFailureSuccess:true,mobile:true,newStreamAndReload:true,pageErrors:errors.length}));
} finally {
 for(const id of ids)await client.threads.delete(id).catch(()=>{});
 await browser.close();
}

