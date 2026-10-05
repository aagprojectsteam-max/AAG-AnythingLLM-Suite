"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("fs"), path = require("path"), os = require("os"), crypto = require("crypto"), vm = require("vm");
const { database } = require("./fixtures/artifact-naming");
const { NAME } = require("../integrations/anythingllm/aagArtifactNaming");
const { cleanupGeneratedImages, MIN_AGE_MS } = require("../integrations/anythingllm/aagGeneratedImageCleanup");
const legacy = () => `img-${crypto.randomUUID()}.png`;
const current = n => `AAG-Image-10-9-20261005-084040-${n}.png`;
const card = (name, extra = {}) => ({ type: "imageGenerationCard", payload: { storageFilename: name, ...extra } });
const json = outputs => JSON.stringify({ text: "Saved conversation", outputs });
async function fixture(t, ledger = true) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aag-cleanup-")), root = path.join(dir, "generated-images"), db = database(path.join(dir,"state.db"));
  fs.mkdirSync(root);
  t.after(async () => { await db.$disconnect(); fs.rmSync(dir, {recursive:true,force:true}); });
  await db.$executeRawUnsafe('CREATE TABLE workspace_chats(id INTEGER PRIMARY KEY, workspaceId INTEGER DEFAULT 10, thread_id INTEGER DEFAULT 9, "include" INTEGER DEFAULT 1, response TEXT)');
  await db.$executeRawUnsafe("CREATE TABLE scheduled_job_runs(id INTEGER PRIMARY KEY, status TEXT DEFAULT 'completed', result TEXT)");
  await db.$executeRawUnsafe("CREATE TABLE workspace_agent_invocations(id INTEGER PRIMARY KEY, workspace_id INTEGER, thread_id INTEGER, closed INTEGER)");
  if (ledger) await db.$executeRawUnsafe("CREATE TABLE aag_artifact_filenames(artifact_id TEXT PRIMARY KEY, job_id TEXT, workspace_id INTEGER, thread_id INTEGER, filename TEXT UNIQUE)");
  let seq = 0;
  return { dir, root, db,
    file(name, age = MIN_AGE_MS + 10000) { const f=path.join(root,name);fs.writeFileSync(f,"isolated image bytes");const stamp=new Date(Date.now()-age);fs.utimesSync(f,stamp,stamp);return f; },
    async chat(outputs, include=1) { await db.$executeRawUnsafe('INSERT INTO workspace_chats(id,response,"include") VALUES(?,?,?)',++seq,json(outputs),include); },
    async run(outputs) {await db.$executeRawUnsafe("INSERT INTO scheduled_job_runs(id,result) VALUES(?,?)",++seq,json(outputs));},
    cleanup(options={}) {return cleanupGeneratedImages({root,database:db,filenamePattern:NAME,...options});},
    exists(name) {return fs.existsSync(path.join(root,name));},
  };
}
async function assignment(f,name,id="artifact-00000000-0000-4000-8000-000000000001",job="aag-00000000-0000-4000-8000-000000000001") {
  await f.db.$executeRawUnsafe("INSERT INTO aag_artifact_filenames VALUES(?,?,10,9,?)",id,job,name);
}

test("reproduces native production deletion, then preserves identical persisted cards with the fix", async t => {
  const f=await fixture(t), old=legacy(), modern=current(1);f.file(old);f.file(modern);await f.chat([card(old)]);await f.chat([card(modern)]);
  const source=fs.readFileSync(path.join(__dirname,"fixtures/legacy-cleanup-generated-images.js"),"utf8");
  await new Promise((resolve,reject) => {
    const dependencies={fs,path,"./helpers/index.js":{log(){},conclude:resolve},"../utils/files/index.js":{generatedImagesPath:f.root,GENERATED_IMAGE_FILENAME_PATTERN:NAME},"../utils/http/index.js":{safeJsonParse:JSON.parse},
      "../models/workspaceChats.js":{WorkspaceChats:{where:()=>f.db.$queryRawUnsafe('SELECT response FROM workspace_chats WHERE "include"=1 AND response LIKE ?',"%img-%")}},
      "../models/scheduledJobRun.js":{ScheduledJobRun:{where:async()=>[]}}};
    try {vm.runInNewContext(source,{require:name=>dependencies[name],console:{error:reject}});}catch(e){reject(e);}
  });
  assert.equal(f.exists(old),true);assert.equal(f.exists(modern),false,"old code deletes a referenced current filename");
  f.file(modern);const result=await f.cleanup();assert.equal(result.result,"PASS");assert.deepEqual(result.deleted,[]);assert.equal(result.decisions.filter(r=>r.state==="REFERENCED").length,2);
});

test("true unreferenced legacy and current files are removed; young/unrelated files are retained", async t => {
  const f=await fixture(t), old=legacy(), modern=current(2), young=current(3);for(const n of [old,modern,"notes.txt"])f.file(n);f.file(young,1000);
  const result=await f.cleanup();assert.deepEqual(new Set(result.deleted),new Set([old,modern]));assert.ok(f.exists(young)&&f.exists("notes.txt"));
});

