"""Current thread details remain dynamic while persisted artifact names/IDs stay fixed.
Only the recorded, newly created predecessor may be deleted, in an owned clone.
"""
from pathlib import Path
import os,json,sys,urllib.request,importlib.util,tempfile,subprocess,sqlite3
P=Path.cwd();E=Path(os.environ['AAG_NUMBERING_EVIDENCE']).resolve();ORIGIN='http://localhost:3109'
runtime=json.loads((E/'native-runtime.json').read_text());C=Path(runtime['storage']);assert C.is_relative_to(P/'evaluation')
labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='live-completion'
sys.path.insert(0,str(P/'image-agent/tools'));from browser_lifecycle import OwnedBrowser,profile_processes
spec=importlib.util.spec_from_file_location('cdp',P/'image-agent/tools/atlas-browser-acceptance.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
case=os.environ.get('AAG_NUMBERING_CASE','thread-artifact-numbering');O=E/case;O.mkdir(exist_ok=True)
source=json.loads((E/'renumbered-live-confirmed/result.json').read_text());target=json.loads((E/'numbering-target/result.json').read_text());t=target['thread'];predecessor=target['precedingOwnedThread'];owned=json.loads((E/'numbering-browser/result.json').read_text())['owned']
assert any(v['id']==predecessor['id'] and v['slug']==predecessor['slug'] and v['workspace']=='image-generator' for v in owned)
report={'result':'RUNNING','checks':{},'thread':t,'filename':source['filename'],'artifactId':source['artifactId']}
def check(k,v):report['checks'][k]='PASS' if v else 'FAIL';save();assert v,k
def save():(O/'result.json').write_text(json.dumps(report,indent=2)+'\n')
def rows():
 with urllib.request.urlopen(ORIGIN+'/api/workspace/image-generator/threads') as r:return json.load(r)['threads']
profile=Path(tempfile.mkdtemp(prefix='aag-thread-artifact-browser-'))
try:
 with OwnedBrowser(['/usr/bin/brave-browser','--headless=new','--no-first-run','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={profile}','--window-size=1450,1150','about:blank'],timeout=180) as browser:
  c=browser.attach(b.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Page.navigate',{'url':source['url']});c.wait('document.querySelector("[data-testid=aag-current-thread]")',45);c.select_option('aag-ui-language','en')
  button='[data-testid=aag-artifact-show-details][data-artifact-id="'+source['artifactId']+'"]'
  def details():
   c.wait('document.querySelector('+json.dumps(button)+')');c.evaluate('document.querySelector('+json.dumps(button)+').click()');c.wait('document.querySelector("[data-detail-field=current_thread_number] dd")',25)
   return c.evaluate('Object.fromEntries(Array.from(document.querySelectorAll("dialog [data-detail-field]")).map(e=>[e.dataset.detailField,e.querySelector("dd")?.textContent]))')
  before=details();report['before']=before
  current=next(i+1 for i,x in enumerate(rows()) if x['id']==t['id']);check('DETAILS_CURRENT_BEFORE',before['current_thread_number']==str(current));check('CREATION_ORDINAL',before['thread_number_at_creation']==str(target['expectedOrdinal']))
  c.evaluate('document.querySelector("dialog button[aria-label=Close]").click()')
  if os.environ.get('AAG_DELETE_PREDECESSOR')=='1':
   assert current==target['expectedOrdinal'];q='[data-thread-slug="'+predecessor['slug']+'"]'
   c.evaluate('window.confirm=()=>true;document.querySelector('+json.dumps(q)+').closest("[role=listitem]").querySelector("[aria-label=\\"Thread options\\"]").click()');c.wait('Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim()==="Delete Thread")')
   c.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==="Delete Thread").click()');c.wait('!document.querySelector('+json.dumps(q)+')',20);current-=1
  check('REN_NUMBER',current==target['expectedOrdinal']-1);c.wait('document.querySelector("[data-testid=aag-current-thread]").dataset.threadOrdinal==='+json.dumps(str(current)))
  after=details();report['after']=after;check('DETAILS_CURRENT_AFTER',after['current_thread_number']==str(current));check('CREATION_UNCHANGED',after['thread_number_at_creation']==str(target['expectedOrdinal']))
  c.evaluate('navigator.clipboard.writeText=async text=>{window.__copiedDetails=text};document.querySelector("[data-testid=aag-artifact-copy-details]").click()');c.wait('window.__copiedDetails');report['copiedDetails']=c.evaluate('window.__copiedDetails');check('COPIED_CURRENT_NUMBER','Current thread number: '+str(current) in report['copiedDetails'])
  c.screenshot(O/'details-current.png');c.evaluate('document.querySelector("dialog button[aria-label=Close]").click()')
  def card():return c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).filter(e=>e.dataset.artifactId==='+json.dumps(source['artifactId'])+').map(e=>({name:e.textContent,rendered:e.parentElement.parentElement.querySelector("img")?.naturalWidth>0}))')
  check('PERSISTED_FILENAME_UNCHANGED',len(card())==1 and card()[0]['name']==source['filename']);c.call('Page.reload');c.wait('document.querySelector("[data-testid=aag-current-thread]")',40);c.wait('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).some(e=>e.dataset.artifactId==='+json.dumps(source['artifactId'])+'&&e.parentElement.parentElement.querySelector("img")?.naturalWidth>0)',30)
  after_reload=details();report['reload']=after_reload;check('RELOAD_CURRENT_NUMBER',after_reload['current_thread_number']==str(current));check('RELOAD_ARTIFACT_ONCE',len(card())==1 and card()[0]['name']==source['filename'] and card()[0]['rendered']);check('INTERNAL_ID_UNCHANGED',next(x['id'] for x in rows() if x['slug']==t['slug'])==t['id']);check('NO_INTERNAL_ID_AS_NUMBER',after_reload['current_thread_number']!=str(t['id']))
  with sqlite3.connect(C/'anythingllm.db') as db:
   ledger=db.execute('select thread_id,thread_serial,filename from aag_artifact_filenames where artifact_id=?',(source['artifactId'],)).fetchone()
  report['ledger']=ledger;check('LEDGER_ID_AND_FILENAME_UNCHANGED',ledger==(t['id'],target['expectedOrdinal'],source['filename']));check('PHYSICAL_FILE_PRESENT',(C/'generated-images'/source['filename']).is_file());report['result']='PASS';save()
 report['orphanBrowsers']=len(profile_processes(profile));save();print(case+'=PASS')
except BaseException as error:report.update(result='ERROR',error=str(error));save();raise
