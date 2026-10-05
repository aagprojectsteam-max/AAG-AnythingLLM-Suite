import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Copy, Check, TextAlignLeft, X } from "@phosphor-icons/react";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";
import { loadUiLanguage } from "@/components/WorkspaceChat/ChatContainer/PromptInput/AagImageComposerPanel/localization";

const labels = {
  en: {show:"Show prompt",title:"Final generation prompt",copy:"Copy prompt",request:"User request",final:"Final prompt",missingRequest:"Original user request was not recorded for this image.",copied:"Copied",close:"Close",missing:"Final prompt was not recorded for this image.",derived:"Original generation prompt",derivedNote:"This output was enhanced without a new prompt. This is the prompt used for its original image.",copyFailed:"Copy failed. You can select and copy the prompt text."},
  he: {show:"הצג פרומפט",title:"פרומפט היצירה הסופי",copy:"העתק פרומפט",request:"בקשת המשתמש",final:"הפרומפט הסופי",missingRequest:"בקשת המשתמש המקורית לא נשמרה עבור תמונה זו.",copied:"הועתק",close:"סגור",missing:"הפרומפט הסופי לא נשמר עבור תמונה זו.",derived:"פרומפט היצירה המקורי",derivedNote:"תוצר זה שופר ללא פרומפט חדש. זהו הפרומפט ששימש ליצירת התמונה המקורית.",copyFailed:"ההעתקה נכשלה. ניתן לבחור ולהעתיק את טקסט הפרומפט."},
};
const promptStyle={whiteSpace:"pre-wrap",overflowWrap:"anywhere",unicodeBidi:"plaintext",textAlign:"start",margin:0,userSelect:"text",fontFamily:"inherit",fontSize:".875rem"};
const actionClass="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-zinc-700 light:border-slate-300 bg-zinc-900 light:bg-white text-xs text-white light:text-slate-700 hover:bg-zinc-800 light:hover:bg-slate-100 disabled:opacity-50 disabled:cursor-default";
export default function AagArtifactPrompt({artifact}) {
  const [value,setValue]=useState(null),[open,setOpen]=useState(false),[copied,setCopied]=useState(false),[copyError,setCopyError]=useState(false);
  const [language,setLanguage]=useState(loadUiLanguage),dialog=useRef(null),button=useRef(null),backdropPress=useRef(null),id=useId();
  const text=labels[language]||labels.en;
  useEffect(()=>{const sync=e=>setLanguage(e.detail?.language||loadUiLanguage());window.addEventListener("aag-composer-language",sync);window.addEventListener("storage",sync);return()=>{window.removeEventListener("aag-composer-language",sync);window.removeEventListener("storage",sync);};},[]);
  useEffect(()=>{
    setValue(null);setOpen(false);const controller=new AbortController();
    if(!artifact?.jobId||!artifact?.artifactId)return;
    fetch(`${API_BASE}/aag/artifact-prompt`,{method:"POST",credentials:"same-origin",headers:{...baseHeaders(),"Content-Type":"application/json"},body:JSON.stringify({artifact:artifact.storageFilename,jobId:artifact.jobId,artifactId:artifact.artifactId}),signal:controller.signal})
      .then(async r=>r.ok?await r.json():{available:false})
      .then(v=>{if(!controller.signal.aborted)setValue(v.available&&typeof v.final_generation_prompt==="string"&&v.artifact_id===artifact.artifactId&&v.job_id===artifact.jobId?v:{available:false});})
      .catch(()=>{if(!controller.signal.aborted)setValue({available:false});});
    return()=>controller.abort();
  },[artifact?.storageFilename,artifact?.jobId,artifact?.artifactId]);
  useEffect(()=>{if(open&&dialog.current&&!dialog.current.open)dialog.current.showModal();},[open]);
  function isBackdrop(event) {
    const node=dialog.current;
    if(!node || event.target!==node)return false;
    const r=node.getBoundingClientRect();
    return event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom;
  }
  function startBackdropPress(event) {
    backdropPress.current=event.button===0 && event.isPrimary && isBackdrop(event)
      ? {id:event.pointerId,x:event.clientX,y:event.clientY,dragged:false} : null;
  }
  function moveBackdropPress(event) {
    const press=backdropPress.current;
    if(press && (Math.hypot(event.clientX-press.x,event.clientY-press.y)>4 || !isBackdrop(event)))press.dragged=true;
  }
  function finishBackdropPress(event) {
    const press=backdropPress.current;backdropPress.current=null;
    if(press && !press.dragged && press.id===event.pointerId && event.button===0 && isBackdrop(event)
      && Math.hypot(event.clientX-press.x,event.clientY-press.y)<=4)close();
  }
  function close(){backdropPress.current=null;dialog.current?.close();setOpen(false);setCopied(false);setCopyError(false);button.current?.focus();}
  function keepFocus(event) {
    if(event.key!=="Tab")return;
    const items=Array.from(dialog.current.querySelectorAll('button:not([disabled]), [tabindex="0"]'));
    const first=items[0],last=items[items.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
  async function copy(){try{await navigator.clipboard.writeText(value.final_generation_prompt);setCopied(true);setCopyError(false);}catch{setCopyError(true);}}
  return <>
    <span title={!value?.available?text.missing:undefined}>
      <button ref={button} type="button" className={actionClass} data-testid="aag-artifact-show-prompt" data-artifact-id={artifact?.artifactId||""}
        disabled={!value?.available} aria-describedby={!value?.available?id+"-missing":undefined} onClick={()=>setOpen(true)}><TextAlignLeft size={14}/>{text.show}</button>
      {!value?.available&&<span id={id+"-missing"} className="sr-only">{text.missing}</span>}
    </span>
    {open&&value?.available&&createPortal(<dialog ref={dialog} data-testid="aag-artifact-prompt-dialog" aria-labelledby={id+"-title"} dir={language==="he"?"rtl":"ltr"}
      className="rounded-xl border border-zinc-600 bg-zinc-900 light:bg-white text-white light:text-slate-800 p-0 shadow-xl"
      style={{width:"min(52rem,94vw)",maxHeight:"85dvh"}} onKeyDown={keepFocus} onCancel={e=>{e.preventDefault();close();}} onPointerDown={startBackdropPress} onPointerMove={moveBackdropPress} onPointerUp={finishBackdropPress} onPointerCancel={()=>{backdropPress.current=null;}}>
      <div style={{display:"flex",flexDirection:"column",maxHeight:"85dvh",padding:"1rem",gap:".75rem"}}>
        <div className="flex items-center justify-between gap-3"><h2 id={id+"-title"} className="text-base font-semibold m-0">{value.derived?text.derived:text.title}</h2>
          <button type="button" aria-label={text.close} onClick={close} className={actionClass}><X size={18}/></button></div>
        {value.derived&&<p className="text-sm m-0">{text.derivedNote}</p>}
        <div style={{overflow:"auto",minHeight:0}} data-testid="aag-artifact-prompt-scroll">
          <section aria-labelledby={id+"-request-title"}>
            <h3 id={id+"-request-title"} className="text-sm font-semibold mb-2">{text.request}</h3>
            {value.original_user_request_available
              ? <pre data-testid="aag-artifact-user-request" dir="auto" tabIndex={0} style={promptStyle}>{value.original_user_request}</pre>
              : <p data-testid="aag-artifact-request-unavailable" className="text-sm opacity-70">{text.missingRequest}</p>}
          </section>
          <section aria-labelledby={id+"-final-title"} style={{marginTop:"1.25rem",paddingTop:"1rem",borderTop:"1px solid #94a3b844"}}>
            <h3 id={id+"-final-title"} className="text-sm font-semibold mb-2">{text.final}</h3>
            <pre data-testid="aag-artifact-final-prompt" dir="auto" tabIndex={0} style={promptStyle}>{value.final_generation_prompt}</pre>
          </section>
        </div>
        <div className="flex items-center gap-3" role="group" aria-labelledby={id+"-final-title"}><button type="button" data-testid="aag-artifact-copy-prompt" className={actionClass} onClick={copy}>{copied?<Check size={14}/>:<Copy size={14}/>} {text.copy}</button>
          <span role="status" aria-live="polite">{copyError?text.copyFailed:copied?text.copied:""}</span></div>
      </div>
    </dialog>,document.body)}
  </>;
}
