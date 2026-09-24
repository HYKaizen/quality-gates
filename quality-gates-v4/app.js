'use strict';
let S, selected=null, gate=null, view='trailers', filter='all';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const can=(...roles)=>roles.includes(S.user.role);
const date=v=>new Date(v*1000).toLocaleString();
async function api(path,data){
  const r=await fetch('/api/'+path,data?{method:'POST',headers:{'Content-Type':'application/json','X-QG-Request':'1'},body:JSON.stringify(data)}:{});
  const result=await r.json(); if(!r.ok)throw Error(result.error||'Request failed');return result;
}
function input(label,name,value='',type='text'){return `<label>${esc(label)}</label><input name="${name}" type="${type}" value="${esc(value)}" required>`;}
function select(label,name,values){return `<label>${esc(label)}</label><select name="${name}">${values.map(v=>`<option value="${esc(v.value??v)}">${esc(v.label??v)}</option>`).join('')}</select>`;}
function note(label='Reason / evidence'){return `<label>${esc(label)}</label><textarea name="reason" required maxlength="2000"></textarea>`;}
function signature(){return `<div class="signature-box"><strong>Digital signature</strong><p>Re-enter your password. The approval content, identity and time will be sealed in the audit record.</p>${input('Your password','signaturePassword','','password')}</div>`;}
function controlFields(i={},operation=''){
  return select('Operation','operation',S.template.gates.map(g=>({value:g.id,label:g.name})).sort((a,b)=>a.value===operation?-1:b.value===operation?1:0))+
    input('Display number','number',i.number||'')+input('Section','section',i.section||'General')+
    `<label>Description</label><textarea name="description" required>${esc(i.description||'')}</textarea>`+
    `<label>Models (comma separated, blank = all)</label><input name="models" value="${esc((i.models||[]).join(', '))}">`+
    `<label>Required option (blank = any)</label><input name="option" value="${esc(i.option||'')}">`;
}
function modal(title,fields,submit){
  $('fields').innerHTML=`<h2>${esc(title)}</h2>${fields}`;$('form-error').textContent='';$('dialog').showModal();
  $('form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;
    try{await submit(Object.fromEntries(new FormData(e.target)));$('dialog').close();}catch(error){$('form-error').textContent=error.message;}finally{button.disabled=false;}};
}
$('cancel').onclick=()=>$('dialog').close();
async function refresh(){S=await api('state');render();$('message').textContent='';}
async function change(data){S=await api('action',data);render();}
function command(action,t,extra={}){return change({action,trailer:t.id,revision:t.revision,...extra});}
function button(label,action,extra='',cls=''){return `<button class="${cls}" data-action="${action}" ${extra}>${esc(label)}</button>`;}
function counts(t){const items=t.gates.flatMap(g=>g.items);return {total:items.length,done:items.filter(i=>['pass','na'].includes(t.checks[i.id]?.status)).length,open:items.filter(i=>['rejected','hold','verification'].includes(t.checks[i.id]?.status)).length};}
function nav(){return `<nav>${['trailers','work','checklist','admin','users'].filter(v=>!['admin','users'].includes(v)||can('admin')).map(v=>button({trailers:'Trailers',work:'Open work',checklist:'Checklist versions',admin:'Admin panel',users:'Team'}[v],'view',`data-view="${v}"`,view===v?'active':'')).join('')}${button('Refresh','refresh')}</nav>`;}
function render(){
  $('identity').innerHTML=`${esc(S.user.name)} · ${esc(S.user.role)} ${button('Sign out','logout')}`;
  let content=nav();
  if(view==='users'){
    content+=`<div class="split"><h1>Team</h1>${button('Add user','user','','primary')}</div><div class="grid">${S.users.map(u=>`<div class="card"><h3>${esc(u.name)}</h3>${esc(u.username)}<p>${esc(u.role)} · ${esc(u.department)}</p></div>`).join('')}</div>`;
  }else if(view==='admin'){
    const active=S.template.gates.filter(g=>g.active!==false);
    content+=`<div class="split"><div><h1>Admin panel</h1><p class="muted">Define the operation flow for new trailers and make controlled POD transfers.</p></div>${button('Add operation','operation-add','','primary')}</div>`;
    content+=`<h2>Operation definitions · version ${S.template.version}</h2><p>Changes are published as a new version. Existing trailers retain their original operation plan.</p><div class="operation-list">${S.template.gates.map((g,n)=>`<div class="card operation-card"><div class="operation"><div><small>${n+1}</small><h3>${esc(g.name)}</h3><span class="badge ${g.active===false?'rejected':'pass'}">${g.active===false?'Inactive':'Active'}</span> · <small>${g.items.length} controls · ${esc(g.id)}</small></div><div class="actions">${button('↑','operation-move',`data-operation="${g.id}" data-direction="up" ${n===0?'disabled':''}`)}${button('↓','operation-move',`data-operation="${g.id}" data-direction="down" ${n===S.template.gates.length-1?'disabled':''}`)}${button('Edit operation','operation-edit',`data-operation="${g.id}"`)}${button('Add control','control-add',`data-operation="${g.id}"`,'primary')}</div></div><details class="admin-controls"><summary>View ${g.items.length} controls</summary>${g.items.length?g.items.map(i=>`<div class="admin-control"><div><strong>${esc(i.number)} · ${esc(i.description)}</strong><small>${esc(i.section)} · ${esc(i.id)}</small></div>${button('Edit / move','control-edit',`data-operation="${g.id}" data-item="${i.id}"`)}</div>`).join(''):'<p class="muted">No controls are defined for this operation.</p>'}</details></div>`).join('')}</div>`;
    content+=`<h2>POD transfers</h2><p>Transfers do not erase inspection results. Every transfer requires a reason and the approver's digital signature.</p><div class="grid">${S.trailers.filter(t=>!t.approved).map(t=>{const op=t.gates.find(g=>g.id===(t.currentOperation||t.gates[0]?.id));return `<div class="card"><h3>${esc(t.inventory)}</h3><p>Current: <strong>${esc(op?.name||'Not assigned')}</strong></p>${button('Transfer POD','transfer',`data-trailer="${t.id}"`)}</div>`;}).join('')}</div>`;
    if(!active.length)content+='<p class="panel">No active operations.</p>';
  }else if(view==='checklist'){
    content+=`<h1>Checklist · version ${S.template.version}</h1><p>Published changes apply to new trailers. Existing trailers retain their original checklist. Empty model and option rules include all trailers.</p><p class="muted">Source: v3 full checklist. Item IDs are permanent; displayed numbers are labels. Applicability rules require your production specifications.</p>`;
    content+=S.template.gates.map(g=>`<details><summary>${esc(g.name)} · ${g.items.length} controls</summary>${g.items.map(i=>`<div class="item"><h3>${esc(i.number)} · ${esc(i.description)}</h3><small>${esc(i.id)} · Models: ${esc(i.models.join(', ')||'All')} · Option: ${esc(i.option||'Any')}</small>${can('admin','supervisor')?button('Edit and publish','template',`data-item="${i.id}"`):''}</div>`).join('')}</details>`).join('');
  }else if(view==='work'){
    content+='<h1>Open work</h1><p>Production reports corrections; QC verifies them. Deferred issues remain open until verified.</p>';
    let n=0;
    for(const t of S.trailers)for(const g of t.gates)for(const i of g.items){const c=t.checks[i.id];if(!c||!['rejected','hold','verification'].includes(c.status))continue;if(can('production')&&c.department!==S.user.department)continue;n++;content+=`<div class="panel"><small>${esc(t.inventory)} · ${esc(g.name)} · ${Math.floor((Date.now()/1000-(c.openedAt||c.at))/3600)}h open</small>${item(t,i)}</div>`;}
    if(!n)content+='<div class="panel">No open work for your team.</div>';
  }else if(selected&&S.trailers.some(t=>t.id===selected)){
    const t=S.trailers.find(t=>t.id===selected),c=counts(t);gate=gate&&t.gates.some(g=>g.id===gate)?gate:t.gates[0].id;
    const current=t.gates.find(g=>g.id===(t.currentOperation||t.gates[0]?.id));
    content+=`${button('← All trailers','back')}<div class="split"><div><h1>${esc(t.inventory)} · ${esc(t.model)}</h1><p>${esc(t.customer)} · Checklist v${t.version} · Revision ${t.revision}</p><span class="badge hold">Current operation: ${esc(current?.name||'Not assigned')}</span></div><div>${button('History','history',`data-trailer="${t.id}"`)} ${can('admin','supervisor')&&!t.approved?button('Transfer POD','transfer',`data-trailer="${t.id}"`):''} ${can('admin','supervisor')?button(t.approved?'Reopen':'Final approval',t.approved?'reopen':'approve',`data-trailer="${t.id}"`):''}</div></div>`;
    if(t.approved)content+=`<p class="badge pass">Approved by ${esc(t.approved.by)} · ${date(t.approved.at)}${t.approved.signature?.verifiedSeal?' · ✓ signature seal verified':''}</p>`;
    content+=`<div class="stats"><div><strong>${c.done}/${c.total}</strong>Verified / N/A</div><div><strong>${c.open}</strong>Open issues</div><div><strong>${Object.keys(t.releases).length}/${t.gates.length}</strong>Gates released</div></div>`;
    content+=`<nav>${t.gates.map(g=>button(`${g.name}${t.releases[g.id]?' ✓':''}`,'gate',`data-gate="${g.id}"`,gate===g.id?'active':'')).join('')}</nav>`;
    const g=t.gates.find(g=>g.id===gate);
    const activity=t.operationActivity?.[g.id],contributors=(activity?.contributors||[]).map(c=>c.name).join(', ');
    content+=`<div class="operation-status"><div><strong>${esc(g.name)}</strong>${activity?`<small>Started by ${esc(activity.startedBy)} · ${date(activity.startedAt)}<br>Quality contributors: ${esc(contributors)}</small>`:'<small>Quality inspection has not started.</small>'}</div>${!t.approved&&can('qc','supervisor','admin')&&g.id===(t.currentOperation||t.gates[0]?.id)?button(activity?'Join / continue operation':'Start operation','start-operation',`data-trailer="${t.id}" data-gate="${g.id}"`,'primary'):''}</div>`;
    content+=`<div class="toolbar">${can('admin','supervisor')&&!t.approved?button('Release gate','release',`data-trailer="${t.id}" data-gate="${g.id}"`):''}${t.releases[g.id]?.signature?.verifiedSeal?'<span class="badge pass">✓ Release signature verified</span>':''}<select id="filter" aria-label="Filter controls">${['all','pending','rejected','hold','verification','pass','na'].map(f=>`<option ${filter===f?'selected':''}>${f}</option>`).join('')}</select></div>`;
    const sections=[...new Set(g.items.map(i=>i.section))];
    content+=sections.map(section=>{const items=g.items.filter(i=>i.section===section&&(filter==='all'||(t.checks[i.id]?.status||'pending')===filter));return items.length?`<details open><summary>${esc(section)} · ${items.length}</summary>${items.map(i=>item(t,i)).join('')}</details>`:'';}).join('');
    if(!g.items.length)content+='<p>No applicable controls in this gate; supervisor can release it.</p>';
  }else{
    content+=`<div class="split"><div><h1>Production quality</h1><p class="muted">Find issues, assign corrections, verify completion.</p></div>${can('admin','supervisor','qc')?button('New trailer','create','','primary'):''}</div><div class="grid">`;
    content+=S.trailers.map(t=>{const c=counts(t);return `<article class="card"><span class="badge ${t.approved?'pass':''}">${t.approved?'Approved':'In progress'}</span><h2>${esc(t.inventory)}</h2><p>${esc(t.customer)} · ${esc(t.model)}</p><div class="progress"><div style="width:${100*c.done/c.total}%"></div></div><small>${c.done}/${c.total} verified · ${c.open} open · v${t.version}</small><br>${button('Open trailer','open',`data-trailer="${t.id}"`)}</article>`;}).join('')+'</div>';
    if(!S.trailers.length)content+='<div class="panel">Create the first trailer to start a quality record.</div>';
  }
  $('app').innerHTML=content;
  if($('filter'))$('filter').onchange=e=>{filter=e.target.value;render();};
}
function item(t,i){
  const c=t.checks[i.id]||{status:'pending'},attrs=`data-trailer="${t.id}" data-item="${i.id}"`;let actions='';
  if(!t.approved){
    if(can('qc','supervisor','admin')){
      if(['pending','pass'].includes(c.status))actions+=button('Pass','pass',attrs);
      actions+=button('Reject / assign','reject',attrs,'danger')+button('Hold','hold',attrs);
      if(c.status==='verification')actions+=button('QC verification','verify',attrs,'primary');
    }
    if(can('supervisor','admin')){
      if(c.status==='pending')actions+=button('N/A','na',attrs);
      if(c.status==='hold')actions+=button('Approve hold','hold-pass',attrs);
      if(['rejected','hold','verification'].includes(c.status))actions+=button('Conditional passage','defer',attrs);
    }
    if(can('production')&&c.department===S.user.department&&c.status==='rejected')actions+=button('Report correction','correct',attrs,'primary');
    if(!can('production')||c.department===S.user.department)actions+=button('Attach evidence','media',attrs);
  }
  return `<div class="item"><div class="item-head"><h3>${esc(i.number)} · ${esc(i.description)}</h3><span class="badge ${c.status}">${esc(c.status)}</span></div><small>${esc(i.id)}</small>${c.reason?`<p>${esc(c.reason)}</p>`:''}${c.department?`<small>Assigned: ${esc(c.department)}</small>`:''}${c.deferredTo?`<p class="badge hold">Due before ${esc(c.deferredTo)} release · ${esc(c.deferralReason)}</p>`:''}${c.correction?`<p>Correction: ${esc(c.correction)} · ${esc(c.correctedBy)}</p>`:''}${c.verification?`<p>QC: ${esc(c.verification)}</p>`:''}${c.by?`<small>${esc(c.by)} · ${date(c.at)}</small>`:''}${c.signature?`<small class="signed">${c.signature.verifiedSeal?'✓':'⚠'} Digitally signed by ${esc(c.signature.name)} · ${date(c.signature.at)} · ${esc(c.signature.digest.slice(0,12))}</small>`:''}<div>${(c.media||[]).map(m=>`<a target="_blank" rel="noopener" href="/api/media/${m.id}">${esc(m.name)}</a> `).join('')}</div><div class="actions">${actions}</div></div>`;
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-action]');if(!b)return;
  const a=b.dataset.action,t=S?.trailers.find(t=>t.id===b.dataset.trailer),key=b.dataset.item;
  try{
    if(a==='logout'){await api('logout',{});location.reload();return;}
    if(a==='refresh'){await refresh();return;}
    if(a==='view'){view=b.dataset.view;selected=null;render();return;}
    if(a==='open'){selected=t.id;view='trailers';gate=null;render();return;}
    if(a==='back'){selected=null;render();return;}
    if(a==='gate'){gate=b.dataset.gate;render();return;}
    if(a==='operation-add'){const version=S.template.version;modal('Add operation',input('Operation name','name'),d=>change({action:a,version,...d}));return;}
    if(a==='operation-edit'){const op=S.template.gates.find(g=>g.id===b.dataset.operation),version=S.template.version;modal('Edit operation',input('Operation name','name',op.name)+`<label class="checkbox"><input type="checkbox" name="active" ${op.active===false?'':'checked'}> Active for new trailers</label><p>Existing trailer plans are not changed.</p>`,d=>change({action:a,version,operation:op.id,name:d.name,active:d.active==='on'}));return;}
    if(a==='operation-move'){b.disabled=true;await change({action:a,version:S.template.version,operation:b.dataset.operation,direction:b.dataset.direction});return;}
    if(a==='control-add'){const version=S.template.version,operation=b.dataset.operation;modal('Add control',controlFields({},operation),d=>change({action:a,version,...d,models:d.models.split(',').map(x=>x.trim()).filter(Boolean)}));return;}
    if(a==='control-edit'){const source=S.template.gates.find(g=>g.id===b.dataset.operation),i=source.items.find(i=>i.id===key),version=S.template.version;modal('Edit or move control',controlFields(i,source.id)+`<p>Permanent ID: ${esc(i.id)}. Existing trailer records are unchanged.</p>`,d=>change({action:a,version,item:i.id,...d,models:d.models.split(',').map(x=>x.trim()).filter(Boolean)}));return;}
    if(a==='create'){modal('New trailer',input('Inventory number','inventory')+input('Customer','customer')+input('Model (match checklist rules exactly)','model')+`<label>Installed options (comma separated)</label><input name="options">`,d=>change({action:'create',...d,options:d.options.split(',').map(v=>v.trim()).filter(Boolean)}));return;}
    if(a==='user'){modal('Add team member',input('Name','name')+input('Username','username')+input('Password (12+ characters)','password','','password')+select('Role','role',['qc','production','supervisor','admin'])+select('Department (required for production)','department',S.departments),d=>change({action:'user',...d}));return;}
    if(a==='template'){const i=S.template.gates.flatMap(g=>g.items).find(i=>i.id===key),version=S.template.version;modal('Publish checklist revision',input('Display number','number',i.number)+`<label>Description</label><textarea name="description" required>${esc(i.description)}</textarea><label>Models (comma separated, blank = all)</label><input name="models" value="${esc(i.models.join(', '))}"><label>Required option (blank = any)</label><input name="option" value="${esc(i.option)}"><p>Permanent ID: ${esc(i.id)}. Existing trailer records retain their checklist.</p>`,d=>change({action:'template',item:key,version,...d,models:d.models.split(',').map(x=>x.trim()).filter(Boolean)}));return;}
    if(a==='history'){const rows=await api('history/'+t.id);modal('Record history',rows.map(r=>`<div class="history"><strong>${esc(r.action)}</strong> · ${esc(r.name)}<small>${date(r.at)}</small><pre>${esc(JSON.stringify(r.body,null,2))}</pre></div>`).join('')||'<p>No history.</p>',async()=>{});return;}
    if(a==='start-operation'){b.disabled=true;await command(a,t,{gate:b.dataset.gate});return;}
    if(a==='transfer'){const current=t.currentOperation||t.gates[0]?.id,targets=t.gates.filter(g=>g.id!==current);if(!targets.length)throw Error('No other operation is available');modal('Transfer POD',select('Target operation','target',targets.map(g=>({value:g.id,label:g.name})))+note('Transfer reason')+signature(),d=>command(a,t,d));return;}
    if(a==='pass'){modal('Approve control',`<p>This quality decision will be digitally signed.</p>${signature()}`,d=>command(a,t,{item:key,...d}));return;}
    if(a==='approve'||a==='release'){modal(a==='approve'?'Final approval':'Release gate','<p>The server checks all prerequisites. Open issues block final approval. Conditional passage requires a later closure gate.</p>'+signature(),d=>command(a,t,{gate:b.dataset.gate,...d}));return;}
    if(a==='media'){modal('Attach evidence','<p>Photo, video or audio · maximum 10 MB</p><input type="file" id="attachment" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/webm,audio/mpeg,audio/mp4,audio/ogg" required>',async()=>{const f=$('attachment').files[0];if(!f||f.size>10*1024*1024)throw Error('Choose a file under 10 MB');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=reject;reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(f);});await command(a,t,{item:key,name:f.name,mime:f.type,content});});return;}
    let fields=note(a==='correct'?'Describe the completed correction':a==='verify'?'QC verification evidence':'Reason / evidence');
    if(a==='reject')fields=select('Responsible department','department',S.departments)+fields;
    if(a==='verify')fields=select('QC decision','result',[{value:'pass',label:'Verified — pass'},{value:'rejected',label:'Still defective — return to department'}])+fields;
    if(a==='defer'){const index=t.gates.findIndex(g=>g.items.some(i=>i.id===key));const later=t.gates.slice(index+1).filter(g=>!t.releases[g.id]);if(!later.length)throw Error('No unreleased later gate available');fields=select('Must close before this gate is released','target',later.map(g=>({value:g.id,label:g.name})))+fields;}
    if(['verify','na','defer','hold-pass'].includes(a))fields+=signature();
    modal({reject:'Reject and assign',hold:'Escalate for supervisor decision',na:'Mark not applicable',correct:'Report correction',verify:'QC verification',defer:'Authorize conditional passage','hold-pass':'Approve held control',reopen:'Reopen approved trailer'}[a]||a,fields,d=>command(a,t,{item:key,...d}));
  }catch(error){$('message').textContent=error.message;}finally{b.disabled=false;}
});
async function start(){
  if(location.protocol==='file:'){
    $('app').innerHTML='<div class="panel auth"><h1>Server required</h1><p>This application cannot run by opening index.html directly.</p><p>Double-click <strong>START-QUALITY-GATES.command</strong>, then use:</p><p><a href="http://127.0.0.1:8040/">http://127.0.0.1:8040/</a></p></div>';
    return;
  }
  try{
    const setup=await api('setup');
    if(!setup.required){try{await refresh();return;}catch(error){if(error.message!=='Please sign in')throw error;}}
    $('app').innerHTML=`<form class="panel auth" id="login"><h1>${setup.required?'Create administrator':'Sign in'}</h1>${setup.required?input('Setup token from server terminal','token')+input('Your name','name'):''}${input('Username','username')}${input('Password','password','','password')}<p id="login-error" role="alert"></p><button class="primary">${setup.required?'Create account':'Sign in'}</button></form>`;
    $('login').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await api(setup.required?'setup':'login',Object.fromEntries(new FormData(e.target)));await start();}catch(error){$('login-error').textContent=error.message;}finally{b.disabled=false;}};
  }catch(error){$('app').textContent=error.message;}
}
start();
