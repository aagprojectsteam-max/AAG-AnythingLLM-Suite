"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("fs"),path=require("path"),crypto=require("crypto");
const {fixture,database}=require("./fixtures/artifact-naming");
const naming=require("../integrations/anythingllm/aagArtifactNaming");
const bytes=Buffer.alloc(128,45),sha=crypto.createHash("sha256").update(bytes).digest("hex");
function parsed(){return {jobId:"aag-"+crypto.randomUUID(),artifacts:[{artifactId:"artifact-"+crypto.randomUUID(),sha256:sha,width:512,height:512}]};}
async function setup(){const p=parsed(),f=await fixture(p),images=path.join(f.dir,"generated-images");return {...f,images,p,
 save(override={}){return naming.persist({database:f.database,job:f.job,parsed:p,artifact:p.artifacts[0],bytes,generatedImagesPath:images,resolveTimezone:f.resolveTimezone,...override});},
 sibling(override={}){const artifact={...p.artifacts[0],artifactId:"artifact-"+crypto.randomUUID()},job={...f.job,...override};job.artifacts=[{...f.job.artifacts[0],artifact_id:artifact.artifactId}];return {artifact,job};}
};}
test("format is numeric ASCII, physical name equals metadata, identity remains separate",async()=>{const f=await setup();try{const v=await f.save();assert.equal(v.storageFilename,"AAG-Image-1-2-20261005-070215.png");assert.equal(v.generatedAt,"2026-10-05T04:02:15.123Z");assert.equal(v.filenameTimezone,"Asia/Jerusalem");assert.ok(naming.NAME.test(v.storageFilename));assert.deepEqual(fs.readFileSync(path.join(f.images,v.storageFilename)),bytes);assert.equal(v.threadSerial,2);}finally{await f.close();}});
test("reload, process restart, rename, reorder and timezone changes never reassign a name",async()=>{const f=await setup();let reopened;try{const before=await f.save();await f.database.$executeRawUnsafe("UPDATE workspaces SET name='שם חדש'");await f.database.$executeRawUnsafe("UPDATE workspace_threads SET name='renamed'");await f.database.$queryRawUnsafe("SELECT * FROM workspace_threads ORDER BY name DESC, id DESC");reopened=database(path.join(f.dir,"test.db"));const after=await f.save({database:reopened,job:{...f.job,artifacts:[{...f.job.artifacts[0],verified_at:"2030-02-03T12:00:00+09:00"}]}});assert.deepEqual(after,before);assert.equal(fs.readdirSync(f.images).length,1);}finally{if(reopened)await reopened.$disconnect();await f.close();}});
test("current thread rank closes deletion gaps while existing filenames stay immutable",async()=>{const f=await setup();try{const old=await f.save();await f.database.$executeRawUnsafe("DELETE FROM workspace_threads WHERE id=5");await f.database.$executeRawUnsafe("INSERT INTO workspace_threads(id,workspace_id,name) VALUES(100,3,'new')");const a=await f.save(f.sibling({owner:{workspace_id:3,thread_id:100}}));assert.equal(a.threadSerial,2);assert.equal((await naming.currentThreadReference(f.database,3,21)).ordinal,1);const b=await f.save(f.sibling({owner:{workspace_id:4,thread_id:9}}));assert.equal(b.threadSerial,1);assert.equal(b.workspaceSerial,2);assert.deepEqual(await f.save(),old);}finally{await f.close();}});
test("native unthreaded workspace conversation reserves zero without consuming thread one",async()=>{const f=await setup();try{const v=await f.save({job:{...f.job,owner:{workspace_id:3,thread_id:"unknown"}}});assert.equal(v.storageFilename,"AAG-Image-1-0-20261005-070215.png");}finally{await f.close();}});
test("concurrent separate database processes allocate unique collision suffixes even for identical pixels",async()=>{const f=await setup(),clients=[];try{await naming.schema(f.database);const work=Array.from({length:8},()=>{const db=database(path.join(f.dir,"test.db"));clients.push(db);return f.save({...f.sibling(),database:db});});const outputs=await Promise.all(work);const expected=Array.from({length:8},(_,i)=>`AAG-Image-1-2-20261005-070215${i?'-'+(i+1):''}.png`).sort();assert.deepEqual(outputs.map(v=>v.storageFilename).sort(),expected);for(const name of expected)assert.deepEqual(fs.readFileSync(path.join(f.images,name)),bytes);}finally{await Promise.all(clients.map(db=>db.$disconnect()));await f.close();}});
test("collision never overwrites an existing file and interrupted partial writes are skipped",async()=>{const f=await setup();try{fs.mkdirSync(f.images);fs.writeFileSync(path.join(f.images,"AAG-Image-1-2-20261005-070215.png"),"other artifact");fs.writeFileSync(path.join(f.images,"AAG-Image-1-2-20261005-070215-2.png"),"interrupted write");assert.equal((await f.save()).storageFilename,"AAG-Image-1-2-20261005-070215-3.png");assert.equal(fs.readFileSync(path.join(f.images,"AAG-Image-1-2-20261005-070215.png"),"utf8"),"other artifact");}finally{await f.close();}});
test("removed files do not free their historical filename reservations",async()=>{const f=await setup();try{const v=await f.save();fs.unlinkSync(path.join(f.images,v.storageFilename));const next=await f.save(f.sibling());assert.equal(next.storageFilename,"AAG-Image-1-2-20261005-070215-2.png");await assert.rejects(f.save(),/ENOENT/);}finally{await f.close();}});
test("persisted historical artifact filenames remain unchanged on recovery",async()=>{const f=await setup();try{fs.mkdirSync(f.images);const old="img-55701b1c-1111-4111-8111-111111111111.png";fs.writeFileSync(path.join(f.images,old),bytes);const response=JSON.stringify({outputs:[{type:"imageGenerationCard",payload:{storageFilename:old,jobId:f.p.jobId,artifactId:f.p.artifacts[0].artifactId}}]});await f.database.$executeRawUnsafe("INSERT INTO workspace_chats VALUES(1,3,?)",response);assert.equal((await f.save()).storageFilename,old);assert.deepEqual(fs.readdirSync(f.images),[old]);}finally{await f.close();}});
test("tampered identity, wrong scope, redirected root and symlink files fail closed",async()=>{const f=await setup();try{const v=await f.save();await assert.rejects(f.save({job:{...f.job,owner:{workspace_id:4,thread_id:9}}}),/NAMING_FAILED/);await assert.rejects(f.save({...f.sibling({owner:{workspace_id:3,thread_id:9}})}),/NAMING_FAILED/);await assert.rejects(f.save({artifact:{...f.p.artifacts[0],sha256:"0".repeat(64)}}),/NAMING_FAILED/);fs.renameSync(f.images,f.images+"-real");fs.symlinkSync(f.images+"-real",f.images);await assert.rejects(f.save(),/NAMING_FAILED/);fs.unlinkSync(f.images);fs.renameSync(f.images+"-real",f.images);const target=path.join(f.images,v.storageFilename);fs.unlinkSync(target);fs.symlinkSync("/etc/passwd",target);await assert.rejects(f.save(),/ELOOP/);}finally{await f.close();}});
test("schema is idempotent and preserves native workspace/thread/chat records",async()=>{const f=await setup();try{const before=await f.database.$queryRawUnsafe("SELECT * FROM workspace_threads ORDER BY id");await f.save();await naming.schema(f.database);await naming.schema(f.database);assert.deepEqual(await f.database.$queryRawUnsafe("SELECT * FROM workspace_threads ORDER BY id"),before);assert.deepEqual(await f.database.$queryRawUnsafe("SELECT * FROM workspace_chats"),[]);}finally{await f.close();}});
test("filename allowlists retain legacy names and reject paths, Unicode and arbitrary extensions",()=>{for(const name of ["3-7-20261005-040215.png","3-7-20261005-040215-2.png","img-55701b1c-1111-4111-8111-111111111111.png"])assert.ok(naming.NAME.test(name));for(const name of ["../3-7-20261005-040215.png","3-7-20261005-040215.png/other","٣-7-20261005-040215.png","3-7-20261005-040215.sh","prompt name.png","03-7-20261005-040215.png"])assert.equal(naming.NAME.test(name),false);});

