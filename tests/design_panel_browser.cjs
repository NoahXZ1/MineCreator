// Uses only synthetic UI state and intercepted localhost responses; no model/game writes.
const {chromium}=require('C:/Users/23710/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8790');await page.waitForFunction(()=>Boolean(state.record?.design));
  assert.equal(await page.locator('#design-status').textContent(),'Ready to build');
  assert.equal(await page.locator('#preview-tabs').count(),0);
  assert.equal(await page.locator('.design #build-status,.design #edit-target-panel,.design #plan-details').count(),0);
  await page.locator('#plan-summary summary').click();await page.getByRole('button',{name:'View block preview',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('large-preview').naturalWidth>0);
  await page.locator('#close-preview').click();
  await page.route('**/api/preview/*/*/concept',route=>route.fulfill({path:'example/01-wooden-small-house/front-perspective.png',contentType:'image/png'}));
  await page.evaluate(()=>{
   const r=state.record;r.design.image_id='fixture-image';
   r.builds=[{id:'fixture-build',design_id:r.design.id,status:'completed',record_path:'fixture.json',origin:[36,67,-179],dimension:'minecraft:overworld',message:'Construction completed; 953 blocks verified.',created_at:'2026-10-09T12:00:00Z'}];
   r.edit_target=r.builds[0];drawMessages(r);drawDesign(r);controls();
  });
  await page.waitForFunction(()=>document.querySelector('#design-preview .preview-image')?.naturalWidth>0);
  assert.equal(await page.locator('#design-status').textContent(),'Completed');
  assert.match(await page.locator('#messages #build-location').textContent(),/36, 67, -179/);
  assert.equal(await page.locator('#messages #edit-target').isVisible(),true);
  assert.equal(await page.locator('.design-scroll').innerText(),'Completed');
  await page.evaluate(()=>{
   state.active={id:'fixture-job',conversation_id:state.selected,operation:'chat'};
   buildEvent({message:'Placing roof blocks...',data:{scope:'build',phase:'placing',current:5,total:12,origin:[40,64,-180],dimension:'minecraft:overworld'}},state.active);
  });
  assert.equal(await page.locator('#design-status').textContent(),'Building...');
  assert.equal(await page.locator('#messages #build-progress').getAttribute('value'),'5');
  assert.equal(await page.locator('#messages #build-message').textContent(),'Placing roof blocks...');
  await page.evaluate(()=>{
   state.active={id:'fixture-edit',conversation_id:state.selected,operation:'apply_edit'};
   editEvent({message:'Applying roof edit...',data:{scope:'edit',phase:'placing',current:3,total:9}},state.active);
  });
  assert.equal(await page.locator('#design-status').textContent(),'Editing...');
  assert.equal(await page.locator('#messages #edit-progress').getAttribute('value'),'3');
  await page.evaluate(()=>{
   state.active=null;state.busy=false;state.record.builds.pop();state.record.edits=[];
   state.record.edit_review={id:'fixture-review',change_count:12,counts:{added:0,removed:0,replaced:12}};
   drawMessages(state.record);drawDesign(state.record);controls();
  });
  assert.equal(await page.locator('#design-status').textContent(),'Awaiting confirmation');
  assert.equal(await page.locator('#messages #apply-edit').isEnabled(),true);
  let submitted=false;
  await page.route('**/api/apply-edit',route=>{submitted=true;return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Offline check: no game writes.'})});});
  await page.locator('#apply-edit').click();await page.waitForFunction(()=>document.getElementById('feedback').textContent.includes('Offline check'));
  assert.equal(submitted,true);
  await page.evaluate(()=>{delete state.record.edit_review;drawDesign(state.record);controls();$('plan-summary').open=false;feedback('Ready');scrollBottom();});
  await page.screenshot({path:'local-docs/gui-designs/approved/design-panel-simple-offline-check.png'});
  await page.reload();await page.waitForFunction(()=>Boolean(state.record?.design));
  assert.equal(await page.locator('#design-status').textContent(),'Ready to build');
  assert.deepEqual(errors,[]);
  console.log('PASS: concept-only panel, block preview in chat, build/edit progress, retained Apply edit action, status and reload.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
