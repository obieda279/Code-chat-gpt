// Optional DOM integration checks: npm install && npm run test:ui
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.join(__dirname,'..');
const cfg=JSON.parse(fs.readFileSync(path.join(root,'docs/example-config.json'),'utf8'));
const schema=JSON.parse(fs.readFileSync(path.join(root,'docs/config-schema.json'),'utf8'));
delete cfg.language;
const modules=Object.keys(cfg).filter(k=>cfg[k]&&typeof cfg[k]==='object'&&!Array.isArray(cfg[k])&&'enabled' in cfg[k]);
const errors=[];
let stored=structuredClone(cfg),version=0,saves=0;
stored.events.enabled=true;
let series=[],occurrences=[],eventCreates=0,seriesChanges=[];
const dom=new JSDOM(fs.readFileSync(path.join(root,'sentinel/static/index.html'),'utf8'),{url:'http://localhost:8080',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.structuredClone=structuredClone;
w.confirm=()=>true;
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.addEventListener('error',e=>errors.push(e.error));
w.fetch=async(url,opts={})=>{
 let data;
 if(url==='/api/me')data={user:'2',csrf:'test-csrf',demo:true,bot_online:false};
 else if(url==='/api/guilds')data=[{id:'1',name:'Example <script>unsafe</script>',members:128}];
 else if(url==='/api/schema')data={schema,modules};
 else if(url.endsWith('/overview'))data={counts:{alliances:2,open_tickets:0},audit:[],jobs:[],alliances:[]};
 else if(url.includes('/event-series/')){
  assert.equal(opts.headers['X-CSRF-Token'],'test-csrf');
  const payload=JSON.parse(opts.body);seriesChanges.push(payload.action);
  assert.equal(payload.version,series[0].version);
  if(payload.action==='stop'){series=[];occurrences=[];}
  else{series[0].version++;series[0].status=payload.action==='pause'?'paused':'active';if(payload.action==='edit'){assert.equal(payload.repeat_every,'2w');series[0].interval_seconds=1209600;}}
  data={ok:true};
 }
 else if(url.endsWith('/events')){
  if(opts.method==='POST'){
   const payload=JSON.parse(opts.body);assert.equal(opts.headers['X-CSRF-Token'],'test-csrf');assert.equal(payload.repeat_every,'12h');assert.deepEqual(payload.definition.reminder_minutes,[60,10]);assert.equal(payload.when,'2090-12-01T20:00');
   eventCreates++;series=[{id:1,name:payload.definition.name,status:'active',interval_seconds:43200,version:1,next_run:3815841600}];occurrences=[];data={id:1,series_id:1};
  }else data=structuredClone({series,occurrences});
 }
 else if(url.endsWith('/config')){
  if(opts.method==='PUT'){
   assert.equal(opts.headers['X-CSRF-Token'],'test-csrf');
   const payload=JSON.parse(opts.body);assert.equal(payload.version,version);
   stored=payload.config;version++;saves++;data={config:stored,version};
  }else data={config:structuredClone(stored),version,roles:[],channels:[{id:'300000000000000001',name:'announcements',type:'text'}],can_edit:true};
 }else throw new Error('Unmocked request '+url);
 return {ok:true,status:200,json:async()=>data};
};
const wait=async(test)=>{for(let i=0;i<100;i++){if(test())return;await new Promise(r=>setTimeout(r,5));}throw Error('UI condition did not settle');};
const button=text=>[...w.document.querySelectorAll('button')].find(b=>b.textContent===text);
const input=(element,value)=>{element.value=value;element.dispatchEvent(new w.Event('input',{bubbles:true}));};
(async()=>{
 w.eval(fs.readFileSync(path.join(root,'sentinel/static/app.js'),'utf8'));
 await wait(()=>w.document.querySelectorAll('article.card').length===3);
 assert.equal(w.document.querySelector('#guild-select').textContent,'Example <script>unsafe</script>');
 assert.equal(w.document.querySelector('#guild-select script'),null);
 button('Community tools').click();
 const birthdays=w.document.querySelector('[aria-label="Enable Birthdays"]');birthdays.click();
 assert.equal(birthdays.getAttribute('aria-checked'),'false'); // Old node; rerender created a new one.
 assert.equal(w.document.querySelector('[aria-label="Enable Birthdays"]').getAttribute('aria-checked'),'true');
 button('Save changes').click();await wait(()=>saves===1&&w.document.querySelector('#savebar').classList.contains('hidden'));
 assert.equal(stored.birthdays.enabled,true);
 button('Server settings').click();input(w.document.querySelector('[aria-label="Timezone"]'),'Europe/Madrid');
 button('Save changes').click();await wait(()=>saves===2&&w.document.querySelector('#savebar').classList.contains('hidden'));assert.equal(stored.timezone,'Europe/Madrid');
 button('Dark War Survival').click();
 const alliance=[...w.document.querySelectorAll('article.card')].find(c=>c.querySelector('h3').textContent==='Alliances');alliance.querySelector('.configure').click();
 input(w.document.querySelector('[aria-label="Management Roles"]'),'not-json');assert.equal(w.document.querySelector('#save').disabled,true);
 input(w.document.querySelector('[aria-label="Management Roles"]'),'[]');input(w.document.querySelector('[aria-label="Max Members"]'),'50');
 button('Done').click();button('Save changes').click();await wait(()=>saves===3&&w.document.querySelector('#savebar').classList.contains('hidden'));assert.equal(stored.alliances.max_members,50);
 button('Community tools').click();const before=stored.welcome.enabled;w.document.querySelector('[aria-label="Enable Welcome & goodbye"]').click();button('Discard').click();
 assert.equal(w.document.querySelector('[aria-label="Enable Welcome & goodbye"]').getAttribute('aria-checked'),String(before));
 button('Events').click();
 input(w.document.querySelector('[aria-label="Event name"]'),'Battle <script>safe</script>');
 input(w.document.querySelector('[aria-label="First start"]'),'2090-12-01T20:00');
 input(w.document.querySelector('[aria-label="Event message"]'),'Gather {countdown}.');
 input(w.document.querySelector('[aria-label="Repeat every"]'),'12');
 const unit=w.document.querySelector('[aria-label="Repeat unit"]');unit.value='h';unit.dispatchEvent(new w.Event('change'));
 button('Create event').click();await wait(()=>eventCreates===1&&!!button('Pause'));
 assert.equal(w.document.querySelector('.series-card script'),null);
 assert.match(w.document.querySelector('.series-card').textContent,/Battle <script>safe<\/script>/);
 button('Pause').click();await wait(()=>!!button('Resume'));
 button('Resume').click();await wait(()=>seriesChanges.length===2&&!!button('Pause'));
 button('Edit timing').click();const editForm=w.document.querySelector('.series-edit');
 input(editForm.querySelector('[aria-label="Repeat every"]'),'2');const editUnit=editForm.querySelector('[aria-label="Repeat unit"]');editUnit.value='w';editUnit.dispatchEvent(new w.Event('change'));
 button('Save timing').click();await wait(()=>seriesChanges.length===3&&!w.document.querySelector('.series-edit:not(.hidden)'));
 button('Stop series').click();await wait(()=>seriesChanges.length===4&&!w.document.querySelector('.series-card'));
 assert.deepEqual(seriesChanges,['pause','resume','edit','stop']);
 assert.equal(errors.length,0);
 console.log('PASS: dashboard boot, safe text rendering, settings save/discard, event creation, custom recurrence, pause, resume, edit, stop.');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
