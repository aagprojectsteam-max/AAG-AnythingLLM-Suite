"""Native chat presentation regressions in an owned isolated app; no generation.
Model-label refresh uses one explicit GET-response fixture after first checking
its real label. Production database, model routing and files are never changed.
"""
from pathlib import Path
import argparse,base64,hashlib,importlib.util,json,shutil,subprocess,sys,tempfile,time,urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent))
from browser_lifecycle import OwnedBrowser,profile_processes
spec=importlib.util.spec_from_file_location('cdp',Path(__file__).with_name('atlas-browser-acceptance.py'));cdp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cdp)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);args=parser.parse_args()
 E=Path(args.evidence).resolve();runtime=json.loads((E/'native-runtime.json').read_text());storage=Path(runtime['storage']);assert storage.is_relative_to(E)
 labels=json.loads(subprocess.check_output(['docker','inspect',runtime['name'],'--format','{{json .Config.Labels}}'],text=True));assert labels.get('aag.evaluation')=='sidebar'
 O=E/'refinement-browser';O.mkdir(exist_ok=True);report={'result':'RUNNING','checks':{},'layouts':{},'fixture_boundary':'one native model-label GET response; generation forbidden'}
 images={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (storage/'generated-images').iterdir() if p.is_file()}
 origin='http://localhost:3111';profile=Path(tempfile.mkdtemp(prefix='aag-composer-refinement-'))
 def api(path):return json.load(urllib.request.urlopen(origin+path,timeout=20))
 workspace=api('/api/workspace/image-generator')['workspace'];threads=api('/api/workspace/image-generator/threads')['threads'];slug='920949ea-b51d-4dff-805b-f0beb1348862';assert any(t['slug']==slug for t in threads)
 def save():(O/'result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 def check(name,ok):report['checks'][name]='PASS' if ok else 'FAIL';save();assert ok,name
 def shot(name):(O/(name+'.png')).write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']))
 def click(id):c.evaluate('document.querySelector('+json.dumps('[data-testid="'+id+'"]')+').click()');time.sleep(.18)
 def key(id,k):
  c.evaluate('document.querySelector('+json.dumps('[data-testid="'+id+'"]')+').focus()')
  for kind in ['keyDown','keyUp']:c.call('Input.dispatchKeyEvent',{'type':kind,'key':k,'code':k})
  time.sleep(.2)
 def metrics():return c.evaluate('''(()=>{const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom,cw:e.clientWidth,sw:e.scrollWidth,ch:e.clientHeight,sh:e.scrollHeight}};
 const surface=document.querySelector('[data-aag-chat-surface]'),history=document.querySelector('#chat-history'),header=document.querySelector('[data-testid=aag-chat-header]'),panel=document.querySelector('[data-testid=aag-composer-panel]');
 return {surface:rect(surface),history:rect(history),header:rect(header),panel:rect(panel),gutter:parseFloat(getComputedStyle(history).paddingLeft),column:rect(document.querySelector('[data-aag-message-column]')),body:rect(document.querySelector('[data-aag-chat-body]')),placement:document.querySelector('.aag-composer-presentation').dataset.placement,side:document.querySelector('.aag-composer-presentation').dataset.side,settings:rect(document.querySelector('[data-testid=aag-chat-settings]')),model:rect(document.querySelector('[data-testid=aag-chat-model-button]')),page:{w:innerWidth,sw:document.documentElement.scrollWidth},images:Array.from(history.querySelectorAll('[data-testid=aag-image-card] img[role=button]')).filter(e=>e.complete&&e.naturalWidth>0).map(e=>({...rect(e),nw:e.naturalWidth,nh:e.naturalHeight})),actions:Array.from(history.querySelectorAll('[data-testid=aag-artifact-actions]')).map(e=>({...rect(e),wrap:getComputedStyle(e).flexWrap,buttons:Array.from(e.children).filter(x=>x.tagName==='BUTTON').map(rect)}))};})()''')
 def layout(name,docked=None):
  time.sleep(.25);v=metrics();report['layouts'][name]=v;save()
  check(name+'_SAFE_GUTTER',v['gutter']>=16 and len(v['images'])>0 and all(i['x']-v['surface']['x']>=15 and v['surface']['right']-i['right']>=15 for i in v['images']))
  check(name+'_IMAGES_RESPONSIVE',all(i['w']<=i['nw']+1 and abs(i['w']/i['h']-i['nw']/i['nh'])<.02 for i in v['images']))
  check(name+'_NO_HORIZONTAL_OVERFLOW',v['page']['sw']<=v['page']['w'] and v['history']['sw']<=v['history']['cw']+1 and all(a['sw']<=a['cw']+1 for a in v['actions']))
  check(name+'_ACTION_REFLOW',all(a['wrap']=='wrap' and all(b['x']>=a['x']-1 and b['right']<=a['right']+1 for b in a['buttons']) for a in v['actions']))
  check(name+'_HEADER_SEPARATE',v['header']['bottom']<=v['history']['y']+1 and v['header']['h']>=48 and v['model']['right']<v['settings']['x'])
  check(name+'_ONE_MESSAGE_SCROLLER',v['body']['sh']<=v['body']['ch']+1 and v['surface']['sh']<=v['surface']['ch']+1)
  if docked:check(name+'_DOCK_NO_OVERLAP',v['placement']=='panel' and v['surface']['w']>=479 and (v['panel']['right']<=v['surface']['x'] or v['panel']['x']>=v['surface']['right']))
  check(name+'_NO_DISPLAY_DROPDOWN',c.evaluate('!document.querySelector("[data-testid=aag-composer-display-mode]")&& !Array.from(document.querySelectorAll(".aag-composer-layout-bar select")).some(e=>Array.from(e.options).some(o=>o.value==="popup"))'))
  return v
 def disclosure(name):
  b='document.querySelector("[data-testid=aag-controls-disclosure]")'
  if c.evaluate(b+'.getAttribute("aria-expanded")')!='true':click('aag-controls-disclosure')
  check(name+'_EXPANDED_COLLAPSE_UP',c.evaluate(b+'.querySelector("[data-disclosure-icon]").dataset.disclosureIcon')=='up' and c.evaluate('!!document.querySelector("#aag-composer-controls")'))
  expanded=c.evaluate(b+'.getAttribute("aria-label")');click('aag-controls-disclosure')
  check(name+'_COLLAPSED_EXPAND_DOWN',c.evaluate(b+'.querySelector("[data-disclosure-icon]").dataset.disclosureIcon')=='down' and c.evaluate('!document.querySelector("#aag-composer-controls")') and c.evaluate(b+'.getAttribute("aria-label")')!=expanded)
  check(name+'_EXPLICIT_ICON_ORIENTATION',c.evaluate('getComputedStyle('+b+'.querySelector("svg")).transform')=='none')
  if name=='right_en':shot('07-collapsed-controls')
  click('aag-controls-disclosure')
  if name=='right_en':shot('06-expanded-controls')
 try:
  with OwnedBrowser(['/usr/bin/brave-browser','--headless=new','--no-first-run','--no-default-browser-check','--password-store=basic','--remote-allow-origins=*',f'--user-data-dir={profile}','--window-size=1600,1080','about:blank'],timeout=450) as browser:
   c=browser.attach(cdp.Cdp(browser.target()['webSocketDebuggerUrl']));c.call('Page.enable');c.call('Runtime.enable');c.call('Emulation.setFocusEmulationEnabled',{'enabled':True})
   c.call('Page.addScriptToEvaluateOnNewDocument',{'source':'''(()=>{localStorage.setItem('theme','light');window.__refinementErrors=[];window.addEventListener('error',e=>window.__refinementErrors.push(e.message));const native=fetch;window.fetch=async(...args)=>{const path=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href).pathname;if(path.endsWith('/stream-chat'))throw Error('Generation forbidden in visual layout test');const response=await native(...args);if(path==='/api/workspace/image-generator'&&window.__modelLabelFixture){const value=await response.json();value.workspace.chatModel=window.__modelLabelFixture;return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})}return response}})()'''})
   c.call('Page.navigate',{'url':origin+'/workspace/image-generator/t/'+slug});c.wait('document.querySelector("[data-testid=aag-composer-dock-toggle]")&&document.querySelector("[data-testid=aag-chat-model-label]")?.textContent',45)
   c.wait('Array.from(document.querySelectorAll("[data-testid=aag-image-card] img")).some(e=>e.complete&&e.naturalWidth>0)',30);c.select_option('aag-ui-language','en')
   check('REAL_NATIVE_MODEL_LABEL',c.evaluate('document.querySelector("[data-testid=aag-chat-model-label]").textContent')==workspace['chatModel'])
   check('DEFAULT_POPUP_DOCK_LABEL',c.evaluate('document.querySelector("[data-testid=aag-composer-dock-toggle]").getAttribute("aria-label")')=='Dock Image Composer')
   check('POPUP_SIDE_CONTROL_HIDDEN',c.evaluate('!document.querySelector("[data-testid=aag-composer-side]")'))
   filenames=c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)');layout('popup');shot('01-popup-dock-only')
   c.evaluate('document.querySelectorAll(".aag-composer-modes button")[1].click()');c.wait('document.querySelector("#aag-composer-controls")');disclosure('popup_en')
   c.select_option('aag-ui-language','he');disclosure('popup_he');layout('popup_rtl');c.select_option('aag-ui-language','en');click('aag-composer-dock-toggle');c.wait('document.querySelector(".aag-composer-presentation[data-placement=panel]")')
   check('PANEL_SIDE_CONTROL_PRESENT',c.evaluate('!!document.querySelector("[data-testid=aag-composer-side]")'))
   check('PANEL_UNDOCK_LABEL',c.evaluate('document.querySelector("[data-testid=aag-composer-dock-toggle]").getAttribute("aria-label")')=='Undock Image Composer')
   for side in ['right','left']:
    c.select_option('aag-composer-side',side);key('aag-composer-resizer','Enter');layout(side+'_default',True);shot('02-'+side+'-default')
    disclosure(side+'_en');c.select_option('aag-ui-language','he');disclosure(side+'_he');layout(side+'_rtl',True);shot(side+'-rtl');c.select_option('aag-ui-language','en')
    key('aag-composer-resizer','End');layout(side+'_wide',True);shot('04-'+side+'-wide-gutter')
   c.select_option('aag-composer-side','right');key('aag-sidebar-resizer','End');wide=layout('nav_wide_panel_wide',True);shot('05-narrow-chat-historical-image')
   check('IMAGE_SCALES_WHEN_CHAT_NARROWS',any(i['w']<450 for i in wide['images']))
   check('ACTIONS_WRAP_TO_MULTIPLE_ROWS',any(len({round(b['y']) for b in a['buttons']})>1 for a in wide['actions']))
   # Native historical conversation already has enough content for real scrolling.
   c.evaluate('document.querySelector("#chat-history").dispatchEvent(new WheelEvent("wheel",{deltaY:-600,bubbles:true}));document.querySelector("#chat-history").scrollTop=0');time.sleep(.3);top=metrics();shot('08-model-header-top')
   check('LONG_HISTORY_SCROLLABLE',top['history']['sh']>top['history']['ch']+200)
   c.evaluate('document.querySelector("#chat-history").scrollTop=document.querySelector("#chat-history").scrollHeight');time.sleep(.3);bottom=metrics();shot('09-model-header-scrolled-down')
   check('HEADER_FIXED_DURING_SCROLL',top['header']==bottom['header'] and top['panel']==bottom['panel'])
   check('HEADER_NOT_INSIDE_SCROLL',c.evaluate('!document.querySelector("#chat-history").contains(document.querySelector("[data-testid=aag-chat-header]"))'))
   label='Review model — מודל בדיקה '+('long label '*14);c.evaluate('window.__modelLabelFixture='+json.dumps(label)+';window.dispatchEvent(new Event("save_llm_selector"))');c.wait('document.querySelector("[data-testid=aag-chat-model-label]").textContent==='+json.dumps(label))
   check('MODEL_LABEL_DYNAMIC',True);check('MODEL_LABEL_ELLIPSIS',c.evaluate('(()=>{const e=document.querySelector("[data-testid=aag-chat-model-label]");return e.scrollWidth>e.clientWidth&&getComputedStyle(e).textOverflow==="ellipsis"})()'))
   key('aag-chat-model-button','Tab');c.evaluate('document.querySelector("[data-testid=aag-chat-model-button]").focus()');c.wait('getComputedStyle(document.querySelector(".aag-chat-model-tooltip")).display!=="none"')
   check('FULL_MODEL_TOOLTIP_ON_FOCUS',c.evaluate('document.querySelector(".aag-chat-model-tooltip").textContent')==label);shot('model-full-tooltip')
   c.evaluate('window.__modelLabelFixture=null;window.dispatchEvent(new Event("save_llm_selector"));document.activeElement.blur()');c.wait('document.querySelector("[data-testid=aag-chat-model-label]").textContent==='+json.dumps(workspace['chatModel']))
   c.call('Emulation.setDeviceMetricsOverride',{'width':1000,'height':900,'deviceScaleFactor':1,'mobile':False});c.wait('document.querySelector(".aag-composer-presentation[data-placement=popup]")');layout('constrained');shot('10-constrained')
   check('CONSTRAINED_SIDE_CONTROL_HIDDEN',c.evaluate('!document.querySelector("[data-testid=aag-composer-side]")'))
   c.call('Emulation.clearDeviceMetricsOverride');c.wait('document.querySelector(".aag-composer-presentation[data-placement=panel]")');key('aag-sidebar-resizer','Enter');key('aag-composer-resizer','Enter')
   # The settings menu shares the header but keeps its original click behavior.
   c.evaluate('document.querySelector("[data-testid=aag-chat-settings] button").click()');check('CHAT_SETTINGS_STILL_OPENS',c.evaluate('document.querySelector("[data-testid=aag-chat-settings]").children.length')>1);c.evaluate('document.querySelector("[data-testid=aag-chat-settings] button").click()')
   c.call('Page.reload');c.wait('document.querySelector(".aag-composer-presentation[data-placement=panel]")',45);c.wait('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).length>0')
   check('RELOAD_PREFERENCES_COMPATIBLE',c.evaluate('JSON.parse(localStorage.getItem("aag.image-composer.layout.v1")).mode')=='panel')
   check('HISTORICAL_LABELS_UNCHANGED',c.evaluate('Array.from(document.querySelectorAll("[data-testid=aag-artifact-filename]")).map(e=>e.textContent)')==filenames)
   check('NO_JS_ERRORS',not c.evaluate('window.__refinementErrors'))
  check('HISTORICAL_FILES_UNCHANGED',images=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (storage/'generated-images').iterdir() if p.is_file()})
  report['result']='PASS'
 except BaseException as error:
  report['error']=repr(error)
  try:report['diagnostic']=metrics();shot('failure')
  except Exception:pass
  raise
 finally:
  report['orphan_browsers']=len(profile_processes(profile));save()
  if not report['orphan_browsers']:shutil.rmtree(profile,ignore_errors=True)
  print(json.dumps({'result':report['result'],'checks':len(report['checks']),'orphan_browsers':report['orphan_browsers']}))
if __name__=='__main__':main()
