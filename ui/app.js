'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="minecreator-token"]').content;
const state = {selected:null, record:null, active:null, stream:'', cursor:0, busy:false, config:null, pollTimer:null, selection:0,
  previewKind:'blocks', previewKey:null, previewUrl:null, previewTicket:0, chatPreviewUrl:null};

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
  if(state.active?.operation==='chat' && state.active.conversation_id===record.id && state.stream) showStream();
  scrollBottom();
}
function showStream() {
  if(state.selected!==state.active?.conversation_id) return;
  let row=[...$('messages').querySelectorAll('.message')].find(node=>node.dataset.turn===state.active.id && node.dataset.role==='assistant');
  if(!row) {
    $('messages').querySelector('.empty-state')?.remove();
    row=message('assistant','',new Date().toISOString(),state.active.id);$('messages').append(row);
  }
  const nearBottom=$('messages').scrollHeight-$('messages').scrollTop-$('messages').clientHeight<110;
  row.querySelector('.message-text').textContent=state.stream || 'Waiting for a reply...';
  if(nearBottom)scrollBottom();
}
async function selectConversation(id) {
  const selection=++state.selection;
  const record=await api(`/api/conversations/${id}`);
  if(selection!==state.selection)return;
  if(state.selected!==id || state.record?.design?.id!==record.design?.id)state.previewKind='blocks';
  state.selected=id;state.record=record;localStorage.setItem('minecreator-conversation',id);
  drawMessages(record);
  drawDesign(record);controls();
  [...$('conversations').children].forEach(button=>button.classList.toggle('active',button.dataset.id===id));
}

