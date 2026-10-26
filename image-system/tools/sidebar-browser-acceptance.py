"""Real native UI acceptance. Writes only to an explicitly owned sidebar clone.
No model calls, production origins, user browser profiles or GUI focus automation.
"""
from pathlib import Path
import argparse, importlib.util, json, shutil, subprocess, sys, tempfile, time, urllib.request, uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_lifecycle import OwnedBrowser, profile_processes
from browser_profile_checkpoint import checkpoint_command, finish_checkpoint
spec = importlib.util.spec_from_file_location('cdp', Path(__file__).with_name('atlas-browser-acceptance.py'))
cdp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cdp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--origin', default='http://localhost:3111')
    parser.add_argument('--history-thread', help='Existing image-generator thread in the private clone; never generates an image')
    args = parser.parse_args()
    E = Path(args.evidence).resolve(); origin = args.origin
    assert origin == 'http://localhost:3111', 'Only the owned localhost evaluation origin is permitted'
    runtime = json.loads((E/'native-runtime.json').read_text())
    assert Path(runtime['storage']).is_relative_to(E)
    labels = json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True))
    assert labels.get('aag.evaluation') == 'sidebar'
    O = E/'browser'; O.mkdir(exist_ok=True)
    report = {'checks':{}, 'layouts':{}, 'result':'RUNNING'}
    def save(): (O/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    def check(name, ok):
        report['checks'][name] = 'PASS' if ok else 'FAIL'; save(); assert ok, name
    def api(path, data=None, method=None):
        req = urllib.request.Request(origin+path, data=json.dumps(data).encode() if data is not None else None, method=method, headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=15) as r: return json.load(r)
    workspace = api('/api/workspace/new', {'name':'Sidebar validation '+uuid.uuid4().hex[:6]}, 'POST')['workspace']; slug=workspace['slug']
    titles = ['Planning the autumn product release — milestones, owners and follow-up decisions', 'תכנון מהדורת הסתיו — משימות, אחריות והחלטות להמשך העבודה', 'תכנון API v2 — English and עברית, October 2026']
    threads=[]
    for title in titles:
        t=api('/api/workspace/'+slug+'/thread/new', {}, 'POST')['thread']
        api('/api/workspace/'+slug+'/thread/'+t['slug']+'/update', {'name':title}, 'POST')
        threads.append(t)
    report['workspace']=slug; report['threads']=threads; save()
    profile=Path(tempfile.mkdtemp(prefix='aag-sidebar-browser-'))
    persisted=Path(tempfile.mkdtemp(prefix='aag-sidebar-restart-'))
    url=origin+'/workspace/'+slug+'/t/'+threads[0]['slug']
    def launch(path):
        argv=['/usr/bin/brave-browser','--headless=new','--no-first-run','--no-default-browser-check','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={path}','--window-size=1440,1080','about:blank']
        return OwnedBrowser(checkpoint_command(argv,persisted) if path==profile else argv,timeout=360)
    def attach(browser):
        c=browser.attach(cdp.Cdp(browser.target()['webSocketDebuggerUrl'])); c.call('Page.enable'); c.call('Runtime.enable'); c.call('Emulation.setFocusEmulationEnabled',{'enabled':True}); c.call('Page.navigate',{'url':url}); c.wait('document.querySelectorAll("[data-testid=aag-thread-title]").length===3 && document.querySelector("#primary-prompt-input")',45); return c
    try:
        with launch(profile) as browser:
            c=attach(browser)
            c.evaluate('localStorage.setItem("theme","light");document.documentElement.classList.add("light")')
            def width(): return c.evaluate('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width')
            def resize(w):
                pos=c.evaluate('(()=>{const r=document.querySelector("[data-testid=aag-sidebar-resizer]").getBoundingClientRect();return {x:r.x+r.width/2,y:200}})()')
                c.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**pos})
                c.call('Input.dispatchMouseEvent',{'type':'mousePressed','button':'left','clickCount':1,**pos})
                c.call('Input.dispatchMouseEvent',{'type':'mouseMoved','button':'left','buttons':1,'x':pos['x']+w-width(),'y':pos['y']})
                c.call('Input.dispatchMouseEvent',{'type':'mouseReleased','button':'left','clickCount':1,'x':w,'y':pos['y']})
                c.wait('Number(document.querySelector("[data-testid=aag-sidebar-resizer]").getAttribute("aria-valuenow"))==='+str(w))
            def layout():
                return c.evaluate('''(()=>{const s=document.querySelector('[data-testid=aag-sidebar]'),r=s.getBoundingClientRect(),main=s.nextElementSibling.getBoundingClientRect();const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width,client:e.clientWidth,scroll:e.scrollWidth}};return {sidebar:box(s),main:{x:main.x,right:main.right,width:main.width},viewport:innerWidth,rows:Array.from(s.querySelectorAll('[data-testid=aag-thread-link]')).map(e=>({label:e.getAttribute('aria-label'),ordinal:Number(e.dataset.threadOrdinal),slug:e.dataset.threadSlug,number:box(e.querySelector('[data-testid=aag-thread-number]')),title:{...box(e.querySelector('bdi')),text:e.querySelector('bdi').textContent,direction:getComputedStyle(e.querySelector('bdi')).direction,overflow:getComputedStyle(e.querySelector('bdi')).textOverflow}})),search:box(s.querySelector('input[type=search]')),footer:box(s.querySelector('[aria-label="Docs"]').closest('.justify-center')),newThread:box(Array.from(s.querySelectorAll('button')).find(e=>e.textContent.includes('New Thread'))),pageOverflow:document.documentElement.scrollWidth>innerWidth}})()''')
            check('DEFAULT_WIDTH',width()==292)
            for name,w in [('default',292),('narrow',260),('wide',520)]:
                if width()!=w: resize(w)
                time.sleep(.3); m=layout(); report['layouts'][name]=m; save()
                check(name+'_NUMBER_VISIBLE',all(r['number']['scroll']<=r['number']['client'] and r['number']['x']>=0 and r['title']['width']>10 for r in m['rows']))
                check(name+'_TITLE_ONLY_ELLIPSIS',all(r['title']['overflow']=='ellipsis' for r in m['rows']) and m['rows'][0]['title']['scroll']>m['rows'][0]['title']['client'])
                check(name+'_MAIN_REFLOW',m['main']['x']>=w and m['main']['right']<=m['viewport'] and not m['pageOverflow'])
                check(name+'_ALIGNMENT',all(m[k]['x']>=0 and m[k]['right']<=w for k in ['search','footer','newThread']))
                check(name+'_BIDI',[r['title']['direction'] for r in m['rows']]==['ltr','rtl','rtl'])
                c.screenshot(O/(name+'.png'))
            check('TITLE_USES_EXTRA_WIDTH',report['layouts']['wide']['rows'][0]['title']['width']-report['layouts']['narrow']['rows'][0]['title']['width']==260)
            check('MAIN_REALLOCATES_WIDTH',abs(report['layouts']['narrow']['main']['width']-report['layouts']['wide']['main']['width']-260)<1)
            # Focus and hover both expose the complete string via the real portal tooltip.
            for idx,mode in [(0,'focus'),(1,'hover')]:
                selector='[data-thread-slug="'+threads[idx]['slug']+'"]'
                if mode=='focus': c.evaluate('document.querySelector('+json.dumps(selector)+').focus()')
                else:
                    c.evaluate('document.activeElement.blur()');pos=c.evaluate('(()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.x+8,y:r.y+r.height/2}})()');c.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**pos})
                full=str(idx+1)+' - '+titles[idx]
                c.wait('(()=>{const e=document.querySelector("#workspace-thread-name");return e&&e.textContent==='+json.dumps(full)+'&&e.getBoundingClientRect().width>0&&Number(getComputedStyle(e).opacity)>=0.85})()',10)
                check('TOOLTIP_'+mode.upper(),True);c.screenshot(O/('tooltip-'+mode+'.png'))
            c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape'})
            c.wait('!document.querySelector("#workspace-thread-name") || getComputedStyle(document.querySelector("#workspace-thread-name")).opacity==="0"');check('TOOLTIP_ESCAPE',True)
            resize(412)
            c.call('Page.reload');c.wait('document.querySelector("[data-testid=aag-sidebar-resizer]")',30);check('RELOAD_WIDTH',width()==412)
            c.evaluate('document.querySelector("[data-testid=aag-sidebar-resizer]").focus()')
            for key,w in [('ArrowRight',422),('Home',260),('End',520),('Enter',292)]:
                c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':key,'code':key});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':key,'code':key});c.wait('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width==='+str(w))
            check('KEYBOARD_RESIZE',True)
            resize(500)
            c.call('Emulation.setDeviceMetricsOverride',{'width':800,'height':1080,'deviceScaleFactor':1,'mobile':False});c.wait('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width===320');check('SMALL_VIEWPORT_CLAMP',True); check('SMALL_VIEWPORT_MAIN_REFLOW',c.evaluate('document.querySelector("[data-testid=aag-sidebar]").nextElementSibling.getBoundingClientRect().right <= innerWidth')); c.screenshot(O/'small-viewport.png')
            c.call('Emulation.clearDeviceMetricsOverride');c.wait('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width===500');check('PREFERRED_WIDTH_RESTORED',True)
            # Close Chromium first, then snapshot the flushed disk profile for a new process.
            finish_checkpoint(browser,c,persisted)
        with launch(persisted) as browser:
            c=attach(browser);check('BROWSER_RESTART_PERSISTENCE',c.evaluate('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width')==500)
            for i,t in enumerate(threads):
                c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+t['slug']+'"]')+').click()');c.wait('location.pathname.endsWith('+json.dumps('/t/'+t['slug'])+')');check('SWITCH_'+str(i+1),c.evaluate('document.querySelector('+json.dumps('[data-thread-slug="'+t['slug']+'"]')+').dataset.threadOrdinal')==str(i+1))
            # Delete only this run's middle fixture using the real UI.
            middle=threads[1];selector='[data-thread-slug="'+middle['slug']+'"]'
            c.evaluate('window.confirm=()=>true;document.querySelector('+json.dumps(selector)+').closest("[role=listitem]").querySelector("[aria-label=\\"Thread options\\"]").click()')
            c.wait('Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim()==="Delete Thread")')
            c.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==="Delete Thread").click()');c.wait('!document.querySelector('+json.dumps(selector)+')')
            check('IMMEDIATE_DELETE_RENUMBER',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-thread-number]")).map(e=>e.textContent)')==['1 -','2 -'])
            rows=api('/api/workspace/'+slug+'/threads')['threads'];check('STABLE_INTERNAL_IDS',[r['id'] for r in rows]==[threads[0]['id'],threads[2]['id']])
            c.call('Page.reload');c.wait('document.querySelectorAll("[data-testid=aag-thread-number]").length===2');check('RELOAD_NUMBERING',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-thread-number]")).map(e=>e.textContent)')==['1 -','2 -'])
            c.screenshot(O/'renumbered.png')
            c.evaluate("""document.querySelector('button[aria-label^="Hide Sidebar"]').click()""")
            c.wait('!document.querySelector("[data-testid=aag-sidebar-resizer]")');check('COLLAPSE_SIDEBAR',True)
            c.evaluate("""document.querySelector('button[aria-label^="Show Sidebar"]').click()""")
            c.wait('document.querySelector("[data-testid=aag-sidebar-resizer]")');check('REOPEN_WIDTH',c.evaluate('document.querySelector("[data-testid=aag-sidebar]").getBoundingClientRect().width')==500)
            if args.history_thread:
                c.call('Page.navigate',{'url':origin+'/workspace/image-generator/t/'+args.history_thread})
                c.wait('document.querySelector("[data-testid=aag-artifact-filename]")',40)
                c.wait('Array.from(document.querySelectorAll("img[role=button]")).some(e=>e.complete&&e.naturalWidth>0)',30)
                filenames=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)')
                for label,key in [('narrow','Home'),('default','Enter'),('wide','End')]:
                    c.evaluate('document.querySelector("[data-testid=aag-sidebar-resizer]").focus()')
                    c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':key,'code':key});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':key,'code':key})
                    time.sleep(.25)
                    check('HISTORY_REFLOW_'+label,c.evaluate('document.querySelector("[data-testid=aag-sidebar]").nextElementSibling.getBoundingClientRect().right<=innerWidth'))
                    check('HISTORY_IMAGES_'+label,c.evaluate('Array.from(document.querySelectorAll("img[role=button]")).every(e=>e.complete&&e.naturalWidth>0) && document.querySelectorAll("img[role=button]").length>0'))
                    c.screenshot(O/('history-'+label+'.png'))
                c.call('Page.reload');c.wait('document.querySelector("[data-testid=aag-artifact-filename]")',40)
                check('HISTORY_FILENAMES_UNCHANGED',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)')==filenames)
        report['orphanBrowsers']=len(profile_processes(profile))+len(profile_processes(persisted));check('NO_ORPHAN_BROWSERS',report['orphanBrowsers']==0)
        report['result']='PASS';save();print('SIDEBAR_BROWSER=PASS; CHECKS='+str(len(report['checks'])))
    except BaseException as error:
        report.update(result='FAIL',error=str(error));save();raise
    finally:
        shutil.rmtree(persisted,ignore_errors=True)

if __name__=='__main__': main()