test("local host timestamp uses IANA DST rules, midnight date boundary and exact prefix",async()=>{
  assert.equal(naming.localStamp("2026-10-05T01:18:29.221Z","Asia/Jerusalem"),"20261005-041829");
  assert.equal(naming.localStamp("2026-10-04T22:15:00Z","Asia/Jerusalem"),"20261005-011500");
  assert.equal(naming.localStamp("2026-03-26T23:59:59Z","Asia/Jerusalem"),"20260327-015959");
  assert.equal(naming.localStamp("2026-03-27T00:00:00Z","Asia/Jerusalem"),"20260327-030000");
  assert.equal(naming.localStamp("2026-01-05T01:18:29Z","Asia/Jerusalem"),"20260105-031829");
  const f=await setup();try{const v=await f.save();assert.match(v.storageFilename,/^AAG-Image-[0-9]+-[0-9]+-[0-9]{8}-[0-9]{6}\.png$/);assert.equal(f.job.artifacts[0].verified_at,"2026-10-05T04:02:15.123Z");}finally{await f.close();}
});
test("DST repeated wall-clock hour safely allocates suffix and does not overwrite",async()=>{
  const f=await setup();try{
    f.job.artifacts[0].verified_at="2026-10-24T22:30:00Z";const first=await f.save();
    const second=f.sibling();second.job.artifacts[0].verified_at="2026-10-24T23:30:00Z";
    const next=await f.save(second);assert.equal(first.storageFilename,"AAG-Image-1-2-20261025-013000.png");assert.equal(next.storageFilename,"AAG-Image-1-2-20261025-013000-2.png");
  }finally{await f.close();}
});
test("persisted filename survives timezone changes and unavailable host resolver without reconversion",async()=>{
  const f=await setup();try{const first=await f.save();const second=await f.save({resolveTimezone:async()=>{throw Error("Timezone must not be requested again");}});assert.deepEqual(second,first);}finally{await f.close();}
});
test("unknown host timezone fails closed before writing an artifact, never falls back to UTC",async()=>{
  const f=await setup();try{await assert.rejects(f.save({resolveTimezone:async()=>"Not/A_Timezone"}));assert.deepEqual(fs.readdirSync(f.images),[]);assert.deepEqual(await f.database.$queryRawUnsafe("SELECT * FROM aag_artifact_filenames"),[]);}finally{await f.close();}
});
test("workspace AUTOINCREMENT identity is stable after another workspace is deleted",async()=>{
  const f=await setup();try{const before=await f.save();await f.database.$executeRawUnsafe("DELETE FROM workspaces WHERE id=4");await f.database.$executeRawUnsafe("INSERT INTO workspaces(name) VALUES('new workspace')");const rows=await f.database.$queryRawUnsafe("SELECT id FROM workspaces ORDER BY id DESC");assert.equal(rows[0].id,5);assert.deepEqual(await f.save(),before);}finally{await f.close();}
});
test("actual PNG, JPEG and WebP formats select matching extensions and preserve bytes",async()=>{
  const presentation=require("../integrations/anythingllm/aagArtifactPresentation");
  for(const [format,ext] of [["png","png"],["jpeg","jpg"],["webp","webp"]]){
    const f=await setup();try{
      const bytes=fs.readFileSync(path.join(__dirname,"fixtures/artifact-formats/verified."+ext)),hash=crypto.createHash("sha256").update(bytes).digest("hex");
      Object.assign(f.p.artifacts[0],{sha256:hash,width:32,height:24,url:"http://127.0.0.1:18190/files/source."+ext,filename:"source."+ext});
      Object.assign(f.job.artifacts[0],{sha256:hash,width:32,height:24,format});
      const outputs=await presentation.buildPresentationOutputs({parsed:{...f.p,requestedCount:1},generatedImagesPath:f.images,naming:{database:f.database,job:f.job,resolveTimezone:f.resolveTimezone},fetchImpl:async()=>({ok:true,headers:{get:()=>"image/"+format},arrayBuffer:async()=>bytes})});
      const filename=outputs[0].payload.storageFilename;assert.equal(filename,`AAG-Image-1-2-20261005-070215.${ext}`);assert.equal(outputs[0].payload.filename,filename);assert.deepEqual(fs.readFileSync(path.join(f.images,filename)),bytes);
    }finally{await f.close();}
  }
});

