"use strict";
const fs = require("fs"), path = require("path"), os = require("os");
const { spawn } = require("child_process"), readline = require("readline");
function database(file) {
  if (process.env.AAG_TEST_PRISMA_PATH) {
    const {PrismaClient}=require(process.env.AAG_TEST_PRISMA_PATH);
    return new PrismaClient({datasources:{db:{url:"file:"+file}}});
  }
  const child = spawn("python3", ["-B", path.join(__dirname,"sqlite-test-client.py"), file], {stdio:["pipe","pipe","inherit"]});
  const pending = [];
  readline.createInterface({input:child.stdout}).on("line", line => {
    const result=JSON.parse(line), next=pending.shift();
    if(result.error) next.reject(new Error(result.error)); else next.resolve(result.value);
  });
  function sql(query,statement,params) {return new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify({query,sql:statement,params})+"\n");});}
  const db={
    $queryRawUnsafe:(s,...p)=>sql(true,s,p), $executeRawUnsafe:(s,...p)=>sql(false,s,p),
    async $transaction(fn){await sql(false,"BEGIN",[]);try{const v=await fn(db);await sql(false,"COMMIT",[]);return v;}catch(e){await sql(false,"ROLLBACK",[]);throw e;}},
    async $disconnect(){child.stdin.end();await new Promise(r=>child.once("exit",r));},
  };
  return db;
}
async function fixture(parsed, options={}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"aag-name-db-")),db=database(path.join(dir,"test.db"));
  await db.$executeRawUnsafe("CREATE TABLE workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)");
  await db.$executeRawUnsafe("CREATE TABLE workspace_threads(id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id INTEGER, name TEXT)");
  await db.$executeRawUnsafe("CREATE TABLE workspace_chats(id INTEGER, workspaceId INTEGER, response TEXT)");
  for(const id of [3,4])await db.$executeRawUnsafe("INSERT INTO workspaces VALUES(?, 'workspace')",id);
  for(const [id,w] of [[5,3],[9,4],[21,3]])await db.$executeRawUnsafe("INSERT INTO workspace_threads VALUES(?, ?, 'thread')",id,w);
  if (options.initialize !== false) await require("../../integrations/anythingllm/aagArtifactNaming").initializeSerials(db, {workspaceIds:[3,4],threads:[{workspaceId:3,threadIds:[5,21]},{workspaceId:4,threadIds:[9]}]});
  const job={job_id:parsed.jobId,owner:{workspace_id:3,thread_id:21},artifacts:parsed.artifacts.map(a=>({artifact_id:a.artifactId,sha256:a.sha256,width:a.width,height:a.height,format:"png",verified_at:"2026-10-05T04:02:15.123Z"})),...options.job};
  return {database:db,job,dir,resolveTimezone:async()=>"Asia/Jerusalem",async close(){await db.$disconnect();fs.rmSync(dir,{recursive:true,force:true});}};
}
module.exports={database,fixture};