function drawDesign(record) {
  const plan=record.design;
  const details=$('plan-details');details.replaceChildren();details.hidden=!plan;
  $('preview-tabs').hidden=!plan;
  $('generate-image').hidden=!plan;
  $('plan-button-label').textContent=plan?'Regenerate plan':'Generate plan';
  $('generate-image').textContent=plan?.image_id?'Regenerate AI image':'Generate AI image';
  $('concept-tab').disabled=!plan?.image_id;
  const notices=[];
  if(record.design_error)notices.push(record.design_error);
  const latestAttempt=record.designs?.at(-1);
  if(latestAttempt?.status==='pending')notices.push('Generating a new version...');
  else if(latestAttempt?.error && latestAttempt.id!==plan?.id)notices.push(latestAttempt.error);
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
    if(plan.needs_regeneration && !record.build_review)notices.push('New messages since this plan. Regenerate to include them.');
    if(plan.preview_error)notices.push(plan.preview_error);
    if(state.previewKind==='concept'&&!plan.image_id)state.previewKind='blocks';
    loadPreview(plan);
  } else {
    resetPreview();
    const area=$('design-preview');area.classList.remove('populated');area.replaceChildren();
    const icon=element('img');icon.src='/icon.svg';icon.alt='';icon.width=64;icon.height=64;
    area.append(icon,element('h3','','Your next build starts here.'),element('p','','Discuss the shape, materials, and details in the conversation.'),element('span','tag','DESIGN WORKSPACE'));
    $('preview-caption').textContent='Discuss an idea, then generate a plan.';
  }
  $('design-notice').hidden=!notices.length;$('design-notice').textContent=notices.join('\n');
  drawBuild(record.builds?.at(-1));
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
async function loadPreview(plan) {
  const kind=state.previewKind;
  $('blocks-tab').classList.toggle('selected',kind==='blocks');
  $('concept-tab').classList.toggle('selected',kind==='concept');
  const caption=kind==='blocks'?'Actual block geometry · click to enlarge.':'AI concept · may differ from the built structure.';
  $('preview-caption').textContent=caption;
  const key=`${state.selected}/${plan.id}/${kind}/${kind==='concept'?plan.image_id:''}`;
  if(state.previewKey===key)return;
  resetPreview();state.previewKey=key;
  const ticket=state.previewTicket,conversationId=state.selected;
  const area=$('design-preview');area.classList.add('populated');area.replaceChildren(element('p','',kind==='blocks'&&!plan.has_block_preview?'Block preview unavailable.':'Loading preview...'));
  if(kind==='blocks'&&!plan.has_block_preview)return;
  try {
    const response=await fetch(`/api/preview/${conversationId}/${plan.id}/${kind}`,{headers:{'X-MineCreator-Token':token}});
    if(!response.ok)throw new Error('Preview could not be loaded.');
    const blob=await response.blob();
    if(ticket!==state.previewTicket)return;
    const url=URL.createObjectURL(blob);state.previewUrl=url;
    const image=element('img','preview-image');image.src=url;image.alt=kind==='blocks'?`Block preview of ${plan.title}`:`AI concept of ${plan.title}`;
    const button=element('button','preview-image-button');button.type='button';button.setAttribute('aria-label','Enlarge preview');button.append(image);
    button.onclick=()=>{$('large-preview').src=url;$('preview-dialog-title').textContent=plan.title;$('large-caption').textContent=caption;$('preview-dialog').showModal();};
    area.replaceChildren(button);
  }catch(error){if(ticket===state.previewTicket){state.previewKey=null;const retry=element('button','','Reload preview');retry.onclick=()=>loadPreview(plan);area.replaceChildren(element('p','',error.message),retry);}}
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
  clearTimeout(state.pollTimer);pollJob();
}
async function pollJob() {
  const job=state.active;if(!job)return;
  try {
    const result=await api(`/api/task?id=${job.id}&cursor=${state.cursor}`);
    state.cursor=result.cursor;
    for(const event of result.events) {
      if(event.data?.scope==='build')buildEvent(event,job);
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
      if(outcome.status==='completed' && (job.operation==='design_image' || (chatPreview && chatPreview.design_id===state.record?.design?.id)) && state.selected===job.conversation_id) {state.previewKind='concept';drawDesign(state.record);}
      const buildResult=outcome.result?.build || (job.operation==='design_build'?outcome.result:null);
      const reviewResult=outcome.result?.review;
      const revisionResult=outcome.result?.revision;
      const wasBuild=job.operation==='design_build' || state.record?.builds?.some(item=>item.id===job.id);
      const completedText=reviewResult?'Review the building description in chat. Confirm to build, or describe changes.':revisionResult?'Plan updated. Review the new version in the Design panel. No game blocks changed.':buildResult?`Construction completed at ${buildResult.origin.join(', ')}. ${buildResult.verified} blocks verified.`:job.operation==='design_plan'?'Plan saved. Review the preview or discuss revisions.':job.operation==='design_image' || chatPreview?'AI concept image saved. See the Design panel.':'Reply saved. Continue the conversation.';
      const stoppedText=wasBuild?'Construction stopped. Placed blocks remain.':job.operation==='chat'?'Reply stopped. Partial text is not used as context.':'Operation stopped. Saved results remain available.';
      feedback(outcome.status==='completed'?completedText:outcome.status==='cancelled'?stoppedText:outcome.error.message,outcome.status==='failed'?'error':'');
      controls();if(state.selected===job.conversation_id)$('prompt').focus();
      return;
    }
    state.pollTimer=setTimeout(pollJob,180);
  } catch(error) {
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
  if(state.busy || (operation!=='design_build' && !state.config?.configured))return;
  if(operation==='design_plan' && (!state.record?.turns.some(turn=>turn.status==='completed') || $('prompt').value.trim()))return;
  if(operation==='design_image' && !state.record?.design)return;
  if(operation==='design_build' && (!state.record?.design || $('prompt').value.trim()))return;
  state.busy=true;controls();feedback('Submitting your request...','busy');
  const payload={conversation_id:state.selected,request_id:crypto.randomUUID().replaceAll('-','')};
  if(operation==='design_image' || operation==='design_build')payload.design_id=state.record.design.id;
  if(operation==='design_build')payload.confirmed=true;
  try {
    const job=await api(operation==='design_build'?'/api/build':operation==='design_plan'?'/api/plan':'/api/image',payload);
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
$('generate-image').onclick=()=>designAction('design_image');
$('build').onclick=()=>designAction('design_build');
$('build').addEventListener('contextmenu',event=>{event.preventDefault();if(!$('build').disabled)designAction('design_build');});
$('blocks-tab').onclick=()=>{state.previewKind='blocks';drawDesign(state.record);};
$('concept-tab').onclick=()=>{state.previewKind='concept';drawDesign(state.record);};
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
