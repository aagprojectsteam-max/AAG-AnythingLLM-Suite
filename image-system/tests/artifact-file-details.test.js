"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),os=require("node:os"),path=require("node:path"),vm=require("node:vm"),crypto=require("node:crypto"),zlib=require("node:zlib");
const frontend=path.join(__dirname,"../integrations/anythingllm/frontend");
const job="aag-11111111-1111-1111-1111-111111111111",artifact="artifact-22222222-2222-2222-2222-222222222222",filename="img-33333333-3333-3333-3333-333333333333.png";
function png(){
 const chunk=(type,data)=>{const size=Buffer.alloc(4);size.writeUInt32BE(data.length);return Buffer.concat([size,Buffer.from(type),data,Buffer.alloc(4)]);};
 const header=Buffer.alloc(13);header.writeUInt32BE(2,0);header.writeUInt32BE(3,4);header[8]=8;header[9]=6;
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk("IHDR",header),chunk("tEXt",Buffer.alloc(100,65)),chunk("IDAT",zlib.deflateSync(Buffer.alloc(27))),chunk("IEND",Buffer.alloc(0))]);
}
function fixture({legacy=false,hostFile=null,storageName=filename,imageBytes=null}={}){
 const filename=storageName;
 const hostCalls=[];
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),"aag-current-file-")),bytes=imageBytes||png(),sha=crypto.createHash("sha256").update(bytes).digest("hex");
 fs.writeFileSync(path.join(directory,filename),bytes);
 const output={type:"imageGenerationCard",payload:{storageFilename:filename,filename:"stale-display-name.png",artifactSha256:sha,artifactWidth:require("../integrations/anythingllm/aagArtifactFormat").inspect(bytes).width,artifactHeight:require("../integrations/anythingllm/aagArtifactFormat").inspect(bytes).height,...legacy?{}:{jobId:job,artifactId:artifact}}};
 const module={exports:{}},routes=new Map(),deps={
  "../utils/http":{safeJsonParse:JSON.parse,userFromSession:async()=>({id:1}),multiUserMode:()=>true},
  "../utils/middleware/validatedRequest":{validatedRequest:()=>{}},
  "../utils/middleware/multiUserProtected":{flexUserRoleValid:()=>()=>{},ROLES:{all:"all"}},
  "../models/workspace":{Workspace:{where:async()=>[{id:1}],whereWithUser:async()=>[{id:1}]}},
  "../models/workspaceChats":{WorkspaceChats:{where:async()=>[{id:7,workspaceId:10,response:JSON.stringify({outputs:[output]})}]}},
  "../utils/files":{generatedImagesPath:directory,GENERATED_IMAGE_FILENAME_PATTERN:require("../integrations/anythingllm/aagArtifactNaming").NAME,isWithin:(root,target)=>target.startsWith(root+path.sep)},
  "./aagArtifactFormat":require("../integrations/anythingllm/aagArtifactFormat"),
  "./aagPdfAssembler":require("../integrations/anythingllm/aagPdfAssembler"),
  "/app/server/storage/plugins/agent-skills/aag-image-task/generation-details.js":{resolve:()=>legacy?{available:false}:{available:true,schema:"aag.generation-details.v1",job_id:job,artifact_id:artifact,groups:[{id:"IMAGE",rows:[{id:"file_size",state:"RECORDED",value:999},{id:"operation",state:"RECORDED",value:"generate"}]}]}}
 };
 if(hostFile)deps.http={request(options,callback){const {EventEmitter}=require("node:events"),req=new EventEmitter();req.end=body=>{hostCalls.push({options,body:JSON.parse(body)});const res=new EventEmitter();res.statusCode=200;callback(res);queueMicrotask(()=>{res.emit("data",JSON.stringify(options.path.endsWith("/reveal")?{revealed:true,file:hostFile}:hostFile));res.emit("end");});};return req;}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../integrations/anythingllm/aagArtifactExport.js"),"utf8"),{module,process:{env:{}},require:name=>deps[name]||require(name),Set,Buffer,URL,console});
 module.exports.aagArtifactExportEndpoints({post:(url,...args)=>routes.set(url,args.at(-1))});
 async function call(kind,body,origin="http://127.0.0.1:3000"){const response={status(code){this.code=code;return this;},setHeader(){},json(value){this.value=value;return this;}};await routes.get("/aag/artifact-"+kind)({body,get:name=>({host:"127.0.0.1:3000",origin,referer:origin+"/workspace/image-generator"})[name]},response);return response;}
 return {api:module.exports,output,directory,bytes,sha,hostCalls,call,details:body=>call("details",body),close:()=>fs.rmSync(directory,{recursive:true,force:true})};
}
const client=()=>import("data:text/javascript;base64,"+Buffer.from(fs.readFileSync(path.join(frontend,"aagArtifactFile.js"))).toString("base64"));
test("current file facts come from verified bytes and the resolvable storage filename, without container paths",()=>{
 const f=fixture();try{const file=f.api.resolveCurrentFile(f.output);assert.equal(file.state,"AVAILABLE");assert.equal(file.filename,filename);assert.equal(file.sizeBytes,f.bytes.length);assert.equal(file.sha256,f.sha);assert.equal(file.width,2);assert.equal(file.height,3);assert.equal(file.format,"PNG");assert.equal(file.localAccessible,false);assert.equal(file.location.scope,"APPLICATION_STORAGE");assert.equal(file.location.value,path.basename(f.directory)+"/"+filename);assert.ok(!JSON.stringify(file).includes(f.directory));}finally{f.close();}
});
test("every lookup reflects deletion, replacement and symlinks; stale receipt facts never stand in for current bytes",()=>{
 const f=fixture();try{
  fs.unlinkSync(path.join(f.directory,filename));assert.equal(f.api.resolveCurrentFile(f.output).state,"NOT_FOUND");
  fs.writeFileSync(path.join(f.directory,filename),png());f.output.payload.artifactSha256="f".repeat(64);const changed=f.api.resolveCurrentFile(f.output);assert.equal(changed.state,"CHANGED");assert.equal(changed.sizeBytes,undefined);assert.equal(changed.location,undefined);
  fs.unlinkSync(path.join(f.directory,filename));fs.writeFileSync(path.join(f.directory,"other.png"),f.bytes);fs.symlinkSync("other.png",path.join(f.directory,filename));assert.equal(f.api.resolveCurrentFile(f.output).state,"NOT_FOUND");
 }finally{f.close();}
});
test("authorized details retain generation facts when file is missing, and current file rows are not duplicated",async()=>{
 const f=fixture();try{let response=await f.details({artifact:filename,jobId:job,artifactId:artifact});assert.equal(response.code,200);assert.equal(response.value.file.sizeBytes,f.bytes.length);assert.deepEqual(Array.from(response.value.groups[0].rows,r=>r.id),["operation"]);
  fs.unlinkSync(path.join(f.directory,filename));response=await f.details({artifact:filename,jobId:job,artifactId:artifact});assert.equal(response.value.available,true);assert.equal(response.value.file.state,"NOT_FOUND");assert.equal(response.value.groups[0].rows[0].value,"generate");
  assert.equal((await f.details({artifact:filename,jobId:job,artifactId:"artifact-44444444-4444-4444-4444-444444444444"})).code,404);
  assert.equal((await f.details({artifact:"../../secret.png"})).code,400);
 }finally{f.close();}
});
test("old persisted cards without generation identities still show measured file metadata",async()=>{
 const f=fixture({legacy:true});try{const response=await f.details({artifact:filename});assert.equal(response.code,200);assert.equal(response.value.available,true);assert.equal(response.value.generationAvailable,false);assert.equal(response.value.file.sha256,f.sha);assert.equal(response.value.file.sizeBytes,f.bytes.length);assert.equal(response.value.artifact_id,null);}finally{f.close();}
});
test("web fallback never presents server storage as a locally revealable path",async()=>{
 const f=fixture();try{const api=await client(),file=await api.resolveDesktopFile(f.api.resolveCurrentFile(f.output),null);assert.equal(file.localAccessible,false);assert.equal((await api.revealCurrentFile(file,null)).revealed,false);assert.equal(api.humanFileSize(1048576),"1.00 MiB (1,048,576 bytes)");assert.equal(api.humanFileSize(undefined),null);const group=api.fileGroup(file,{applicationStorage:"Application storage (server)",missing:"Not recorded"});assert.equal(group.rows.find(r=>r.id==="current_file_size").value,api.humanFileSize(f.bytes.length));assert.match(group.rows.find(r=>r.id==="application_path").value,/generated-images\//);}finally{f.close();}
});
test("desktop reveal and details share fresh hash-bound resolver; host selects the actual file through an opaque token",async()=>{
 const f=fixture();try{const api=await client(),file=f.api.resolveCurrentFile(f.output);let location="/Users/test/Images/current.png",calls=0,revealed=null;
  const bridge={resolveArtifact:async ref=>{calls++;assert.equal(ref.sha256,f.sha);assert.equal(ref.storageFilename,filename);assert.equal(ref.path,undefined);return {exists:true,scope:"DESKTOP_LOCAL",sha256:f.sha,sizeBytes:f.bytes.length,path:location,token:"current-file-handle"};},revealArtifact:async ref=>{revealed=ref;return {revealed:true};}};
  const initial=await api.resolveDesktopFile(file,bridge);assert.equal(initial.location.value,location);location="/Users/test/Moved/current.png";
  const result=await api.revealCurrentFile(initial,bridge);assert.equal(result.revealed,true);assert.equal(result.file.location.value,location);assert.equal(calls,2);assert.equal(revealed.token,"current-file-handle");assert.equal(revealed.path,undefined);
  bridge.resolveArtifact=async()=>({exists:false});assert.equal((await api.revealCurrentFile(initial,bridge)).revealed,false);
  bridge.resolveArtifact=async()=>({exists:true,scope:"DESKTOP_LOCAL",sha256:"f".repeat(64),sizeBytes:f.bytes.length,path:location,token:"handle"});assert.equal((await api.resolveDesktopFile(file,bridge)).localAccessible,false);
 }finally{f.close();}
});
test("file section has accurate missing states and icon action has no visible text, with accessible tooltip",async()=>{
 const api=await client(),group=api.fileGroup({state:"NOT_FOUND",filename}, {fileNotFound:"File not found",notAvailable:"Not available"});assert.equal(group.rows.find(r=>r.id==="current_file_location").value,"Not available");assert.equal(group.rows.find(r=>r.id==="current_file_size").state,"NOT_RECORDED");
 const source=fs.readFileSync(path.join(frontend,"AagArtifactDetails.jsx"),"utf8");const action=source.match(/<button[^>]*data-testid="aag-artifact-open-location"[\s\S]*?<\/button>/)[0];assert.match(action,/title="Open file location"/);assert.match(action,/aria-label="Open file location"/);assert.match(action,/<FolderOpen/);assert.match(action,/aria-hidden="true"/);assert.match(action,/disabled=\{!currentFile\?\.localAccessible/);assert.doesNotMatch(action,/\}\s*[^<\s]+\s*<\/button>/);
 assert.match(source,/function show\(\)[^\n]*load\(\)/);assert.match(source,/disabled=\{!artifact\?\.storageFilename\}/);
});

test("host details and reveal use authorized persisted chat identity; browser never sends host paths",async()=>{
 const hostFile={schema:"aag.current-artifact-file.v1",state:"AVAILABLE",jobId:job,artifactId:artifact,filename,sha256:"a".repeat(64),sizeBytes:200,format:"PNG",width:2,height:3,exists:true,applicationPath:"generated-images/"+filename,location:{scope:"HOST_LOCAL",value:"/authorized/real/generated-images/"+filename},localAccessible:true};
 const f=fixture({hostFile});try{const body={artifact:filename,jobId:job,artifactId:artifact};const details=await f.details(body);assert.equal(details.value.file.location.value,hostFile.location.value);assert.equal(details.value.file.localAccessible,true);
  const shown=await f.call("reveal",body);assert.equal(shown.value.revealed,true);assert.equal(shown.value.file.location.value,details.value.file.location.value);
  for(const call of f.hostCalls){assert.deepEqual(call.body,{chatId:7,workspaceId:10,...body});assert.equal(call.body.path,undefined);}
  const n=f.hostCalls.length;assert.equal((await f.call("reveal",{...body,path:"/etc/passwd"})).code,400);assert.equal((await f.call("reveal",{...body,artifactId:"artifact-44444444-4444-4444-4444-444444444444"})).code,404);assert.equal(f.hostCalls.length,n);
 }finally{f.close();}
});
test("remote clients retain host file facts but cannot trigger local desktop actions",async()=>{
 const hostFile={schema:"aag.current-artifact-file.v1",state:"AVAILABLE",jobId:job,artifactId:artifact,filename,location:{scope:"HOST_LOCAL",value:"/safe/file.png"},localAccessible:true};const f=fixture({hostFile});try{
  const body={artifact:filename,jobId:job,artifactId:artifact};for(const origin of ["https://remote.example","http://127.0.0.1.evil.example","null",undefined]){assert.equal((await f.call("reveal",body,origin===undefined?"":origin)).code,403);}
  assert.equal(f.hostCalls.length,0);const details=await f.call("details",body,"https://remote.example");assert.equal(details.code,200);assert.equal(details.value.file.localAccessible,false);assert.equal(details.value.file.location.scope,"HOST_LOCAL");
 }finally{f.close();}
});
test("host-aware web client shares physical path with details and passes only identity to reveal",async()=>{
 const api=await client(),file={schema:"aag.current-artifact-file.v1",state:"AVAILABLE",jobId:job,artifactId:artifact,filename,sha256:"a".repeat(64),sizeBytes:200,applicationPath:"generated-images/"+filename,location:{scope:"HOST_LOCAL",value:"/real/file.png"},localAccessible:true};
 assert.deepEqual(await api.resolveDesktopFile(file,null),file);let submitted;const result=await api.revealCurrentFile(file,null,async ref=>{submitted=ref;return {revealed:true,file};});assert.equal(result.revealed,true);assert.deepEqual(submitted,{artifact:filename,jobId:job,artifactId:artifact});const rows=api.fileGroup(file,{notAvailable:"Not available"}).rows;assert.equal(rows.find(r=>r.id==="filename").value,filename);assert.equal(rows.find(r=>r.id==="current_file_location").value,"/real/file.png");assert.equal(rows.find(r=>r.id==="application_path").value,"generated-images/"+filename);
 assert.equal((await api.revealCurrentFile(file,null,async()=>{throw Error("unavailable");})).revealed,false);
});

test("loopback browser capability includes the installed HTTPS localhost subdomain and excludes remote/lookalike origins",()=>{
 const f=fixture();try{for(const host of ["127.0.0.1:3000","localhost:3000","[::1]:3000","anythingllm.localhost"]){const origin="https://"+host;assert.equal(f.api.localArtifactBrowser({get:k=>({host,origin,referer:origin+"/workspace/image-generator"})[k]}),true);}
 for(const host of ["anythingllm.example","localhost.example","127.0.0.1.evil.example"]){const origin="https://"+host;assert.equal(f.api.localArtifactBrowser({get:k=>({host,origin,referer:origin+"/"})[k]}),false);}}finally{f.close();}
});

test("current JPEG and WebP metadata match bytes, name and extension; mismatches fail closed",()=>{
 for(const [ext,format] of [["jpg","JPEG"],["webp","WEBP"]]){
  const bytes=fs.readFileSync(path.join(__dirname,"fixtures/artifact-formats/verified."+ext));
  const f=fixture({storageName:"AAG-Image-10-3-20261005-041829."+ext,imageBytes:bytes});
  try {const facts=f.api.resolveCurrentFile(f.output);assert.equal(facts.state,"AVAILABLE");assert.equal(facts.format,format);assert.equal(facts.width,32);assert.equal(facts.height,24);assert.equal(facts.sha256,f.sha);assert.equal(facts.filename,f.output.payload.storageFilename);}
  finally{f.close();}
  const mismatch=fixture({storageName:"AAG-Image-10-3-20261005-041829.png",imageBytes:bytes});
  try{assert.equal(mismatch.api.resolveCurrentFile(mismatch.output).state,"CHANGED");}finally{mismatch.close();}
 }
});
