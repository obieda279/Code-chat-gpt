'use strict';
const $ = id => document.getElementById(id);
const meta = {
  moderation:['Moderation','Keep conversations safe with clear rules and accountable actions.','M'],
  automod:['Auto moderation','Filter spam, duplicate messages, unwanted links, and mass mentions.','A'],
  welcome:['Welcome & goodbye','Give members a warm arrival and the right starting roles.','W'],
  logs:['Activity logs','Route moderation and community activity to dedicated channels.','L'],
  reaction_roles:['Self-service roles','Let members choose their interests with buttons or reactions.','R'],
  suggestions:['Suggestions','Give good ideas a place to grow with voting and staff decisions.','S'],
  polls:['Polls','Make decisions together with timed, persistent voting.','P'],
  starboard:['Starboard','Keep the community’s favorite moments in the spotlight.','★'],
  giveaways:['Giveaways','Run timed draws with one entry per member and rerolls.','G'],
  messages:['Message templates','Save announcements and send them whenever you need them.','T'],
  translation:['Translation','Translate messages and transcribe audio with optional providers.','↗'],
  tickets:['Support tickets','Create private conversations between members and your team.','?'],
  birthdays:['Birthdays','Remember your members with a thoughtful birthday greeting.','B'],
  alliances:['Alliances','Organize your roster, R4 officers, and R5 leadership.','⚑'],
  presidents:['Presidents','Manage terms, appointments, and server-wide decrees.','♜'],
  armory:['Armory rotation','Coordinate eight weekly slots across your alliances.','8'],
  apc:['APC rankings','Track main and second APC power with optional private rankings.','↗'],
  duel:['Duel reminders','Send the right strategy at the right time, every day.','D'],
  events:['Scheduled events','Plan recurring events, countdowns, and reminders.','◷']
};
const dws = ['alliances','presidents','armory','apc','duel','events'];
const navigation = [['overview','Overview'],['community','Community tools'],['events','Events'],['dws','Dark War Survival'],['settings','Server settings'],['activity','Activity & jobs']];
const state = {user:null,guilds:[],guild:null,schema:null,modules:[],draft:null,saved:null,version:0,channels:[],roles:[],overview:null,events:{series:[],occurrences:[]},view:'overview',dirty:false,canEdit:false,invalid:new Set()};
function node(tag, cls, text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function toast(text,error=false){const t=$('toast');t.textContent=text;t.classList.toggle('error',error);t.classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.add('hidden'),5500);}
async function api(path,options={}){options.headers={...(options.headers||{})};if(options.body){options.headers['Content-Type']='application/json';options.headers['X-CSRF-Token']=state.user?.csrf||'';options.body=JSON.stringify(options.body);}const res=await fetch(path,options);const data=await res.json();if(!res.ok){const e=new Error(data.error||'The request failed.');e.status=res.status;throw e;}return data;}
function markDirty(){state.dirty=JSON.stringify(state.draft)!==JSON.stringify(state.saved);$('savebar').classList.toggle('hidden',!state.dirty);$('save').disabled=!state.canEdit||state.invalid.size>0;}
function resolve(schema){return schema.$ref?state.schema.$defs[schema.$ref.split('/').pop()]:schema;}
function human(key){return key.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());}
function field(parent,key,schema,obj,path){
  schema=resolve(schema);let nullable=false;
  if(schema.anyOf){nullable=schema.anyOf.some(x=>x.type==='null');schema=resolve(schema.anyOf.find(x=>x.type!=='null'));}
  const val=obj[key],label=human(key),id=path+'.'+key;
  if(schema.type==='object'&&schema.properties){const fs=node('fieldset');fs.append(node('legend','',label));for(const [k,s] of Object.entries(schema.properties))field(fs,k,s,obj[key],id);parent.append(fs);return;}
  const wrapper=node('label','field'),caption=node('span','field-label',label);wrapper.append(caption);let input;
  if(schema.type==='boolean'){input=node('input');input.type='checkbox';input.checked=Boolean(val);input.onchange=()=>{obj[key]=input.checked;markDirty();};}
  else if(schema.enum){input=node('select');for(const choice of schema.enum){const opt=node('option','',choice);opt.value=choice;input.append(opt);}input.value=val;input.onchange=()=>{obj[key]=input.value;markDirty();};}
  else if(schema.type==='array'||schema.type==='object'){
    input=node('textarea');input.rows=schema.type==='object'||resolve(schema.items||{}).type==='object'?8:3;input.value=JSON.stringify(val,null,2);
    input.oninput=()=>{try{const parsed=JSON.parse(input.value);if(schema.type==='array'&&!Array.isArray(parsed))throw Error();if(schema.type==='object'&&(parsed===null||Array.isArray(parsed)||typeof parsed!=='object'))throw Error();obj[key]=parsed;state.invalid.delete(id);input.classList.remove('field-error');}catch{state.invalid.add(id);input.classList.add('field-error');}markDirty();};
    wrapper.append(node('small','',schema.type==='array'?'Enter a JSON list. Role and channel IDs are strings; use the reference below.':'Enter a JSON object. Weekday template keys run from "1" (Monday) to "7" (Sunday).'));
  }else if((key.endsWith('channel_id')||key==='category_id'||key==='role_id'||key==='traveler_role_id')&&nullable){
    input=node('select');const none=node('option','','Not configured');none.value='';input.append(none);
    const choices=key.includes('role')?state.roles:state.channels;
    for(const c of choices){const opt=node('option','',c.name+' · '+c.id);opt.value=c.id;input.append(opt);}if(val&&!choices.some(c=>c.id===val)){const opt=node('option','',val+' (not found)');opt.value=val;input.append(opt);}input.value=val||'';input.onchange=()=>{obj[key]=input.value||null;markDirty();};
  }else{
    input=node(schema.type==='string'&&(schema.maxLength>500||key==='message'||key==='goodbye')?'textarea':'input');input.value=val??'';
    if(schema.type==='integer'||schema.type==='number'){input.type='number';if(schema.minimum!==undefined)input.min=schema.minimum;if(schema.maximum!==undefined)input.max=schema.maximum;}
    if(schema.maxLength)input.maxLength=schema.maxLength;
    input.oninput=()=>{if(input.type==='number'){const n=input.value===''&&nullable?null:Number(input.value);if(n!==null&&(!Number.isFinite(n)||!input.checkValidity())){state.invalid.add(id);input.classList.add('field-error');}else{obj[key]=n;state.invalid.delete(id);input.classList.remove('field-error');}}else{obj[key]=input.value|| (nullable?null:'');}markDirty();};
  }
  input.disabled=!state.canEdit;input.setAttribute('aria-label',label);wrapper.insertBefore(input,wrapper.children[1]||null);
  if(nullable)wrapper.append(node('small','','Optional. Leave empty to disable this setting.'));
  if(schema.description)wrapper.append(node('small','',schema.description));parent.append(wrapper);
}
function references(parent){const box=node('fieldset');box.append(node('legend','','Channel and role reference'));const list=node('div','reference-list');for(const r of [...state.channels,...state.roles]){const chip=node('div','chip');chip.append(node('span','',r.name+' '),node('code','',r.id));list.append(chip);}box.append(list);parent.append(box);}
function editModule(key){$('editor-title').textContent=meta[key][0];const root=$('editor-fields');root.replaceChildren();root.append(node('p','help-line',meta[key][1]));const schema=resolve(state.schema.properties[key]);for(const [k,s] of Object.entries(schema.properties))field(root,k,s,state.draft[key],key);references(root);$('editor').showModal();}
function moduleCard(key){const [title,desc,icon]=meta[key],m=state.draft[key];const c=node('article','card'),top=node('div','card-top');top.append(node('span','module-icon',icon));const toggle=node('button','toggle'+(m.enabled?' on':''));toggle.setAttribute('aria-label','Enable '+title);toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(m.enabled));toggle.disabled=!state.canEdit;toggle.onclick=()=>{m.enabled=!m.enabled;markDirty();render();};top.append(toggle);c.append(top,node('h3','',title),node('p','',desc));const bottom=node('div','card-footer');bottom.append(node('span','state'+(m.enabled?' enabled':''),m.enabled?'● Enabled':'○ Disabled'));const button=node('button','configure','Configure →');button.onclick=()=>editModule(key);bottom.append(button);c.append(bottom);return c;}
function heading(root,title,sub){const h=node('div','section-heading');h.append(node('h2','',title));if(sub)h.append(node('span','muted',sub));root.append(h);}
function modules(root,keys){const grid=node('div','grid');keys.forEach(k=>grid.append(moduleCard(k)));root.append(grid);}
function activity(root,limit=8){const entries=state.overview?.audit||[];if(!entries.length){root.append(node('p','empty','Your server activity will appear here.'));return;}entries.slice(0,limit).forEach(a=>{const row=node('div','activity');row.append(node('span','',human(a.action.replaceAll('.',' '))),node('small','',new Date(a.created*1000).toLocaleString()));root.append(row);});}
function render(){
  const root=$('content');root.replaceChildren();const current=navigation.find(x=>x[0]===state.view);$('page-title').textContent=state.view==='overview'?'Command center':current[1];$('breadcrumb').textContent='WORKSPACE / '+current[1].toUpperCase();document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
  if(state.view==='overview'){
    const hero=node('section','hero'),text=node('div');text.append(node('p','eyebrow','BUILT FOR YOUR COMMUNITY'),node('h2','','Less busywork. More time together.'),node('p','','Keep your server running smoothly, your members connected, and your alliance ready for what comes next.'));hero.append(text,node('span','hero-symbol','◇'));root.append(hero);
    const enabled=state.modules.filter(k=>state.draft[k].enabled).length,counts=state.overview?.counts||{};const stats=node('div','stats');[[enabled,'Enabled modules'],[state.guild?.members||0,'Community members'],[counts.alliances||0,'Alliances'],[counts.open_tickets||0,'Open tickets']].forEach(([value,title])=>{const s=node('div','stat');s.append(node('b','',value),node('span','',title));stats.append(s);});root.append(stats);
    heading(root,'Your essentials','Configure once. Keep things moving.');modules(root,['moderation','welcome','tickets']);
    const two=node('div','two-col');const start=node('section','box'),recent=node('section','box');start.append(node('h3','','Ready when you are'));const ol=node('ol');[['Choose your modules','Switch on the tools your server needs.'],['Set channels and roles','Choose where messages go and who can manage them.'],['Save and try a command','Open Discord and type / to explore enabled commands.']].forEach(([a,b])=>{const li=node('li');li.append(node('strong','',a),document.createTextNode(b));ol.append(li);});start.append(ol);recent.append(node('h3','','Recent activity'));activity(recent,4);two.append(start,recent);heading(root,'At a glance');root.append(two);
  }else if(state.view==='community'){root.append(node('p','help-line','A toolkit for a welcoming, organized server. Every module can be switched on independently.'));modules(root,state.modules.filter(k=>!dws.includes(k)));}
  else if(state.view==='dws'){
    root.append(node('p','help-line','Bring your Dark War Survival community together, from daily reminders to weekly armory rotation.'));modules(root,dws);heading(root,'Alliance roster');const box=node('div','box'),table=node('table'),head=node('tr');['Alliance','Tag','Members'].forEach(t=>head.append(node('th','',t)));table.append(head);(state.overview?.alliances||[]).forEach(a=>{const tr=node('tr');[a.name,a.tag,a.members].forEach(t=>tr.append(node('td','',t)));table.append(tr);});box.append(table);if(!state.overview?.alliances?.length)box.append(node('p','empty','Create an alliance with /alliance create in Discord.'));root.append(box);
  }else if(state.view==='events'){renderEvents(root);}
  else if(state.view==='settings'){
    const box=node('section','box settings-wrap');box.append(node('h2','','Server preferences'),node('p','help-line','Use an IANA timezone such as Asia/Amman. Delegated dashboard users can view settings; changes require a server administrator.'));for(const [key,schema] of Object.entries(state.schema.properties)){if(!state.modules.includes(key))field(box,key,schema,state.draft,'server');}references(box);root.append(box);
  }else{
    const box=node('section','box');box.append(node('h2','','Recent changes'));activity(box,25);root.append(box);heading(root,'Jobs that need attention');const jobs=state.overview?.jobs||[];if(!jobs.length)root.append(node('p','empty','No failed background jobs.'));else jobs.forEach(job=>{const c=node('div','box');c.append(node('h3','',human(job.kind)),node('p','danger-text',job.error||'Job failed'));const b=node('button','secondary','Retry job');b.disabled=!state.canEdit;b.onclick=async()=>{try{await api(`/api/guilds/${state.guild.id}/jobs/${job.id}/retry`,{method:'POST',body:{}});await refreshOverview();render();toast('Job queued for retry.');}catch(e){toast(e.message,true);}};c.append(b);root.append(c);});
  }
}
async function refreshOverview(){const id=state.guild.id;const [overview,events]=await Promise.all([api(`/api/guilds/${id}/overview`),api(`/api/guilds/${id}/events`)]);if(state.guild.id===id){state.overview=overview;state.events=events;}}
async function selectGuild(id){if(state.dirty&&!confirm('Discard unsaved changes and switch servers?')){$('guild-select').value=state.guild.id;return;}state.guild=state.guilds.find(g=>g.id===id);const data=await api(`/api/guilds/${id}/config`);state.saved=data.config;state.draft=structuredClone(data.config);state.version=data.version;state.channels=data.channels;state.roles=data.roles;state.canEdit=data.can_edit;state.invalid.clear();await refreshOverview();markDirty();render();}
function eventDate(seconds){return new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:state.saved.timezone}).format(new Date(seconds*1000));}
function repeatLabel(seconds){for(const [unit,size] of [['weeks',604800],['days',86400],['hours',3600],['minutes',60]])if(seconds%size===0)return `${seconds/size} ${unit}`;}
function formInput(parent,label,type='text',value='',hint=''){
  const wrap=node('label','field'),input=node(type==='textarea'?'textarea':'input');
  if(type!=='textarea')input.type=type;input.value=value;input.setAttribute('aria-label',label);input.disabled=!state.canEdit;
  wrap.append(node('span','field-label',label),input);if(hint)wrap.append(node('small','',hint));parent.append(wrap);return input;
}
function repeatFields(parent,seconds=86400){
  const row=node('div','repeat-fields'),amount=formInput(row,'Repeat every','number','1');amount.min='1';amount.max='525600';amount.step='1';amount.required=true;
  const wrap=node('label','field'),unit=node('select');unit.setAttribute('aria-label','Repeat unit');unit.disabled=!state.canEdit;
  for(const [v,label] of [['m','Minutes'],['h','Hours'],['d','Days'],['w','Weeks']]){const option=node('option','',label);option.value=v;unit.append(option);}
  const units={m:60,h:3600,d:86400,w:604800};let selected='m';for(const key of ['w','d','h','m'])if(seconds%units[key]===0){selected=key;break;}
  unit.value=selected;amount.value=seconds/units[selected];wrap.append(node('span','field-label','Unit'),unit);row.append(wrap);parent.append(row);
  return {amount,unit,row,value:()=>amount.value+unit.value,seconds:()=>Number(amount.value)*units[unit.value]};
}
async function eventAction(button,path,method,body){
  button.disabled=true;
  try{await api(path,{method,body});await refreshOverview();render();toast('Event schedule saved.');}
  catch(e){toast(e.message,true);button.disabled=!state.canEdit;}
}
function renderEvents(root){
  const gid=state.guild.id,zone=state.saved.timezone;
  root.append(node('p','help-line',`Create it once, and let it repeat. All displayed event times use ${zone}. Repeats use elapsed time; a day is 24 hours.`));
  if(state.dirty)root.append(node('p','notice','Save or discard your pending settings before creating an event.'));
  if(!state.saved.events.enabled)root.append(node('p','notice','Enable Scheduled events, then save your settings to start scheduling.'));
  const settings=node('button','secondary','Event settings');settings.onclick=()=>editModule('events');root.append(settings);
  const layout=node('div','event-layout'),form=node('form','box event-form');form.id='create-event-form';form.append(node('h2','','Create an event'));
  const name=formInput(form,'Event name');name.required=true;name.maxLength=80;
  const channelWrap=node('label','field'),channel=node('select');channel.setAttribute('aria-label','Event channel');channel.required=true;channel.disabled=!state.canEdit;
  for(const c of state.channels.filter(c=>c.type==='text')){const option=node('option','',`#${c.name}`);option.value=c.id;channel.append(option);}if(state.saved.events.channel_id)channel.value=state.saved.events.channel_id;
  channelWrap.append(node('span','field-label','Channel'),channel);form.append(channelWrap);
  const when=formInput(form,'First start','datetime-local','',`Server time: ${zone}. For an ambiguous daylight-saving time, use /event create with an explicit UTC offset.`);when.required=true;
  const repeat=formInput(form,'Repeat automatically','checkbox');repeat.checked=true;
  const cadence=repeatFields(form),preview=node('p','notice');form.append(preview);
  function previewRuns(){cadence.row.classList.toggle('hidden',!repeat.checked);cadence.amount.disabled=!repeat.checked||!state.canEdit;cadence.unit.disabled=!repeat.checked||!state.canEdit;preview.textContent=repeat.checked?`Repeat every ${cadence.amount.value||'…'} ${cadence.unit.selectedOptions[0].textContent.toLowerCase()} until paused or stopped.`:'Run once at the selected start time.';}
  repeat.onchange=previewRuns;cadence.amount.oninput=previewRuns;cadence.unit.onchange=previewRuns;previewRuns();
  const message=formInput(form,'Event message','textarea','', 'Use {event} for the event name and {countdown} for the start countdown.');message.maxLength=4096;message.required=true;
  const length=formInput(form,'Duration in minutes','number','15');length.required=true;length.min='1';length.max='43200';length.step='1';
  const reminders=formInput(form,'Reminders before start','text','60, 10','Minutes before every run, separated by commas. Leave blank for no reminders.');
  const keep=formInput(form,'Keep event messages','checkbox');keep.checked=false;
  const create=node('button','primary','Create event');create.type='submit';create.disabled=!state.canEdit||state.dirty||!state.saved.events.enabled;form.append(create);
  form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;const offsets=reminders.value.trim()?reminders.value.split(',').map(s=>Number(s.trim())):[];
    if(offsets.length>10||offsets.some(x=>!Number.isInteger(x)||x<1||x>10080)){toast('Use up to 10 reminder offsets between 1 and 10080 minutes.',true);return;}
    if(repeat.checked&&(!Number.isInteger(Number(cadence.amount.value))||cadence.seconds()<300||cadence.seconds()>31536000)){toast('Choose a repeat interval from 5 minutes to 365 days.',true);return;}
    form.inert=true;try{await eventAction(create,`/api/guilds/${gid}/events`,'POST',{when:when.value,repeat_every:repeat.checked?cadence.value():'off',definition:{name:name.value,channel_id:channel.value,template:{title:name.value,description:message.value},duration_minutes:Number(length.value),reminder_minutes:offsets,keep_messages:keep.checked}});}finally{form.inert=false;}
  };
  const schedules=node('section','event-schedules');schedules.append(node('h2','','Recurring schedules'));
  const refresh=node('button','secondary','Refresh schedules');refresh.onclick=async()=>{refresh.disabled=true;try{await refreshOverview();render();}catch(e){toast(e.message,true);refresh.disabled=false;}};schedules.append(refresh);
  if(!state.events.series.length)schedules.append(node('p','empty','Your recurring schedules will appear here.'));
  for(const series of state.events.series){
    const card=node('article','box series-card'),top=node('div','section-heading');top.append(node('h3','',series.name),node('span','badge',series.status.toUpperCase()));card.append(top,node('p','',`Series #${series.id} · Every ${repeatLabel(series.interval_seconds)}`),node('p','help-line',series.next_run?`Next run: ${eventDate(series.next_run)}`:'Paused. Resume to continue on the original schedule.'));
    const actions=node('div','event-actions');
    for(const [action,label] of [[series.status==='active'?'pause':'resume',series.status==='active'?'Pause':'Resume'],['stop','Stop series']]){const b=node('button','secondary',label);b.disabled=!state.canEdit;b.onclick=()=>{if(action==='stop'&&!confirm(`Stop all future runs of ${series.name}?`))return;eventAction(b,`/api/guilds/${gid}/event-series/${series.id}`,'PATCH',{action,version:series.version});};actions.append(b);}
    const edit=node('button','secondary','Edit timing');edit.disabled=!state.canEdit;actions.append(edit);card.append(actions);
    const editForm=node('form','series-edit hidden'),fields=repeatFields(editForm,series.interval_seconds),next=formInput(editForm,'New first start (optional)','datetime-local','',`Leave blank to keep the original anchor. Timezone: ${zone}.`),save=node('button','primary','Save timing');save.type='submit';editForm.append(save);edit.onclick=()=>editForm.classList.toggle('hidden');
    editForm.onsubmit=async e=>{e.preventDefault();if(!editForm.reportValidity())return;editForm.inert=true;try{await eventAction(save,`/api/guilds/${gid}/event-series/${series.id}`,'PATCH',{action:'edit',version:series.version,repeat_every:fields.value(),when:next.value||null});}finally{editForm.inert=false;}};card.append(editForm);schedules.append(card);
  }
  layout.append(form,schedules);root.append(layout);heading(root,'Upcoming occurrences','Cancel one run without stopping its series.');
  if(!state.events.occurrences.length)root.append(node('p','empty','No upcoming occurrences.'));
  for(const occurrence of state.events.occurrences){const item=node('div','box occurrence'),text=node('div');text.append(node('strong','',`#${occurrence.id} · ${occurrence.name}`),node('p','help-line',`${eventDate(occurrence.starts)}${occurrence.series_id?` · Series #${occurrence.series_id}`:' · One-time event'}`));const cancel=node('button','secondary','Cancel occurrence');cancel.disabled=!state.canEdit;cancel.onclick=()=>eventAction(cancel,`/api/guilds/${gid}/events/${occurrence.id}/cancel`,'POST',{});item.append(text,cancel);root.append(item);}
}

async function boot(){
  try{state.user=await api('/api/me');}catch(e){$('login').classList.remove('hidden');try{const h=await api('/healthz');if(h.demo){document.querySelector('#login a').textContent='Explore the local demo';$('login-hint').textContent='Demo changes stay on this computer. No Discord connection is made.';}}catch{}return;}
  const [schema,guilds]=await Promise.all([api('/api/schema'),api('/api/guilds')]);state.schema=schema.schema;state.modules=schema.modules;state.guilds=guilds;
  if(!guilds.length){$('login').classList.remove('hidden');$('login-hint').textContent='You are signed in, but no accessible servers were found. Install this bot in your server and ensure you have Administrator permission.';return;}
  $('shell').classList.remove('hidden');$('mode').textContent=state.user.demo?'LOCAL DEMO':'CONNECTED';$('connection').textContent=state.user.demo?'Demo workspace':state.user.bot_online?'Bot online':'Bot reconnecting';
  if(state.user.demo){$('banner').textContent='Demo workspace · Explore the dashboard and save settings locally. Discord actions run only after you configure your own bot.';$('banner').classList.remove('hidden');}
  for(const guild of guilds){const opt=node('option','',guild.name);opt.value=guild.id;$('guild-select').append(opt);}
  navigation.forEach(([view,label])=>{const b=node('button','',label);b.dataset.view=view;b.onclick=()=>{state.view=view;render();};$('nav').append(b);});
  $('guild-select').onchange=()=>selectGuild($('guild-select').value).catch(e=>toast(e.message,true));
  $('logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST',body:{}});location.reload();}catch(e){toast(e.message,true);}};
  $('save').onclick=async()=>{if(state.invalid.size){toast('Correct the highlighted fields before saving.',true);return;}$('save').disabled=true;for(const id of ['shell','editor','savebar'])$(id).inert=true;try{const result=await api(`/api/guilds/${state.guild.id}/config`,{method:'PUT',body:{config:state.draft,version:state.version}});state.version=result.version;state.saved=result.config;state.draft=structuredClone(result.config);markDirty();$('editor').close();await refreshOverview();render();toast('Your settings have been saved.');}catch(e){toast(e.message,true);$('save').disabled=false;}finally{for(const id of ['shell','editor','savebar'])$(id).inert=false;}};
  $('discard').onclick=()=>{state.draft=structuredClone(state.saved);state.invalid.clear();markDirty();$('editor').close();render();};
  const closeEditor=()=>{if(state.invalid.size){toast('Correct invalid JSON or numbers before closing.',true);return;}$('editor').close();render();};$('close-editor').onclick=closeEditor;$('done-editor').onclick=closeEditor;
  $('editor').addEventListener('cancel',e=>{if(state.invalid.size){e.preventDefault();toast('Correct the highlighted fields first.',true);}});
  window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
  await selectGuild(guilds[0].id);
}
boot().catch(e=>toast(e.message,true));
