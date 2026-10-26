"""Native new-thread lifecycle regression. Private DB/assets; no real generation.
Only taxonomy/prepare and stream output use explicit browser fixtures. Native
thread creation, navigation, Composer controls and draft migration are real.
"""
from pathlib import Path
import argparse,base64,hashlib,importlib.util,json,sqlite3,subprocess,sys,tempfile,time,urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent))
from browser_lifecycle import OwnedBrowser,profile_processes
spec=importlib.util.spec_from_file_location('cdp',Path(__file__).with_name('atlas-browser-acceptance.py'));cdp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cdp)
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);a=p.parse_args();E=Path(a.evidence).resolve();O=E/'new-thread-browser';O.mkdir(exist_ok=True)
 runtime=json.loads((E/'native-runtime.json').read_text());storage=Path(runtime['storage']);assert storage.is_relative_to(E)
 labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='sidebar'
 origin='http://localhost:3111';report={'result':'RUNNING','checks':{},'fixture':'No engine: native first-submit with controlled prepare + stream reply; native private thread API and historical data.'}
 def save():(O/'result.json').write_text(json.dumps(report,indent=2)+'\n')
 def check(name,value):
  report['checks'][name]='PASS' if value else 'FAIL';save();assert value,name
 def api(path,data=None):
  with urllib.request.urlopen(urllib.request.Request(origin+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'}),timeout=20) as r:return json.load(r)
 images={v.name:hashlib.sha256(v.read_bytes()).hexdigest() for v in (storage/'generated-images').iterdir() if v.is_file()}
 taxonomy=(Path(__file__).resolve().parents[1]/'integrations/model-neutral-compatibility/composer/visual-taxonomy.json').read_text()
 inject='''(()=>{const native=window.fetch;window.__calls=[];window.__errors=[];
 addEventListener('error',e=>window.__errors.push(e.message));
 window.fetch=async(...args)=>{const path=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href).pathname,body=args[1]?.body;
 window.__calls.push({path,body});const reply=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
 if(path.endsWith('/aag-composer/image-generator/taxonomy'))return reply(TAXONOMY);
 if(path.endsWith('/aag-composer/image-generator/session'))return reply({csrf:'isolated-new-thread'});
 if(path.endsWith('/aag-composer/image-generator/lifecycle/status'))return reply({state:'READY'});
 if(path.includes('/aag-composer/image-generator/lifecycle/'))throw Error('Forbidden engine action');
 if(path.endsWith('/aag-composer/image-generator/prepare'))return reply({modelMessage:'CONTROLLED_ADVANCED '+body});
 if(path.endsWith('/stream-chat'))return new Response('data: '+JSON.stringify({id:'new-thread-failure-fixture',type:'abort',textResponse:'Image generation could not finish. Please try again later.',sources:[],close:true,error:null})+'\\n\\n',{headers:{'Content-Type':'text/event-stream'}});
 return native(...args)};})()'''.replace('TAXONOMY',taxonomy)
 profile=Path(tempfile.mkdtemp(prefix='aag-new-thread-acceptance-'))
 try:
  with OwnedBrowser(['/usr/bin/brave-browser','--headless=new','--no-first-run','--no-default-browser-check','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={profile}','--window-size=1600,1080','about:blank'],timeout=540) as b:
   c=b.attach(cdp.Cdp(b.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Emulation.setFocusEmulationEnabled',{'enabled':True});c.call('Page.addScriptToEvaluateOnNewDocument',{'source':inject})
   def ready():c.wait('document.querySelector(".aag-composer-presentation[data-scope-ready=true]") && (document.querySelector("[data-testid=aag-composer-dock-toggle]") || document.querySelector("[data-testid=aag-composer-reopen]"))',40)
   def click(id):c.evaluate('document.querySelector('+json.dumps('[data-testid='+id+']')+').click()');time.sleep(.15)
   def key(id,name):
    c.evaluate('document.querySelector('+json.dumps('[data-testid='+id+']')+').focus()')
    for kind in ['keyDown','keyUp']:c.call('Input.dispatchKeyEvent',{'type':kind,'key':name,'code':name})
    time.sleep(.1)
   def state():return c.evaluate('''(()=>{const p=document.querySelector('.aag-composer-presentation');return {mode:[...document.querySelectorAll('.aag-composer-modes button')].map(e=>e.getAttribute('aria-checked')),values:[...document.querySelectorAll('.aag-inline-composer select,.aag-inline-composer input')].map(e=>[e.dataset.testid||e.type,e.value,e.checked]),placement:p.dataset.placement,side:p.dataset.side,closed:p.dataset.closed,layout:JSON.parse(localStorage.getItem('aag.image-composer.layout.v1')||'null')}})()''')
   def shot(name):c.screenshot(O/(name+'.png'))
   def prompt(text):c.evaluate('(()=>{const e=document.querySelector("#primary-prompt-input");Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,'+json.dumps(text)+');e.dispatchEvent(new Event("input",{bubbles:true}))})()');time.sleep(.1)
   def submit():
    c.evaluate('window.__calls=[];window.__oldForm=document.querySelector("#primary-prompt-input").closest("form");window.__oldRoute=location.pathname;window.__oldForm.requestSubmit()');c.wait('window.__calls.some(v=>v.path.endsWith("/stream-chat"))',35);c.wait('document.body.innerText.includes("Image generation could not finish. Please try again later.")',20);ready()
   c.call('Page.navigate',{'url':origin+'/workspace/image-generator/t/920949ea-b51d-4dff-805b-f0beb1348862'});ready();c.evaluate('localStorage.setItem("theme","light");localStorage.removeItem("aag.image-composer.layout.v1")')
   owned=[]
   for mode in ['advanced','basic','auto','advanced-on']:
    c.call('Page.navigate',{'url':origin+'/'});ready();c.select_option('aag-ui-language','en');time.sleep(.25)
    check(mode+'_EMPTY_HOST',c.evaluate('!!document.querySelector("#prompt-input-wrapper").closest("[data-aag-chat-layout]")'))
    check(mode+'_NEW_DRAFT_AUTO',state()['mode']==['true','false'])
    if mode=='advanced':
     click('aag-composer-dock-toggle');c.wait('document.querySelector(".aag-composer-presentation[data-placement=panel]")');check('ZERO_MESSAGES_DOCK',True)
     c.select_option('aag-composer-side','left');key('aag-composer-resizer','ArrowRight');check('ZERO_MESSAGES_LEFT_RESIZE',state()['layout']['width']==394)
     c.select_option('aag-composer-side','right');key('aag-composer-resizer','ArrowLeft');check('ZERO_MESSAGES_RIGHT_RESIZE',state()['layout']['width']==404)
     click('aag-composer-dock-toggle');check('ZERO_MESSAGES_UNDOCK',state()['placement']=='popup');click('aag-composer-dock-toggle');c.select_option('aag-composer-side','left')
     c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');c.wait('document.querySelector("[data-testid=aag-visual-family]")');check('ADVANCED_EXPERT_DEFAULT',c.evaluate('document.querySelector("[data-testid=aag-expert-prompt-toggle]").checked'))
     click('aag-expert-prompt-toggle');c.select_option('aag-visual-family','photography');c.select_option('aag-visual-subfamily','product');c.select_option('aag-technical-quality','quality')
     c.evaluate('(()=>{const e=[...document.querySelectorAll(".aag-inline-composer select")].find(e=>[...e.options].some(o=>o.value==="landscape"));e.value="landscape";e.dispatchEvent(new Event("change",{bubbles:true}))})()')
     c.evaluate('(()=>{const e=[...document.querySelectorAll(".aag-inline-composer input")].find(e=>e.placeholder==="auto"&&e.type==="text");if(e){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(e,"123");e.dispatchEvent(new Event("input",{bubbles:true}))}})()');time.sleep(.2)
     check('ZERO_MESSAGES_NO_DUMMY_RECORD',c.evaluate('!window.__calls.some(v=>v.path.endsWith("/thread/new")||v.path.endsWith("/stream-chat"))'))
     shot('home-advanced-docked-before-first')
    elif mode=='basic':
     # AUTO is the product's basic mode, not a third invented selection.
     c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');time.sleep(.1);c.evaluate('document.querySelectorAll(".aag-composer-modes button")[0].click()')
    if mode=='advanced-on':
     c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');c.wait('document.querySelector("[data-testid=aag-visual-family]")')
     if not c.evaluate('document.querySelector("[data-testid=aag-expert-prompt-toggle]").checked'):click('aag-expert-prompt-toggle')
    if mode=='basic':click('aag-composer-close')
    if mode=='auto' and state()['closed']=='true':click('aag-composer-reopen')
    prompt('First message lifecycle fixture — no model call');before=state();submit();after=state();route=c.evaluate('location.pathname');slug=route.split('/t/')[-1];owned.append(slug)
    check(mode+'_SURVIVES_FIRST_MESSAGE',before==after)
    check(mode+'_EXACT_ONE_STABLE_SUBMISSION',c.evaluate('window.__calls.filter(v=>v.path.endsWith("/stream-chat")).length')==1 and '/t/' in route)
    threads=api('/api/workspace/image-generator/threads')['threads'];item=next(t for t in threads if t['slug']==slug)
    report.setdefault('threads',[]).append({'mode':mode,'id':item['id'],'slug':slug,'before':before,'after':after})
    check(mode+'_STABLE_INTERNAL_ID',item['id']>0 and item['slug']==slug)
    check(mode+'_HUMAN_FAILURE',c.evaluate('document.body.innerText.includes("Image generation could not finish. Please try again later.")&&!document.body.innerText.includes("AAG_IMAGE_RESULT")'))
    if mode=='basic':
     check('CLOSED_PANEL_SURVIVES_FIRST_MESSAGE',after['closed']=='true');click('aag-composer-reopen')
    if mode=='advanced-on':check('EXPERT_ON_SURVIVES_FIRST_MESSAGE',c.evaluate('document.querySelector("[data-testid=aag-expert-prompt-toggle]").checked'))
    if mode=='advanced':
     check('PREPARED_ADVANCED_REACHES_NATIVE_STREAM',c.evaluate('JSON.parse(window.__calls.find(v=>v.path.endsWith("/stream-chat")).body).message.startsWith("CONTROLLED_ADVANCED ")'))
     check('SENT_PROMPT_CLEARED',c.evaluate('document.querySelector("#primary-prompt-input").value')=='');shot('stable-thread-advanced-after-first')
     click('aag-composer-close');check('CLOSE_STATE',c.evaluate('document.querySelector(".aag-composer-presentation").dataset.closed')=='true');click('aag-composer-reopen')
     original=state();c.evaluate('localStorage.setItem("aag_image_composer_enabled:linux","true")');other=api('/api/workspace/image-generator/thread/new',{})['thread']
     # Refresh sidebar list without reloading page, then follow native SPA links.
     c.evaluate('document.querySelector("[data-workspace-slug=linux]").click()');c.wait('location.pathname==="/workspace/linux"');c.wait('document.querySelector(".aag-composer-presentation[data-workspace-scope=linux][data-scope-ready=true]")');check('WORKSPACE_NO_STATE_LEAK',state()['mode']==['true','false'])
     c.evaluate('document.querySelector("[data-workspace-slug=image-generator]").click()');c.wait('location.pathname==="/workspace/image-generator"');ready();c.wait('document.querySelector('+json.dumps('[data-thread-slug="'+other['slug']+'"]')+')')
     c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+other['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(other['slug'])+')');ready();check('THREAD_NO_STATE_LEAK',state()['mode']==['true','false'])
     c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+slug+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps(slug)+')');ready();check('RETURN_THREAD_SELECTIONS',state()==original)
     c.call('Page.reload');ready();check('RELOAD_LAYOUT',state()['layout']==original['layout']);check('RELOAD_PAGE_DRAFT_SEMANTICS',state()['mode']==['true','false']);check('RELOAD_EXPERT_OVERRIDE',c.evaluate('localStorage.getItem("aag_expert_prompt_enabled:image-generator")')=='false')
   # An already created empty native thread keeps the same identity at first message.
   stable=api('/api/workspace/image-generator/thread/new',{})['thread'];c.call('Page.navigate',{'url':origin+'/workspace/image-generator/t/'+stable['slug']});ready();c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');time.sleep(.2);prompt('Persisted empty-thread first message fixture');before=state();submit();check('STABLE_EMPTY_FIRST_MESSAGE',state()==before and c.evaluate('location.pathname').endswith(stable['slug']))
   check('NO_BROWSER_ERRORS',c.evaluate('window.__errors')==[]);check('NO_REAL_ENGINE_CALLS',not c.evaluate('window.__calls.some(v=>v.path.includes("/lifecycle/")&&!v.path.endsWith("/status"))'))
  check('HISTORICAL_FILENAMES_BYTES_UNCHANGED',images=={v.name:hashlib.sha256(v.read_bytes()).hexdigest() for v in (storage/'generated-images').iterdir() if v.is_file()})
  check('NO_ORPHAN_BROWSERS',not profile_processes(profile));report['result']='PASS';save()
 except Exception as exc:
  report['result']='FAIL';report['error']=str(exc);save();raise
 print(json.dumps({'result':report['result'],'checks':len(report['checks'])}))
if __name__=='__main__':main()
