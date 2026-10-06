'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const naming=require('../integrations/anythingllm/aagArtifactNaming');
const {fixture,database}=require('./fixtures/artifact-naming');
const {threadOrdinal,threadLabel,createThreadOrderStore}=require('../integrations/anythingllm/frontend/aagThreadOrder');
const bytes=fs.readFileSync(path.join(__dirname,'fixtures/artifact-formats/verified.png')),hash=crypto.createHash('sha256').update(bytes).digest('hex');
function parsed(){return {jobId:'aag-'+crypto.randomUUID(),artifacts:[{artifactId:'artifact-'+crypto.randomUUID(),sha256:hash,width:32,height:24}]};}
async function setup(){
 const p=parsed(),f=await fixture(p,{initialize:false});
 await f.database.$executeRawUnsafe('DELETE FROM workspace_threads');
 for(const id of [101,217,309,444])await f.database.$executeRawUnsafe('INSERT INTO workspace_threads(id,workspace_id,name) VALUES(?,3,?)',id,'opaque-'+id);
 const snapshot=ids=>({workspaceIds:[3,4],threads:[{workspaceId:3,threadIds:ids},{workspaceId:4,threadIds:[]}]});
 await naming.initializeSerials(f.database,snapshot([101,217,309,444]));
 return {...f,snapshot,async image(threadId){const p=parsed(),job={job_id:p.jobId,owner:{workspace_id:3,thread_id:threadId},artifacts:[{artifact_id:p.artifacts[0].artifactId,sha256:hash,width:32,height:24,format:'png',verified_at:'2026-10-05T18:00:00Z'}]};return naming.persist({...f,parsed:p,artifact:p.artifacts[0],job,bytes,generatedImagesPath:path.join(f.dir,'images')});}};
}
for(const deleted of [101,217,444])test('delete '+deleted+' reranks the live list without rewriting native identities or historical filenames',async()=>{
 const f=await setup();try{
  const before=await f.image(309);assert.equal(before.threadSerial,3);
  const original=await f.database.$queryRawUnsafe('SELECT * FROM workspace_threads WHERE id != ? ORDER BY id',deleted);
  await f.database.$executeRawUnsafe('DELETE FROM workspace_threads WHERE id=?',deleted);
  const ids=[101,217,309,444].filter(id=>id!==deleted);
  for(const [index,id] of ids.entries())assert.equal((await naming.currentThreadReference(f.database,3,id)).ordinal,index+1);
  const image=await f.image(309);assert.equal(image.threadSerial,ids.indexOf(309)+1);assert.ok(image.storageFilename.startsWith('AAG-Image-1-'+image.threadSerial+'-'));
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM workspace_threads ORDER BY id'),original);
  assert.ok(fs.existsSync(path.join(f.dir,'images',before.storageFilename)));
  assert.equal((await f.database.$queryRawUnsafe('SELECT filename FROM aag_artifact_filenames ORDER BY rowid'))[0].filename,before.storageFilename);
  const reopened=database(path.join(f.dir,'test.db'));try{assert.equal((await naming.currentThreadReference(reopened,3,309)).ordinal,ids.indexOf(309)+1);}finally{await reopened.$disconnect();}
 }finally{await f.close();}
});
test('fresh workspace starts at one; empty, deleted and default threads never expose database IDs as numbers',async()=>{
 const f=await setup();try{
  await f.database.$executeRawUnsafe('DELETE FROM workspace_threads');await naming.initializeSerials(f.database,f.snapshot([]));
  assert.deepEqual(await naming.currentThreadReference(f.database,3,309),{ordinal:null,state:'DELETED'});
  assert.deepEqual(await naming.currentThreadReference(f.database,3,0),{ordinal:null,state:'DEFAULT'});
  await f.database.$executeRawUnsafe("INSERT INTO workspace_threads(id,workspace_id,name) VALUES(900,3,'first')");
  assert.equal((await f.image(900)).threadSerial,1);
 }finally{await f.close();}
});
test('current visible reorder replaces display positions while native identity and creation metadata stay fixed',async()=>{
 const f=await setup();try{
  const native=await f.database.$queryRawUnsafe('SELECT * FROM workspace_threads ORDER BY id');const first=await f.image(309);
  await naming.initializeSerials(f.database,f.snapshot([444,309,101,217]));
  assert.equal((await naming.currentThreadReference(f.database,3,309)).ordinal,2);assert.equal((await f.image(309)).threadSerial,2);
  assert.deepEqual(await f.database.$queryRawUnsafe('SELECT * FROM workspace_threads ORDER BY id'),native);
  assert.equal((await f.database.$queryRawUnsafe('SELECT thread_serial FROM aag_artifact_filenames WHERE filename=?',first.storageFilename))[0].thread_serial,3);
 }finally{await f.close();}
});
test('thread rank is scoped to the same user-visible list as the native sidebar',async()=>{
 const f=await setup();try{
  await f.database.$executeRawUnsafe('UPDATE workspace_threads SET user_id=7 WHERE id IN (101,309)');
  assert.equal((await naming.currentThreadReference(f.database,3,309)).ordinal,2);
  assert.equal((await naming.currentThreadReference(f.database,3,444)).ordinal,2);
 }finally{await f.close();}
});
test('details distinguish current ordinal from filename creation ordinal and include no numeric internal identity',async()=>{
 const f=await setup();try{
  await f.database.$executeRawUnsafe('ALTER TABLE workspaces ADD COLUMN slug TEXT');await f.database.$executeRawUnsafe("UPDATE workspaces SET slug='image-generator' WHERE id=3");
  await f.database.$executeRawUnsafe('ALTER TABLE workspace_threads ADD COLUMN slug TEXT');await f.database.$executeRawUnsafe("UPDATE workspace_threads SET slug='chosen-thread' WHERE id=309");
  const image=await f.image(309);const row=(await f.database.$queryRawUnsafe('SELECT artifact_id FROM aag_artifact_filenames WHERE filename=?',image.storageFilename))[0];
  await f.database.$executeRawUnsafe('DELETE FROM workspace_threads WHERE id=217');
  assert.deepEqual(await naming.artifactThreadReference(f.database,row.artifact_id),{workspaceSlug:'image-generator',threadSlug:'chosen-thread',ordinal:2,state:'CURRENT',creationOrdinal:3});
 }finally{await f.close();}
});
test('sidebar, current-thread switching, deletion and reorder derive display numbers from the same array',()=>{
 const store=createThreadOrderStore(),rows=[101,217,309,444].map(id=>({id,slug:'opaque-'+id,name:'Thread'}));let calls=0;store.subscribe(()=>calls++);
 store.publish('a',[]);assert.equal(threadOrdinal(store.get('a'),'opaque-101'),null);
 store.publish('a',rows);assert.equal(threadOrdinal(store.get('a'),'opaque-309'),3);assert.equal(threadLabel(3,'Thread'),'3 - Thread');
 store.remove('a',['opaque-217']);assert.equal(threadOrdinal(store.get('a'),'opaque-309'),2);assert.equal(threadOrdinal(store.get('a'),'opaque-444'),3);assert.equal(threadOrdinal(store.get('a'),'opaque-101'),1);
 store.publish('a',[rows[3],rows[0],rows[2]]);assert.equal(threadOrdinal(store.get('a'),'opaque-309'),3);
 assert.deepEqual(rows.map(r=>r.id),[101,217,309,444]);assert.equal(threadOrdinal(store.get('b'),'opaque-309'),null);assert.equal(calls,4);
});
