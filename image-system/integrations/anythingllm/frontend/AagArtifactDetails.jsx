import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info, Copy, Check, X, FolderOpen } from "@phosphor-icons/react";
import { desktopArtifacts, resolveDesktopFile, revealCurrentFile, fileGroup } from "@/utils/aagArtifactFile";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";
import { loadUiLanguage } from "@/components/WorkspaceChat/ChatContainer/PromptInput/AagImageComposerPanel/localization";

const labels={
 en:{show:"Show details",title:"Generation details",subtitle:"Recorded settings and execution facts for this image.",copy:"Copy details",copied:"Copied",close:"Close",technical:"Technical details",missing:"Not recorded",unknown:"Unknown",na:"Not applicable",unavailable:"Generation details are unavailable.",historical:"Historical image: only facts saved with this artifact are shown. Missing values were not reconstructed.",loading:"Loading details…",retry:"Try again",copyFailed:"Copy failed. You can select and copy the details.",yes:"On",no:"Off"},
 he:{show:"הצג פרטים",title:"פרטי היצירה",subtitle:"הגדרות ונתוני ביצוע שנשמרו עבור תמונה זו.",copy:"העתק פרטים",copied:"הועתק",close:"סגור",technical:"פרטים טכניים",missing:"לא נשמר",unknown:"לא ידוע",na:"לא רלוונטי",unavailable:"פרטי היצירה אינם זמינים.",historical:"תמונה היסטורית: מוצגים רק נתונים שנשמרו עם התוצר. נתונים חסרים לא שוחזרו.",loading:"טוען פרטים…",retry:"נסה שוב",copyFailed:"ההעתקה נכשלה. ניתן לבחור ולהעתיק את הפרטים.",yes:"פעיל",no:"כבוי"}
};
Object.assign(labels.en,{fileNotFound:"File not found",fileChanged:"File changed; original artifact could not be verified",notAvailable:"Not available",copyHash:"Copy complete SHA-256",localUnavailable:"File reveal is unavailable on this device.",revealFailed:"Could not reveal the current file.",openLocation:"Open file location",generationMissing:"Generation settings were not recorded or could not be verified."});
Object.assign(labels.he,{fileNotFound:"הקובץ לא נמצא",fileChanged:"הקובץ השתנה; לא ניתן לאמת את התוצר המקורי",notAvailable:"לא זמין",copyHash:"העתק SHA-256 מלא",localUnavailable:"הצגת הקובץ בתיקייה אינה זמינה במכשיר זה.",revealFailed:"לא ניתן להציג את מיקום הקובץ הנוכחי.",openLocation:"פתח את מיקום הקובץ",generationMissing:"הגדרות היצירה לא נשמרו או שלא ניתן לאמת אותן."});
const groupsHe={FILE:"קובץ",IMAGE:"תמונה","MODEL & ENGINE":"מודל ומנוע",QUALITY:"איכות",STYLE:"סגנון",GENERATION:"יצירה",PROCESSING:"עיבוד",PROVENANCE:"מקור הנתונים"};
const actionClass="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-zinc-700 light:border-slate-300 bg-zinc-900 light:bg-white text-xs text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-400";
const format=(row,text)=>row.state==="RECORDED"?typeof row.value==="boolean"?row.value?text.yes:text.no:String(row.value):row.state==="UNKNOWN"?text.unknown:row.state==="NOT_APPLICABLE"?text.na:text.missing;
export default function AagArtifactDetails({artifact,children,onFileResolved}){
 const [language,setLanguage]=useState(loadUiLanguage),[open,setOpen]=useState(false),[value,setValue]=useState(null),[loading,setLoading]=useState(false),[copied,setCopied]=useState(false),[copyError,setCopyError]=useState(false),[technical,setTechnical]=useState(false),[hashCopied,setHashCopied]=useState(false);
 const [currentFile,setCurrentFile]=useState(null),[revealing,setRevealing]=useState(false),[revealError,setRevealError]=useState("");
 const dialog=useRef(null),trigger=useRef(null),press=useRef(null),request=useRef(null),id=useId(),text=labels[language]||labels.en;
 useEffect(()=>{const sync=e=>setLanguage(e.detail?.language||loadUiLanguage());window.addEventListener("aag-composer-language",sync);window.addEventListener("storage",sync);return()=>{window.removeEventListener("aag-composer-language",sync);window.removeEventListener("storage",sync);};},[]);
 useEffect(()=>{setValue(null);setCurrentFile(null);setOpen(false);setTechnical(false);setRevealError("");if(artifact?.storageFilename)load();return()=>request.current?.abort();},[artifact?.storageFilename,artifact?.jobId,artifact?.artifactId]);
 useEffect(()=>{onFileResolved?.(currentFile);},[currentFile,onFileResolved]);
 useEffect(()=>{if(open&&dialog.current&&!dialog.current.open)dialog.current.showModal();},[open]);
 async function load(){
  request.current?.abort();const controller=new AbortController();request.current=controller;setLoading(true);
  try{const response=await fetch(`${API_BASE}/aag/artifact-details`,{method:"POST",credentials:"same-origin",headers:{...baseHeaders(),"Content-Type":"application/json"},body:JSON.stringify({artifact:artifact.storageFilename,jobId:artifact.jobId,artifactId:artifact.artifactId}),signal:controller.signal});
   const v=response.ok?await response.json():{available:false};
   if(v.available&&v.artifact_id===(artifact.artifactId||null)&&v.job_id===(artifact.jobId||null)&&v.schema==="aag.generation-details.v1"&&Array.isArray(v.groups)&&v.file?.schema==="aag.current-artifact-file.v1"){
    const file=await resolveDesktopFile(v.file);
    if(!controller.signal.aborted){setCurrentFile(file);setValue({...v,file});return file;}
   }else if(!controller.signal.aborted){setValue({available:false});setCurrentFile(null);}
  }catch{if(!controller.signal.aborted){setValue({available:false});setCurrentFile(null);}}finally{if(!controller.signal.aborted)setLoading(false);}
 }
 function show(){setOpen(true);setCopied(false);setCopyError(false);setTechnical(false);setHashCopied(false);load();}
 async function reveal(){setRevealing(true);setRevealError("");try{const file=await load();if(!file)return setRevealError(text.revealFailed);const result=await revealCurrentFile(file,desktopArtifacts(),async identity=>{const response=await fetch(`${API_BASE}/aag/artifact-reveal`,{method:"POST",credentials:"same-origin",headers:{...baseHeaders(),"Content-Type":"application/json"},body:JSON.stringify(identity)});if(!response.ok)throw new Error("unavailable");return response.json();});setCurrentFile(result.file);setValue(v=>v?.available?{...v,file:result.file}:v);if(!result.revealed)setRevealError(text.revealFailed);}finally{setRevealing(false);}}
 function close(){press.current=null;dialog.current?.close();setOpen(false);trigger.current?.focus();}
 function outside(event){const n=dialog.current;if(!n||event.target!==n)return false;const r=n.getBoundingClientRect();return event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom;}
 function start(event){press.current=event.button===0&&event.isPrimary&&outside(event)?{id:event.pointerId,x:event.clientX,y:event.clientY,dragged:false}:null;}
 function move(event){const p=press.current;if(p&&(Math.hypot(event.clientX-p.x,event.clientY-p.y)>4||!outside(event)))p.dragged=true;}
 function end(event){const p=press.current;press.current=null;if(p&&!p.dragged&&p.id===event.pointerId&&event.button===0&&outside(event)&&Math.hypot(event.clientX-p.x,event.clientY-p.y)<=4)close();}
 function keyboard(event){if(event.key!=="Tab")return;const items=Array.from(dialog.current.querySelectorAll('button:not([disabled]),summary,[tabindex="0"]'));const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
 const title=g=>language==="he"?groupsHe[g.id]||text.technical:g.id;
 const displayGroups=value?.available?[fileGroup(currentFile,text),...value.groups]:[];
 async function copy(){try{const lines=displayGroups.filter(g=>g.id!=="TECHNICAL"||technical).map(g=>`${title(g)}\n`+g.rows.map(r=>`${r.label[language]||r.label.en}: ${format(r,text)}`).join("\n"));await navigator.clipboard.writeText(lines.join("\n\n"));setCopied(true);setCopyError(false);}catch{setCopyError(true);}}
 async function copyHash(hash){try{await navigator.clipboard.writeText(hash);setHashCopied(true);setCopyError(false);}catch{setCopyError(true);}}
 const renderGroup=g=><section key={g.id} aria-label={title(g)} className="rounded-lg border border-zinc-700 light:border-slate-200 p-3" style={{minWidth:0}}>
  <h3 className="m-0 mb-3 text-xs font-semibold tracking-wide text-zinc-300 light:text-slate-600">{title(g)}</h3>
  <dl className="m-0" style={{display:"grid",gap:".6rem"}}>{g.rows.map(r=><div key={r.id} data-detail-field={r.id} data-detail-state={r.state} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) minmax(0,1.4fr)",gap:"1rem",alignItems:"start"}}>
   <dt className="text-xs text-zinc-400 light:text-slate-500">{r.label[language]||r.label.en}</dt>
   {r.id==="current_sha256"&&r.state==="RECORDED"?<dd dir="ltr" className="m-0 text-sm" style={{display:"flex",alignItems:"center",gap:".35rem",minWidth:0}}><code data-testid="aag-file-sha256" title={r.value} style={{display:"block",minWidth:0,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",fontFamily:"monospace",userSelect:"text"}}>{r.value}</code><button type="button" data-testid="aag-copy-sha256" className={actionClass} style={{flexShrink:0,padding:".25rem"}} title={text.copyHash} aria-label={text.copyHash} onClick={()=>copyHash(r.value)}>{hashCopied?<Check size={14}/>:<Copy size={14}/>}</button></dd>:<dd dir="auto" className="m-0 text-sm" style={{overflowWrap:"anywhere",unicodeBidi:"plaintext",textAlign:"start",userSelect:"text",whiteSpace:"pre-wrap",opacity:r.state==="RECORDED"?1:.6}}>{format(r,text)}</dd>}
  </div>)}</dl></section>;
 return <>
  <button type="button" className={actionClass} style={{minWidth:34,minHeight:32,justifyContent:"center",flexShrink:0}} data-testid="aag-artifact-open-location" title="Open file location" aria-label="Open file location" aria-description={!currentFile?.localAccessible?text.localUnavailable:undefined} disabled={!currentFile?.localAccessible||loading||revealing} onClick={reveal}><FolderOpen size={14} weight="bold" aria-hidden="true"/></button>
  {children}
  <button ref={trigger} type="button" className={actionClass} data-testid="aag-artifact-show-details" data-artifact-id={artifact?.artifactId||""} disabled={!artifact?.storageFilename} onClick={show}><Info size={14}/>{text.show}</button>
  {revealError&&<span role="status" className="text-xs text-red-500">{revealError}</span>}
  {open&&createPortal(<dialog ref={dialog} data-testid="aag-artifact-details-dialog" aria-labelledby={id+"-title"} dir={language==="he"?"rtl":"ltr"} className="rounded-xl border border-zinc-600 bg-zinc-900 light:bg-white text-white light:text-slate-800 p-0 shadow-xl" style={{width:"min(62rem,94vw)",maxHeight:"85dvh"}} onKeyDown={keyboard} onCancel={e=>{e.preventDefault();close();}} onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={()=>press.current=null}>
   <div style={{display:"flex",flexDirection:"column",maxHeight:"85dvh",padding:"1rem",gap:"1rem"}}>
    <div className="flex items-start justify-between gap-3"><div><h2 id={id+"-title"} className="m-0 text-lg font-semibold">{text.title}</h2><p className="m-0 mt-1 text-xs text-zinc-400 light:text-slate-500">{text.subtitle}</p></div><button type="button" aria-label={text.close} className={actionClass} onClick={close}><X size={18}/></button></div>
    <div data-testid="aag-artifact-details-scroll" tabIndex={0} style={{overflow:"auto",minHeight:0,userSelect:"text"}}>
     {loading?<p role="status">{text.loading}</p>:!value?.available?<div><p role="status">{text.unavailable}</p><button type="button" className={actionClass} onClick={load}>{text.retry}</button></div>:<>
      {value.source==="historical-persisted-evidence"&&<p className="m-0 mb-3 text-xs text-amber-400 light:text-amber-700">{text.historical}</p>}
      {value.generationAvailable===false&&<p role="status" className="m-0 mb-3 text-xs opacity-60">{text.generationMissing}</p>}
      {!currentFile?.localAccessible&&<p className="m-0 mb-3 text-xs opacity-60">{currentFile?.state==="NOT_FOUND"?text.fileNotFound:currentFile?.state==="CHANGED"?text.fileChanged:text.localUnavailable}</p>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{displayGroups.filter(g=>g.id!=="TECHNICAL").map(renderGroup)}</div>
      <details className="mt-3 rounded-lg border border-zinc-700 light:border-slate-200 p-3" data-testid="aag-details-technical" onToggle={e=>setTechnical(e.currentTarget.open)}><summary className="cursor-pointer text-sm font-semibold">{text.technical}</summary><div className="mt-3">{value.groups.filter(g=>g.id==="TECHNICAL").map(renderGroup)}{value.sha256&&<p dir="ltr" className="m-0 mt-3 text-xs opacity-60" style={{overflowWrap:"anywhere"}}>Details SHA256: {value.sha256}</p>}</div></details>
     </>}
    </div>
    <div className="flex items-center gap-3"><button type="button" data-testid="aag-artifact-copy-details" className={actionClass} disabled={!value?.available||loading} onClick={copy}>{copied?<Check size={14}/>:<Copy size={14}/>} {text.copy}</button><span role="status" aria-live="polite" className="text-xs">{copyError?text.copyFailed:copied?text.copied:""}</span></div>
   </div>
  </dialog>,document.body)}
 </>;
}
