const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tableAt = {1:{x:150,y:182},2:{x:362,y:182},3:{x:256,y:352},4:{x:648,y:182},5:{x:860,y:182},6:{x:754,y:352}};
function tableApproach(id){const p=tableAt[id];return{x:p.x+68,y:p.y-34};}
const initialPositions = {W1:{x:520,y:720,room:null},W2:{x:650,y:720,room:null},W3:{x:850,y:720,room:null}};
const STORY_HOLD = 'Guest asked: Please delay our mains';
const STORY_ORDER = 'floor-story-order';
const STORY_REQUEST = 'floor-story-water';
let state = null, events = [], generation = 0, writeQueue = Promise.resolve(), refreshTimer, clockTimer, frameId;
let playing = false, starting = false, tickBusy = false, storyStarted = false, holdReported = false, orderReady = false, waterSent = false, waterReported = false, workerBReleased = false, holdCleared = false;
let holdSent = false, orderSent = false, waterRequestSent = false, clearSent = false, readySent = false, guestAskedAt = null, waterAskedAt = null;
let positions = structuredClone(initialPositions), plans = new Map(), sceneMoves = new Map(), busyTasks = new Set(), storyBubble = null, toastTimer, lastFrame = 0;

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
  const room=routeTable(task.table_id);
  if(task.type==='food_delivery')return{first:'kitchen',firstKind:'kitchen',destination:tableApproach(task.table_id),room};
  if(task.type==='service_request')return{first:'bar',firstKind:'bar',destination:tableApproach(task.table_id),room};
  return{first:null,firstKind:'table',destination:tableApproach(task.table_id),room};
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
function detectStory(){
  const run=storyEvents(),hold=run.find(item=>item.type==='service_hold_started'&&item.details?.table_id==='4'&&item.details?.reason===STORY_HOLD),order=run.find(item=>item.type==='order_ready'&&item.details?.order_id===STORY_ORDER),request=run.find(item=>item.type==='request_created'&&item.details?.request_id===STORY_REQUEST);
  storyStarted=['W1','W2','W3'].every(id=>run.some(item=>item.type==='worker_availability_changed'&&item.details?.worker_id===id&&item.details?.availability==='busy'))||!!hold||!!order;
  holdReported=!!hold;orderReady=!!order;waterSent=!!request;holdSent=holdReported;orderSent=orderReady;waterRequestSent=waterSent;
  waterReported=!!request&&run.some(item=>item.id>request.id&&item.type==='worker_availability_changed'&&item.details?.worker_id==='W2'&&item.details?.availability==='available');
  workerBReleased=!!request&&run.some(item=>item.id>request.id&&item.type==='worker_availability_changed'&&item.details?.worker_id==='W2'&&item.details?.availability==='available');
  holdCleared=!!hold&&run.some(item=>item.id>hold.id&&item.type==='service_hold_cleared'&&item.details?.table_id==='4'&&item.details?.worker_id==='W1');
  readySent=!!hold&&run.some(item=>item.id>hold.id&&item.type==='worker_availability_changed'&&item.details?.worker_id==='W1'&&item.details?.availability==='available');
}
function freshFloor(){return state&&state.tasks.length===0&&state.requests.length===0&&state.orders.length===0&&state.tables.every(t=>!t.hold?.active&&t.meal_state==='eating')&&state.workers.every(w=>w.availability==='available');}
function startReporterVisit(){
  const target=tableApproach(4);if(plans.has('W1')||sceneMoves.has('W1')||(positions.W1.room===2&&Math.hypot(positions.W1.x-target.x,positions.W1.y-target.y)<8))return;
  const path=routePoints(positions.W1,tableApproach(4),2,'table');sceneMoves.set('W1',{worker:'W1',path,progress:0});
}
async function seedStory(token=generation){
  starting=true;syncPlayback();
  if(!state)return;
  if(!freshFloor())return;
  for(const worker of state.workers){if(worker.availability==='busy')continue;const result=await send(`/api/simulation/workers/${worker.id}/availability`,{availability:'busy'},{token,quiet:true});if(!result||token!==generation)return;}
  storyStarted=true;startReporterVisit();
  $('#stage-caption').textContent='Worker A is visiting Table 4 before reporting back.';
}
async function play(){
  if(playing||starting)return;
  const token=generation;
  detectStory();
  if(!storyStarted&&freshFloor())await seedStory(token);
  if(token!==generation)return;
  detectStory();if(storyStarted&&!holdCleared)startReporterVisit();
  starting=false;detectStory();playing=true;syncPlayback();
  $('#stage-caption').textContent=storyStarted?'Four-beat story running · scripted inputs and backend decisions are shown on the floor.':'Live floor running · existing simulation state is being resumed.';
  if(clockTimer)clearInterval(clockTimer);
  clockTimer=setInterval(()=>{if(!playing||tickBusy)return;tickBusy=true;const tickGen=generation;send('/api/simulation/clock/advance',{seconds:1},{token:tickGen,quiet:true,playOnly:true}).finally(()=>{if(tickGen===generation)tickBusy=false;});},1000);
}
function pause(){playing=false;syncPlayback();$('#stage-caption').textContent='Paused · time, scripted inputs, worker movement, and dwell are held.';}
async function restart(){
  playing=false;starting=true;syncPlayback();generation++;const token=generation;
  if(clockTimer)clearInterval(clockTimer);tickBusy=false;plans.clear();sceneMoves.clear();busyTasks.clear();positions=structuredClone(initialPositions);storyStarted=false;holdReported=false;orderReady=false;waterSent=false;waterReported=false;workerBReleased=false;holdCleared=false;holdSent=false;orderSent=false;waterRequestSent=false;clearSent=false;readySent=false;guestAskedAt=null;waterAskedAt=null;storyBubble=null;state=null;events=[];renderRoutes();renderActors();renderBubbles();
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
  $('#hold-table').disabled=starting;$('#clear-hold').disabled=starting||!tableFor('4')?.hold?.active;
}
function runScript(){
  if(!playing||!storyStarted||!state)return;
  const elapsed=state.clock_seconds,runGeneration=generation;
  const approach=tableApproach(4),atTable4=positions.W1.room===2&&Math.hypot(positions.W1.x-approach.x,positions.W1.y-approach.y)<8;
  if(elapsed>=10&&atTable4&&!guestAskedAt){guestAskedAt=elapsed;storyBubble={source:'table',table:'4',label:'GUEST · TABLE 4',text:'Please delay our mains.',until:elapsed+4};}
  if(guestAskedAt!==null&&elapsed>=guestAskedAt+3&&!holdReported&&!holdSent){holdSent=true;storyBubble={source:'worker',worker:'W1',label:'WORKER A · SIMULATED GLASSES',text:'Table 4 asked us to wait.',until:elapsed+5};send('/api/simulation/tables/4/hold',{worker_id:'W1',source:'kitchen',reason:STORY_HOLD},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)holdSent=false;});$('#stage-caption').textContent='Worker A reported the guest request. The backend has placed a Table 4 hold.';}
  if(holdReported&&elapsed>=20&&!orderReady&&!orderSent){orderSent=true;storyBubble={source:'kitchen',label:'KITCHEN',text:'Table 4 order ready.',until:elapsed+6};send('/api/simulation/tables/4/order-ready',{source:'kitchen',order_id:STORY_ORDER},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)orderSent=false;});$('#stage-caption').textContent='Kitchen reports Table 4 ready. The backend decides whether it can be routed.';}
  if(orderReady&&elapsed>=30&&!waterSent&&!waterRequestSent){waterRequestSent=true;waterAskedAt=elapsed;storyBubble={source:'table',table:'2',label:'GUEST · TABLE 2',text:'Could we have some water?',until:elapsed+4};send('/api/simulation/tables/2/requests',{request_type:'water',request_id:STORY_REQUEST},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)waterRequestSent=false;});}
  if(waterSent&&waterAskedAt===null)waterAskedAt=Math.max(0,elapsed-3);
  if(waterSent&&waterAskedAt!==null&&elapsed>=waterAskedAt+3&&!waterReported){waterReported=true;storyBubble={source:'worker',worker:'W1',label:'WORKER A · SIMULATED GLASSES',text:'Table 2 asked us for water.',until:elapsed+5};$('#stage-caption').textContent='Worker A reports Table 2 water. Worker B becomes available; the backend routes the request.';send('/api/simulation/workers/W2/availability',{availability:'available'},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)waterReported=false;});}
  if(waterReported&&workerBReleased&&!holdCleared&&elapsed>=40){const hold=tableFor('4')?.hold;if(hold?.active){if(hold.reason===STORY_HOLD&&hold.worker_id==='W1'&&!clearSent){clearSent=true;storyBubble={source:'worker',worker:'W1',label:'WORKER A · SIMULATED GLASSES',text:'Table 4 ready now.',until:elapsed+5};$('#stage-caption').textContent='Worker A reports Table 4 ready. The original story hold is cleared.';send('/api/simulation/tables/4/hold/clear',{worker_id:'W1'},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)clearSent=false;});}else $('#stage-caption').textContent='Manual Table 4 hold remains active. Clear it explicitly to continue.';}else if(!clearSent){clearSent=true;storyBubble={source:'worker',worker:'W1',label:'WORKER A · SIMULATED GLASSES',text:'Table 4 ready now.',until:elapsed+5};$('#stage-caption').textContent='Table 4 is clear. Worker A is available for live backend dispatch.';}}
  if(holdCleared&&!readySent){readySent=true;send('/api/simulation/workers/W1/availability',{availability:'available'},{quiet:true,playOnly:true}).then(result=>{if(!result&&generation===runGeneration)readySent=false;});}
  const order=state.orders.find(item=>item.id===STORY_ORDER),water=state.requests.find(item=>item.id===STORY_REQUEST);
  if(elapsed>=60&&order?.status==='delivered'&&water?.status==='fulfilled'){playing=false;syncPlayback();$('#stage-caption').textContent='Story complete · both backend tasks were completed at their tables.';}
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
  const length=Math.max(1,routeLength(plan.path));plan.progress=Math.min(1,plan.progress+dt/(length/80));
  const point=pointOnRoute(plan.path,plan.progress);positions[plan.worker]={...positions[plan.worker],x:point.x,y:point.y};
  if(plan.progress>=1){
    if(plan.stage==='to-source'){plan.stage='source-dwell';plan.dwell=1.1;positions[plan.worker].room=null;}
    else{plan.stage='table-dwell';plan.dwell=1.25;positions[plan.worker].room=plan.room;}
  }
}
function advanceScene(dt){
  if(!playing)return;
  for(const [worker,move] of sceneMoves){move.progress=Math.min(1,move.progress+dt/(Math.max(1,routeLength(move.path))/80));const point=pointOnRoute(move.path,move.progress);positions[worker]={...positions[worker],...point};if(move.progress>=1){positions[worker].room=2;sceneMoves.delete(worker);}}
}
function animate(now){
  const dt=lastFrame?Math.min(.12,(now-lastFrame)/1000):0;lastFrame=now;
  if(playing){advanceScene(dt);for(const plan of plans.values())advancePlan(plan,dt);}
  renderActors();renderBubbles();frameId=requestAnimationFrame(animate);
}
function svgTable(table){
  const point=tableAt[table.id],hold=table.hold?.active;
  const request=state.requests.find(item=>item.table_id===String(table.id)&&item.id===STORY_REQUEST),order=state.orders.find(item=>item.table_id===String(table.id)&&item.id===STORY_ORDER),foodTask=state.tasks.find(item=>item.order_id===STORY_ORDER&&active(item));
  let status='';if(table.id==='4'){status=order?.status==='delivered'?'DELIVERED':foodTask?.status==='assigned'?'EN ROUTE':foodTask?.status==='offered'?`OFFER ${foodTask.offered_to}`:hold&&order?'HELD · READY':hold?'HELD':order?'READY':'';}else if(table.id==='2'){const task=state.tasks.find(item=>item.request_id===STORY_REQUEST&&active(item));status=request?.status==='fulfilled'?'WATER SERVED':task?.status==='assigned'?'WATER MOVING':task?.status==='offered'?`WATER · ${task.offered_to}`:request?'WATER ASKED':'';}
  const highlight=!!status||!!hold;
  return `<g class="table-unit ${highlight?'highlight':''}" aria-label="Table ${esc(table.id)}${status?`, ${esc(status)}`:''}"><title>Table ${esc(table.id)}${status?` · ${esc(status)}`:''}</title><circle class="guest-token" cx="${point.x-36}" cy="${point.y-27}" r="15"><title>Guest</title></circle><path class="guest-token-path" d="M${point.x-36} ${point.y-21}v10m-8-7q8-8 16 0"/><circle class="guest-token" cx="${point.x+36}" cy="${point.y+28}" r="15"><title>Guest</title></circle><path class="guest-token-path" d="M${point.x+36} ${point.y+34}v10m-8-7q8-8 16 0"/><rect class="table-top" x="${point.x-48}" y="${point.y-31}" width="96" height="62" rx="17"/><ellipse class="table-plate" cx="${point.x}" cy="${point.y}" rx="19" ry="15"/><text class="table-id" x="${point.x}" y="${point.y-2}">T${esc(table.id)}</text>${status?`<text class="table-status" x="${point.x}" y="${point.y+51}">${esc(status)}</text>`:''}</g>`;
}
function renderTables(){
  const items=state?.tables||[];$('#table-layer').innerHTML=items.map(svgTable).join('');
  const hold=tableFor('4')?.hold;$('#hold-table').disabled=starting;$('#clear-hold').disabled=starting||!hold?.active;$('#hold-table').textContent=hold?.active?'Change to manual hold':'Set manual hold';
}
function renderActors(){
  const layer=$('#worker-layer');if(!layer)return;
  const workers=state?.workers||[{id:'W1',availability:'available'},{id:'W2',availability:'available'},{id:'W3',availability:'available'}];
  layer.innerHTML=workers.map(worker=>{const p=positions[worker.id]||initialPositions[worker.id],plan=plans.get(worker.id),klass=`worker-token ${worker.availability==='available'?'':'busy'} ${plan?.blocked?'blocked':''}`;return `<g id="actor-${esc(worker.id)}" class="${klass}" transform="translate(${p.x} ${p.y})"><circle class="worker-ring" r="25"/><circle class="token-body" r="19"/><text class="token-letter" y="1">${esc(worker.id)}</text><text class="worker-name" y="37">${esc(worker.id)}</text></g>`;}).join('');
}
function renderRoutes(){
  const layer=$('#route-layer');if(!layer)return;
  const taskPaths=[...plans.values()].filter(plan=>plan.path?.length>1).map(plan=>`<path class="route-path ${plan.blocked?'blocked':''} ${playing?'':'paused'}" d="${plan.path.map((p,i)=>`${i?'L':'M'}${p.x} ${p.y}`).join(' ')}" />`);
  const visitPaths=[...sceneMoves.values()].filter(move=>move.path?.length>1).map(move=>`<path class="route-path story-visit ${playing?'':'paused'}" d="${move.path.map((p,i)=>`${i?'L':'M'}${p.x} ${p.y}`).join(' ')}" />`);layer.innerHTML=[...visitPaths,...taskPaths].join('');
}
function renderActorsAndRoutes(){renderRoutes();renderActors();}
function renderBubbles(){
  const layer=$('#bubble-layer');if(!layer)return;
  const bubbles=[];
  if(storyBubble&&state&&state.clock_seconds<=storyBubble.until){let x=30,y=100;if(storyBubble.source==='table'){const p=tableAt[storyBubble.table];x=Math.max(24,Math.min(720,p.x-128));y=Math.max(108,p.y-96);}else if(storyBubble.source==='worker'){const p=positions[storyBubble.worker];x=Math.max(20,Math.min(720,p.x-120));y=Math.max(110,p.y-105);}else{x=18;y=500;}
    bubbles.push(`<g class="speech-bubble story-bubble" transform="translate(${x} ${y})"><rect width="260" height="54" rx="11"/><path d="M56 53l8 10 11-10"/><text x="11" y="19">${esc(storyBubble.label)}</text><text x="11" y="40">${esc(storyBubble.text)}</text></g>`);}
  const plan=[...plans.values()].find(item=>item.stage==='to-source'||item.stage==='to-table'||item.stage==='blocked'||item.stage==='source-dwell'||item.stage==='table-dwell');
  if(plan){const p=positions[plan.worker],copy=actorMessage(plan).slice(0,37),x=Math.max(15,Math.min(765,p.x-65)),y=Math.max(112,p.y-85);bubbles.push(`<g class="speech-bubble actor-bubble" transform="translate(${x} ${y})"><rect width="220" height="45" rx="11"/><path d="M62 44l8 10 11-10"/><text x="11" y="18">Simulated glasses · ${esc(plan.worker)}</text><text x="11" y="36">${esc(copy)}</text></g>`);}
  layer.innerHTML=bubbles.join('');
}
function captionFor(item){
  const d=item.details||{},worker=d.worker_id||'Worker',table=d.table_id?`Table ${d.table_id}`:'';
  if(item.type==='task_offered')return{label:'Brain · dispatch',text:`Offer to ${worker}: ${d.reason||item.message}`};
  if(item.type==='routing_decision')return{label:'Brain · waiting',text:item.message};
  if(item.type==='task_accepted')return{label:`${worker} · simulated glasses / phone`,text:`Accepting ${nice(d.task_type||'task')} ${table}.`};
  if(item.type==='task_hold_conflict')return{label:`${worker} · simulated glasses / phone`,text:`${table} is held. Pausing here and acknowledging the conflict.`};
  if(item.type==='task_conflict_acknowledged')return{label:`${worker} · simulated glasses / phone`,text:`Conflict acknowledged. Waiting for the hold to clear.`};
  if(item.type==='service_hold_started'&&d.reason===STORY_HOLD)return{label:'Guest · Table 4',text:'“Please delay our mains.” · Worker A reports: “Table 4 asked us to wait.”'};
  if(item.type==='order_ready'&&d.order_id===STORY_ORDER)return{label:'Kitchen',text:'Table 4 order ready.'};
  if(item.type==='request_created'&&d.request_id===STORY_REQUEST)return{label:'Guest · Table 2',text:'“Could we have some water?” · Worker A reports the request.'};
  if(item.type==='service_hold_cleared'&&d.table_id==='4'&&d.worker_id==='W1')return{label:'Worker A · simulated glasses / phone',text:'“Table 4 ready now.” The hold is cleared.'};
  if(item.type==='service_hold_started'||item.type==='service_hold_updated')return{label:`${worker} · simulated glasses / phone`,text:`${table} is blocked: ${d.reason||'service hold'}.`};
  if(item.type==='service_hold_cleared')return{label:`${worker} · simulated glasses / phone`,text:`${table} hold cleared. The blocked delivery can continue.`};
  if(item.type==='task_completed')return d.order_id||d.request_id?null:{label:`${worker} · simulated glasses / phone`,text:item.message};
  if(item.type==='order_delivered'||item.type==='request_fulfilled')return{label:'Backend · completed',text:item.message};
  return null;
}
function renderCaptions(){
  const current=storyEvents().slice().reverse().map(captionFor).filter(Boolean).slice(0,8).map(caption=>`<li><strong>${esc(caption.label)}</strong>${esc(caption.text)}</li>`);
  $('#caption-feed').innerHTML=current.join('')||'<li class="caption-empty">Captions appear from actual task and event messages.</li>';
}
function renderBrain(){
  if(!state)return;
  const assigned=state.tasks.filter(item=>item.status==='assigned').at(-1),offered=state.tasks.find(item=>item.status==='offered'),pending=state.tasks.find(item=>item.status==='pending');
  const activeTask=assigned||offered||pending;
  const completed=state.orders.some(item=>item.id===STORY_ORDER&&item.status==='delivered')&&state.requests.some(item=>item.id===STORY_REQUEST&&item.status==='fulfilled');
  if(assigned){const plan=plans.get(assigned.worker_id);$('#brain-message').textContent=plan?.blocked?`${assigned.worker_id} is stopped at Table ${assigned.table_id}. The task remains assigned until the hold clears.`:`${assigned.worker_id} is ${plan?actorMessage(plan).toLowerCase():'assigned to '+taskName(assigned)}.`;$('#decision-meta').textContent=assigned.decision_reason||`Backend assigned ${taskName(assigned)} to ${assigned.worker_id}.`;$('#active-instruction').textContent=`${assigned.worker_id} · ${taskName(assigned)}`;}
  else if(offered){$('#brain-message').textContent=`Offer to ${offered.offered_to}: ${offered.decision_reason||taskName(offered)}.`;$('#decision-meta').textContent=`Live dispatch · ${taskName(offered)} · ${clockText(offered.wait_seconds)} eligible wait`;$('#active-instruction').textContent=`Offer · ${taskName(offered)}`;}
  else if(pending){$('#brain-message').textContent=pending.decision_reason||`Waiting to route ${taskName(pending)}.`;$('#decision-meta').textContent=`Pending · ${taskName(pending)} · ${clockText(pending.wait_seconds)} eligible wait`;$('#active-instruction').textContent=`Waiting · ${taskName(pending)}`;}
  else if(completed){$('#brain-message').textContent='Table 4 delivered. Table 2 water served.';$('#decision-meta').textContent='Both tasks were completed through live backend dispatch.';$('#active-instruction').textContent='Story complete · 2 tasks done';}
  else{$('#brain-message').textContent=storyStarted&&playing?'No active tasks. Waiting for the next story event.':'Waiting for simulated floor events.';$('#decision-meta').textContent='Backend task routing is the source of every assignment.';$('#active-instruction').textContent='No active task';}
  const beat=holdCleared?4:workerBReleased?3:orderReady?2:1;$('#beat-count').textContent=`${beat} / 4`;$('#beat-title').textContent=[holdReported?'Worker A reports the Table 4 delay':'Worker A visits Table 4','Kitchen ready · hold blocks delivery','Worker A reports Table 2 water · B is dispatched',completed?'Table 4 delivered · water served':'Worker A clears the hold · delivery moves'][beat-1];
}
function render(){
  if(!state)return;
  $('#floor-clock').textContent=clockText(state.clock_seconds);renderTables();renderActorsAndRoutes();renderBubbles();renderCaptions();renderBrain();
  const order=state.orders.find(item=>item.id===STORY_ORDER),waterTask=state.tasks.some(item=>item.request_id===STORY_REQUEST&&active(item));
  $('#kitchen-state').textContent=order?.status==='ready'?'Table 4 order ready':order?.status==='delivered'?'Table 4 delivered':'Waiting for ticket';$('#bar-state').textContent=waterTask?'Table 2 water pickup':'Water pickup';
}
async function inputAction(action){
  if(!state)return;
  const table=tableFor('4');
  if(action==='hold'){
    await send('/api/simulation/tables/4/hold',{worker_id:table?.hold?.active?table.hold.worker_id:'W1',source:'kitchen',reason:'Manual floor hold'});return;
  }
  if(action==='clear'){
    if(table?.hold?.active)await send('/api/simulation/tables/4/hold/clear',{worker_id:table.hold.worker_id});return;
  }
}
$('#play').addEventListener('click',play);$('#pause').addEventListener('click',pause);$('#restart').addEventListener('click',restart);
$('#hold-table').addEventListener('click',()=>inputAction('hold'));$('#clear-hold').addEventListener('click',()=>inputAction('clear'));
refresh();refreshTimer=setInterval(()=>refresh(),800);frameId=requestAnimationFrame(animate);
