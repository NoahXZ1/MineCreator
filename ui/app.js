'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="minecreator-token"]').content;
const state = {selected:null, record:null, active:null, stream:'', cursor:0, busy:false, config:null, pollTimer:null, selection:0,
  previewKey:null, previewUrl:null, previewTicket:0, chatPreviewUrl:null};

async function api(path, body) {
  let response;
  try {response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
    headers:{'X-MineCreator-Token':token, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
    body:body === undefined ? undefined : JSON.stringify(body)});}
  catch {throw new Error('The local MineCreator server is disconnected. Reopen MineCreator.vbs; saved conversations and previews are kept.');}
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || 'The request failed.'); error.http = response.status; throw error; }
  return result;
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function feedback(text, kind='') { $('feedback').textContent=text; $('feedback').className=`feedback ${kind}`; }
function controls() {
  $('send').disabled = state.busy || !state.config?.configured || !$('prompt').value.trim();
  $('prompt').disabled = state.busy;
  $('stop').hidden = !state.active;
  const hasIdea=state.record?.turns.some(turn=>turn.status==='completed');
  const hasDraft=Boolean($('prompt').value.trim());
  $('generate-plan').disabled=state.busy || !state.config?.configured || !hasIdea || hasDraft;
  $('generate-plan').title=hasDraft?'Send your draft message first.':'Generate a blueprint from this conversation.';
  $('generate-image').disabled=state.busy || !state.config?.configured || !state.record?.design;
  $('build').disabled=state.busy || !state.record?.design || hasDraft;
  $('build').title=hasDraft?'Send or clear your draft first.':'Start building the saved plan nearby. Left-click or right-click.';
  $('edit-target').disabled=state.busy;
  $('save-library').disabled=state.busy || !state.record?.design;
  $('save-settings').disabled=state.busy;
  $('check-connection').disabled=state.busy;
  $('apply-edit').disabled=state.busy || hasDraft || !(state.record?.edit_review?.change_count>0);
  drawDesignStatus();
}
function scrollBottom() { $('messages').scrollTop=$('messages').scrollHeight; }
function message(role, text, timestamp, turnId) {
  const row=element('article','message');
  row.dataset.turn=turnId; row.dataset.role=role;
  const avatar=element('div',`avatar ${role}`);
  avatar.setAttribute('aria-hidden','true');
  if (role==='assistant') {const icon=element('img');icon.src='/icon.svg';icon.alt='';avatar.append(icon);}
  else avatar.textContent='Y';
  const content=element('div','message-content');
  const heading=element('div','message-heading',role==='user'?'You':'MineCreator');
  const date=new Date(timestamp);
  const time=element('time','',date.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'}));
  time.dateTime=timestamp; heading.append(time);
  content.append(heading,element('div','message-text',text));
  row.append(avatar,content); return row;
}
function drawMessages(record) {
  const activity=$('conversation-details');activity.remove();
  $('messages').replaceChildren();
  $('chat-title').textContent=record.title;
  if (!record.turns.length) {
    const empty=element('div','empty-state');
    const mark=element('div','empty-mark'),icon=element('img');icon.src='/icon.svg';icon.alt='';icon.width=42;icon.height=48;mark.append(icon);
    const starter=element('button','starter');starter.type='button';
    starter.append(element('span','','＋'),document.createTextNode('A small wooden cabin beside a lake'));
    starter.onclick=()=>{if(state.busy)return;$('prompt').value='I would like to design a small wooden cabin beside a lake.';controls();$('prompt').focus();};
    empty.append(mark,element('h3','','What shall we build?'),element('p','','Start with an idea. We can work out the shape, materials, and finer details together.'),starter);
    $('messages').append(empty);
  }
  for(const turn of record.turns) {
    $('messages').append(message('user',turn.user,turn.created_at,turn.id));
    const reply=message('assistant',turn.assistant || (turn.status==='pending'?'Waiting for a reply...':'No complete reply.'),turn.created_at,turn.id);
    if(turn.status!=='completed' && turn.status!=='pending') {
      reply.querySelector('.message-content').append(element('div','turn-note',turn.error || 'This reply was not completed.'));
    }
    if(turn.preview) {
      const view=element('button','chat-preview-link','View AI preview');view.type='button';
      view.onclick=()=>openChatPreview(turn.preview).catch(error=>feedback(error.message,'error'));
      reply.querySelector('.message-content').append(view);
    }
    $('messages').append(reply);
  }
  $('messages').append(activity);
  if(state.active?.operation==='chat' && state.active.conversation_id===record.id && state.stream) showStream();
  scrollBottom();
}
function showStream() {
  if(state.selected!==state.active?.conversation_id) return;
  let row=[...$('messages').querySelectorAll('.message')].find(node=>node.dataset.turn===state.active.id && node.dataset.role==='assistant');
  if(!row) {
    $('messages').querySelector('.empty-state')?.remove();
    row=message('assistant','',new Date().toISOString(),state.active.id);$('messages').insertBefore(row,$('conversation-details'));
  }
  const nearBottom=$('messages').scrollHeight-$('messages').scrollTop-$('messages').clientHeight<110;
  row.querySelector('.message-text').textContent=state.stream || 'Waiting for a reply...';
  if(nearBottom)scrollBottom();
}
async function selectConversation(id) {
  const selection=++state.selection;
  const record=await api(`/api/conversations/${id}`);
  if(selection!==state.selection)return;
  state.selected=id;state.record=record;localStorage.setItem('minecreator-conversation',id);
  drawMessages(record);
  drawDesign(record);controls();scrollBottom();
  [...$('conversations').children].forEach(button=>button.classList.toggle('active',button.dataset.id===id));
}

function drawDesign(record) {
  const plan=record.design;
  const details=$('plan-details');details.replaceChildren();$('plan-summary').hidden=!plan;
  $('generate-image').hidden=!plan;
  $('plan-button-label').textContent=plan?'Regenerate plan':'Generate plan';
  $('generate-image').textContent=plan?.image_id?'Regenerate AI image':'Generate AI image';
  const notices=[];
  if(record.design_error)notices.push(record.design_error);
  const latestAttempt=record.designs?.at(-1);
  if(latestAttempt?.status==='pending')notices.push('Generating a new version...');
  else if(latestAttempt?.error && latestAttempt.id!==plan?.id)notices.push(latestAttempt.error);
  const imageAttempt=latestAttempt?.images?.at(-1);
  if(imageAttempt?.error)notices.push(imageAttempt.error);
  if(plan) {
    details.append(element('div','plan-version',`VERSION ${plan.version} · SAVED`),element('h3','plan-title',plan.title),element('p','plan-description',plan.description));
    const stats=element('div','plan-stats');
    for(const [value,label] of [[`${plan.bounds.x} × ${plan.bounds.z}`,'Footprint'],[`${plan.bounds.y}`,'Height'],[`${plan.final_blocks}`,'Final blocks']]) {
      const stat=element('div');stat.append(element('strong','',value),element('span','',label));stats.append(stat);
    }
    details.append(stats,element('h4','','Materials'));
    const materials=element('ul','materials');
    for(const material of plan.materials) {
      const row=element('li');row.append(element('span','',material.block.replace('minecraft:','').split('[')[0].replaceAll('_',' ')),element('span','',String(material.count)));materials.append(row);
    }
    const stages=element('ol','plan-stages');
    for(const stage of plan.stages) {
      const row=element('li');row.append(element('strong','',stage.name),element('span','',`${stage.count} placements · ${stage.description}`));stages.append(row);
    }
    details.append(materials,element('h4','','Construction stages'),stages);
    if(plan.has_block_preview){
      const blockPreview=element('button','chat-preview-link','View block preview');
      blockPreview.onclick=()=>openBlockPreview(record.id,plan).catch(error=>feedback(error.message,'error'));details.append(blockPreview);
    }
    if(plan.needs_regeneration && !record.build_review && !record.edit_review)notices.push('New messages since this plan. Regenerate to include them.');
    if(plan.preview_error)notices.push(plan.preview_error);
    loadPreview(plan);
  } else {
    emptyConcept();
  }
  $('design-notice').hidden=!notices.length;$('design-notice').textContent=notices.join('\n');
  drawBuild(record.builds?.at(-1));
  drawEdit(record);
  if(typeof drawReferences==='function')drawReferences(record);
  $('conversation-details').hidden=!(plan || notices.length || record.builds?.length || record.edits?.length || record.references?.length);
  drawDesignStatus();
}
function drawDesignStatus(){
  const record=state.record,job=state.active?.conversation_id===state.selected?state.active:null;
  const edits=record?.edits || [],builds=record?.builds || [];
  let text='Ready';
  if(job){
    text=job.operation==='apply_edit' || edits.some(e=>e.id===job.id)?'Editing...':
      job.operation==='design_build' || builds.some(b=>b.id===job.id)?'Building...':
      job.operation==='connection'?'Checking connection...':'Designing...';
  }else if(record?.edit_review || record?.build_review)text='Awaiting confirmation';
  else{
    const latest=[...builds,...edits].sort((a,b)=>(a.updated_at || a.created_at || '').localeCompare(b.updated_at || b.created_at || '')).at(-1);
    const attempt=record?.designs?.at(-1);
    if(record?.design_error || attempt?.error || attempt?.images?.at(-1)?.error)text='Needs attention';
    else if(latest?.status==='running')text=edits.includes(latest)?'Editing...':'Building...';
    else if(record?.design && latest?.design_id && latest.design_id!==record.design.id && record.design.created_at>latest.created_at)text='Ready to build';
    else if(latest?.status==='failed')text='Needs attention';
    else if(['cancelled','interrupted'].includes(latest?.status))text='Stopped';
    else if(latest?.status==='completed')text='Completed';
    else if(attempt?.status==='pending')text='Designing...';
    else if(record?.design)text='Ready to build';
  }
  $('design-status').textContent=text;
}
function drawEdit(record) {
  const targets=(record.builds || []).filter(item=>item.status==='completed' && item.record_path);
  $('edit-target-panel').hidden=!targets.length;
  const select=$('edit-target');select.replaceChildren();
  targets.forEach((item,index)=>{
    const option=element('option','',`Build ${index+1} · ${(item.origin || []).join(', ')} · ${item.dimension || ''}`);
    option.value=item.id;option.title=option.textContent;select.append(option);
  });
  select.value=record.edit_target?.id || record.selected_build_id || targets.at(-1)?.id || '';
  select.title=select.selectedOptions[0]?.textContent || '';
  $('edit-review').hidden=!record.edit_review;
  const review=record.edit_review;
  $('edit-summary').textContent=review?`${review.change_count} changed blocks: ${review.counts.added} added, ${review.counts.removed} removed, ${review.counts.replaced} replaced.\nReview the full description in chat, then confirm below or reply in chat.`:'';
  drawEditProgress(record.edits?.at(-1));
}
function drawEditProgress(edit) {
  $('edit-status').hidden=!edit;
  if(!edit)return;
  $('edit-message').textContent=edit.message || 'Checking the target building...';
  const progress=$('edit-progress');progress.hidden=edit.status!=='running';
  if(edit.total>0 && Number.isFinite(edit.current)){progress.max=edit.total;progress.value=edit.current;}
  else progress.removeAttribute('value');
}
function editEvent(event,job) {
  if(state.selected!==job.conversation_id || !state.record)return;
  const edits=state.record.edits || (state.record.edits=[]);
  let edit=edits.find(item=>item.id===job.id);
  if(!edit){edit={id:job.id,status:'running'};edits.push(edit);}
  if(edit.phase!==event.data.phase){delete edit.current;delete edit.total;}
  Object.assign(edit,event.data,{message:event.message});drawEditProgress(edit);
  $('conversation-details').hidden=false;$('edit-target-panel').hidden=false;drawDesignStatus();
}
function drawBuild(build) {
  $('build-status').hidden=!build;
  if(!build)return;
  $('build-location').textContent=build.origin?`${build.dimension} · ${build.origin.join(', ')} · ${build.site_mode || ''}`:'';
  $('build-message').textContent=build.message || 'Preparing construction...';
  const progress=$('build-progress');
  progress.hidden=build.status!=='running';
  if(build.total>0 && Number.isFinite(build.current)) {progress.max=build.total;progress.value=build.current;}
  else progress.removeAttribute('value');
}
function buildEvent(event, job) {
  if(event.data.phase==='connected')$('game-status').textContent=`Connected · ${event.data.dimension}`;
  if(state.selected!==job.conversation_id || !state.record)return;
  const builds=state.record.builds || (state.record.builds=[]);
  let build=builds.find(item=>item.id===job.id);
  if(!build){build={id:job.id,status:'running'};builds.push(build);}
  // Searching, placing, and verification have separate counters.
  if(build.phase!==event.data.phase || build.stage!==event.data.stage){delete build.current;delete build.total;}
  Object.assign(build,event.data,{message:event.message});
  drawBuild(build);
  $('conversation-details').hidden=false;drawDesignStatus();
}
function resetPreview() {
  state.previewTicket++;
  if(state.previewUrl)URL.revokeObjectURL(state.previewUrl);
  state.previewUrl=null;state.previewKey=null;
}
async function openChatPreview(preview) {
  const response=await fetch(`/api/preview/${preview.conversation_id}/${preview.design_id}/concept?image=${preview.image_id}`,{headers:{'X-MineCreator-Token':token}});
  if(!response.ok)throw new Error('This saved preview could not be loaded.');
  const blob=await response.blob();
  if(state.chatPreviewUrl)URL.revokeObjectURL(state.chatPreviewUrl);
  state.chatPreviewUrl=URL.createObjectURL(blob);
  $('large-preview').src=state.chatPreviewUrl;
  $('preview-dialog-title').textContent='AI concept preview';
  $('large-caption').textContent='AI illustration of the saved blueprint, not an in-game screenshot.';
  $('preview-dialog').showModal();
}
async function openBlockPreview(conversationId,plan){
  const response=await fetch(`/api/preview/${conversationId}/${plan.id}/blocks`,{headers:{'X-MineCreator-Token':token}});
  if(!response.ok)throw new Error('This block preview could not be loaded.');
  const blob=await response.blob();
  if(state.chatPreviewUrl)URL.revokeObjectURL(state.chatPreviewUrl);
  state.chatPreviewUrl=URL.createObjectURL(blob);$('large-preview').src=state.chatPreviewUrl;
  $('preview-dialog-title').textContent=plan.title;$('large-caption').textContent='Actual block geometry';$('preview-dialog').showModal();
}
function emptyConcept(text='No concept image yet.'){
  resetPreview();const area=$('design-preview');area.classList.remove('populated');
  const icon=element('img');icon.src='/icon.svg';icon.alt='';icon.width=64;icon.height=64;
  area.replaceChildren(icon,element('p','',text));
}
async function loadPreview(plan) {
  if(!plan.image_id){emptyConcept();return;}
  const caption='AI concept · may differ from the built structure.';
  const key=`${state.selected}/${plan.id}/concept/${plan.image_id}`;
  if(state.previewKey===key)return;
  resetPreview();state.previewKey=key;
  const ticket=state.previewTicket,conversationId=state.selected;
  const area=$('design-preview');area.classList.add('populated');area.replaceChildren(element('p','','Loading preview...'));
  try {
    const response=await fetch(`/api/preview/${conversationId}/${plan.id}/concept`,{headers:{'X-MineCreator-Token':token}});
    if(!response.ok)throw new Error('Preview could not be loaded.');
    const blob=await response.blob();
    if(ticket!==state.previewTicket)return;
    const url=URL.createObjectURL(blob);state.previewUrl=url;
    const image=element('img','preview-image');image.src=url;image.alt=`AI concept of ${plan.title}`;
    const button=element('button','preview-image-button');button.type='button';button.setAttribute('aria-label','Enlarge preview');button.append(image);
    button.onclick=()=>{$('large-preview').src=url;$('preview-dialog-title').textContent=plan.title;$('large-caption').textContent=caption;$('preview-dialog').showModal();};
    area.replaceChildren(button);
  }catch(error){if(ticket===state.previewTicket){emptyConcept('Preview unavailable.');feedback(error.message,'error');}}
}
function drawCatalog(items) {
  $('conversations').replaceChildren();
  for(const item of items) {
    const button=element('button',`conversation${item.id===state.selected?' active':''}`);button.dataset.id=item.id;button.title=item.title;
    const date=new Date(item.updated_at), today=date.toDateString()===new Date().toDateString();
    button.append(element('span','chat-symbol','▤'),element('span','label',item.title),element('time','',today?'Today':date.toLocaleDateString('en-US',{month:'short',day:'numeric'})));
    button.onclick=()=>selectConversation(item.id).catch(error=>feedback(error.message,'error'));
    $('conversations').append(button);
  }
}
async function refreshState() {
  const result=await api('/api/state');state.config=result.configuration;drawCatalog(result.conversations);
  $('model').textContent=state.config.model || 'Not configured';
  $('api-state').textContent=state.config.configured?'API key configured locally':'Open Settings to configure chat';
  $('settings-model').textContent=state.config.model || 'Not configured';
  $('settings-key').textContent=state.config.configured?'Configured · kept in Python backend':'Missing configuration';
  controls();return result;
}
async function createConversation() {
  const record=await api('/api/conversations',{});
  await refreshState();await selectConversation(record.id);
  if(!state.busy) {$('prompt').value='';controls();$('prompt').focus();feedback(state.config.configured?'Ready to discuss your next build.':'Set your API key and model in .env, then reload settings.');}
}
function attachJob(job) {
  state.active={...job,operation:job.operation || 'chat'};state.cursor=0;state.stream='';state.busy=true;controls();
  feedback(job.operation==='design_build'?'Checking Minecraft and selecting a nearby site.':job.operation==='design_plan'?'Generating a plan from the conversation...':job.operation==='design_image'?'Generating an AI concept image...':'Waiting for the model. No automatic retries.','busy');
  if(job.operation==='apply_edit')feedback('Checking the building and applying the reviewed edit...','busy');
  clearTimeout(state.pollTimer);pollJob();
}
async function pollJob() {
  const job=state.active;if(!job)return;
  try {
    const result=await api(`/api/task?id=${job.id}&cursor=${state.cursor}`);
    state.cursor=result.cursor;
    for(const event of result.events) {
      if(event.data?.scope==='build')buildEvent(event,job);
      if(event.data?.scope==='edit')editEvent(event,job);
      if(event.kind==='replace_text' && job.operation==='chat') {state.stream=event.data.text;showStream();}
      else if(event.kind==='delta' && job.operation==='chat') {state.stream+=event.data.text;showStream();feedback('MineCreator is replying...','busy');}
      else if(event.kind==='status' || event.kind==='progress')feedback(event.message,'busy');
    }
    if(result.done) {
      await refreshState();
      if(state.selected===job.conversation_id)await selectConversation(state.selected);
      state.active=null;state.busy=false;
      const outcome=result.outcome;
      const chatPreview=outcome.result?.preview;
      if(outcome.status==='completed' && (job.operation==='design_image' || (chatPreview && chatPreview.design_id===state.record?.design?.id)) && state.selected===job.conversation_id)drawDesign(state.record);
      const buildResult=outcome.result?.build || (job.operation==='design_build'?outcome.result:null);
      const reviewResult=outcome.result?.review;
      const revisionResult=outcome.result?.revision;
      const editResult=outcome.result?.edit || (job.operation==='apply_edit'?outcome.result:null);
      const editReview=outcome.result?.edit_review;
      if(job.operation==='connection') {
        const connection=outcome.result;
        const text=outcome.status==='completed'?`Connected · ${connection.Minecraft} · ${connection.player_position.dimension} · world writes ${connection.world_write_available?'enabled':'disabled'}`:outcome.error.message;
        $('game-status').textContent=outcome.status==='completed'?'Minecraft connected':'Minecraft disconnected';
        $('settings-feedback').textContent=text;feedback(text,outcome.status==='completed'?'':'error');controls();return;
      }
      const wasEdit=job.operation==='apply_edit' || state.record?.edits?.some(item=>item.id===job.id);
      const wasBuild=job.operation==='design_build' || state.record?.builds?.some(item=>item.id===job.id);
      const completedText=reviewResult?'Review the building description in chat. Confirm to build, or describe changes.':revisionResult?'Plan updated. Blueprint details are available in chat.':buildResult?`Construction completed at ${buildResult.origin.join(', ')}. ${buildResult.verified} blocks verified.`:job.operation==='design_plan'?'Plan saved. View blueprint details in chat or generate a concept image.':job.operation==='design_image' || chatPreview?'AI concept image saved. See the Design panel.':'Reply saved. Continue the conversation.';
      const stoppedText=wasBuild?'Construction stopped. Placed blocks remain.':job.operation==='chat'?'Reply stopped. Partial text is not used as context.':'Operation stopped. Saved results remain available.';
      const editText=editReview?'Review the edit summary. Confirm in chat or click Apply edit.':editResult?`Edit verified: ${editResult.changed} changed blocks; ${editResult.unchanged || 0} other blocks unchanged.`:null;
      feedback(outcome.status==='completed'?(editText || completedText):outcome.status==='cancelled'?(wasEdit?'Edit stopped. Applied changes remain.':stoppedText):outcome.error.message,outcome.status==='failed'?'error':'');
      controls();if(state.selected===job.conversation_id)$('prompt').focus();
      return;
    }
    state.pollTimer=setTimeout(pollJob,180);
  } catch(error) {
    if(error.http>=400 && error.http<500){
      clearTimeout(state.pollTimer);state.active=null;state.busy=false;controls();
      feedback(`${error.message} Reload MineCreator to recover saved status. This task was not resubmitted.`,'error');
      return;
    }
    feedback('Connection interrupted. Reconnecting to the same request; no new API request is sent.','error');
    state.pollTimer=setTimeout(pollJob,2000);
  }
}
async function submit(event) {
  event.preventDefault();if(state.busy || !state.config?.configured)return;
  const prompt=$('prompt').value.trim();if(!prompt)return;
  state.busy=true;controls();feedback('Submitting your message...','busy');
  const payload={conversation_id:state.selected, request_id:crypto.randomUUID().replaceAll('-',''), prompt};
  try {
    const job=await api('/api/chat',payload);
    $('prompt').value='';await selectConversation(state.selected);attachJob(job);
  }catch(error) {
    if(!error.http) {
      try {
        await api(`/api/task?id=${payload.request_id}&cursor=0`);
        $('prompt').value='';await selectConversation(state.selected);
        attachJob({id:payload.request_id,conversation_id:payload.conversation_id,operation:'chat'});return;
      }catch {feedback('Could not confirm submission. Reload the page to recover its status before sending again.','error');state.busy=true;controls();return;}
    }
    state.busy=false;feedback(error.message,'error');controls();
  }
}

async function designAction(operation) {
  if(state.busy || (!['design_build','apply_edit'].includes(operation) && !state.config?.configured))return;
  if(operation==='apply_edit' && (!(state.record?.edit_review?.change_count>0) || $('prompt').value.trim()))return;
  if(operation==='design_plan' && (!state.record?.turns.some(turn=>turn.status==='completed') || $('prompt').value.trim()))return;
  if(operation==='design_image' && !state.record?.design)return;
  if(operation==='design_build' && (!state.record?.design || $('prompt').value.trim()))return;
  state.busy=true;controls();feedback('Submitting your request...','busy');
  const payload={conversation_id:state.selected,request_id:crypto.randomUUID().replaceAll('-','')};
  if(operation==='design_image' || operation==='design_build')payload.design_id=state.record.design.id;
  if(operation==='design_build')payload.confirmed=true;
  if(operation==='apply_edit'){payload.confirmed=true;payload.review_id=state.record.edit_review.id;}
  try {
    const job=await api(operation==='apply_edit'?'/api/apply-edit':operation==='design_build'?'/api/build':operation==='design_plan'?'/api/plan':'/api/image',payload);
    attachJob(job);await selectConversation(state.selected);
  }catch(error) {
    if(!error.http) {
      try {await api(`/api/task?id=${payload.request_id}&cursor=0`);attachJob({id:payload.request_id,conversation_id:payload.conversation_id,operation});return;}
      catch {feedback('Could not confirm submission. Reload before sending another request.','error');state.busy=true;controls();return;}
    }
    state.busy=false;feedback(error.message,'error');controls();
  }
}
$('generate-plan').onclick=()=>designAction('design_plan');
$('apply-edit').onclick=()=>designAction('apply_edit');
$('edit-target').onchange=async()=>{
  if(state.busy)return;
  const id=state.selected;state.busy=true;controls();
  try{await api('/api/edit-target',{conversation_id:id,build_id:$('edit-target').value});if(state.selected===id)await selectConversation(id);feedback('Building selected. Ask for an in-game edit in chat.');}
  catch(error){feedback(error.message,'error');}
  finally{state.busy=false;controls();}
};
$('generate-image').onclick=()=>designAction('design_image');
$('build').onclick=()=>designAction('design_build');
$('build').addEventListener('contextmenu',event=>{event.preventDefault();if(!$('build').disabled)designAction('design_build');});
$('close-preview').onclick=()=>{$('preview-dialog').close();};
$('preview-dialog').addEventListener('close',()=>{if(state.chatPreviewUrl){URL.revokeObjectURL(state.chatPreviewUrl);state.chatPreviewUrl=null;}});
$('chat-form').addEventListener('submit',submit);
$('prompt').addEventListener('input',controls);
$('prompt').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}});
$('new-chat').onclick=()=>createConversation().catch(error=>feedback(error.message,'error'));
$('stop').onclick=async()=>{if(!state.active)return;try{await api('/api/cancel',{id:state.active.id});feedback('Stop requested; waiting for the current network read to return.','busy');}catch(error){feedback(error.message,'error');}};
$('settings').onclick=()=>{$('settings-dialog').showModal();};
$('close-settings').onclick=()=>{$('settings-dialog').close();};
$('refresh-settings').onclick=()=>refreshState().then(()=>feedback(state.config.configured?'Local configuration reloaded.':state.config.message,state.config.configured?'':'error')).catch(error=>feedback(error.message,'error'));
$('quit').onclick=async()=>{try{await api('/api/quit',{});$('settings-dialog').close();state.busy=true;controls();feedback('MineCreator closed. You can close this window.');clearTimeout(state.pollTimer);clearInterval(heartbeat);}catch(error){feedback(error.message,'error');}};
// Keep the local process alive while this window is open. No OpenAI or game calls.
const heartbeat=setInterval(()=>api('/api/heartbeat').catch(error=>feedback(error.message,'error')),6000);
window.addEventListener('pagehide',()=>{
  clearInterval(heartbeat);
  // Keepalive delivers an explicit close signal even as the page is leaving.
  // A reload's next request cancels this signal before the 10-second grace ends.
  fetch('/api/window-close',{method:'POST',keepalive:true,
    headers:{'X-MineCreator-Token':token,'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
});
window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
const mobileNew=element('button','mobile-new','＋ New chat');mobileNew.onclick=$('new-chat').onclick;
$('chat-title').parentElement.append(mobileNew);
(async()=>{
  try{
    const result=await refreshState();
    const remembered=localStorage.getItem('minecreator-conversation');
    const selected=result.active?.conversation_id || (result.conversations.some(item=>item.id===remembered)?remembered:result.conversations[0]?.id);
    if(selected)await selectConversation(selected);else await createConversation();
    if(result.active)attachJob(result.active);
    else feedback(state.config.configured?'Ready to discuss your next build.':state.config.message,state.config.configured?'':'error');
    controls();if(!state.busy)$('prompt').focus();
  }catch(error){feedback(`Could not load your workspace. ${error.message}`,'error');}
})();