test("all completed scheduled outputs protect files; hidden chats do not establish current ownership", async t => {
  const f=await fixture(t), a=legacy(), b=current(4), orphan=current(5);for(const n of [a,b,orphan])f.file(n);
  await f.run([card(a),card(b)]);await f.chat([card(orphan)],0);
  const result=await f.cleanup();assert.deepEqual(result.deleted,[orphan]);assert.ok(f.exists(a)&&f.exists(b));
});

test("older database without filename ledger remains compatible", async t => {
  const f=await fixture(t,false), a=legacy(), b=current(6);f.file(a);f.file(b);await f.chat([card(a),card(b)]);
  assert.deepEqual((await f.cleanup()).deleted,[]);
});

test("persisted artifact ID/job binding reconstructs the exact ledger filename", async t => {
  const f=await fixture(t), name=current(7);f.file(name);await assignment(f,name);await f.chat([{type:"imageGenerationCard",payload:{artifactId:"artifact-00000000-0000-4000-8000-000000000001",jobId:"aag-00000000-0000-4000-8000-000000000001"}}]);
  const result=await f.cleanup();assert.equal(result.decisions[0].state,"REFERENCED");assert.ok(f.exists(name));
});

test("ledger reservations alone do not protect true orphans forever", async t => {
  const f=await fixture(t), name=current(8);f.file(name);await assignment(f,name);
  assert.deepEqual((await f.cleanup()).deleted,[name]);
});

test("pending presentation fails closed until the existing invocation closes", async t => {
  const f=await fixture(t), name=current(9);f.file(name);await assignment(f,name);await f.chat([]);
  await f.db.$executeRawUnsafe("INSERT INTO workspace_agent_invocations VALUES(1,10,9,0)");
  assert.equal((await f.cleanup()).decisions[0].state,"UNKNOWN");assert.ok(f.exists(name));
  await f.db.$executeRawUnsafe("UPDATE workspace_agent_invocations SET closed=1");assert.deepEqual((await f.cleanup()).deleted,[name]);
});

for (const [label,value] of [
  ["invalid JSON","{"],["unexpected top-level array","[]"],["malformed outputs",JSON.stringify({outputs:{}})],
  ["malformed card",json([{type:"imageGenerationCard",payload:null}])],
  ["unresolvable artifact identity",json([{type:"imageGenerationCard",payload:{artifactId:"missing",jobId:"missing"}}])],
  ["traversal",json([card("../../outside.png")])],
  ["absolute host path",json([card("/etc/passwd")])],
  ["forged application path",json([{type:"imageGenerationCard",payload:{applicationPath:"/tmp/outside.png"}}])],
]) test("ownership uncertainty skips ALL deletion: "+label, async t => {
  const f=await fixture(t), name=current(10);f.file(name);await f.db.$executeRawUnsafe("INSERT INTO workspace_chats(id,response) VALUES(1,?)",value);
  const result=await f.cleanup();assert.equal(result.result,"SKIPPED_UNKNOWN");assert.equal(result.decisions[0].state,"UNKNOWN");assert.deepEqual(result.deleted,[]);assert.ok(f.exists(name));assert.ok(result.diagnostics.length);
});

test("normal text, other output types and null completed-run results are not fabricated image references", async t => {
  const f=await fixture(t), name=current(11);f.file(name);
  await f.db.$executeRawUnsafe('INSERT INTO workspace_chats(id,response) VALUES(99,?)',JSON.stringify({text:"ordinary conversation"}));
  await f.db.$executeRawUnsafe("INSERT INTO scheduled_job_runs(id,result) VALUES(1,NULL)");
  await f.chat([{type:"textCard",payload:{storageFilename:name}}]);assert.deepEqual((await f.cleanup()).deleted,[name]);
});

test("conflicting ledger identity never authorizes deletion", async t => {
  const f=await fixture(t), a=current(12), b=current(13);f.file(a);f.file(b);await assignment(f,a);await f.chat([card(b,{artifactId:"artifact-00000000-0000-4000-8000-000000000001",jobId:"aag-00000000-0000-4000-8000-000000000001"})]);
  assert.equal((await f.cleanup()).result,"SKIPPED_UNKNOWN");assert.ok(f.exists(a)&&f.exists(b));
});

for(const failure of ["database error","incomplete chat page","incomplete ledger"]) test("fail closed on "+failure,async t=>{
  const f=await fixture(t), name=current(14);f.file(name);await f.chat([card(name)]);await assignment(f,name);
  const wrapped={$transaction:fn=>f.db.$transaction(tx=>fn({$executeRawUnsafe:(...args)=>tx.$executeRawUnsafe(...args),$queryRawUnsafe:async(sql,...args)=>{
    if(failure==="database error"&&sql.includes("scheduled_job_runs"))throw Error("database unavailable");
    if(failure==="incomplete chat page"&&sql.startsWith("SELECT id, response"))return [];
    if(failure==="incomplete ledger"&&sql.startsWith("SELECT artifact_id"))return [];
    return tx.$queryRawUnsafe(sql,...args);
  }}))};
  const result=await f.cleanup({database:wrapped});assert.equal(result.result,"SKIPPED_UNKNOWN");assert.deepEqual(result.deleted,[]);assert.ok(f.exists(name));
});

