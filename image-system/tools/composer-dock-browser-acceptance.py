"""Non-generative native UI regression, private database + owned browser only.
The real native app/assets/history APIs run in an isolated container. Composer
catalog/READY and a terminal stream error are explicit browser-boundary fixtures;
no model or lifecycle action is permitted. This is not real-engine validation.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,shutil,sqlite3,subprocess,sys,tempfile,time,urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent))
from browser_lifecycle import OwnedBrowser,profile_processes
from browser_profile_checkpoint import checkpoint_command,finish_checkpoint
spec=importlib.util.spec_from_file_location('cdp',Path(__file__).with_name('atlas-browser-acceptance.py'));cdp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cdp)

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);args=p.parse_args()
 E=Path(args.evidence).resolve();runtime=json.loads((E/'native-runtime.json').read_text());storage=Path(runtime['storage']);assert storage.is_relative_to(E)
 labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='sidebar'
 origin='http://localhost:3111';O=E/'composer-browser';O.mkdir(exist_ok=True)
 report={'result':'RUNNING','checks':{},'fixture_boundary':'catalog/READY and terminal stream failure; native app, stable IDs, assets and historical artifact APIs are real'}
 def save(): (O/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 def check(name,value):
  report['checks'][name]='PASS' if value else 'FAIL'
  if not value:
   try: report['diagnostic']={'body':c.evaluate('document.body.innerText'),'state':form_state(c),'route':c.evaluate('location.pathname')};shot(c,'failure')
   except Exception:pass
  save();assert value,name
 def api(path,data=None):
  with urllib.request.urlopen(urllib.request.Request(origin+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'}),timeout=20) as r:return json.load(r)
 images={v.name:hashlib.sha256(v.read_bytes()).hexdigest() for v in (storage/'generated-images').iterdir() if v.is_file()}
 threads=api('/api/workspace/image-generator/threads')['threads'];historical=next(t for t in threads if t['slug']=='920949ea-b51d-4dff-805b-f0beb1348862')
 new=api('/api/workspace/image-generator/thread/new',{})['thread'];report['owned_test_thread']=new
 artifact_message=next(row for row in reversed(api('/api/workspace/image-generator/thread/'+historical['slug']+'/chats')['history']) if row.get('outputs'))
 taxonomy=json.loads((Path(__file__).resolve().parents[1]/'integrations/model-neutral-compatibility/composer/visual-taxonomy.json').read_text())
 inject='''(()=>{const native=window.fetch;window.__dockCalls=[];window.__layoutWrites=0;window.__dockErrors=[];
 window.addEventListener('error',e=>window.__dockErrors.push(e.message));
 window.__presented=[];window.addEventListener('aag-artifact-presented',e=>window.__presented.push(e.detail.artifactId));
 const Socket=window.WebSocket;class FixtureSocket extends EventTarget {
 constructor(url){super();this.url=url;this.readyState=1;window.__fixtureSocket=this;} send(){} close(){if(this.readyState===3)return;this.readyState=3;this.dispatchEvent(new Event('close'));}
 }
 window.WebSocket=class extends Socket {constructor(url,...rest){if(url.endsWith('/aag-dock-fixture'))return new FixtureSocket(url);super(url,...rest)}};
 const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='aag.image-composer.layout.v1')window.__layoutWrites++;return set.call(this,k,v)};
 window.fetch=async(...args)=>{const path=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href).pathname;
 window.__dockCalls.push({path,method:args[1]?.method||'GET',body:args[1]?.body});
 const reply=body=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
 if(path===window.__holdChatPath){await new Promise(resolve=>window.__releaseHistory=resolve);window.__holdChatPath=null;}
 if(path.endsWith('/chats')&&window.__hideArtifactChatId){const r=await native(...args),v=await r.json();v.history=v.history.filter(row=>row.chatId!==window.__hideArtifactChatId);return reply(v)}
 if(path.includes('/aag-composer/image-generator/progress/')&&window.__dockProgress)return reply(window.__dockProgress);
 if(path.endsWith('/stream-chat')&&window.__streamMode==='socket')return new Response('data: '+JSON.stringify({type:'agentInitWebsocketConnection',websocketUUID:'aag-dock-fixture'})+'\\n\\n',{headers:{'Content-Type':'text/event-stream'}});
 if(path.endsWith('/aag-composer/image-generator/taxonomy'))return reply(TAXONOMY);
 if(path.endsWith('/aag-composer/image-generator/session'))return reply({csrf:'isolated-ui-fixture'});
 if(path.endsWith('/aag-composer/image-generator/lifecycle/status'))return reply({state:'READY'});
 if(path.includes('/aag-composer/image-generator/lifecycle/'))throw Error('Real lifecycle action forbidden in UI test');
 if(path.endsWith('/aag-composer/image-generator/prepare'))return reply({modelMessage:'ISOLATED COMPOSER CONTRACT FIXTURE'});
 if(path.endsWith('/stream-chat'))return new Response('data: '+JSON.stringify({id:'dock-ui-error',type:'abort',textResponse:'Image generation could not finish. Please try again later.',sources:[],close:true,error:null})+'\\n\\n',{headers:{'Content-Type':'text/event-stream'}});
 return native(...args);
 };})()'''.replace('TAXONOMY',json.dumps(taxonomy))
 profile=Path(tempfile.mkdtemp(prefix='aag-composer-dock-'));restart=Path(tempfile.mkdtemp(prefix='aag-composer-dock-restart-'))
 def launch(prof):
  argv=['/usr/bin/brave-browser','--headless=new','--no-first-run','--no-default-browser-check','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={prof}','--window-size=1600,1080','about:blank']
  return OwnedBrowser(checkpoint_command(argv,restart) if prof==profile else argv,timeout=600)
 def attach(browser):
  c=browser.attach(cdp.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Emulation.setFocusEmulationEnabled',{'enabled':True});c.call('Page.addScriptToEvaluateOnNewDocument',{'source':inject});return c
 def ready(c): c.wait('document.querySelector("[data-testid=aag-composer-dock-toggle], [data-testid=aag-composer-reopen]") && document.querySelector(".aag-composer-presentation[data-scope-ready=true]") && document.querySelector(".aag-composer-presentation").dataset.threadScope === (location.pathname.split("/t/")[1] || "")',45)
 def navigate(c,slug): c.call('Page.navigate',{'url':origin+'/workspace/image-generator/t/'+slug});ready(c)
 def click(c,testid):c.evaluate('document.querySelector('+json.dumps('[data-testid="'+testid+'"]')+').click()');time.sleep(.18)
 def key(c,testid,name):
  c.evaluate('document.querySelector('+json.dumps('[data-testid="'+testid+'"]')+').focus()')
  for t in ['keyDown','keyUp']:c.call('Input.dispatchKeyEvent',{'type':t,'key':name,'code':name})
  time.sleep(.15)
 def selected(c): return c.evaluate('JSON.parse(localStorage.getItem("aag.image-composer.layout.v1")||"null")')
 def geometry(c):return c.evaluate('''(()=>{const rect=e=>{const r=e.getBoundingClientRect();return{x:r.x,right:r.right,width:r.width,y:r.y,bottom:r.bottom,client:e.clientWidth,scroll:e.scrollWidth}};return {panel:rect(document.querySelector('[data-testid=aag-composer-panel]')),chat:rect(document.querySelector('[data-aag-chat-surface]')),nav:rect(document.querySelector('[data-testid=aag-sidebar]')),page:document.documentElement.scrollWidth,viewport:innerWidth,side:document.querySelector('.aag-composer-presentation').dataset.side,docked:document.querySelector('[data-aag-chat-layout]').dataset.aagComposerDocked}})()''')
 def assert_layout(c,name):
  c.wait('document.querySelector("[data-aag-chat-surface]").getBoundingClientRect().width>=479',5)
  m=geometry(c);report.setdefault('layouts',{})[name]=m;save()
  check(name+'_CHAT_REFLOW',m['chat']['width']>=479 and m['page']<=m['viewport'] and m['panel']['right']<=m['viewport']+1)
  if m['docked']!='false':check(name+'_NO_OVERLAY',m['panel']['right']<=m['chat']['x'] or m['panel']['x']>=m['chat']['right'])
 def form_state(c): return c.evaluate('''(()=>{const form=document.querySelector('#primary-prompt-input').closest('form');return {prompt:document.querySelector('#primary-prompt-input').value,values:Array.from(form.querySelectorAll('.aag-inline-composer select,.aag-inline-composer input')).map(e=>[e.dataset.testid||e.type,e.value,e.checked]),mode:Array.from(form.querySelectorAll('.aag-composer-modes button')).map(e=>e.getAttribute('aria-checked'))}})()''')
 def type_prompt(c,text): c.evaluate('(()=>{const e=document.querySelector("#primary-prompt-input");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,'+json.dumps(text)+');e.dispatchEvent(new Event("input",{bubbles:true}))})()');time.sleep(.12)
 def shot(c,name):
  import base64
  (O/(name+'.png')).write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']))
 try:
  with launch(profile) as browser:
   c=attach(browser);navigate(c,historical['slug']);c.evaluate('localStorage.setItem("theme","light");document.documentElement.classList.add("light")');c.select_option('aag-ui-language','en')
   check('DEFAULT_POPUP',c.evaluate('document.querySelector(".aag-composer-presentation").dataset.placement')=='popup' and geometry(c)['docked']=='false')
   check('BASIC_AUTO',c.evaluate('document.querySelector(".aag-composer-modes button").getAttribute("aria-checked")')=='true')
   c.wait('Array.from(document.querySelectorAll("img[role=button]")).some(e=>e.complete&&e.naturalWidth>0)',30)
   filenames=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)');check('HISTORICAL_RENDER',len(filenames)>0);shot(c,'01-popup')
   c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');c.wait('document.querySelector("[data-testid=aag-visual-family]")')
   check('ADVANCED_EXPERT_DEFAULT',c.evaluate('document.querySelector("[data-testid=aag-expert-prompt-toggle]").checked'))
   click(c,'aag-expert-prompt-toggle');check('EXPERT_MANUAL_OVERRIDE',not c.evaluate('document.querySelector("[data-testid=aag-expert-prompt-toggle]").checked'))
   c.select_option('aag-visual-family','photography');c.select_option('aag-visual-subfamily','product');c.evaluate('(()=>{const e=Array.from(document.querySelectorAll(".aag-inline-composer select")).find(e=>Array.from(e.options).some(o=>o.value==="16:9"));e.value="16:9";e.dispatchEvent(new Event("change",{bubbles:true}))})()');type_prompt(c,'Studio product photo — ספל כחול API v2')
   # Deliberately keep strong DOM identity references: checking values alone can miss a remount.
   c.evaluate('window.__sameComposer=document.querySelector(".aag-inline-composer");window.__samePrompt=document.querySelector("#primary-prompt-input");window.__sameForm=window.__samePrompt.closest("form");true');before=form_state(c)
   click(c,'aag-composer-dock-toggle');c.wait('document.querySelector("[data-aag-chat-layout]").dataset.aagComposerDocked==="right"')
   check('DOCK_SAME_COMPONENT_AND_FORM',c.evaluate('window.__sameComposer===document.querySelector(".aag-inline-composer")&&window.__samePrompt===document.querySelector("#primary-prompt-input")&&window.__sameForm===window.__samePrompt.closest("form")'))
   check('DOCK_ALL_STATE',before==form_state(c));assert_layout(c,'right-default');shot(c,'02-right-default')
   check('DEFAULT_PANEL_WIDTH',geometry(c)['panel']['width']==384)
   check('DOCK_INPUT_VISIBLE',c.evaluate('(()=>{const r=document.querySelector("#primary-prompt-input").getBoundingClientRect();return r.y>0&&r.bottom<=innerHeight})()'))
   for name,k,w in [('narrow','Home',320),('wide','End',560),('default','Enter',384)]:
    key(c,'aag-composer-resizer',k);c.wait('Number(document.querySelector("[data-testid=aag-composer-resizer]").getAttribute("aria-valuenow"))==='+str(w));assert_layout(c,'right-'+name);shot(c,'03-right-'+name)
   # A real pointer drag must write preferences once, not per raw pointer event.
   pos=c.evaluate('(()=>{const r=document.querySelector("[data-testid=aag-composer-resizer]").getBoundingClientRect();return{x:r.x+5,y:r.y+150}})()');writes=c.evaluate('window.__layoutWrites')
   for kind,extra in [('mouseMoved',{}),('mousePressed',{'button':'left','clickCount':1})]:c.call('Input.dispatchMouseEvent',{'type':kind,**pos,**extra})
   for delta in [8,16,24,32,48,60]:c.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':pos['x']-delta,'y':pos['y'],'button':'left','buttons':1})
   c.call('Input.dispatchMouseEvent',{'type':'mouseReleased','x':pos['x']-60,'y':pos['y'],'button':'left','clickCount':1});c.wait('JSON.parse(localStorage.getItem("aag.image-composer.layout.v1")).width===444')
   check('POINTER_RESIZE',geometry(c)['panel']['width']==444);check('COALESCED_STORAGE_WRITES',c.evaluate('window.__layoutWrites')-writes==1)
   c.select_option('aag-composer-side','left');check('SIDE_CHANGE_PRESERVES_STATE',before==form_state(c));assert_layout(c,'left');key(c,'aag-composer-resizer','Enter');shot(c,'05-left-default')
   key(c,'aag-composer-resizer','ArrowRight');check('LEFT_KEYBOARD_EDGE',geometry(c)['panel']['width']==394)
   click(c,'aag-composer-close');check('CLOSE_HIDES_NOT_UNMOUNTS',c.evaluate('window.__sameComposer===document.querySelector(".aag-inline-composer")&&getComputedStyle(window.__sameComposer).display==="none"'))
   check('CLOSE_FOCUS_LAUNCHER',c.evaluate('document.activeElement.dataset.testid')=='aag-composer-reopen');shot(c,'10-closed-launcher')
   click(c,'aag-composer-reopen');check('REOPEN_STATE',before==form_state(c));check('REOPEN_FOCUS',c.evaluate('document.activeElement.dataset.testid')=='aag-composer-close')
   click(c,'aag-composer-dock-toggle');check('UNDOCK_STATE_AND_IDENTITY',before==form_state(c) and c.evaluate('window.__sameComposer===document.querySelector(".aag-inline-composer")'))
   click(c,'aag-composer-dock-toggle');check('DOCK_BUTTON_ONLY_MODE_SWITCH',geometry(c)['docked']=='left' and c.evaluate('!document.querySelector("[data-testid=aag-composer-display-mode]")'))
   c.select_option('aag-ui-language','he');check('HEBREW_RTL',c.evaluate('document.querySelector(".aag-inline-composer").dir')=='rtl');type_prompt(c,'צילום של ספל כחול');shot(c,'06-hebrew');check('RTL_PHYSICAL_LEFT',geometry(c)['panel']['right']<=geometry(c)['chat']['x'])
   type_prompt(c,'An English prompt in Hebrew UI');shot(c,'06-hebrew-english');c.select_option('aag-composer-side','right');check('RTL_PHYSICAL_RIGHT',geometry(c)['panel']['x']>=geometry(c)['chat']['right'])
   c.select_option('aag-ui-language','en');type_prompt(c,'English and עברית 16:9');shot(c,'07-ltr-mixed');check('MIXED_PROMPT_UNCHANGED',form_state(c)['prompt']=='English and עברית 16:9')
   for navkey,label in [('Home','narrow'),('Enter','default'),('End','wide')]:
    key(c,'aag-sidebar-resizer',navkey);assert_layout(c,'nav-'+label);shot(c,'08-nav-'+label)
   preferred=selected(c);c.call('Emulation.setDeviceMetricsOverride',{'width':1000,'height':900,'deviceScaleFactor':1,'mobile':False});c.wait('document.querySelector("[data-aag-chat-layout]").dataset.aagComposerDocked==="false"');check('CONSTRAINED_FALLBACK',c.evaluate('!!document.querySelector(".aag-composer-layout-note")'))
   check('CLAMP_PREFERENCE_PRESERVED',selected(c)==preferred);check('CONSTRAINED_NO_PAGE_OVERFLOW',geometry(c)['page']<=geometry(c)['viewport'])
   c.wait('document.querySelector("#prompt-input-wrapper").getBoundingClientRect().height<innerHeight-200',5)
   check('CONSTRAINED_LEAVES_READABLE_CHAT',True)
   c.wait('parseFloat(getComputedStyle(document.querySelector("#chat-history")).paddingBottom)+1>=document.querySelector("#prompt-input-wrapper").getBoundingClientRect().height')
   check('POPUP_NATIVE_HISTORY_RESERVATION',True)
   check('FALLBACK_TOOLBAR_OPAQUE',c.evaluate('getComputedStyle(document.querySelector(".aag-composer-layout-bar")).backgroundColor')=='rgb(255, 255, 255)')
   check('CONSTRAINED_SINGLE_COLUMN',c.evaluate('getComputedStyle(document.querySelector(".aag-composer-grid")).gridTemplateColumns.split(" ").length')==1);shot(c,'09-constrained')
   c.call('Emulation.clearDeviceMetricsOverride');c.wait('document.querySelector("[data-aag-chat-layout]").dataset.aagComposerDocked==="right"');check('RESTORED_PREFERRED_WIDTH',geometry(c)['panel']['width']==preferred['width']);key(c,'aag-sidebar-resizer','Enter')
   c.call('Emulation.setDeviceMetricsOverride',{'width':1160,'height':950,'deviceScaleFactor':1,'mobile':False});c.wait('document.querySelector("[data-aag-chat-layout]").dataset.aagComposerDocked==="right"');assert_layout(c,'tight-fit');check('CLAMP_WITHOUT_PREFERENCE_LOSS',selected(c)==preferred);c.call('Emulation.clearDeviceMetricsOverride')
   # Real historical dialogs, no dialog fixture. Both sides and all panel widths.
   for side in ['left','right']:
    c.select_option('aag-composer-side',side)
    for widthkey,label in [('Home','narrow'),('Enter','default'),('End','wide')]:
     key(c,'aag-composer-resizer',widthkey)
     for action in ['prompt','details']:
      click(c,'aag-artifact-show-'+action);c.wait('document.querySelector("dialog[open]")',20)
      if action=='details':c.wait('document.querySelector("dialog[open] [data-detail-field=current_sha256]")',20)
      else:c.wait('document.querySelector("dialog[open] [data-testid=aag-artifact-final-prompt]")',20)
      check(side+'_'+label+'_SHOW_'+action.upper(),c.evaluate('(()=>{const r=document.querySelector("dialog[open]").getBoundingClientRect();return r.width>250&&r.x>=0&&r.right<=innerWidth&&r.bottom<=innerHeight})()'))
      if label=='default':shot(c,'11-'+side+'-'+action)
      if action=='prompt':
       c.evaluate('(()=>{const d=document.querySelector("dialog[open]"),r=document.createRange();r.selectNodeContents(d.querySelector("pre")||d);const s=getSelection();s.removeAllRanges();s.addRange(r)})()');check(side+'_'+label+'_TEXT_SELECTION_STAYS_OPEN',c.evaluate('!!document.querySelector("dialog[open]")&&getSelection().toString().length>0'));c.evaluate('getSelection().removeAllRanges()')
      c.evaluate('document.querySelector("dialog[open] button[aria-label=Close]").click()')
   click(c,'aag-browse-visual-atlas');c.wait('document.querySelector("[data-testid=aag-visual-atlas-browser]")')
   check('VISUAL_ATLAS_DOCKED_NOT_CLIPPED',c.evaluate('(()=>{const r=document.querySelector("[data-testid=aag-visual-atlas-browser]").getBoundingClientRect();return r.width>560&&r.x>=0&&r.right<=innerWidth})()'));shot(c,'atlas-docked')
   click(c,'aag-atlas-close');c.wait('!document.querySelector("[data-testid=aag-visual-atlas-browser]")');check('VISUAL_ATLAS_CLOSE',True)
   key(c,'aag-composer-resizer','Enter');type_prompt(c,'Returning scope draft');scope_state=form_state(c)
   # Native SPA navigation; no ordinal is ever used in the route.
   c.evaluate('window.__holdChatPath='+json.dumps('/api/workspace/image-generator/thread/'+new['slug']+'/chats'))
   c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+new['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(new['slug'])+')')
   c.wait('window.__releaseHistory && document.querySelector(".aag-composer-presentation[data-scope-ready=false]")')
   check('TRANSITION_OLD_FORM_INERT',c.evaluate('document.querySelector(".aag-composer-native-form").inert'))
   count=c.evaluate('window.__dockCalls.filter(v=>v.path.endsWith("/stream-chat")).length');c.evaluate('document.querySelector("#primary-prompt-input").closest("form").requestSubmit()');time.sleep(.15)
   check('STALE_SCOPE_SUBMIT_BLOCKED',c.evaluate('window.__dockCalls.filter(v=>v.path.endsWith("/stream-chat")).length')==count)
   c.evaluate('window.__releaseHistory();delete window.__releaseHistory');ready(c)
   check('THREAD_SWITCH_CURRENT_SCOPE',c.evaluate('document.querySelector(".aag-composer-presentation").dataset.scopeReady')=='true');check('NEW_THREAD_HAS_OWN_DRAFT',form_state(c)['prompt']!='Returning scope draft')
   c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+historical['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(historical['slug'])+')');ready(c);c.wait('document.querySelector("#primary-prompt-input").value==="Returning scope draft"')
   check('RETURN_SCOPE_DRAFT',scope_state==form_state(c))
   c.evaluate('document.querySelector("[data-workspace-slug=linux]").click()');c.wait('location.pathname==="/workspace/linux"');c.wait('document.querySelector(".aag-composer-presentation[data-scope-ready=true]")');check('WORKSPACE_SWITCH_REMOVES_DOCK',geometry(c)['docked']=='false')
   c.evaluate('document.querySelector("[data-workspace-slug=image-generator]").click()');c.wait('location.pathname==="/workspace/image-generator"');ready(c)
   c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+new['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(new['slug'])+')');ready(c)
   # Actual native form submission + normal stable-slug stream route. Only the
   # external generation boundary is replaced by a deterministic terminal error.
   c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');type_prompt(c,'UI boundary validation without a model call');c.evaluate('document.querySelector("#primary-prompt-input").closest("form").requestSubmit()')
   c.wait('window.__dockCalls.some(v=>v.path.endsWith("/stream-chat"))');c.wait('document.body.innerText.includes("Image generation could not finish. Please try again later.")')
   streams=c.evaluate('window.__dockCalls.filter(v=>v.path.endsWith("/stream-chat"))');check('SINGLE_NATIVE_SUBMISSION',len(streams)==1)
   check('CURRENT_STABLE_THREAD_ROUTE',streams[0]['path']=='/api/workspace/image-generator/thread/'+new['slug']+'/stream-chat')
   check('ADVANCED_PREPARE_SAME_PATH',c.evaluate('window.__dockCalls.some(v=>v.path.endsWith("/prepare"))'))
   check('HUMAN_FAILURE',not c.evaluate('document.body.innerText.includes("AAG_IMAGE_RESULT")'));shot(c,'failure-presentation')
   # Replay an existing artifact completion through the unchanged native socket
   # reducer, within its real original stable scope. It is hidden only in the
   # initial history response fixture, then restored by a real history reload.
   card=artifact_message['outputs'][0]['payload'];event={'type':'imageGenerationCard','uuid':'dock-replay','chatId':artifact_message['chatId'],'content':'','outputs':artifact_message['outputs']}
   c.evaluate('window.__hideArtifactChatId='+json.dumps(artifact_message['chatId'])+';window.__streamMode="socket"')
   c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+historical['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(historical['slug'])+')');ready(c)
   absent='document.querySelectorAll('+json.dumps('[data-testid=aag-artifact-filename][data-artifact-id="'+card['artifactId']+'"]')+').length'
   c.wait(absent+'===0');check('LIVE_CARD_ABSENT_BEFORE_EVENT',True)
   c.evaluate('document.querySelectorAll(".aag-composer-modes button")[0].click()');type_prompt(c,'Controlled existing artifact event replay, no engine call')
   c.evaluate('document.querySelector("#primary-prompt-input").closest("form").requestSubmit()');c.wait('window.__fixtureSocket')
   origin_time=c.evaluate('performance.timeOrigin');history_calls=c.evaluate('window.__dockCalls.filter(v=>v.path.endsWith("/chats")).length')
   c.evaluate('window.__dockProgress='+json.dumps({'active':True,'status':'COMPLETED','jobId':card['jobId'],'stages':[{'key':'creatingImage','state':'complete'},{'key':'returningToChat','state':'complete'},{'key':'complete','state':'complete'}]}))
   c.wait('document.querySelector("[data-testid=aag-image-progress]")');check('PROGRESS_UI_DOCKED',True)
   c.evaluate('window.__fixtureSocket.dispatchEvent(new MessageEvent("message",{data:'+json.dumps(json.dumps(event))+'}))')
   c.wait(absent+'===1');c.wait('window.__presented.includes('+json.dumps(card['artifactId'])+')')
   check('LIVE_ARTIFACT_NO_REFRESH',c.evaluate('performance.timeOrigin')==origin_time and c.evaluate('window.__dockCalls.filter(v=>v.path.endsWith("/chats")).length')==history_calls)
   c.evaluate('window.__fixtureSocket.dispatchEvent(new MessageEvent("message",{data:'+json.dumps(json.dumps(event))+'}))');c.evaluate('window.__fixtureSocket.close()');time.sleep(.2)
   check('LIVE_DUPLICATE_AND_SOCKET_CLOSE_ONCE',c.evaluate(absent)==1);check('COMPLETE_UI_DOCKED',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-image-progress] li")).every(e=>e.classList.contains("is-complete"))'));shot(c,'live-completion-fixture')
   c.evaluate('delete window.__hideArtifactChatId');old=c.evaluate('performance.timeOrigin');c.call('Page.reload');c.wait('performance.timeOrigin!=='+str(old));ready(c);c.wait(absent+'===1')
   check('LIVE_RELOAD_MATCHES_REAL_HISTORY',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)')==filenames)
   c.select_option('aag-composer-side','left');key(c,'aag-composer-resizer','ArrowRight');click(c,'aag-composer-close');saved=selected(c)
   old=c.evaluate('performance.timeOrigin');c.call('Page.reload');c.wait('performance.timeOrigin!=='+str(old));ready(c)
   check('RELOAD_ALL_PREFERENCES',selected(c)==saved and c.evaluate('!!document.querySelector("[data-testid=aag-composer-reopen]")'))
   finish_checkpoint(browser,c,restart)
  with launch(restart) as browser:
   c=attach(browser);navigate(c,historical['slug']);check('RESTART_ALL_PREFERENCES',selected(c)==saved and c.evaluate('!!document.querySelector("[data-testid=aag-composer-reopen]")'))
   click(c,'aag-composer-reopen');check('RESTART_REOPEN_LOCATION_WIDTH',geometry(c)['docked']=='left' and geometry(c)['panel']['width']==saved['width'])
   c.wait('Array.from(document.querySelectorAll("img[role=button]")).some(e=>e.complete&&e.naturalWidth>0)',30)
   check('HISTORY_RELOAD_IDENTICAL_ONCE',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)')==filenames and len(filenames)==len(set(filenames)))
   check('ORDINAL_PREFIX_FORMAT',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-workspace-number],[data-testid=aag-thread-number]")).every(e=>/^\\d+ -$/.test(e.textContent))'))
   check('ONE_COMPOSER_ONE_FORM',c.evaluate('document.querySelectorAll(".aag-inline-composer").length===1&&document.querySelectorAll("#primary-prompt-input").length===1'))
   # Repeated moves must neither remount lifecycle/catalog nor accumulate DOM.
   calls=c.evaluate('window.__dockCalls.filter(v=>/\\/(taxonomy|session|threads)$/.test(v.path)).length')
   for _ in range(4):click(c,'aag-composer-dock-toggle');click(c,'aag-composer-dock-toggle')
   check('NO_REPEATED_SCOPE_CATALOG_FETCHES',c.evaluate('window.__dockCalls.filter(v=>/\\/(taxonomy|session|threads)$/.test(v.path)).length')==calls)
   check('NO_LEAKED_LAYOUT_NODES',c.evaluate('document.querySelectorAll("[data-testid=aag-composer-panel]").length===1&&document.querySelectorAll("[data-testid=aag-composer-resizer]").length===1'))
   check('NO_JS_ERRORS',not c.evaluate('window.__dockErrors'))
   counters=c.call('Performance.getMetrics');c.call('Performance.enable');first=c.call('Performance.getMetrics');time.sleep(6);last=c.call('Performance.getMetrics')
   metrics=lambda d:{v['name']:v['value'] for v in d['metrics']};a=metrics(first);b=metrics(last);report['idle_task_seconds_per_second']=(b.get('TaskDuration',0)-a.get('TaskDuration',0))/6;check('LOW_IDLE_TASK_TIME',report['idle_task_seconds_per_second']<.08)
  check('HISTORICAL_FILES_UNCHANGED',images=={v.name:hashlib.sha256(v.read_bytes()).hexdigest() for v in (storage/'generated-images').iterdir() if v.is_file()})
  check('STABLE_THREAD_IDS_UNCHANGED',all(next(t['id'] for t in api('/api/workspace/image-generator/threads')['threads'] if t['slug']==old['slug'])==old['id'] for old in threads))
  report['result']='PASS'
 except BaseException as error:
  report['error']=repr(error)
  try:report['body']=c.evaluate('document.body.innerText');shot(c,'failure')
  except Exception:pass
  raise
 finally:
  report['orphan_browsers']=len(profile_processes(profile))+len(profile_processes(restart));save()
  if report['orphan_browsers']==0:
   for d in [profile,restart]:
    if d.exists():shutil.rmtree(d)
  print(json.dumps({'result':report['result'],'checks':len(report['checks']),'orphan_browsers':report['orphan_browsers']}))
if __name__=='__main__':main()
