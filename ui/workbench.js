'use strict';
let libraryRows=[], librarySelected=null, libraryUrls=[], archiveRequest=null;
const libraryDescriptionDrafts=new Map();
function uuid(){return crypto.randomUUID().replaceAll('-','');}
async function bytes64(file){
  const bytes=new Uint8Array(await file.arrayBuffer());let text='';
  for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return btoa(text);
}
async function blobRequest(path){
  const response=await fetch(path,{headers:{'X-MineCreator-Token':token}});
  if(!response.ok){let detail;try{detail=await response.json();}catch{}throw new Error(detail?.error || 'This file could not be loaded.');}
  return response.blob();
}
async function download(path,name){
  const url=URL.createObjectURL(await blobRequest(path));const a=element('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
async function viewAsset(path,title){
  const blob=await blobRequest(path),url=URL.createObjectURL(blob);
  if(state.chatPreviewUrl)URL.revokeObjectURL(state.chatPreviewUrl);state.chatPreviewUrl=url;
  $('large-preview').src=url;$('preview-dialog-title').textContent=title;$('large-caption').textContent='Saved reference or preview';$('preview-dialog').showModal();
}
function drawReferences(record){
  $('reference-list').replaceChildren();
  for(const ref of record.references || []){
    const b=element('button','',ref.name);b.title=ref.name;
    b.onclick=()=>viewAsset(`/api/reference?conversation_id=${record.id}&id=${ref.id}`,ref.name).catch(e=>feedback(e.message,'error'));
    $('reference-list').append(b);
  }
}
function fillSettings(){
  $('setting-api-key').value='';$('setting-clear-key').checked=false;
  $('setting-model').value=state.config?.model || 'gpt-6-luna';$('setting-game-dir').value=state.config?.game_dir || '';
  $('data-location').textContent=`Local data: ${state.config?.data_dir || ''}`;
}
$('settings').onclick=()=>{fillSettings();$('settings-feedback').textContent='A blank key keeps the current key. Saving settings does not call the model.';$('settings-dialog').showModal();};
$('settings-dialog').addEventListener('close',()=>{$('setting-api-key').value='';});
$('refresh-settings').onclick=()=>refreshState().then(fillSettings).catch(e=>{$('settings-feedback').textContent=e.message;});
$('save-settings').onclick=async()=>{
  if(state.busy)return;state.busy=true;controls();
  try{await api('/api/settings',{model:$('setting-model').value,api_key:$('setting-api-key').value,clear_key:$('setting-clear-key').checked,game_dir:$('setting-game-dir').value});
    await refreshState();fillSettings();$('settings-feedback').textContent='Settings saved. API key is protected for this Windows user.';
  }catch(e){$('settings-feedback').textContent=e.message;}finally{$('setting-api-key').value='';state.busy=false;controls();}
};
$('choose-game-dir').onclick=async()=>{if(state.busy)return;$('choose-game-dir').disabled=true;try{const result=await api('/api/pick-directory',{});if(result.path)$('setting-game-dir').value=result.path;}catch(e){$('settings-feedback').textContent=e.message;}finally{$('choose-game-dir').disabled=false;}};
$('check-connection').onclick=async()=>{
  if(state.busy)return;
  if($('setting-game-dir').value!==state.config.game_dir){$('settings-feedback').textContent='Save the new directory before checking Minecraft.';return;}
  state.busy=true;controls();
  try{attachJob(await api('/api/connection',{conversation_id:state.selected,request_id:uuid()}));$('settings-feedback').textContent='Checking Minecraft...';}
  catch(e){state.busy=false;controls();$('settings-feedback').textContent=e.message;}
};
$('save-library').onclick=async()=>{
  if(state.busy || !state.record?.design)return;
  archiveRequest=null;
  $('archive-name').value=state.record.library_origin?.name || state.record.design.title;
  $('archive-notes').value=state.record.archive_notes || '';$('archive-feedback').textContent='';
  $('archive-design').replaceChildren();
  const designs=(state.record.designs || []).filter(d=>d.plan_path);
  designs.forEach((d,i)=>{const option=element('option','',`Version ${i+1} · ${new Date(d.created_at).toLocaleString('en-US')}`);option.value=d.id;$('archive-design').append(option);});
  $('archive-design').value=state.record.design.id;
  try{libraryRows=await api('/api/library');$('archive-group').replaceChildren(new Option('New building',''));
    const groups=new Map();for(const row of libraryRows)if(row.library_id && !groups.has(row.library_id))groups.set(row.library_id,row.name);
    for(const [id,name] of groups)$('archive-group').append(new Option(`New version of ${name}`,id));
    $('archive-group').value=state.record.library_origin?.library_id || '';
    $('save-dialog').showModal();
  }catch(e){feedback(e.message,'error');}
};
for(const id of ['archive-name','archive-notes','archive-design','archive-group'])$(id).addEventListener('input',()=>{archiveRequest=null;});
$('close-save').onclick=()=>$('save-dialog').close();
$('confirm-save').onclick=async()=>{
  if(state.busy)return;
  archiveRequest ||= {request_id:uuid(),conversation_id:state.selected,design_id:$('archive-design').value,name:$('archive-name').value,notes:$('archive-notes').value,library_id:$('archive-group').value || null};
  state.busy=true;controls();$('confirm-save').disabled=true;$('archive-feedback').textContent='Saving complete archive...';
  try{const saved=await api('/api/library-save',archiveRequest);$('save-dialog').close();feedback(`Saved ${saved.name}, version ${saved.version}, with all linked files.`);archiveRequest=null;}
  catch(e){$('archive-feedback').textContent=e.message;}
  finally{state.busy=false;controls();$('confirm-save').disabled=false;}
};
function renderLibrary(){
  const query=$('library-search').value.toLowerCase();$('library-list').replaceChildren();
  for(const item of libraryRows.filter(row=>`${row.name} ${row.notes} ${row.title}`.toLowerCase().includes(query))){
    const b=element('button',item.id===librarySelected?'selected':'',item.name);b.append(element('small','',`Version ${item.version} · ${item.created_at?new Date(item.created_at).toLocaleDateString('en-US'):''}`));
    b.onclick=()=>{if(!state.busy)showLibraryVersion(item.id);};$('library-list').append(b);
  }
  if(!$('library-list').children.length)$('library-list').append(element('p','muted','No saved buildings found.'));
}
async function showLibraryVersion(id){
  librarySelected=id;renderLibrary();const detail=$('library-detail');detail.replaceChildren(element('p','','Loading archive...'));
  libraryUrls.forEach(URL.revokeObjectURL);libraryUrls=[];
  try{
    const m=await api(`/api/library-detail?id=${id}`);if(librarySelected!==id)return;
    detail.replaceChildren(element('h3','',`${m.name} · Version ${m.version}`));
    const description=element('section','library-description');description.id='library-description';
    const heading=element('div','description-heading'),descriptionTitle=element('h4','','Building description');
    const editDescription=element('button','library-edit-action','Edit'),saveDescription=element('button','library-edit-action green','Save');
    editDescription.id='edit-description';saveDescription.id='save-description';
    saveDescription.dataset.inactive='true';saveDescription.disabled=true;editDescription.disabled=state.busy;
    const descriptionText=element('p','description-text',m.description),editor=element('textarea');
    editor.id='description-editor';editor.value=libraryDescriptionDrafts.get(id) ?? m.description;editor.maxLength=8000;editor.rows=6;editor.hidden=true;
    editor.setAttribute('aria-label','Building description');
    let descriptionRequest=null;
    editor.oninput=()=>{descriptionRequest=null;libraryDescriptionDrafts.set(id,editor.value);saveDescription.dataset.inactive=String(!editor.value.trim());lockLibraryImages(state.busy);};
    editDescription.onclick=()=>{
      if(state.busy)return;descriptionText.hidden=true;editor.hidden=false;
      editDescription.dataset.inactive='true';editor.oninput();editor.focus();
    };
    saveDescription.onclick=async()=>{
      if(state.busy || saveDescription.disabled)return;
      descriptionRequest ||= {id,request_id:uuid(),description:editor.value};
      state.busy=true;controls();lockLibraryImages(true);
      try{await changeLibraryVersion(descriptionRequest,'description','Description saved');}
      catch(e){$('library-feedback').textContent=e.message;}
      finally{state.busy=false;controls();lockLibraryImages(false);}
    };
    heading.append(descriptionTitle,editDescription,saveDescription);description.append(heading,descriptionText,editor);
    detail.append(description,element('pre','',m.notes));
    if(libraryDescriptionDrafts.has(id)){
      descriptionText.hidden=true;editor.hidden=false;editor.readOnly=state.busy;
      editDescription.dataset.inactive='true';editDescription.disabled=true;
      saveDescription.dataset.inactive=String(!editor.value.trim());saveDescription.disabled=state.busy || !editor.value.trim();
    }
    const actions=element('div','library-actions'),open=element('button','green','Open working copy'),save=element('button','','Export ZIP');
    let openRequest=uuid();
    open.onclick=async()=>{if(state.busy)return;state.busy=true;controls();open.disabled=true;
      try{const record=await api('/api/library-open',{id,request_id:openRequest});await refreshState();await selectConversation(record.id);$('library-dialog').close();feedback('Opened a working copy. Select a new site when building.');}
      catch(e){$('library-feedback').textContent=e.message;}finally{state.busy=false;controls();open.disabled=false;}
    };
    save.onclick=()=>download(`/api/library-export?id=${id}`,`${m.name.replace(/[^a-zA-Z0-9_-]/g,'_')}-v${m.version}.zip`).catch(e=>{$('library-feedback').textContent=e.message;});
    const upload=element('button','library-edit-action','Upload images');upload.type='button';upload.disabled=state.busy;
    const input=element('input');input.type='file';input.accept='.jpg,.jpeg,.png,image/jpeg,image/png';input.multiple=true;input.hidden=true;input.id='library-image-files';
    upload.onclick=()=>{if(!state.busy)input.click();};
    input.onchange=async()=>{
      const files=[...input.files];if(!files.length || state.busy)return;
      try{
        if(files.length>30 || files.some(file=>file.size>8*1024*1024) || files.reduce((sum,file)=>sum+file.size,0)>32*1024*1024)
          throw new Error('Upload up to 30 JPG/PNG images, at most 8 MB each and 32 MB per batch.');
        // Lock controls while reading files as well as while saving the new version.
        state.busy=true;controls();lockLibraryImages(true);
        const images=[];for(const file of files)images.push({name:file.name,data:await bytes64(file)});
        await changeLibraryVersion({id,request_id:uuid(),images});
      }catch(e){$('library-feedback').textContent=e.message;}
      finally{state.busy=false;controls();lockLibraryImages(false);input.value='';}
    };
    actions.append(open,save,upload,input);detail.append(actions,element('h4','','Images and references'));
    detail.append(element('p','muted','Upload JPG/PNG or remove any image below. Each change saves a new archive version.'));
    const grid=element('div','asset-grid'),links=element('div','asset-links');detail.append(grid);
    for(const asset of m.assets){
      const path=`/api/library-asset?id=${id}&file=${encodeURIComponent(asset.file)}`;
      const b=element('button','',asset.label);
      if(/\.(png|jpg|jpeg|webp)$/i.test(asset.file)){
        const card=element('div','library-image-card'),remove=element('button','library-edit-action remove-image','Remove');
        remove.type='button';remove.setAttribute('aria-label',`Remove ${asset.label}`);remove.disabled=state.busy;
        const removeRequest={id,request_id:uuid(),remove:[asset.file]};
        remove.onclick=async()=>{
          if(state.busy)return;state.busy=true;controls();lockLibraryImages(true);
          try{await changeLibraryVersion(removeRequest);}
          catch(e){$('library-feedback').textContent=e.message;}
          finally{state.busy=false;controls();lockLibraryImages(false);}
        };
        card.append(b,remove);grid.append(card);b.onclick=()=>viewAsset(path,asset.label).catch(e=>{$('library-feedback').textContent=e.message;});
        blobRequest(path).then(blob=>{if(librarySelected!==id)return;const url=URL.createObjectURL(blob);libraryUrls.push(url);const img=element('img');img.alt=asset.label;img.src=url;b.prepend(img);}).catch(e=>{b.textContent=e.message;});
      }else{b.onclick=()=>download(path,asset.label).catch(e=>{$('library-feedback').textContent=e.message;});links.append(b);}
    }
    if(!grid.children.length)grid.append(element('p','muted','No images in this version. Upload a JPG or PNG to add one.'));
    const history=element('details'),summary=element('summary','','Design conversation');history.append(summary);
    history.append(element('pre','',m.conversation.turns.map(t=>`You: ${t.user}\nMineCreator: ${t.assistant}`).join('\n\n')));detail.append(history);
    const records=element('details','library-records');records.id='library-records';
    records.append(element('summary','','Blueprints and execution records'),links);detail.append(records);
  }catch(e){detail.replaceChildren(element('p','library-detail-error',e.message));}
}
function lockLibraryImages(disabled){
  document.querySelectorAll('.library-edit-action').forEach(button=>{button.disabled=disabled || button.dataset.inactive==='true';});
  const editor=$('description-editor');if(editor)editor.readOnly=disabled;
}
async function changeLibraryVersion(payload,action='images',message='Images updated'){
  $('library-feedback').textContent='Saving changes...';
  let saved;
  try{saved=await api(`/api/library-${action}`,payload);}
  catch(error){
    if(error.http)throw error;
    // Recover a successful save whose response was lost; never submit it twice.
    try{saved=await api(`/api/library-detail?id=${payload.request_id}`);}
    catch{throw new Error('Could not confirm the save. Reopen the library to check for the new version before trying again.');}
  }
  if(action==='description')libraryDescriptionDrafts.delete(payload.id);
  else if(libraryDescriptionDrafts.has(payload.id))libraryDescriptionDrafts.set(saved.id,libraryDescriptionDrafts.get(payload.id));
  libraryRows=await api('/api/library');renderLibrary();await showLibraryVersion(saved.id);
  $('library-feedback').textContent=`${message} in version ${saved.version}. The original version is still available.`;
}
$('open-library').onclick=async()=>{try{libraryRows=await api('/api/library');renderLibrary();$('library-feedback').textContent='';$('library-dialog').showModal();}catch(e){feedback(e.message,'error');}};
$('close-library').onclick=()=>$('library-dialog').close();
$('library-search').oninput=renderLibrary;
$('import-library').onclick=()=>{if(!state.busy)$('library-file').click();};
$('library-file').onchange=async()=>{
  const file=$('library-file').files[0];if(!file || state.busy)return;
  state.busy=true;controls();$('import-library').disabled=true;
  try{if(file.size>128*1024*1024)throw new Error('Archive exceeds 128 MB.');
    $('library-feedback').textContent='Checking and importing archive...';const saved=await api('/api/library-import',{data:await bytes64(file)});
    libraryRows=await api('/api/library');renderLibrary();await showLibraryVersion(saved.id);$('library-feedback').textContent='Archive imported. No game blocks changed.';
  }catch(e){$('library-feedback').textContent=e.message;}finally{state.busy=false;controls();lockLibraryImages(false);$('import-library').disabled=false;$('library-file').value='';}
};
