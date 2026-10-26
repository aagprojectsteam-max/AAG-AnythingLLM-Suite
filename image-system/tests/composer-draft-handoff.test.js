'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { createComposerDraftStore } = require('../integrations/anythingllm/frontend/aagComposerDraftStore');
for (const mode of ['auto', 'advanced']) test(`${mode} survives first-message handoff with complete selections`, () => {
  const store = createComposerDraftStore(), before = store.acquire('workspace-A', null, 'home-A');
  const values = { mode, settings: {visualFamily:'photography', visualSubfamily:'product', quality:'high', aspectRatio:'16:9', seed:'123'}, expanded:false, sourceIndex:'2', files:[] };
  for (const [field,value] of Object.entries(values)) store.write(before, field, value);
  assert.equal(store.handoff('workspace-A', 'stable-uuid', 'home-A'),true);
  const after = store.acquire('workspace-A','stable-uuid');
  assert.deepEqual(after.values,values);
  assert.equal(after.thread,'stable-uuid');
  assert.equal(store.write(before,'mode','auto'),false);
  assert.equal(store.handoff('workspace-A','another-uuid','home-A'),false);
  assert.deepEqual(store.acquire('workspace-A','another-uuid').values,{});
});
test('existing stable draft wins; temporary state consumed exactly once', () => {
  const s=createComposerDraftStore(), old=s.acquire('w',null,'home'), stable=s.acquire('w','stable');
  s.write(old,'mode','advanced');s.write(stable,'mode','auto');
  assert.equal(s.handoff('w','stable','home'),true);assert.equal(stable.values.mode,'auto');
  assert.equal(s.handoff('w','other','home'),false);
});
test('home scopes, bare default, other threads and workspaces never alias', () => {
  const s=createComposerDraftStore(), a=s.acquire('w',null,'one');s.write(a,'mode','advanced');
  for(const args of [['w',null,'two'],['w',null],['x',null,'one'],['w','uuid']]) assert.deepEqual(s.acquire(...args).values,{});
  s.handoff('x','uuid','one');assert.deepEqual(s.acquire('x','uuid').values,{});
  assert.equal(s.acquire('w',null,'one'),a);
});
test('bare workspace creates a stable thread; subsequent virtual draft starts fresh', () => {
  const s=createComposerDraftStore(), a=s.acquire('w',null);s.write(a,'mode','advanced');s.handoff('w','stable');
  assert.equal(s.acquire('w','stable').values.mode,'advanced');assert.deepEqual(s.acquire('w',null).values,{});
  assert.equal(s.write(a,'mode','auto'),false);
});
test('same stable scope remount/switch restores selections; page restart resets page drafts', () => {
  const s=createComposerDraftStore(), a=s.acquire('w','stable');s.write(a,'settings',{quality:'high'});
  s.acquire('w','other');assert.equal(s.acquire('w','stable'),a);
  assert.deepEqual(createComposerDraftStore().acquire('w','stable').values,{});
});
test('functional updates use current draft synchronously before submission', () => {
  const s=createComposerDraftStore(), a=s.acquire('w',null);s.write(a,'settings',{quality:'auto'});
  s.write(a,'settings',old=>({...old,quality:'high'}));s.handoff('w','stable');
  assert.equal(s.acquire('w','stable').values.settings.quality,'high');
});
test('bounded drafts retire evicted writers and invalid targets never consume source', () => {
  const s=createComposerDraftStore(2), a=s.acquire('w',null);s.write(a,'mode','advanced');
  for(const target of [null,'',42]) assert.equal(s.handoff('w',target),false);
  s.acquire('w','one');s.acquire('w','two');assert.equal(s.write(a,'mode','auto'),false);
});
