# Controlled native end-to-end regression: initial absence, submit, complete,
# render without reload/refetch, close retention, then reload consistency.
# The evaluation server and external provider/engine fixtures must be isolated.
from pathlib import Path
import sys,os,json,time,importlib.util,tempfile,urllib.request
P=Path.cwd();E=Path(os.environ['AAG_COMPLETION_EVIDENCE']).resolve();ORIGIN=os.environ.get('AAG_COMPLETION_ORIGIN','http://localhost:3109');case=os.environ.get('AAG_CASE','baseline');baseline=case=='baseline';O=E/case;O.mkdir(exist_ok=True)
sys.path.insert(0,str(P/'image-agent/tools'));from browser_lifecycle import OwnedBrowser,profile_processes
spec=importlib.util.spec_from_file_location('cdp',P/'image-agent/tools/atlas-browser-acceptance.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
request=urllib.request.Request(ORIGIN+'/api/workspace/image-generator/thread/new',data=b'',method='POST',headers={'Content-Type':'application/json'})
if os.environ.get('AAG_THREAD'):
 thread=json.loads((E/os.environ['AAG_THREAD']/'result.json').read_text())['thread']
else:
 with urllib.request.urlopen(request) as response:thread=json.load(response)['thread']
(E/'release-engine').unlink(missing_ok=True)
if os.environ.get('AAG_EXPECT_FAILURE')=='1':(E/'fail-engine').write_text('Controlled failed-engine response\n')
else:(E/'fail-engine').unlink(missing_ok=True)
url=ORIGIN+'/workspace/image-generator/t/'+thread['slug'];report={'case':case,'thread':thread,'url':url,'result':'RUNNING'}
def save(): (O/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
profile=Path(tempfile.mkdtemp(prefix='aag-completion-browser-'));args=[os.environ.get('AAG_BROWSER','/usr/bin/brave-browser'),'--no-first-run','--no-default-browser-check','--password-store=basic','--disable-dev-shm-usage','--remote-allow-origins=*',f'--user-data-dir={profile}','--window-size=1450,1200','about:blank']
if os.environ.get('AAG_HEADED')!='1':args.insert(1,'--headless=new')
observe=r'''(()=>{window.__trace={events:[],requests:[],cards:[],errors:[]};const now=()=>new Date().toISOString();const W=window.WebSocket;window.WebSocket=class extends W {constructor(...args){super(...args);window.__ownedTestSocket=this;this.addEventListener('message',e=>{let data;try{data=JSON.parse(e.data)}catch{}window.__trace.events.push({at:now(),data})});this.addEventListener('close',()=>window.__trace.closed=now());}};const f=window.fetch;window.fetch=(...args)=>{window.__trace.requests.push({at:now(),url:String(args[0]),method:args[1]?.method||'GET'});if(window.__gateImages&&String(args[0]).includes('/image-generation/generated-images/'))return new Promise(resolve=>{(window.__heldImages??=[]).push(()=>resolve(f(...args)));window.__releaseImage=()=>{window.__trace.releasedImageRequests=window.__heldImages.length;for(const release of window.__heldImages.splice(0))release()};});return f(...args)};window.addEventListener('error',e=>window.__trace.errors.push(e.message));window.addEventListener('unhandledrejection',e=>window.__trace.errors.push(String(e.reason)));window.addEventListener('aag-artifact-presented',e=>window.__trace.cards.push({at:now(),...e.detail}));const seen=new Set();new MutationObserver(()=>{for(const n of document.querySelectorAll('[data-testid=aag-artifact-filename]')){if(!seen.has(n.dataset.artifactId)){seen.add(n.dataset.artifactId);window.__trace.cards.push({at:now(),boundary:'CARD_RENDER_ATTEMPT_AT',artifactId:n.dataset.artifactId,filename:n.textContent})}}}).observe(document,{subtree:true,childList:true});})();'''
state=r'''(()=>{let e=document.querySelector('#chat-history');if(!e)return [];let f=e[Object.keys(e).find(k=>k.startsWith('__reactFiber'))];while(f){if(Array.isArray(f.memoizedProps?.history))return f.memoizedProps.history.map(({uuid,type,content,role,pending,chatId,outputs})=>({uuid,type,content,role,pending,chatId,outputs}));f=f.return;}return null})()'''
try:
 with OwnedBrowser(args,timeout=480) as browser:
  c=browser.attach(b.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Page.addScriptToEvaluateOnNewDocument',{'source':observe});c.call('Page.navigate',{'url':url});c.wait('document.querySelector("[data-testid=aag-expert-prompt-toggle]")',50)
  if os.environ.get('AAG_REFRESH_BEFORE')=='1':
   c.call('Page.reload');c.wait('document.querySelector("[data-testid=aag-expert-prompt-toggle]")',45)
  c.evaluate('window.__expertCase='+('true' if os.environ.get('AAG_EXPERT')=='1' else 'false'));c.select_option('aag-ui-language','en');c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');c.select_option("aag-visual-family","photography");c.select_option("aag-visual-subfamily","product");c.evaluate('(()=>{const e=document.querySelector("[data-testid=aag-expert-prompt-toggle]");if(e.checked!==(window.__expertCase===true))e.click()})()')
  initial_count=c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length');report['initialCardCount']=initial_count
  report['timeOrigin']=c.evaluate('performance.timeOrigin');report['startRequests']=c.evaluate('window.__trace.requests.length');save()
  c.evaluate('(()=>{const e=document.querySelector("textarea");const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set;setter.call(e,"A studio product photograph of one blue ceramic mug with exactly one handle on a neutral gray surface.");e.dispatchEvent(new Event("input",{bubbles:true}));})()')
  if os.environ.get('AAG_EXPERT')=='1':
   c.evaluate('(()=>{const e=document.querySelector("textarea");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,"A studio product photograph of one blue ceramic mug.");e.dispatchEvent(new Event("input",{bubbles:true}));})()')
  c.evaluate('window.__gateImages='+('true' if os.environ.get('AAG_HOLD_IMAGE')=='1' else 'false'))
  c.evaluate('document.querySelector("textarea").closest("form").requestSubmit()')
  report['afterSubmit']={'body':c.evaluate('document.body.innerText'),'trace':c.evaluate('window.__trace'),'textarea':c.evaluate('document.querySelector("textarea")?.outerHTML')};save()
  try:c.wait('window.__trace.events.some(e=>e.data?.content?.type==="toolCallInvocation")',45)
  except BaseException:
   report['trace']=c.evaluate('window.__trace');report['body']=c.evaluate('document.body.innerText');c.screenshot(O/'error.png');save();raise
  assert c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length')==initial_count
  report['absentBeforeCompletion']=True;save()
  # External fixture gate controls completion, not product timing or state.
  (E/'release-engine').write_text('Complete the controlled engine job\n')
  if os.environ.get('AAG_EXPECT_FAILURE')=='1':
   c.wait('window.__trace.events.some(e=>e.data?.content?.type==="fullTextResponse"&&e.data.content.content.includes("status=failed"))',60)
   c.wait('document.querySelector("#chat-history")?.innerText.includes("Image generation could not finish.")',15)
   assert c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length')==initial_count
   assert c.evaluate('!document.querySelector("#chat-history").innerText.includes("AAG_IMAGE_RESULT")')
   report['failureLiveState']=c.evaluate(state);report['trace']=c.evaluate('window.__trace');report['humanReadableFailure']=True;report['noFakeCardOrFilename']=True
   c.call('Page.reload');c.wait('document.querySelector("#chat-history")?.innerText.includes("Image generation could not finish.")',30)
   assert c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length')==initial_count
   report['failureReloadConsistent']=True;report['result']='PASS';browser.close();report['orphanBrowsers']=len(profile_processes(profile));save();print(case+'=PASS');sys.exit(0)
  c.wait('window.__trace.events.some(e=>e.data?.type==="imageGenerationCard")',100)
  c.evaluate('window.__expectedArtifact=window.__trace.events.find(e=>e.data?.type==="imageGenerationCard").data.outputs.find(o=>o.type==="imageGenerationCard").payload.artifactId')
  if not baseline:
   c.wait('document.querySelectorAll("[data-testid=aag-artifact-filename]").length==='+str(initial_count+1),35)
   if os.environ.get('AAG_HOLD_IMAGE')=='1':
    c.wait('window.__releaseImage&&document.querySelector(".aag-image-progress li.is-current")?.textContent.includes("Returning image")',15)
    assert c.evaluate('!Array.from(document.querySelectorAll(".aag-image-progress li.is-complete")).some(e=>e.textContent.trim()==="✓Complete")')
    report['noPrematureCompletion']=True;c.evaluate('window.__gateImages=false;window.__releaseImage()')
   try:c.wait('window.__trace.cards.some(e=>e.jobId&&e.artifactId===window.__expectedArtifact)',35)
   except BaseException:
    report['trace']=c.evaluate('window.__trace');report['body']=c.evaluate('document.body.innerText');report['imageElements']=c.evaluate('Array.from(document.querySelectorAll("#chat-history img")).map(e=>({src:e.src,complete:e.complete,width:e.naturalWidth}))');c.screenshot(O/'error.png');save();raise
  report['trace']=c.evaluate('window.__trace');report['liveState']=c.evaluate(state);report['liveCardCount']=c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length');report['liveText']=c.evaluate('document.querySelector("#chat-history")?.innerText');report['sameDocument']=c.evaluate('performance.timeOrigin')==report['timeOrigin'];report['chatFetchesDuringGeneration']=[r for r in report['trace']['requests'][report['startRequests']:] if r['url'].endswith('/chats')];c.screenshot(O/'live.png');save()
  if not baseline:
   c.wait('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).filter(e=>e.dataset.artifactId===window.__expectedArtifact).length===1&&Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).find(e=>e.dataset.artifactId===window.__expectedArtifact)?.parentElement.parentElement.querySelector("img")?.naturalWidth>0',35)
   assert report['liveCardCount']==initial_count+1,'Live card missing or duplicate'
   assert report['sameDocument'] and not report['chatFetchesDuringGeneration'],'Completion required navigation/refetch'
   report['filename']=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).at(-1).textContent');report['artifactId']=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).at(-1).dataset.artifactId')
  if not baseline and os.environ.get('AAG_REVEAL')=='1':
   c.evaluate('(()=>{const label=Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).at(-1);label.parentElement.parentElement.dataset.liveTarget="true";})()')
   c.evaluate('(()=>{if(document.querySelector("#aag-composer-controls"))document.querySelector("[data-testid=aag-controls-disclosure]").click()})()')
   c.wait('!document.querySelector("[data-live-target] [data-testid=aag-artifact-open-location]").disabled',20)
   c.evaluate('document.querySelector("[data-live-target] [data-testid=aag-artifact-open-location]").scrollIntoView({block:"center"})')
   c.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
   pos=c.evaluate('(()=>{const e=document.querySelector("[data-live-target] [data-testid=aag-artifact-open-location]"),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest("button")===e}})()')
   report['folderClickPosition']=pos;save()
   assert pos['hit'],'Folder button obscured by UI'
   for kind in ['mousePressed','mouseReleased']:c.call('Input.dispatchMouseEvent',{'type':kind,'x':pos['x'],'y':pos['y'],'button':'left','clickCount':1})
   import gi
   gi.require_version('Atspi','2.0');from gi.repository import Atspi
   def selection():
    rows=[]
    def walk(n,depth=0):
     if depth>45:return
     try:
      role=n.get_role_name();name=n.get_name();st=n.get_state_set()
      if role=='frame' or (report['filename'] in name and (st.contains(Atspi.StateType.SELECTED) or 'selected' in name)):rows.append({'role':role,'name':name,'selected':st.contains(Atspi.StateType.SELECTED) or 'selected' in name,'showing':st.contains(Atspi.StateType.SHOWING),'active':st.contains(Atspi.StateType.ACTIVE)})
      for i in range(n.get_child_count()):walk(n.get_child_at_index(i),depth+1)
     except Exception:pass
    desktop=Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
     app=desktop.get_child_at_index(i)
     if app.get_name()=='org.gnome.Nautilus':walk(app)
    return rows
   deadline=time.monotonic()+20
   while time.monotonic()<deadline:
    rows=selection()
    if any(r['selected'] for r in rows) and any(r['role']=='frame' and r['showing'] for r in rows):break
    time.sleep(.2)
   report['fileManager']=rows;report['liveCardReveal']=any(r['selected'] for r in rows) and any(r['role']=='frame' and r['showing'] for r in rows);save();assert report['liveCardReveal'],'Exact file reveal did not become visible'
  if not baseline:
   c.wait('window.__trace.events.some(e=>e.data?.type==="WAITING_ON_INPUT")',15)
   c.evaluate('window.__ownedTestSocket.close()');c.wait('window.__trace.closed',10)
   assert c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length')==initial_count+1
   report['socketCloseRetainsCards']=True
  c.call('Page.reload');c.wait('document.querySelectorAll("[data-testid=aag-artifact-filename]").length==='+str(initial_count+1),45)
  report['reloadState']=c.evaluate(state);report['reloadCardCount']=c.evaluate('document.querySelectorAll("[data-testid=aag-artifact-filename]").length');report['reloadFilename']=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).at(-1).textContent');c.screenshot(O/'reloaded.png')
  if baseline:assert report['liveCardCount']==0 and report['reloadCardCount']==1,'Golden defect was not reproduced'
  else:assert report['reloadCardCount']==initial_count+1 and report['reloadFilename']==report['filename']
  report['result']='REPRODUCED' if baseline else 'PASS';save()
 report['orphanBrowsers']=len(profile_processes(profile));save();print(case+'='+report['result'],flush=True)
except Exception as error:
 report['error']=repr(error)
 try:report['trace']=c.evaluate('window.__trace');report['body']=c.evaluate('document.body.innerText');c.screenshot(O/'error.png')
 except:pass
 report['result']='ERROR';save();raise