test("dry run exposes decisions without deleting or changing records", async t=>{
  const f=await fixture(t), a=current(15), b=legacy();f.file(a);f.file(b);await f.chat([card(a)]);
  const before=await f.db.$queryRawUnsafe("SELECT * FROM workspace_chats");const r=await f.cleanup({dryRun:true});assert.equal(r.deleted.length,0);assert.equal(r.decisions.find(x=>x.name===b).state,"ORPHAN");assert.ok(f.exists(a)&&f.exists(b));assert.deepEqual(await f.db.$queryRawUnsafe("SELECT * FROM workspace_chats"),before);
});

test("symlink/hardlink/directory candidates cannot delete outside the generated-image root",async t=>{
  const f=await fixture(t), out=path.join(f.dir,"outside.png"), link=current(16), hard=current(17), directory=current(18);fs.writeFileSync(out,"outside sentinel");fs.symlinkSync(out,path.join(f.root,link));fs.linkSync(out,path.join(f.root,hard));fs.mkdirSync(path.join(f.root,directory));
  const r=await f.cleanup();assert.ok(r.decisions.every(x=>x.state==="UNKNOWN"));assert.deepEqual(r.deleted,[]);assert.equal(fs.readFileSync(out,"utf8"),"outside sentinel");
});

test("redirected root and symlink ancestors are rejected",async t=>{
  const f=await fixture(t), name=current(19);f.file(name);const alias=path.join(f.dir,"alias");fs.symlinkSync(f.root,alias);
  assert.equal((await f.cleanup({root:alias})).result,"SKIPPED_UNKNOWN");
  const ancestor=path.join(f.dir,"alias-parent");fs.symlinkSync(f.dir,ancestor);assert.equal((await f.cleanup({root:path.join(ancestor,"generated-images")})).result,"SKIPPED_UNKNOWN");assert.ok(f.exists(name));
});

test("candidate replacement during reference discovery is skipped",async t=>{
  const f=await fixture(t), name=current(20);f.file(name);let replaced=false;
  const wrapped={$transaction:fn=>f.db.$transaction(tx=>fn({$executeRawUnsafe:(...args)=>tx.$executeRawUnsafe(...args),$queryRawUnsafe:async(sql,...args)=>{
    const rows=await tx.$queryRawUnsafe(sql,...args);if(!replaced&&sql.startsWith("SELECT id, result")){replaced=true;fs.unlinkSync(path.join(f.root,name));f.file(name);}return rows;
  }}))};
  const r=await f.cleanup({database:wrapped});assert.equal(replaced,true);assert.equal(r.decisions[0].state,"UNKNOWN");assert.ok(f.exists(name));
});

test("root replacement during discovery cannot redirect unlink",async t=>{
  const f=await fixture(t), name=current(21);f.file(name);const outside=path.join(f.dir,"outside");fs.mkdirSync(outside);fs.writeFileSync(path.join(outside,name),"outside sentinel");let replaced=false;
  const wrapped={$transaction:fn=>f.db.$transaction(tx=>fn({$executeRawUnsafe:(...args)=>tx.$executeRawUnsafe(...args),$queryRawUnsafe:async(sql,...args)=>{
    const rows=await tx.$queryRawUnsafe(sql,...args);if(!replaced&&sql.startsWith("SELECT id, result")){replaced=true;fs.renameSync(f.root,f.root+"-original");fs.symlinkSync(outside,f.root);}return rows;
  }}))};
  assert.equal((await f.cleanup({database:wrapped})).result,"SKIPPED_UNKNOWN");assert.equal(replaced,true);assert.equal(fs.readFileSync(path.join(outside,name),"utf8"),"outside sentinel");assert.ok(fs.existsSync(path.join(f.root+"-original",name)));
});

test("5,000 mixed files: malformed ownership prevents deletion, then exact survivor/deletion sets",async t=>{
  const f=await fixture(t), survivors=new Set(), deleted=new Set();let seq=0;
  await f.db.$transaction(async tx=>{
    for(let i=0;i<1000;i++){
      const a=legacy(),b=legacy(),c=current(100+i),d=current(2000+i),other=`unrelated-${i}.txt`;
      for(const n of [a,b,c,d,other])f.file(n);survivors.add(a).add(c).add(other);deleted.add(b).add(d);
      await tx.$executeRawUnsafe("INSERT INTO workspace_chats(id,response) VALUES(?,?)",++seq,json([card(a),card(c)]));
    }
    await tx.$executeRawUnsafe("INSERT INTO workspace_chats(id,response) VALUES(?,?)",++seq,"{");
  });
  assert.equal((await f.cleanup()).result,"SKIPPED_UNKNOWN");assert.equal(fs.readdirSync(f.root).length,5000);
  await f.db.$executeRawUnsafe("DELETE FROM workspace_chats WHERE id=?",seq);
  const result=await f.cleanup();assert.equal(result.result,"PASS");assert.deepEqual(new Set(result.deleted),deleted);assert.deepEqual(new Set(fs.readdirSync(f.root)),survivors);
});
