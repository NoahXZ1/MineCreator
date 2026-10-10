// Deterministic browser fault injection against workbench_fixture.py only.
const {chromium}=require('C:/Users/23710/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),failures=[];
 async function check(name,test){
  const page=await browser.newPage({viewport:{width:1440,height:960}});
  try{await page.goto('http://127.0.0.1:8790');await page.waitForFunction(()=>Boolean(state.record?.design));await test(page);console.log('PASS:',name);}
  catch(error){failures.push(name);console.error('FAIL:',name,error.message);}
  finally{await page.close();}
 }
 try{
  await check('permanent task error stops polling and releases the composer',async page=>{
   await page.route('**/api/task?*',r=>r.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Request not found. Reload the conversation.'})}));
   const result=await page.evaluate(async()=>{state.active={id:'missing',operation:'chat',conversation_id:state.selected};state.busy=true;controls();await pollJob();return {active:state.active,busy:state.busy,feedback:$('feedback').textContent};});
   assert.equal(result.active,null);assert.equal(result.busy,false);assert.match(result.feedback,/Reload/);
  });
  await check('temporary disconnect keeps the same task and never resubmits',async page=>{
   let writes=0;page.on('request',r=>{if(r.method()==='POST')writes++;});
   await page.route('**/api/task?*',r=>r.abort('failed'));
   const result=await page.evaluate(async()=>{state.active={id:'existing',operation:'chat',conversation_id:state.selected};state.busy=true;await pollJob();clearTimeout(state.pollTimer);return {active:state.active.id,busy:state.busy};});
   assert.equal(result.active,'existing');assert.equal(result.busy,true);assert.equal(writes,0);
  });
  await check('interrupted construction displays Stopped after restart',async page=>{
   await page.evaluate(()=>{state.record.builds=[{id:'interrupted-build',status:'interrupted',created_at:'2099-01-01',message:'Interrupted'}];drawDesign(state.record);});
   assert.equal(await page.locator('#design-status').textContent(),'Stopped');
  });
  await check('failed image generation remains visible after reload',async page=>{
   await page.evaluate(()=>{state.record.designs.at(-1).images=[{id:'failed-image',status:'failed',error:'Image generation failed.'}];drawDesign(state.record);});
   assert.equal(await page.locator('#design-status').textContent(),'Needs attention');
   assert.match(await page.locator('#design-notice').textContent(),/Image generation failed/);
  });
  await check('description draft survives browsing another archive version',async page=>{
   await page.locator('#save-library').click();await page.locator('#confirm-save').click();
   await page.waitForFunction(()=>!document.getElementById('save-dialog').open);
   await page.locator('#open-library').click();await page.locator('#library-list button').first().click();
   await page.locator('#edit-description').click();await page.locator('#description-editor').fill('Unsaved lake description');
   await page.locator('#library-list button').first().click();await page.locator('#description-editor').waitFor();
   assert.equal(await page.locator('#description-editor').inputValue(),'Unsaved lake description');
  });
  await check('preview arriving after a conversation switch is discarded',async page=>{
   let release,requested;const started=new Promise(resolve=>requested=resolve);const gate=new Promise(resolve=>release=resolve);
   await page.route('**/api/preview/*/*/concept',async r=>{requested();await gate;await r.fulfill({path:'example/01-wooden-small-house/front-perspective.png',contentType:'image/png'});});
   await page.evaluate(()=>{state.record.design.image_id='slow';drawDesign(state.record);});await started;
   await page.evaluate(()=>{state.selected='another-conversation';state.record={id:state.selected,turns:[]};drawMessages(state.record);drawDesign(state.record);});
   release();await page.waitForResponse(r=>r.url().includes('/concept'));
   assert.equal(await page.locator('#design-preview .preview-image').count(),0);
   assert.match(await page.locator('#design-preview').textContent(),/No concept image/);
  });
  assert.deepEqual(failures,[],'Fault-injection failures');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
