'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {displayArtifactText}=require('../integrations/anythingllm/aagArtifactText');
const cases=[
 ['completed','', 'Returning image to chat…'],
 ['failed','EXPERT_SEMANTIC_REVIEW_FAILED','The image prompt could not be validated. Review your request and the selected style.'],
 ['failed','ENGINE_TIMEOUT','Image generation could not finish. Please try again later.'],
 ['failed','INVALID_STYLE_ID','The image request could not be validated. Check the selected settings and your request.'],
 ['failed','PROMPT_SEMANTIC_DRIFT','The image request could not be validated. Check the selected settings and your request.'],
 ['failed','PUBLIC_SCHEMA_VIOLATION','The image request could not be validated. Check the selected settings and your request.'],
 ['failed','INTERNAL_ERROR','The image could not be generated.'],
 ['partial','','Some images could not be generated.'],
 ['cancelled','','Image generation was cancelled.'],
 ['running','','Image generation is in progress.'],
 ['queued','','Image generation is in progress.'],
 ['future_state','','Image generation status is unavailable.']
];
for(const [status,code,expected] of cases) test(`new and persisted ${status}/${code} hide all protocol with or without image cards`,()=>{
 const raw=`AAG_IMAGE_RESULT\nstatus=${status}\nerror_code=${code}\njob_id=private-id\nmessage=fact-3 /private/path Error: internal\nretryable=false\nsame_turn_retry=forbidden\nartifact_count=0\nfuture_field=secret`;
 for(const outputs of [[],[{type:'imageGenerationCard',payload:{artifactId:'kept'}}]])for(const content of [raw,raw.replaceAll('\n',' '),'```text\n'+raw+'\n  at private (/private/trace.js:1)\n```']) {
  const history={role:'assistant',content,outputs,metadata:{retryable:false,failed_invariant_id:'fact-3'}},before=JSON.stringify(history);
  const expectation=status==='completed'&&outputs.length?'':expected;
  assert.equal(displayArtifactText(content,outputs),expectation);
  const persisted=JSON.parse(before);assert.equal(displayArtifactText(persisted.content,persisted.outputs,persisted.role),expectation);
  assert.equal(JSON.stringify(history),before);assert.equal(persisted.metadata.failed_invariant_id,'fact-3');
  assert.equal(displayArtifactText(content,outputs,'user'),content);
 }
});
test('all streaming stages after the marker are sanitized and ordinary prose survives',()=>{
 for(const raw of ['AAG_IMAGE_RESULT','AAG_IMAGE_RESULT\nstatus=','AAG_IMAGE_RESULT\nstatus=failed\nmessage=private'])assert.doesNotMatch(displayArtifactText(raw),/AAG_IMAGE_RESULT|status=|message=|private/);
 assert.equal(displayArtifactText('Before.\nAAG_IMAGE_RESULT status=failed job_id=private\nAfter.'),'Before.\nThe image could not be generated.\nAfter.');
 assert.equal(displayArtifactText('Documentation: AAG_IMAGE_RESULT is a protocol.'),'Documentation: AAG_IMAGE_RESULT is a protocol.');
 const prose='  Ordinary response.\n\n\n    Code indentation.\n';assert.equal(displayArtifactText(prose),prose);
 assert.equal(displayArtifactText('AAG_IMAGE_RESULT\nstatus=failed\nmessage=Error\n    at /private/trace.js:1'),'The image could not be generated.');
});
test('failure messages use established localization and never the machine message',()=>{
 const file=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/AagImageComposerPanel/localization.js'),'utf8');
 const translations=Object.fromEntries([...file.matchAll(/(imageResult\w+): "([^"]+)"/g)].map(m=>[m[1],m[2]]));
 for(const [status,code] of cases){const english=displayArtifactText(`AAG_IMAGE_RESULT status=${status} error_code=${code}`);const hebrew=displayArtifactText(`AAG_IMAGE_RESULT status=${status} error_code=${code}`,[],'assistant',(key)=>{assert.ok(translations[key],key);return translations[key];});assert.notEqual(hebrew,english);assert.match(hebrew,/[א-ת]/);}
});

test('success labels use authoritative basenames and leave persisted history intact',()=>{
 const {artifactFilename}=require('../integrations/anythingllm/aagArtifactText');
 const names=['AAG-Image-1-4-20261005-094500.png','img-55701b1c-1111-4111-8111-111111111111.png'];
 const outputs=names.map(storageFilename=>({type:'imageGenerationCard',payload:{storageFilename,filename:'prompt-derived-name.png'}}));
 const saved=JSON.stringify(outputs);
 assert.deepEqual(outputs.map(o=>artifactFilename(o.payload)),names);
 assert.equal(displayArtifactText('Image generated successfully.',outputs),'');
 assert.equal(displayArtifactText('Image generated successfully.\n'+names[0]+'\nEnjoy.',outputs),'Enjoy.');
 assert.equal(displayArtifactText('Image generated successfully.',outputs,'user'),'Image generated successfully.');
 assert.equal(JSON.stringify(outputs),saved);
 assert.equal(artifactFilename({applicationPath:'generated-images/legacy.png'}),'legacy.png');
 assert.equal(artifactFilename({filename:'older-saved-name.png'}),'older-saved-name.png');
 assert.equal(artifactFilename({storageFilename:'../../etc/passwd',filename:'image.png'}),'image.png');
 assert.equal(artifactFilename({storageFilename:'bad\nname.png',artifactUrl:'http://host/invented.png'}),null);
 assert.equal(artifactFilename({storageFilename:names[0]},{state:'AVAILABLE',filename:'unrelated.png'}),names[0]);
 assert.equal(displayArtifactText('AAG_IMAGE_RESULT status=failed error_code=ENGINE_TIMEOUT',outputs),'Image generation could not finish. Please try again later.');
});
