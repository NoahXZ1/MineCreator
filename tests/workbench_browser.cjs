const {chromium}=require('C:/Users/23710/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:960},acceptDownloads:true});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8790');await page.waitForFunction(()=>!document.getElementById('save-library').disabled);
  await page.locator('#settings').click();
  await page.locator('#setting-api-key').fill('sk-fake-browser-test');await page.locator('#setting-model').fill('offline-model');
  const dataDir=await page.locator('#data-location').textContent();await page.locator('#setting-game-dir').fill(dataDir.replace('Local data: ',''));
  await page.locator('#save-settings').click();await page.waitForFunction(()=>document.getElementById('settings-feedback').textContent.includes('Settings saved'));
  assert.equal(await page.locator('#setting-api-key').inputValue(),'');
  await page.locator('#check-connection').click();await page.waitForFunction(()=>document.getElementById('settings-feedback').textContent.includes('Minecraft is disconnected'));
  assert.equal(await page.locator('#save-settings').isEnabled(),true);
  await page.screenshot({path:'local-docs/gui-designs/approved/settings-offline-check.png'});
  await page.locator('#close-settings').click();
  await page.waitForFunction(()=>document.getElementById('reference-list').children.length===1);
  await page.locator('#save-library').click();await page.locator('#archive-name').fill('Pavilion <script>');
  await page.locator('#archive-notes').fill('Keep the garden natural.');await page.locator('#confirm-save').click();
  await page.waitForFunction(()=>!document.getElementById('save-dialog').open);
  await page.locator('#open-library').click();await page.locator('#library-list button').first().click();
  await page.getByRole('button',{name:'Open working copy',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('#library-detail img').length===2);
  await page.screenshot({path:'local-docs/gui-designs/approved/library-offline-check.png'});
  const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'Export ZIP',exact:true}).click();
  await (await downloading).saveAs('data/browser-library-test.zip');
  await page.getByRole('button',{name:'Open working copy',exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('library-dialog').open);
  assert.equal(await page.locator('#edit-target-panel').isVisible(),false);
  assert.equal(await page.locator('#reference-list button').count(),1);
  await page.locator('#save-library').click();
  await page.waitForFunction(()=>document.getElementById('save-dialog').open);
  assert.notEqual(await page.locator('#archive-group').inputValue(),'');
  await page.locator('#archive-notes').fill('Second approved version.');await page.locator('#confirm-save').click();
  await page.waitForFunction(()=>!document.getElementById('save-dialog').open);
  await page.locator('#open-library').click();await page.waitForFunction(()=>document.getElementById('library-dialog').open && document.querySelectorAll('#library-list button').length===2);
  await page.locator('#library-search').fill('Second approved');assert.equal(await page.locator('#library-list button').count(),1);
  await page.locator('#library-search').fill('');await page.locator('#library-file').setInputFiles('data/browser-library-test.zip');
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('Archive imported'));
  assert.equal(await page.locator('#library-list button').count(),2);
  await page.waitForFunction(()=>document.querySelectorAll('#library-detail img').length===2);
  await page.locator('.remove-image').first().click();
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('Images updated in version 3'));
  await page.waitForFunction(()=>document.querySelectorAll('#library-detail img').length===1);
  assert.equal(await page.getByRole('button',{name:'Upload images',exact:true}).isEnabled(),true);
  const jpg=await page.screenshot({type:'jpeg',quality:50});
  await page.locator('#library-image-files').setInputFiles([
   {name:'Uploaded garden.jpg',mimeType:'image/jpeg',buffer:jpg},
   {name:'Uploaded room.png',mimeType:'image/png',buffer:await page.screenshot({type:'png'})}
  ]);
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('Images updated in version 4'));
  await page.waitForFunction(()=>document.querySelectorAll('#library-detail img').length===3);
  await page.screenshot({path:'local-docs/gui-designs/approved/library-images-offline-check.png'});
  await page.locator('#library-image-files').setInputFiles({name:'Broken.png',mimeType:'image/png',buffer:Buffer.from('invalid PNG')});
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('damaged'));
  assert.equal(await page.locator('#library-list button').count(),4);
  // All image kinds can be removed, including the selected design's generated preview.
  for(let version=5;version<=7;version++){
   await page.locator('.remove-image').first().click();
   await page.waitForFunction(v=>document.getElementById('library-feedback').textContent.includes(`Images updated in version ${v}`),version);
  }
  assert.equal(await page.locator('#library-detail img').count(),0);
  await page.getByRole('button',{name:'Open working copy',exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('library-dialog').open);
  assert.equal(await page.locator('#reference-list button').count(),0);
  await page.reload();await page.waitForFunction(()=>!document.getElementById('save-library').disabled);
  assert.equal(await page.locator('#reference-list button').count(),0);
  await page.locator('#open-library').click();await page.locator('#library-list button').first().click();
  await page.locator('#edit-description').waitFor();
  assert.equal(await page.locator('#library-records').evaluate(e=>e.open),false);
  assert.equal(await page.locator('#library-detail').evaluate(e=>e.lastElementChild.id),'library-records');
  assert.equal(await page.locator('#library-detail').evaluate(e=>e.children[1].id),'library-description');
  assert.equal(await page.locator('#description-editor').isVisible(),false);
  assert.equal(await page.locator('#save-description').isDisabled(),true);
  await page.locator('#edit-description').click();
  const description='A lakeside pavilion with room to relax.\nKeep <windows> facing the lake.';
  await page.locator('#description-editor').fill('   ');
  assert.equal(await page.locator('#save-description').isDisabled(),true);
  await page.locator('#description-editor').fill(description);
  // A failed save keeps the user's draft editable for an explicit retry.
  await page.route('**/api/library-description',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Simulated save failure'})}));
  await page.locator('#save-description').click();
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('Simulated save failure'));
  assert.equal(await page.locator('#description-editor').inputValue(),description);
  assert.equal(await page.locator('#save-description').isEnabled(),true);
  await page.unroute('**/api/library-description');await page.locator('#save-description').click();
  await page.waitForFunction(()=>document.getElementById('library-feedback').textContent.includes('Description saved in version 8'));
  assert.equal(await page.locator('.description-text').textContent(),description);
  assert.equal(await page.locator('#description-editor').isVisible(),false);
  assert.equal(await page.locator('#library-records .asset-links').isVisible(),false);
  await page.locator('#library-records summary').click();
  assert.equal(await page.locator('#library-records .asset-links').isVisible(),true);
  await page.locator('#library-records summary').click();
  await page.screenshot({path:'local-docs/gui-designs/approved/library-description-offline-check.png'});
  await page.reload();await page.waitForFunction(()=>!document.getElementById('save-library').disabled);
  await page.locator('#open-library').click();await page.locator('#library-list button').first().click();
  await page.waitForFunction(text=>document.querySelector('.description-text')?.textContent===text,description);
  assert.equal(await page.locator('#library-records').evaluate(e=>e.open),false);
  assert.deepEqual(errors,[]);
  await page.evaluate(()=>fetch('/api/quit',{method:'POST',headers:{'Content-Type':'application/json','X-MineCreator-Token':document.querySelector('meta[name="minecreator-token"]').content},body:'{}'}));
  console.log('PASS: settings, archives, image upload/removal, description edit/save/retry/reload and collapsed records; no real API or game.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
