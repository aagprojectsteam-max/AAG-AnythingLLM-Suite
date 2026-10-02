from pathlib import Path
import importlib.util,json,subprocess,tempfile,time,sys,shutil,math
import argparse
parser=argparse.ArgumentParser(description="Live Atlas UI acceptance; never submits generation")
parser.add_argument('--url',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
P=Path(__file__).resolve().parents[2];R=Path(args.output).resolve();R.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('browser',P/'image-system/tools/atlas-browser-acceptance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
mode='after';profile=Path(tempfile.mkdtemp(prefix='atlas-polish-check-'));url=args.url;c=None;checks={}

proc=subprocess.Popen(['/usr/bin/brave-browser','--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--ignore-certificate-errors','--remote-allow-origins=*','--remote-debugging-port=9241','--user-data-dir='+str(profile),'--window-size=1440,900',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def calljs(js):return c.evaluate(js)
def click(sel):calljs('document.querySelector('+json.dumps(sel)+').click()')
def wait(js):return c.wait(js,30)
def open_atlas():
 wait("document.querySelector('[data-aag-inline-composer=\"v1.3\"]')!==null")
 calljs("[...document.querySelectorAll('[role=radio]')].find(x=>x.textContent.trim()==='ADVANCED').click()")
 wait("document.querySelector('[data-testid=\"aag-browse-visual-atlas\"]')!==null");click('[data-testid="aag-browse-visual-atlas"]');wait("document.querySelector('[data-atlas-style]')!==null")
def metrics():
 return calljs("""(()=>{const g=document.querySelector('.aag-atlas-grid'),card=g.querySelector('article'),img=card.querySelector('img'),r=card.getBoundingClientRect(),i=img.getBoundingClientRect();return{size:g.dataset.thumbnailSize,width:r.width,height:r.height,imageWidth:i.width,imageHeight:i.height,overflow:g.scrollWidth-g.clientWidth,columns:getComputedStyle(g).gridTemplateColumns.split(' ').length,viewport:[innerWidth,innerHeight],dpr:devicePixelRatio}})()""")
try:
 for _ in range(100):
  try:page=next(x for x in m.fetch_json('http://127.0.0.1:9241/json/list') if x['type']=='page');break
  except Exception:time.sleep(.2)
 c=m.Cdp(page['webSocketDebuggerUrl']);c.socket.settimeout(30);c.call('Page.enable');c.call('Runtime.enable');c.call('Browser.grantPermissions',{'origin':'https://anythingllm.localhost','permissions':['clipboardReadWrite','clipboardSanitizedWrite']});open_atlas();sizes=['small','medium','large'] if mode=='before' else ['small','medium','large','xlarge'];checks['sizes']={}
 for size in sizes:
  click('[data-testid="aag-atlas-size-'+size+'"]');wait('document.querySelector(".aag-atlas-grid").dataset.thumbnailSize==='+json.dumps(size));wait("document.querySelector('.aag-atlas-grid article img')?.naturalWidth>0");time.sleep(.1);checks['sizes'][size]=metrics()
 if mode=='before':
  checks['result']='PASS'
 else:
  # Executed inside browser_check.py, against the live AnythingLLM page.
  for value in checks['sizes'].values():
   assert value['overflow']==0,value
   assert abs(value['imageWidth']-value['imageHeight'])<1,value
  widths=[checks['sizes'][s]['width'] for s in sizes];assert all(a<b for a,b in zip(widths,widths[1:])),widths
  assert widths[-1]>widths[-2]*1.5,widths
  assert all(v['height']-v['imageHeight']<=104 for v in checks['sizes'].values())
  checks['card_height_regression']=False
  controls=calljs("""(()=>{let card=document.querySelector('[data-atlas-style]');return [...card.querySelectorAll('[data-atlas-action]')].map(b=>{let r=b.getBoundingClientRect();return{action:b.dataset.atlasAction,label:b.getAttribute('aria-label'),font:getComputedStyle(b).fontSize,icon:getComputedStyle(b,'::before').maskImage,x:r.x,y:r.y,w:r.width,h:r.height}})})()""")
  assert len(controls)==2 and abs(controls[0]['y']-controls[1]['y'])<1 and all(x['font']=='0px' and x['w']>=40 and x['h']>=40 and x['icon']!='none' for x in controls),controls
  checks['card_controls']=controls
  # Real keyboard focus, tooltip, and activation; keep original React Select handler.
  select='[data-atlas-style] [data-atlas-action="select"]';prompt='[data-atlas-style] [data-atlas-action="prompt"]'
  calljs('document.querySelector('+json.dumps(select)+').focus()')
  wait("!document.querySelector('#aag-atlas-action-tooltip').hidden && document.querySelector('#aag-atlas-action-tooltip').textContent==='Select style'")
  c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Tab','code':'Tab','windowsVirtualKeyCode':9});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
  assert calljs("document.activeElement.dataset.atlasAction")=='prompt'
  wait("!document.querySelector('#aag-atlas-action-tooltip').hidden && document.querySelector('#aag-atlas-action-tooltip').textContent==='Show Prompt'")
  c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Enter','code':'Enter','windowsVirtualKeyCode':13,'text':'\r'});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Enter','code':'Enter','windowsVirtualKeyCode':13,'text':'\r'})
  wait("document.activeElement.getAttribute('aria-label')==='Hide Prompt'")
  click(prompt);calljs('document.querySelector('+json.dumps(select)+').focus()')
  c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Enter','code':'Enter','windowsVirtualKeyCode':13,'text':'\r'});c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Enter','code':'Enter','windowsVirtualKeyCode':13,'text':'\r'})
  if not calljs("Boolean(document.querySelector('[data-testid=\"aag-visual-atlas-browser\"]'))"):click('[data-testid="aag-browse-visual-atlas"]')
  wait("document.querySelector('[data-atlas-style].selected [data-atlas-action=select]')?.getAttribute('aria-pressed')==='true'")
  checks['keyboard_tooltips_selection']='PASS'
  # Pointer hover tooltip is rendered above the original button.
  calljs("document.querySelector('[data-atlas-action=prompt]').scrollIntoView({block:'center'})");time.sleep(.2)
  r=calljs("(()=>{let r=document.querySelector('[data-atlas-action=prompt]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
  c.call('Input.dispatchMouseEvent',{'type':'mouseMoved',**r})
  wait("!document.querySelector('#aag-atlas-action-tooltip').hidden && document.querySelector('#aag-atlas-action-tooltip').textContent==='Show Prompt'")
  checks['hover_tooltip']='PASS'
  c.screenshot(R/'xlarge-desktop.png')
  assert calljs("localStorage.getItem('aag.image-composer.v1.2.atlas-thumbnail-size')")=='xlarge'
  c.call('Page.reload',{'ignoreCache':True});open_atlas();wait("document.querySelector('.aag-atlas-grid').dataset.thumbnailSize==='xlarge'");checks['xlarge_persistence']='PASS'
  # Narrow desktop, existing responsive grid, no fixed column count.
  checks['responsive']={}
  for w,h in [(1000,800),(760,700)]:
   c.call('Emulation.setDeviceMetricsOverride',{'width':w,'height':h,'deviceScaleFactor':1,'mobile':False});time.sleep(.2);v=metrics();assert v['overflow']==0 and abs(v['imageWidth']-v['imageHeight'])<1;checks['responsive'][str(w)]=v
  c.call('Emulation.clearDeviceMetricsOverride');time.sleep(.2)
  # Choose the longest available prompt by string length only, not a visual review.
  cat=calljs("(async()=>{let r=await fetch('/api/aag-composer/image-generator/taxonomy',{headers:{'X-AAG-Workspace-Path':location.pathname,'X-AAG-Workspace-Slug':'image-generator'},cache:'no-store'});return r.json()})()")
  long=max([(f,s) for f in cat['families'] for s in f['subfamilies']],key=lambda fs:len(fs[1]['atlas']['prompt']));cid=long[0]['id']+'/'+long[1]['id'];exact=long[1]['atlas']['prompt']
  def open_long():
   calljs("(()=>{let x=document.querySelector('[data-testid=\"aag-atlas-search\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(x,"+json.dumps(long[1]['id'])+");x.dispatchEvent(new Event('input',{bubbles:true}))})()")
   sel='[data-atlas-style="'+cid+'"] .aag-atlas-image-button';wait('document.querySelector('+json.dumps(sel)+')!==null');click(sel)
   wait("document.querySelector('[data-testid=\"aag-atlas-large-preview\"] img')?.naturalWidth>0")
   click('[data-testid="aag-atlas-large-preview"] [data-testid="aag-atlas-show-prompt"]')
   text=wait("document.querySelector('[data-testid=\"aag-atlas-large-preview\"] pre')?.textContent");assert text==exact

  def modal_metrics():
   return calljs("""(()=>{let d=document.querySelector('[data-testid="aag-atlas-large-preview"]'),pre=d.querySelector('pre');const rect=e=>{let r=e.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height,bottom:r.bottom,right:r.right}};return{viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,modal:rect(d),image:rect(d.querySelector('img')),meta:rect(d.querySelector('.aag-atlas-lightbox-meta')),select:rect(d.querySelector('.aag-atlas-preview-select')),hide:rect(d.querySelector('[data-testid="aag-atlas-show-prompt"]')),copy:rect(d.querySelector('[data-testid="aag-atlas-copy-prompt"]')),prompt:rect(pre),promptScroll:pre.scrollHeight,promptClient:pre.clientHeight}})()""")
  def assert_modal(v):
   for name in ('modal','image','meta','select','hide','copy','prompt'):
    r=v[name];assert r['y']>=0 and r['bottom']<=v['viewport'][1]+1 and r['x']>=0 and r['right']<=v['viewport'][0]+1 and r['h']>0,(name,v)
   assert v['image']['h']>=100 and v['promptScroll']>v['promptClient'],v
  open_long();v=modal_metrics();assert_modal(v);assert v['dpr']==1,v;checks['modal_100_percent']=v
  calljs("document.querySelector('.aag-atlas-lightbox-card pre').scrollTop=99999")
  v2=modal_metrics();assert v2['copy']==v['copy'] and v2['hide']==v['hide'];checks['internal_scroll_fixed_controls']='PASS'
  click('[data-testid="aag-atlas-copy-prompt"]');wait("document.querySelector('.aag-atlas-lightbox-card [role=status]')?.textContent==='Copied'");assert calljs('navigator.clipboard.readText()')==exact;checks['exact_copy']='PASS';c.screenshot(R/'long-prompt-100.png')
  click('[data-testid="aag-atlas-large-preview"] [data-testid="aag-atlas-show-prompt"]');wait("document.querySelector('.aag-atlas-lightbox-card .aag-atlas-prompt-body').hidden");click('[data-testid="aag-atlas-large-preview"] .aag-atlas-close')
  # Change actual browser default zoom through the browser's own settings page.
  target=c.call('Target.createTarget',{'url':'chrome://settings/appearance'})['targetId'];time.sleep(.5);pages=m.fetch_json('http://127.0.0.1:9241/json/list');settings=m.Cdp(next(p for p in pages if p['id']==target)['webSocketDebuggerUrl'])
  settings.wait("typeof chrome.settingsPrivate?.setDefaultZoom==='function'",10)
  settings.evaluate("new Promise(resolve=>chrome.settingsPrivate.setDefaultZoom(0.8,()=>resolve(true)))")
  settings.call('Target.closeTarget',{'targetId':target});settings.socket.close();c.call('Page.reload',{'ignoreCache':True});open_atlas();wait('Math.abs(devicePixelRatio-0.8)<0.02');checks['actual_browser_zoom_80']=calljs('devicePixelRatio')
  click('[data-testid="aag-atlas-size-xlarge"]');v=metrics();assert v['overflow']==0 and abs(v['imageWidth']-v['imageHeight'])<1;checks['xlarge_80_percent']=v;c.screenshot(R/'xlarge-80.png')
  open_long();v=modal_metrics();assert_modal(v);checks['modal_80_percent']=v;c.screenshot(R/'long-prompt-80.png')
  checks['result']='PASS'
except Exception as e:
 checks['result']='FAIL';checks['error']=repr(e)
 if c:
  try:checks['browser_text']=calljs('document.body.innerText.slice(-2500)');c.screenshot(R/'failure.png')
  except Exception:pass
finally:
 (R/(mode+'-browser.json')).write_text(json.dumps(checks,indent=2)+'\n')
 if c:
  try:c.call('Browser.close')
  except Exception:pass
 try:proc.wait(timeout=8)
 except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=8)
 shutil.rmtree(profile,ignore_errors=True)
print(json.dumps(checks));sys.exit(0 if checks.get('result')=='PASS' else 1)
