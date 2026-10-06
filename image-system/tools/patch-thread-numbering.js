"use strict";
const fs=require('fs'),path=require('path');
function once(source,old,next){if(source.split(old).length!==2)throw Error('Thread-numbering patch anchor missing/ambiguous: '+old.slice(0,80));return source.replace(old,next);}
function patchThreadNumbering(checkout){
 const root=path.join(checkout,'frontend/src');
 const model=path.join(root,'models/workspaceThread.js');let s=fs.readFileSync(model,'utf8');
 s='import { threadOrder } from "@/utils/aagThreadOrder";\n'+s;
 s=once(s,'    return { threads, defaultThreadChatCount };','    threadOrder.publish(workspaceSlug, threads);\n    return { threads, defaultThreadChatCount };');
 // Only confirmed native deletion changes the display order. Bulk-selection
 // marks are not deletions. No IDs or endpoint/request contracts are replaced.
 for(const [method,next,slugs] of [['delete','deleteBulk','[threadSlug]'],['deleteBulk','chatHistory','threadSlugs']]){
  const start=s.indexOf('  '+method+': async function'),end=s.indexOf('  '+next+': async function',start);let part=s.slice(start,end);
  part=once(part,'.then((res) => res.ok)',`.then((res) => { if(res.ok) threadOrder.remove(workspaceSlug, ${slugs}); return res.ok; })`);
  s=s.slice(0,start)+part+s.slice(end);
 }
 fs.writeFileSync(model,s);
 const container=path.join(root,'components/Sidebar/ActiveWorkspaces/ThreadContainer/index.jsx');s=fs.readFileSync(container,'utf8');
 s='import { threadOrder } from "@/utils/aagThreadOrder";\n'+s;
 const start=s.indexOf('  function removeThread(threadId) {'),end=s.indexOf('  function getActiveThreadIdx()',start);
 s=s.slice(0,start)+'  function removeThread(threadId) {\n    setThreads(prev => prev.filter(thread => thread.id !== threadId));\n  }\n\n'+s.slice(end);
 s=once(s,'  if (loading) {','  useEffect(() => {\n    if (!loading) threadOrder.publish(workspace.slug, threads);\n  }, [workspace.slug, threads, loading]);\n\n  if (loading) {');
 s=once(s,'          key={thread.slug}\n', '          key={thread.slug}\n          visibleOrdinal={i + 1}\n');
 fs.writeFileSync(container,s);
 const item=path.join(root,'components/Sidebar/ActiveWorkspaces/ThreadContainer/ThreadItem/index.jsx');s=fs.readFileSync(item,'utf8');
 s='import { threadLabel } from "@/utils/aagThreadOrder";\n'+s;
 s=once(s,'  idx,\n','  idx,\n  visibleOrdinal = null,\n');
 s=once(s,'            data-tooltip-content={thread.name}', '            data-tooltip-content={threadLabel(visibleOrdinal, thread.name)}\n            data-testid="aag-thread-link"\n            data-thread-ordinal={visibleOrdinal || undefined}\n            data-thread-slug={thread.slug || undefined}');
 s=once(s,'              {thread.name}','              {threadLabel(visibleOrdinal, thread.name)}');fs.writeFileSync(item,s);
}
module.exports={patchThreadNumbering};