test("canonical visible workspace one/thread four uses neither native workspace nor thread IDs",async()=>{
 const p=parsed(),f=await fixture(p,{initialize:false});try{
  await f.database.$executeRawUnsafe("INSERT INTO workspaces VALUES(10,'Image Generator')");
  for(const id of [101,102,103,104])await f.database.$executeRawUnsafe("INSERT INTO workspace_threads(id,workspace_id,name) VALUES(?,10,'visible thread')",id);
  const snapshot={workspaceIds:[10,4,3],threads:[{workspaceId:10,threadIds:[104,102,101,103]},{workspaceId:4,threadIds:[9]},{workspaceId:3,threadIds:[21,5]}]};
  await naming.initializeSerials(f.database,snapshot);
  const save=async()=>naming.persist({...f,parsed:p,artifact:p.artifacts[0],bytes,generatedImagesPath:path.join(f.dir,'generated-images'),job:{...f.job,owner:{workspace_id:10,thread_id:103},artifacts:[{...f.job.artifacts[0],verified_at:'2026-10-05T06:45:00Z'}]}});
  const v=await save();assert.equal(v.storageFilename,'AAG-Image-1-4-20261005-094500.png');assert.notEqual(v.storageFilename,'AAG-Image-10-4-20261005-094500.png');assert.equal(v.workspaceSerial,1);assert.equal(v.threadSerial,4);
  const native=(await f.database.$queryRawUnsafe('SELECT workspace_id,thread_id FROM aag_artifact_filenames'))[0];assert.deepEqual(native,{workspace_id:10,thread_id:103});
  await f.database.$executeRawUnsafe("UPDATE workspaces SET name='renamed'");await f.database.$executeRawUnsafe("UPDATE workspace_threads SET name='renamed'");
  await naming.initializeSerials(f.database,{workspaceIds:[3,4,10],threads:[snapshot.threads[2],snapshot.threads[1],{workspaceId:10,threadIds:[103,101,102,104]}]});
  assert.deepEqual(await save(),v);
  assert.equal((await f.database.$queryRawUnsafe('SELECT serial FROM aag_visible_thread_serials WHERE workspace_id=10 AND thread_id=104'))[0].serial,1);
 }finally{await f.close();}
});
test("deleted workspace reservations survive and unseen workspaces append monotonically",async()=>{
 const f=await setup();try{
  await f.save();await f.database.$executeRawUnsafe('DELETE FROM workspaces WHERE id=4');await f.database.$executeRawUnsafe("INSERT INTO workspaces VALUES(10,'new')");await f.database.$executeRawUnsafe("INSERT INTO workspace_threads(id,workspace_id,name) VALUES(100,10,'new')");
  await naming.initializeSerials(f.database,{workspaceIds:[10,3],threads:[{workspaceId:10,threadIds:[100]},{workspaceId:3,threadIds:[21,5]}]});
  const v=await f.save(f.sibling({owner:{workspace_id:10,thread_id:100}}));assert.equal(v.workspaceSerial,3);assert.equal(v.threadSerial,1);
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT workspace_id,serial FROM aag_workspace_serials ORDER BY serial'),[{workspace_id:3,serial:1},{workspace_id:4,serial:2},{workspace_id:10,serial:3}]);assert.equal((await f.save()).workspaceSerial,1);
 }finally{await f.close();}
});
test("legacy mappings and already-persisted AAG names are never rewritten",async()=>{
 const p=parsed(),f=await fixture(p,{initialize:false});try{
  await naming.schema(f.database);await f.database.$executeRawUnsafe('INSERT INTO aag_thread_serials VALUES(3,21,2)');
  const images=path.join(f.dir,'generated-images');fs.mkdirSync(images);const old='AAG-Image-3-2-20261005-070215.png';fs.writeFileSync(path.join(images,old),bytes);
  await f.database.$executeRawUnsafe('INSERT INTO aag_artifact_filenames VALUES(?,?,?,?,?,?,?,?,?)',p.artifacts[0].artifactId,p.jobId,3,21,2,sha,'2026-10-05T04:02:15.123Z','Asia/Jerusalem',old);
  const before=await f.database.$queryRawUnsafe('SELECT * FROM aag_artifact_filenames');
  await naming.initializeSerials(f.database,{workspaceIds:[4,3],threads:[{workspaceId:4,threadIds:[9]},{workspaceId:3,threadIds:[21,5]}]});
  const v=await naming.persist({...f,parsed:p,artifact:p.artifacts[0],bytes,generatedImagesPath:images});assert.equal(v.storageFilename,old);assert.equal(v.workspaceSerial,3);assert.equal(v.threadSerial,2);
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM aag_artifact_filenames'),before);assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM aag_thread_serials'),[{workspace_id:3,thread_id:21,serial:2}]);assert.deepEqual(fs.readdirSync(images),[old]);
 }finally{await f.close();}
});
test("first visible order initializes atomically across competing processes",async()=>{
 const p=parsed(),f=await fixture(p,{initialize:false}),clients=[];try{
  await naming.schema(f.database);
  const orders=[[3,4],[4,3]],groups={3:{workspaceId:3,threadIds:[21,5]},4:{workspaceId:4,threadIds:[9]}};
  await Promise.all(Array.from({length:6},(_,i)=>{const db=database(path.join(f.dir,'test.db'));clients.push(db);return naming.initializeSerials(db,{workspaceIds:orders[i%2],threads:orders[i%2].map(id=>groups[id])});}));
  const rows=await f.database.$queryRawUnsafe('SELECT workspace_id,serial FROM aag_workspace_serials ORDER BY serial');assert.deepEqual(rows.map(r=>r.serial),[1,2]);assert.ok(orders.some(order=>order.join()===rows.map(r=>r.workspace_id).join()));
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT thread_id,serial FROM aag_visible_thread_serials WHERE workspace_id=3 ORDER BY serial'),[{thread_id:21,serial:1},{thread_id:5,serial:2}]);
 }finally{await Promise.all(clients.map(db=>db.$disconnect()));await f.close();}
});
test("malformed or mismatched visible identities roll back without partial assignments",async()=>{
 const p=parsed(),f=await fixture(p,{initialize:false});try{
  for(const snapshot of [{workspaceIds:[3,3],threads:[]},{workspaceIds:[3],threads:[{workspaceId:3,threadIds:[5,9]}]},{workspaceIds:[999],threads:[{workspaceId:999,threadIds:[]}]}])await assert.rejects(naming.initializeSerials(f.database,snapshot));
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM aag_workspace_serials'),[]);assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM aag_visible_thread_serials'),[]);
  await assert.rejects(naming.persist({...f,parsed:p,artifact:p.artifacts[0],bytes,generatedImagesPath:path.join(f.dir,'generated-images')}),/AAG_SERIALS_NOT_INITIALIZED/);assert.deepEqual(fs.readdirSync(path.join(f.dir,'generated-images')),[]);
 }finally{await f.close();}
});
