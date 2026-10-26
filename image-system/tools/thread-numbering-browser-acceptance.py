"""Destructive cases are confined to newly created threads in an owned clone."""
from pathlib import Path
import os,json,sys,uuid,urllib.request,importlib.util,tempfile,subprocess,time
P=Path.cwd();E=Path(os.environ['AAG_NUMBERING_EVIDENCE']).resolve();ORIGIN='http://localhost:3109'
runtime=json.loads((E/'native-runtime.json').read_text());assert Path(runtime['storage']).is_relative_to(P/'evaluation')
labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='live-completion'
sys.path.insert(0,str(P/'image-agent/tools'));from browser_lifecycle import OwnedBrowser,profile_processes
spec=importlib.util.spec_from_file_location('cdp',P/'image-agent/tools/atlas-browser-acceptance.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
O=E/'numbering-browser';O.mkdir(exist_ok=True);report={'checks':{},'owned':[],'result':'RUNNING'}
def save(): (O/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
def check(key,value):report['checks'][key]='PASS' if value else 'FAIL';save();assert value,key

def api(path,data=None,method=None):
 request=urllib.request.Request(ORIGIN+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=20) as r:return json.load(r)
def new_thread(slug):
 t=api('/api/workspace/'+slug+'/thread/new',{},'POST')['thread'];report['owned'].append({'workspace':slug,**t});save();return t
workspace=api('/api/workspace/new',{'name':'Ordinal Test '+uuid.uuid4().hex[:8]},'POST')['workspace'];slug=workspace['slug'];report['workspace']=workspace
profile=Path(tempfile.mkdtemp(prefix='aag-thread-number-browser-'))
try:
 with OwnedBrowser(['/usr/bin/brave-browser','--headless=new','--no-first-run','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={profile}','--window-size=1450,1150','about:blank'],timeout=420) as browser:
  c=browser.attach(b.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Page.navigate',{'url':ORIGIN+'/workspace/'+slug})
  def listing(workspace):return c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]")).filter(e=>new URL(e.href).pathname.startsWith('+json.dumps('/workspace/'+workspace+'/t/')+')).map(e=>({slug:e.dataset.threadSlug,ordinal:Number(e.dataset.threadOrdinal),label:e.textContent.trim()}))')
  c.wait('document.querySelector("#primary-prompt-input")',45);check('FRESH_WORKSPACE',listing(slug)==[])
  own=[new_thread(slug) for _ in range(4)];c.call('Page.navigate',{'url':ORIGIN+'/workspace/'+slug+'/t/'+own[-1]['slug']});c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length>=4',30)
  check('MULTIPLE_THREADS',[(r['slug'],r['ordinal']) for r in listing(slug)]==[(t['slug'],i+1) for i,t in enumerate(own)])
  def delete_thread(workspace,thread):
   assert any(x['workspace']==workspace and x['id']==thread['id'] for x in report['owned'])
   q='[data-thread-slug="'+thread['slug']+'"]'
   c.evaluate('window.confirm=()=>true;document.querySelector('+json.dumps(q)+').closest("[role=listitem]").querySelector("[aria-label=\\"Thread options\\"]").click()')
   c.wait('Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim()==="Delete Thread")')
   c.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==="Delete Thread").click()');c.wait('!document.querySelector('+json.dumps(q)+')',25)
  for name,index in [('FIRST',0),('MIDDLE',1),('LAST',-1)]:
   chosen=own[index];delete_thread(slug,chosen);own.remove(chosen)
   rows=listing(slug);check('DELETE_'+name,[(r['slug'],r['ordinal']) for r in rows]==[(t['slug'],i+1) for i,t in enumerate(own)])
   persisted=api('/api/workspace/'+slug+'/threads')['threads'];check('INTERNAL_IDS_'+name,[t['id'] for t in persisted]==[t['id'] for t in own])
  c.call('Page.reload');c.wait('document.querySelector("#primary-prompt-input")',30);c.wait('document.querySelector('+json.dumps('[data-thread-slug="'+own[0]['slug']+'"]')+')',30);check('RELOAD_NUMBERING',listing(slug)[0]['ordinal']==1)
  # Image-workspace case uses only appended test threads. Historical chats stay untouched.
  image_workspace='image-generator';before=api('/api/workspace/'+image_workspace+'/threads')['threads'];image_threads=[new_thread(image_workspace) for _ in range(3)];target=image_threads[1]
  c.call('Page.navigate',{'url':ORIGIN+'/workspace/'+image_workspace+'/t/'+target['slug']});c.wait('document.querySelector("[data-testid=aag-current-thread]")',40);c.select_option('aag-ui-language','en')
  c.wait('Number(document.querySelector("[data-testid=aag-current-thread]").dataset.threadOrdinal)==='+str(len(before)+2))
  delete_thread(image_workspace,image_threads[0]);expected=len(before)+1;c.wait('Number(document.querySelector("[data-testid=aag-current-thread]").dataset.threadOrdinal)==='+str(expected));check('CURRENT_INDICATOR_RENUMBER',True)
  for t,wanted in [(image_threads[2],expected+1),(target,expected)]:
   c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+t['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps('/t/'+t['slug'])+')&&Number(document.querySelector("[data-testid=aag-current-thread]").dataset.threadOrdinal)==='+str(wanted))
  check('THREAD_SWITCHING',True);check('INTERNAL_IDS_STABLE',next(t['id'] for t in api('/api/workspace/'+image_workspace+'/threads')['threads'] if t['slug']==target['slug'])==target['id'])
  report['renumberedThread']={'thread':target,'workspace':image_workspace,'expectedOrdinal':expected,'nativeId':target['id']};c.screenshot(O/'renumbered-thread.png');report['result']='PASS';save()
 report['orphanBrowsers']=len(profile_processes(profile));save();print('THREAD_NUMBERING_BROWSER=PASS')
except BaseException as error:
 report.update(result='FAIL',error=str(error));save();raise
