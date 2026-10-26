"""Workspace/thread ordinal acceptance in a private native AnythingLLM clone.
Workspace reordering uses native keyboard drag/drop. Thread reordering uses a
clearly scoped ordered-list response fixture: upstream has no thread drag UI.
Neither test replaces IDs, changes routing contracts or generates an image.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.request, uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_lifecycle import OwnedBrowser, profile_processes
from browser_profile_checkpoint import checkpoint_command, finish_checkpoint
spec=importlib.util.spec_from_file_location('cdp',Path(__file__).with_name('atlas-browser-acceptance.py'))
cdp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cdp)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);args=parser.parse_args()
    E=Path(args.evidence).resolve();origin='http://localhost:3111';runtime=json.loads((E/'native-runtime.json').read_text());storage=Path(runtime['storage'])
    assert storage.is_relative_to(E)
    labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='sidebar'
    O=E/'ordinal-browser';O.mkdir(exist_ok=True);report={'checks':{},'result':'RUNNING','thread_reorder_basis':'ordered API response fixture; stable database IDs and native requests preserved'}
    def save(): (O/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def check(name,ok): report['checks'][name]='PASS' if ok else 'FAIL';save();assert ok,name
    def api(path,data=None,method=None):
        req=urllib.request.Request(origin+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
    def history():
        images={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (storage/'generated-images').iterdir() if p.is_file()}
        with sqlite3.connect((storage/'anythingllm.db').as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT * FROM aag_artifact_filenames ORDER BY rowid').fetchall()
        return {'images':images,'metadata':rows}
    before=history();known=api('/api/workspaces')['workspaces'];token=uuid.uuid4().hex[:6]
    titles=['Planning a complete product launch with milestones, owners and review notes '+token,'תכנון סביבת עבודה — משימות חשובות והחלטות להמשך הפעילות '+token,'תכנון API v2 — English and עברית '+token]
    workspaces=[api('/api/workspace/new',{'name':title},'POST')['workspace'] for title in titles]
    thread_names=['Planning the next release with detailed milestones and owner responsibilities','תכנון השלב הבא — משימות ואחריות לכל המשתתפים','Review API v2 — סיכום מפורט והחלטות']
    threads={}
    for workspace in workspaces:
        own=[]
        for title in thread_names:
            t=api('/api/workspace/'+workspace['slug']+'/thread/new',{},'POST')['thread']
            api('/api/workspace/'+workspace['slug']+'/thread/'+t['slug']+'/update',{'name':title},'POST');t['name']=title;own.append(t)
        threads[workspace['slug']]=own
    report['owned_workspaces']=workspaces;report['owned_threads']=threads;save()
    profile=Path(tempfile.mkdtemp(prefix='aag-sidebar-ordinal-'));persisted=Path(tempfile.mkdtemp(prefix='aag-sidebar-ordinal-restart-'))
    def launch(p):
        argv=['/usr/bin/brave-browser','--headless=new','--no-first-run','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={p}','--window-size=1440,1080','about:blank']
        return OwnedBrowser(checkpoint_command(argv,persisted) if p==profile else argv,timeout=420)
    def attach(browser):
        c=browser.attach(cdp.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Emulation.setFocusEmulationEnabled',{'enabled':True});return c
    def navigate(c,w,t=None):
        c.call('Page.navigate',{'url':origin+'/workspace/'+w['slug']+('/t/'+t['slug'] if t else '')});c.wait('document.querySelector("#primary-prompt-input")',40);c.wait('document.querySelectorAll("[data-testid=aag-workspace-link]").length>0',30)
    def workspace_rows(c):return c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-workspace-link]")).map(e=>({slug:e.dataset.workspaceSlug,ordinal:Number(e.dataset.workspaceOrdinal),prefix:e.querySelector("[data-testid=aag-workspace-number]").textContent,title:e.querySelector("bdi").textContent,full:e.getAttribute("aria-label")}))')
    def thread_rows(c):return c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]")).map(e=>({slug:e.dataset.threadSlug,ordinal:Number(e.dataset.threadOrdinal),prefix:e.querySelector("[data-testid=aag-thread-number]").textContent,title:e.querySelector("bdi").textContent,full:e.getAttribute("aria-label")}))')
    def format_ok(rows):return all(r['ordinal']==i+1 and r['prefix']==str(i+1)+' -' and r['full']==str(i+1)+' - '+r['title'] for i,r in enumerate(rows))
    def key(c,name,code=None,vk=None):
        params={'key':name,'code':code or name}
        if vk:params['windowsVirtualKeyCode']=vk
        for kind in ['keyDown','keyUp']:c.call('Input.dispatchKeyEvent',{'type':kind,**params})
    def drag_workspace(c,wanted,target_index):
        rows=workspace_rows(c);start=next(i for i,r in enumerate(rows) if r['slug']==wanted)
        selector='[data-workspace-slug="'+wanted+'"]'
        c.evaluate('(()=>{const e=document.querySelector('+json.dumps(selector)+').querySelector("[data-rbd-drag-handle-draggable-id]");e.scrollIntoView({block:"center"});e.focus()})()')
        key(c,' ','Space',32);time.sleep(.2)
        for _ in range(abs(target_index-start)):
            key(c,'ArrowUp' if target_index<start else 'ArrowDown',vk=38 if target_index<start else 40);time.sleep(.13)
        key(c,' ','Space',32);time.sleep(.5)
        expected=[r['slug'] for r in rows];expected.insert(target_index,expected.pop(start))
        c.wait('Array.from(document.querySelectorAll("[data-testid=aag-workspace-link]")).map(e=>e.dataset.workspaceSlug).join(",")==='+json.dumps(','.join(expected)),10)
        return expected
    try:
        with launch(profile) as browser:
            c=attach(browser);navigate(c,workspaces[0],threads[workspaces[0]['slug']][0]);c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===3')
            c.evaluate('document.documentElement.classList.add("light")')
            check('WORKSPACE_INITIAL_NUMBERING',format_ok(workspace_rows(c)))
            check('THREAD_INITIAL_NUMBERING',format_ok(thread_rows(c)))
            # Put the owned workspaces at the top using the actual native DnD controls.
            for index,w in enumerate(workspaces):drag_workspace(c,w['slug'],index)
            ordered=workspace_rows(c);check('WORKSPACE_REORDER_IMMEDIATE',format_ok(ordered) and [r['slug'] for r in ordered[:3]]==[w['slug'] for w in workspaces])
            native=api('/api/workspaces')['workspaces'];check('WORKSPACE_IDS_STABLE_REORDER',all(next(v['id'] for v in native if v['slug']==w['slug'])==w['id'] for w in known+workspaces))
            c.evaluate('document.querySelector("[data-testid=aag-sidebar] .overflow-y-scroll").scrollTop=0')
            for label,action,width in [('narrow','Home',260),('default','Enter',292),('wide','End',520)]:
                c.evaluate('document.querySelector("[data-testid=aag-sidebar-resizer]").focus()');key(c,action);c.wait('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width==='+str(width))
                metrics=c.evaluate('''(()=>{const box=e=>({width:e.clientWidth,scroll:e.scrollWidth,x:e.getBoundingClientRect().x,right:e.getBoundingClientRect().right});return ['workspace','thread'].map(kind=>Array.from(document.querySelectorAll('[data-testid=aag-'+kind+'-link]')).filter(e=>e.querySelector('[data-testid=aag-'+kind+'-number]')).map(e=>({prefix:box(e.querySelector('[data-testid=aag-'+kind+'-number]')),title:box(e.querySelector('bdi')),direction:getComputedStyle(e.querySelector('bdi')).direction,ellipsis:getComputedStyle(e.querySelector('bdi')).textOverflow})))})()''')
                check(label+'_BOTH_PREFIXES_VISIBLE',all(r['prefix']['scroll']<=r['prefix']['width'] and r['title']['width']>10 for group in metrics for r in group))
                check(label+'_TITLE_ONLY_ELLIPSIS',all(r['ellipsis']=='ellipsis' for group in metrics for r in group) and metrics[0][0]['title']['scroll']>metrics[0][0]['title']['width'] and metrics[1][0]['title']['scroll']>metrics[1][0]['title']['width'])
                check(label+'_RTL_LTR',[r['direction'] for r in metrics[0][:3]]==['ltr','rtl','rtl'] and [r['direction'] for r in metrics[1]]==['ltr','rtl','ltr'])
                check(label+'_MAIN_REFLOW',c.evaluate('document.querySelector("[data-testid=aag-sidebar]").nextElementSibling.getBoundingClientRect().right<=innerWidth'))
                report.setdefault('layouts',{})[label]=metrics;save();c.screenshot(O/(label+'.png'))
            check('BOTH_TITLES_GAIN_REMAINING_WIDTH',all(abs(report['layouts']['wide'][group][0]['title']['width']-report['layouts']['narrow'][group][0]['title']['width']-260)<=1 for group in [0,1]))
            for kind in ['workspace','thread']:
                for mode,index in [('focus',0),('hover',1)]:
                    selected=workspaces[index] if kind=='workspace' else threads[workspaces[0]['slug']][index]
                    selector='[data-'+kind+'-slug="'+selected['slug']+'"]';tooltip='workspace-name' if kind=='workspace' else 'workspace-thread-name'
                    c.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':1100,'y':80});c.evaluate('document.activeElement.blur()')
                    if mode=='focus':c.evaluate('document.querySelector('+json.dumps(selector)+').focus()')
                    else:
                        pos=c.evaluate('(()=>{const r=document.querySelector('+json.dumps(selector)+').querySelector("bdi").getBoundingClientRect();return {x:r.x+4,y:r.y+r.height/2}})()');c.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**pos})
                    full=str(index+1)+' - '+selected['name']
                    c.wait('(()=>{const e=document.getElementById('+json.dumps(tooltip)+');return e&&e.textContent==='+json.dumps(full)+'&&Number(getComputedStyle(e).opacity)>=.85})()',10)
                    check(kind.upper()+'_TOOLTIP_'+mode.upper(),True);c.screenshot(O/(kind+'-tooltip-'+mode+'.png'));key(c,'Escape',vk=27)
            for w in [workspaces[1],workspaces[2],workspaces[0]]:
                c.evaluate('document.querySelector('+json.dumps('[data-workspace-slug="'+w['slug']+'"]')+').click()');c.wait('location.pathname==='+json.dumps('/workspace/'+w['slug']));c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===3')
                rows=thread_rows(c);check('WORKSPACE_SWITCH_'+w['slug'],format_ok(rows) and [r['slug'] for r in rows]==[t['slug'] for t in threads[w['slug']]])
            w=workspaces[0];own=threads[w['slug']]
            # Reorder the native list response, not the database IDs or routes.
            # This exercises the same presentation boundary as any future ordering UI.
            wanted=[own[2]['slug'],own[0]['slug'],own[1]['slug']]
            c.evaluate('''(()=>{const nativeFetch=window.fetch;const path='''+json.dumps('/api/workspace/'+w['slug']+'/threads')+''';const order='''+json.dumps(wanted)+''';window.__restoreOrdinalFetch=()=>{window.fetch=nativeFetch;delete window.__restoreOrdinalFetch};window.fetch=async(...args)=>{const response=await nativeFetch(...args);const url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);if(url.pathname!==path)return response;const body=await response.clone().json();body.threads.sort((a,b)=>order.indexOf(a.slug)-order.indexOf(b.slug));return new Response(JSON.stringify(body),{status:response.status,headers:response.headers})}})()''')
            c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+own[2]['slug']+'"]')+').click()');c.wait('document.querySelector("[data-testid=aag-thread-link][data-thread-slug]").dataset.threadSlug==='+json.dumps(wanted[0]));check('THREAD_REORDER_RESPONSE',format_ok(thread_rows(c)) and [r['slug'] for r in thread_rows(c)]==wanted)
            for t in [own[0],own[1],own[2]]:
                c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+t['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps('/t/'+t['slug'])+')');check('THREAD_SWITCH_'+t['slug'],next(r['ordinal'] for r in thread_rows(c) if r['slug']==t['slug'])==wanted.index(t['slug'])+1)
            check('THREAD_IDS_STABLE_REORDER',[t['id'] for t in api('/api/workspace/'+w['slug']+'/threads')['threads']]==[t['id'] for t in own])
            c.evaluate('window.__restoreOrdinalFetch()');c.call('Page.reload');c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===3');check('NATIVE_ORDER_RELOAD',format_ok(thread_rows(c)) and [r['slug'] for r in thread_rows(c)]==[t['slug'] for t in own])
            middle=own[1];selector='[data-thread-slug="'+middle['slug']+'"]'
            c.evaluate('window.confirm=()=>true;document.querySelector('+json.dumps(selector)+').closest("[role=listitem]").querySelector("[aria-label=\\"Thread options\\"]").click()');c.wait('Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim()==="Delete Thread")');c.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==="Delete Thread").click()');c.wait('!document.querySelector('+json.dumps(selector)+')')
            check('THREAD_DELETE_RENUMBER',format_ok(thread_rows(c)) and [r['slug'] for r in thread_rows(c)]==[own[0]['slug'],own[2]['slug']])
            # Delete only our middle workspace using its native settings UI.
            doomed=workspaces[1];c.call('Page.navigate',{'url':origin+'/workspace/'+doomed['slug']+'/settings/general-appearance'});c.wait('Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim().toLowerCase()==="delete workspace")',30)
            c.evaluate('window.confirm=()=>true;Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim().toLowerCase()==="delete workspace").click()');c.wait('location.pathname==="/" && document.querySelectorAll("[data-testid=aag-workspace-link]").length>0',30)
            c.wait('!document.querySelector('+json.dumps('[data-workspace-slug="'+doomed['slug']+'"]')+')');rows=workspace_rows(c);check('WORKSPACE_DELETE_RENUMBER',format_ok(rows) and [r['slug'] for r in rows[:2]]==[workspaces[0]['slug'],workspaces[2]['slug']])
            c.evaluate('document.querySelector('+json.dumps('[data-workspace-slug="'+workspaces[2]['slug']+'"]')+').click()');c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===3');check('THREAD_NUMBERING_INDEPENDENT',format_ok(thread_rows(c)) and [r['slug'] for r in thread_rows(c)]==[t['slug'] for t in threads[workspaces[2]['slug']]])
            check('STABLE_NATIVE_IDENTIFIERS_AFTER_DELETIONS',all(next(v['id'] for v in api('/api/workspaces')['workspaces'] if v['slug']==item['slug'])==item['id'] for item in known+[workspaces[0],workspaces[2]]) and [v['id'] for v in api('/api/workspace/'+w['slug']+'/threads')['threads']]==[own[0]['id'],own[2]['id']])
            c.screenshot(O/'renumbered.png');c.call('Page.reload');c.wait('document.querySelectorAll("[data-testid=aag-workspace-link]").length>0');c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===3');check('RELOAD_BOTH_NUMBERINGS',format_ok(workspace_rows(c)) and format_ok(thread_rows(c)))
            report['saved_workspace_order']=[r['slug'] for r in workspace_rows(c)];report['saved_stored_order']=c.evaluate('localStorage.getItem("anythingllm-workspace-order")');save()
            finish_checkpoint(browser,c,persisted)
        with launch(persisted) as browser:
            c=attach(browser);navigate(c,workspaces[0],own[2]);c.wait('document.querySelectorAll("[data-testid=aag-thread-link][data-thread-slug]").length===2')
            report['restart_workspace_rows']=workspace_rows(c);report['restart_stored_order']=c.evaluate('localStorage.getItem("anythingllm-workspace-order")');save()
            check('BROWSER_RESTART_WORKSPACE_ORDER',format_ok(workspace_rows(c)) and [r['slug'] for r in workspace_rows(c)]==report['saved_workspace_order'])
            check('BROWSER_RESTART_THREAD_ORDER',format_ok(thread_rows(c)) and [r['slug'] for r in thread_rows(c)]==[own[0]['slug'],own[2]['slug']])
            check('BROWSER_RESTART_WIDTH',c.evaluate('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width')==520)
            check('THREAD_PREFIX_WORD_REMOVED',all(r['prefix']==str(i+1)+' -' for i,r in enumerate(thread_rows(c))))
        check('HISTORICAL_IMAGES_AND_METADATA_UNCHANGED',history()==before)
        check('NO_ORPHAN_BROWSERS',not profile_processes(profile) and not profile_processes(persisted))
        report['result']='PASS';save();print('SIDEBAR_ORDINAL_BROWSER=PASS; CHECKS='+str(len(report['checks'])))
    except BaseException as error:
        report.update(result='FAIL',error=str(error));save();raise
    finally:shutil.rmtree(persisted,ignore_errors=True)
if __name__=='__main__':main()
