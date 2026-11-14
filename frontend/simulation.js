const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tableAt = {1:{x:150,y:182},2:{x:362,y:182},3:{x:256,y:352},4:{x:648,y:182},5:{x:860,y:182},6:{x:754,y:352}};
const initialPositions = {W1:{x:520,y:720,room:null},W2:{x:650,y:720,room:null},W3:{x:850,y:720,room:null}};
const STORY_HOLD = 'Story setup: kitchen quality check';
const STORY_ORDER = 'floor-story-order';
const STORY_REQUEST = 'floor-story-water';
let state = null, events = [], generation = 0, writeQueue = Promise.resolve(), refreshTimer, clockTimer, frameId;
let playing = false, starting = false, tickBusy = false, storyStarted = false, waterSent = false, holdCleared = false;
let positions = structuredClone(initialPositions), plans = new Map(), busyTasks = new Set(), toastTimer, lastFrame = 0;

function clockText(seconds){seconds=Math.max(0,Number(seconds)||0);return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;}
function announce(message){const toast=$('#toast');toast.textContent=message;toast.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.hidden=true,3500);}
function nice(value){return String(value||'').replaceAll('_',' ');}
function active(task){return !['completed','cancelled'].includes(task.status);}
function latestRunEvents(list){const reset=[...list].reverse().findIndex(item=>item.type==='simulation_reset');return reset<0?list:list.slice(list.length-1-reset);}
function routeTable(tableId){return String(tableId)<=3?1:2;}
function doorX(room){return room===1?260:740;}
function appendPoint(points, point){const previous=points.at(-1);if(!previous||previous.x!==point.x||previous.y!==point.y)points.push(point);}
function routePoints(start, target, room, targetKind){
  const points=[{x:start.x,y:start.y}];
  if(start.room){const exit=doorX(start.room);appendPoint(points,{x:exit,y:474});appendPoint(points,{x:exit,y:500});}
  if(targetKind==='kitchen'){
    appendPoint(points,{x:500,y:720});appendPoint(points,{x:40,y:720});appendPoint(points,{x:40,y:602});
  }else if(targetKind==='bar'){
    appendPoint(points,{x:500,y:720});appendPoint(points,{x:444,y:602});
  }else{
    const door=doorX(room);
    if(start.x<70&&start.y<650)appendPoint(points,{x:40,y:720});
    appendPoint(points,{x:500,y:720});appendPoint(points,{x:door,y:500});appendPoint(points,{x:door,y:474});appendPoint(points,{x:target.x,y:target.y});
  }
  if(!points.length||points.at(-1).x!==target.x||points.at(-1).y!==target.y)appendPoint(points,target);
  return points;
}
function routeLength(points){return points.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p.x-points[i].x,p.y-points[i].y),0);}
function pointOnRoute(points, fraction){let distance=routeLength(points)*fraction;for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i],length=Math.hypot(b.x-a.x,b.y-a.y);if(distance<=length||i===points.length-1){const t=length?Math.min(1,distance/length):1;return{x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t};}distance-=length;}return points.at(-1);}
function routeFor(task, position){
  const table=tableAt[task.table_id], room=routeTable(task.table_id);
  if(task.type==='food_delivery')return{first:'kitchen',firstKind:'kitchen',destination:table,room};
  if(task.type==='service_request')return{first:'bar',firstKind:'bar',destination:table,room};
  return{first:null,firstKind:'table',destination:table,room};
}
function makePlan(task){
  const pos=positions[task.worker_id]||{...initialPositions[task.worker_id]};
  const route=routeFor(task,pos), source=route.firstKind==='kitchen'?{x:40,y:602}:route.firstKind==='bar'?{x:444,y:602}:null;
  const first=source?routePoints(pos,source,pos.room,route.firstKind):routePoints(pos,route.destination,route.room,'table');
  return{taskId:task.id,worker:task.worker_id,table:task.table_id,kind:task.type,source:route.first,sourcePoint:source,destination:route.destination,room:route.room,stage:source?'to-source':'to-table',path:first,progress:0,dwell:0,completionSent:false,ackFor:null,blocked:false,resumeStage:null};
}
function currentTask(plan){return state?.tasks.find(item=>item.id===plan.taskId);}
function tableFor(id){return state?.tables.find(item=>String(item.id)===String(id));}
function conflictKey(task){return events.findLast(item=>item.type==='task_hold_conflict'&&item.details?.task_id===task.id)?.id??task.conflict?.raised_at;}
function taskName(task){if(task.type==='food_delivery')return`Kitchen order · Table ${task.table_id}`;if(task.type==='service_request')return`${nice(task.request_type)} · Table ${task.table_id}`;return`Table check · Table ${task.table_id}`;}
function taskSummary(task){return `${task.worker_id||task.offered_to||'Floor'} · ${taskName(task)}`;}
function actorMessage(plan){if(plan.blocked)return`Waiting for Table ${plan.table} hold clearance`;if(plan.stage==='to-source')return`Picking up at the ${plan.source}`;if(plan.stage==='source-dwell')return`Order ready · checking ticket`;if(plan.stage==='to-table')return`On the way to Table ${plan.table}`;if(plan.stage==='table-dwell')return`At Table ${plan.table} · serving`;return taskName(currentTask(plan)||{type:plan.kind,table_id:plan.table});}
function beginLeg(plan, target, room, kind){const pos=positions[plan.worker]||initialPositions[plan.worker];plan.path=routePoints(pos,target,room,kind);plan.progress=0;}
function startAssigned(task){
  if(!task?.id||!task.worker_id){console.error('Cannot animate an invalid assigned task',task);return;}
  const existing=plans.get(task.worker_id);
  if(existing?.taskId===task.id)return;
  if(existing)return;
  const plan=makePlan(task);plans.set(task.worker_id,plan);busyTasks.add(task.id);
  if(task.type==='food_delivery'||task.type==='service_request')beginLeg(plan,plan.sourcePoint,null,plan.source);
  if(!plan.sourcePoint)plan.stage='to-table';
  $('#stage-caption').textContent=`${task.worker_id} accepted ${taskName(task)}. Route and completion follow the live task.`;
}
function startTableLeg(plan){plan.stage='to-table';beginLeg(plan,plan.destination,plan.room,'table');}
async function refresh(token=generation){
  try{const [s,e]=await Promise.all([fetch('/api/simulation/state'),fetch('/api/simulation/events')]);if(!s.ok||!e.ok)throw new Error('Simulation API unavailable');const next=await s.json(),log=await e.json();if(token!==generation)return;state=next;events=log;$('#connection').classList.add('connected');$('#connection').innerHTML='<i></i> Connected';render();reconcile();}catch(error){if(token===generation){console.error('Simulation refresh failed',error);$('#connection').classList.remove('connected');$('#connection').innerHTML='<i></i> Reconnecting';$('#brain-message').textContent=`Could not refresh simulation: ${error.message}`;}}
}
function send(path,body={},options={}){
  const token=options.token??generation,force=options.force||false,quiet=options.quiet||false,playOnly=options.playOnly||false;
  const operation=writeQueue.then(async()=>{
    if(!force&&token!==generation||playOnly&&!playing)return null;
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let result={};try{result=await response.json();}catch{}
    if(!response.ok)throw new Error(result.detail||result.message||'Action could not be completed.');
    if(token===generation)await refresh(token);
    if(result.message&&token===generation&&!quiet)announce(result.message);
    return result;
  });
  writeQueue=operation.catch(()=>{});
  return operation.catch(error=>{if(token===generation&&!quiet)announce(error.message||'Simulation request failed.');return null;});
}
function storyEvents(){return latestRunEvents(events);}
function detectStory(){const run=storyEvents();storyStarted=run.some(item=>item.type==='order_ready'&&item.details?.order_id===STORY_ORDER);waterSent=run.some(item=>item.type==='request_created'&&item.details?.request_id===STORY_REQUEST);holdCleared=run.some(item=>item.type==='service_hold_cleared'&&item.details?.table_id==='4'&&item.details?.worker_id==='W1');}
function freshFloor(){return state&&state.clock_seconds===0&&state.tasks.length===0&&state.requests.length===0&&state.orders.length===0&&state.tables.every(t=>!t.hold?.active&&t.meal_state==='eating')&&state.workers.every(w=>w.availability==='available');}
async function seedStory(token=generation){
  starting=true;syncPlayback();
  if(!state)return;
  if(!freshFloor())return;
  const hold=await send('/api/simulation/tables/4/hold',{worker_id:'W1',source:'kitchen',reason:STORY_HOLD},{token,quiet:true});if(!hold||token!==generation)return;
  const order=await send('/api/simulation/tables/4/order-ready',{source:'kitchen',order_id:STORY_ORDER},{token,quiet:true});if(!order||token!==generation)return;
  storyStarted=true;waterSent=false;holdCleared=false;
  $('#stage-caption').textContent='Kitchen order is ready at Table 4. The floor brain is waiting for an eligible worker.';
}
async function play(){
  if(playing||starting)return;
  const token=generation;
  detectStory();
  if(!storyStarted&&freshFloor())await seedStory(token);
  if(token!==generation)return;
  starting=false;detectStory();playing=true;syncPlayback();
  $('#stage-caption').textContent=storyStarted?'Story running · scripted inputs arrive at 00:08 and 00:32; backend decisions route the work.':'Live floor running · existing simulation state is being resumed.';
  if(clockTimer)clearInterval(clockTimer);
  clockTimer=setInterval(()=>{if(!playing||tickBusy)return;tickBusy=true;const tickGen=generation;send('/api/simulation/clock/advance',{seconds:1},{token:tickGen,quiet:true,playOnly:true}).finally(()=>{if(tickGen===generation)tickBusy=false;});},1000);
}
function pause(){playing=false;syncPlayback();$('#stage-caption').textContent='Paused · time, scripted inputs, worker movement, and dwell are held.';}
async function restart(){
  playing=false;starting=true;syncPlayback();generation++;const token=generation;
  if(clockTimer)clearInterval(clockTimer);plans.clear();busyTasks.clear();positions=structuredClone(initialPositions);storyStarted=false;waterSent=false;holdCleared=false;state=null;events=[];renderRoutes();renderActors();
  const result=await send('/api/simulation/reset',{}, {token,force:true,quiet:true});
  if(token!==generation)return;
  if(!result){starting=false;syncPlayback();return;}
  await refresh(token);if(!state)return;
  await seedStory(token);if(token!==generation)return;
  starting=false;playing=true;syncPlayback();
  $('#stage-caption').textContent='Fresh run · Table 4 is held and the kitchen ticket is waiting for clearance.';
  if(clockTimer)clearInterval(clockTimer);
  clockTimer=setInterval(()=>{if(!playing||tickBusy)return;tickBusy=true;const tickGen=generation;send('/api/simulation/clock/advance',{seconds:1},{token:tickGen,quiet:true,playOnly:true}).finally(()=>{if(tickGen===generation)tickBusy=false;});},1000);
}
function syncPlayback(){
  $('#play').disabled=playing||starting;$('#pause').disabled=!playing;$('#restart').disabled=starting;
  $('#run-state').textContent=starting?'Preparing':playing?'Playing':'Paused';
  if(starting){$('#selected-table').disabled=true;$('#hold-table').disabled=true;$('#clear-hold').disabled=true;$('#request-water').disabled=true;document.querySelectorAll('[data-worker-action]').forEach(button=>button.disabled=true);}
}
function runScript(){
  if(!playing||!storyStarted||!state)return;
  const elapsed=state.clock_seconds,runGeneration=generation;
  if(elapsed>=8&&!waterSent){waterSent=true;send(`/api/simulation/tables/2/requests`,{request_type:'water',request_id:STORY_REQUEST},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)waterSent=false;});$('#stage-caption').textContent='Table 2 asked for water. Dispatch is choosing a worker from the live floor state.';}
  if(elapsed>=32&&!holdCleared){holdCleared=true;const table=tableFor('4'),hold=table?.hold;if(hold?.active&&hold.worker_id==='W1'&&hold.reason===STORY_HOLD){send('/api/simulation/tables/4/hold/clear',{worker_id:'W1'},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)holdCleared=false;});$('#stage-caption').textContent='The story clears its own Table 4 hold. The kitchen delivery is eligible for dispatch.';}else if(hold?.active){$('#stage-caption').textContent='A manually changed hold remains in place. Clear it explicitly when ready.';}}
  if(elapsed>=70&&!state.tasks.some(active)){playing=false;syncPlayback();$('#stage-caption').textContent='Story complete · all work finished through backend task completion.';}
}
function reconcile(){
  detectStory();runScript();
  const activeAssigned=state.tasks.filter(task=>task.status==='assigned');
  for(const [workerId,plan] of [...plans]){
    const task=state.tasks.find(item=>item.id===plan.taskId),worker=state.workers.find(item=>item.id===workerId);
    if(!task||task.status!=='assigned'){
      plans.delete(workerId);busyTasks.delete(plan.taskId);continue;
    }
    const table=tableFor(task.table_id),hold=table?.hold;
    const waitingForAck=task.conflict?.active&&!task.conflict.acknowledged;
    if(task.type==='food_delivery'&&(hold?.active||waitingForAck)){
      plan.blocked=true;plan.resumeStage=plan.resumeStage||plan.stage;plan.stage='blocked';
      const conflictAt=conflictKey(task);
      if(waitingForAck&&plan.ackFor!==conflictAt){plan.ackFor=conflictAt;send(`/api/simulation/tasks/${encodeURIComponent(task.id)}/acknowledge`,{worker_id:workerId},{quiet:true,playOnly:true}).then(result=>{if(!result&&plan.ackFor===conflictAt)plan.ackFor=null;});}
    }else if(plan.blocked){plan.blocked=false;plan.stage=plan.resumeStage||'to-table';plan.resumeStage=null;$('#stage-caption').textContent=`${workerId} is moving again after the explicit hold clearance.`;}
  }
  if(playing){
    for(const task of state.tasks.filter(item=>item.status==='offered')){
      if(busyTasks.has(task.id))continue;
      const worker=state.workers.find(item=>item.id===task.offered_to);
      if(worker?.availability!=='available'||plans.has(worker.id))continue;
      busyTasks.add(task.id);send(`/api/simulation/tasks/${encodeURIComponent(task.id)}/accept`,{worker_id:worker.id},{quiet:true,playOnly:true}).then(result=>{if(!result)busyTasks.delete(task.id);});
    }
  }
  for(const task of activeAssigned){if(!plans.has(task.worker_id))startAssigned(task);}
  render();
}
function advancePlan(plan,dt){
  if(plan.blocked||!playing||plan.completionSent)return;
  const task=currentTask(plan);if(!task||task.status!=='assigned')return;
  if(plan.stage==='source-dwell'||plan.stage==='table-dwell'){
    plan.dwell-=dt;if(plan.dwell>0)return;
    if(plan.stage==='source-dwell'){startTableLeg(plan);return;}
    if(task.type==='food_delivery'&&tableFor(task.table_id)?.hold?.active){plan.blocked=true;plan.resumeStage='table-dwell';plan.stage='blocked';return;}
    if(task.conflict?.active&&!task.conflict.acknowledged){const conflictAt=conflictKey(task);if(plan.ackFor!==conflictAt){plan.ackFor=conflictAt;send(`/api/simulation/tasks/${encodeURIComponent(task.id)}/acknowledge`,{worker_id:plan.worker},{quiet:true,playOnly:true}).then(result=>{if(!result&&plan.ackFor===conflictAt)plan.ackFor=null;});}plan.blocked=true;plan.resumeStage='table-dwell';plan.stage='blocked';return;}
    plan.completionSent=true;send(`/api/simulation/tasks/${encodeURIComponent(task.id)}/complete`,{worker_id:plan.worker},{quiet:true,playOnly:true}).then(result=>{if(!result){plan.completionSent=false;plan.blocked=true;plan.resumeStage='table-dwell';plan.stage='blocked';}});return;
  }
  if(plan.stage!=='to-source'&&plan.stage!=='to-table')return;
  const length=Math.max(1,routeLength(plan.path));plan.progress=Math.min(1,plan.progress+dt/(length/54));
  const point=pointOnRoute(plan.path,plan.progress);positions[plan.worker]={...positions[plan.worker],x:point.x,y:point.y};
  if(plan.progress>=1){
    if(plan.stage==='to-source'){plan.stage='source-dwell';plan.dwell=1.1;positions[plan.worker].room=null;}
    else{plan.stage='table-dwell';plan.dwell=1.25;positions[plan.worker].room=plan.room;}
  }
}
function animate(now){
  const dt=lastFrame?Math.min(.12,(now-lastFrame)/1000):0;lastFrame=now;
  if(playing){for(const plan of plans.values())advancePlan(plan,dt);}
  renderActors();renderBubbles();frameId=requestAnimationFrame(animate);
}
function svgTable(table){
  const point=tableAt[table.id],hold=table.hold?.active,activeTask=state.tasks.find(t=>String(t.table_id)===String(table.id)&&active(t));
  const status=hold?'HOLD':activeTask?({food_delivery:'ORDER READY',service_request:`${nice(activeTask.request_type||'request').toUpperCase()} REQUEST`,check_table:'CHECK TABLE'}[activeTask.type]):nice(table.status).toUpperCase();
  const selected=String($('#selected-table').value)===String(table.id),highlight=!!hold||!!activeTask;
  return `<g class="table-unit ${selected?'selected':''} ${highlight?'highlight':''}" data-table="${esc(table.id)}" role="button" tabindex="0" aria-label="Select Table ${esc(table.id)}, ${esc(status)}"><title>Table ${esc(table.id)} · ${esc(nice(table.status))}${hold?' · service hold active':''}</title><circle class="guest-token" cx="${point.x-36}" cy="${point.y-27}" r="15"><title>Guest</title></circle><path class="guest-token-path" d="M${point.x-36} ${point.y-21}v10m-8-7q8-8 16 0"/><circle class="guest-token" cx="${point.x+36}" cy="${point.y+28}" r="15"><title>Guest</title></circle><path class="guest-token-path" d="M${point.x+36} ${point.y+34}v10m-8-7q8-8 16 0"/><rect class="table-top" x="${point.x-48}" y="${point.y-31}" width="96" height="62" rx="17"/><ellipse class="table-plate" cx="${point.x}" cy="${point.y}" rx="19" ry="15"/><text class="table-id" x="${point.x}" y="${point.y-2}">T${esc(table.id)}</text><text class="table-status" x="${point.x}" y="${point.y+51}">${esc(status)}</text></g>`;
}
function renderTables(){
  const items=state?.tables||[];$('#table-layer').innerHTML=items.map(svgTable).join('');
  const table=tableFor($('#selected-table').value),hold=table?.hold;$('#selected-table').disabled=starting;$('#hold-table').disabled=starting;$('#request-water').disabled=starting;$('#clear-hold').disabled=starting||!hold?.active;
  $('#hold-table').textContent=hold?.active?'Update hold':'Place hold';
}
function renderWorkers(){
  if(!state)return;
  $('#worker-controls').innerHTML=state.workers.map(worker=>{
    const task=state.tasks.find(item=>item.status==='assigned'&&item.worker_id===worker.id);
    const action=worker.availability==='available'?'Set unavailable':'Set available';
    return `<div class="worker-control-row"><span>${esc(worker.id)} · ${esc(nice(worker.availability))}${task?' · assigned':''}</span><span><button type="button" data-worker-action="availability" data-id="${esc(worker.id)}" data-value="${worker.availability==='available'?'busy':'available'}" ${starting||task?'disabled':''}>${task?'On task':action}</button>${task?` <button type="button" data-worker-action="release" data-id="${esc(worker.id)}" data-task="${esc(task.id)}" ${starting?'disabled':''}>Release task</button>`:''}</span></div>`;
  }).join('');
}
function renderActors(){
  const layer=$('#worker-layer');if(!layer)return;
  const workers=state?.workers||[{id:'W1',availability:'available'},{id:'W2',availability:'available'},{id:'W3',availability:'available'}];
  layer.innerHTML=workers.map(worker=>{const p=positions[worker.id]||initialPositions[worker.id],plan=plans.get(worker.id),klass=`worker-token ${worker.availability==='available'?'':'busy'} ${plan?.blocked?'blocked':''}`;return `<g id="actor-${esc(worker.id)}" class="${klass}" transform="translate(${p.x} ${p.y})"><circle class="worker-ring" r="25"/><circle class="token-body" r="19"/><text class="token-letter" y="1">${esc(worker.id)}</text><text class="worker-name" y="37">${esc(worker.id)}</text></g>`;}).join('');
}
function renderRoutes(){
  const layer=$('#route-layer');if(!layer)return;
  layer.innerHTML=[...plans.values()].filter(plan=>plan.path?.length>1).map(plan=>`<path class="route-path ${plan.blocked?'blocked':''} ${playing?'':'paused'}" d="${plan.path.map((p,i)=>`${i?'L':'M'}${p.x} ${p.y}`).join(' ')}" />`).join('');
}
function renderActorsAndRoutes(){renderRoutes();renderActors();}
function renderBubbles(){
  const layer=$('#bubble-layer');if(!layer)return;
  const plan=[...plans.values()].find(item=>item.stage==='to-source'||item.stage==='to-table'||item.stage==='blocked'||item.stage==='source-dwell'||item.stage==='table-dwell');
  if(!plan){layer.innerHTML='';return;}
  const p=positions[plan.worker],copy=actorMessage(plan).slice(0,37),x=Math.max(15,Math.min(765,p.x-65)),y=Math.max(112,p.y-85);
  layer.innerHTML=`<g class="speech-bubble" transform="translate(${x} ${y})"><rect width="220" height="45" rx="11"/><path d="M62 44l8 10 11-10"/><text x="11" y="18">To glasses / phone · ${esc(plan.worker)}</text><text x="11" y="36">${esc(copy)}</text></g>`;
}
function captionFor(item){
  const d=item.details||{},worker=d.worker_id||'Worker',table=d.table_id?`Table ${d.table_id}`:'';
  if(item.type==='task_offered')return{label:'Brain · dispatch',text:`Offer to ${worker}: ${item.message}`};
  if(item.type==='routing_decision')return{label:'Brain · waiting',text:item.message};
  if(item.type==='task_accepted')return{label:`${worker} · simulated glasses / phone`,text:`Accepting ${nice(d.task_type||'task')} ${table}.`};
  if(item.type==='task_hold_conflict')return{label:`${worker} · simulated glasses / phone`,text:`${table} is held. Pausing here and acknowledging the conflict.`};
  if(item.type==='task_conflict_acknowledged')return{label:`${worker} · simulated glasses / phone`,text:`Conflict acknowledged. Waiting for the hold to clear.`};
  if(item.type==='service_hold_started'||item.type==='service_hold_updated')return{label:`${worker} · simulated glasses / phone`,text:`${table} is blocked: ${d.reason||'service hold'}.`};
  if(item.type==='service_hold_cleared')return{label:`${worker} · simulated glasses / phone`,text:`${table} hold cleared. The blocked delivery can continue.`};
  if(item.type==='task_completed'||item.type==='order_delivered'||item.type==='request_fulfilled')return{label:`${worker} · simulated glasses / phone`,text:item.message};
  return{label:item.type?.replaceAll('_',' ')||'Floor event',text:item.message||'Simulation event'};
}
function renderCaptions(){
  const current=storyEvents().filter(item=>!['clock_advanced','simulation_reset','order_delivered','request_fulfilled'].includes(item.type)).slice(-6).reverse().map(item=>{const caption=captionFor(item);return`<li><strong>${esc(caption.label)}</strong>${esc(caption.text)}</li>`;});
  $('#caption-feed').innerHTML=current.join('')||'<li class="caption-empty">Captions appear from actual task and event messages.</li>';
}
function renderBrain(){
  if(!state)return;
  const assigned=state.tasks.filter(item=>item.status==='assigned').at(-1),offered=state.tasks.find(item=>item.status==='offered'),pending=state.tasks.find(item=>item.status==='pending');
  const activeTask=assigned||offered||pending;
  if(assigned){const plan=plans.get(assigned.worker_id);$('#brain-message').textContent=plan?.blocked?`${assigned.worker_id} is stopped at Table ${assigned.table_id}. The task remains assigned until the hold clears.`:`${assigned.worker_id} is ${plan?actorMessage(plan).toLowerCase():'assigned to '+taskName(assigned)}.`;$('#decision-meta').textContent=assigned.decision_reason||`Backend assigned ${taskName(assigned)} to ${assigned.worker_id}.`;$('#active-instruction').textContent=`${assigned.worker_id} · ${taskName(assigned)}`;}
  else if(offered){$('#brain-message').textContent=`Offer to ${offered.offered_to}: ${offered.decision_reason||taskName(offered)}.`;$('#decision-meta').textContent=`Live dispatch · ${taskName(offered)} · ${clockText(offered.wait_seconds)} eligible wait`;$('#active-instruction').textContent=`Offer · ${taskName(offered)}`;}
  else if(pending){$('#brain-message').textContent=pending.decision_reason||`Waiting to route ${taskName(pending)}.`;$('#decision-meta').textContent=`Pending · ${taskName(pending)} · ${clockText(pending.wait_seconds)} eligible wait`;$('#active-instruction').textContent=`Waiting · ${taskName(pending)}`;}
  else{$('#brain-message').textContent=storyStarted&&playing?'No active tasks. Waiting for the next scripted input.':'Waiting for simulated floor events.';$('#decision-meta').textContent='Backend task routing is the source of every assignment.';$('#active-instruction').textContent='No active task';}
}
function render(){
  if(!state)return;
  $('#floor-clock').textContent=clockText(state.clock_seconds);renderTables();renderWorkers();renderActorsAndRoutes();renderBubbles();renderCaptions();renderBrain();
  const kitchen=state.orders.filter(item=>item.source==='kitchen'&&item.status==='ready').length,bar=state.orders.filter(item=>item.source==='bar'&&item.status==='ready').length;
  $('#kitchen-state').textContent=kitchen?`${kitchen} ready ${kitchen===1?'order':'orders'}`:'Ready orders';$('#bar-state').textContent=bar?`${bar} ready`:'Water · dessert';
}
function selectTable(id){if(!tableAt[id])return;$('#selected-table').value=String(id);renderTables();}
async function inputAction(action){
  if(!state)return;
  const id=$('#selected-table').value,table=tableFor(id);
  if(action==='hold'){
    const existing=table?.hold?.active;const body={worker_id:existing?table.hold.worker_id:'W1',source:existing?table.hold.source:'kitchen',reason:existing?table.hold.reason:'Manual floor hold'};
    await send(`/api/simulation/tables/${encodeURIComponent(id)}/hold`,body);return;
  }
  if(action==='clear'){
    if(table?.hold?.active)await send(`/api/simulation/tables/${encodeURIComponent(id)}/hold/clear`,{worker_id:table.hold.worker_id});return;
  }
  if(action==='water')await send(`/api/simulation/tables/${encodeURIComponent(id)}/requests`,{request_type:'water'});
}
document.addEventListener('click',event=>{
  const table=event.target.closest('[data-table]');if(table){selectTable(table.dataset.table);return;}
  const workerButton=event.target.closest('[data-worker-action]');if(workerButton){const id=encodeURIComponent(workerButton.dataset.id);if(workerButton.dataset.workerAction==='release')send(`/api/simulation/tasks/${encodeURIComponent(workerButton.dataset.task)}/release`,{worker_id:workerButton.dataset.id});else send(`/api/simulation/workers/${id}/availability`,{availability:workerButton.dataset.value});return;}
});
document.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('[data-table]')){event.preventDefault();selectTable(event.target.dataset.table);}});
$('#play').addEventListener('click',play);$('#pause').addEventListener('click',pause);$('#restart').addEventListener('click',restart);
$('#selected-table').addEventListener('change',()=>renderTables());
$('#hold-table').addEventListener('click',()=>inputAction('hold'));$('#clear-hold').addEventListener('click',()=>inputAction('clear'));$('#request-water').addEventListener('click',()=>inputAction('water'));
refresh();refreshTimer=setInterval(()=>refresh(),800);frameId=requestAnimationFrame(animate);
