"""Exact, selective lookup. No image payload, benchmark prompt or global Atlas policy."""
import difflib,hashlib,json,pathlib,re
ROOT=pathlib.Path(__file__).resolve().parent
class KnowledgeError(ValueError): pass
class Registry:
    def __init__(self,root=ROOT/'knowledge',taxonomy=None):
        self.root=pathlib.Path(root)
        taxonomy=taxonomy or ROOT.parent/'integrations/model-neutral-compatibility/composer/visual-taxonomy.json'
        tax=json.loads(pathlib.Path(taxonomy).read_text());sha=hashlib.sha256(pathlib.Path(taxonomy).read_bytes()).hexdigest()
        self.families=self._load('families.json',sha);self.styles=self._load('styles.json',sha)
        expected={f['id']+'/'+s['id'] for f in tax['families'] for s in f['subfamilies']}
        if set(self.families)!={f['id'] for f in tax['families']} or set(self.styles)!=expected or len(expected)!=493 or len(self.families)!=28: raise KnowledgeError('CANONICAL_BIJECTION_FAILED')
        self.universal=json.loads((self.root/'universal.json').read_text())
        self.tasks={p[0]:p[1] for p in self._tsv('tasks.tsv',2)}
        self.failures={p[0]:{'trigger':p[1],'prevention':p[2]} for p in self._tsv('failures.tsv',3)}
        self.rubric=json.loads((self.root/'rubric.json').read_text())
        for cid,s in self.styles.items():
            if cid!=s['family_id']+'/'+s['style_id'] or s['family_id'] not in self.families: raise KnowledgeError('FAMILY_STYLE_MISMATCH')
    def _tsv(self,name,n):
        rows=[l.split('|') for l in (self.root/name).read_text().splitlines() if l and not l.startswith('#')]
        if any(len(p)!=n for p in rows) or len({p[0] for p in rows})!=len(rows): raise KnowledgeError('INVALID_KNOWLEDGE_TABLE')
        return rows
    def _load(self,name,sha):
        data=json.loads((self.root/name).read_text());profiles=data['profiles']
        if data.get('taxonomy_sha256')!=sha or len({p['canonical_id'] for p in profiles})!=len(profiles): raise KnowledgeError('REGISTRY_IDENTITY_FAILED')
        return {p['canonical_id']:p for p in profiles}
    def resolve(self,family_id,style_id):
        if not isinstance(family_id,str) or not isinstance(style_id,str): raise KnowledgeError('INVALID_STYLE_ID')
        cid=family_id+'/'+style_id
        if family_id not in self.families or cid not in self.styles: raise KnowledgeError('INVALID_STYLE_ID')
        return {'family':self.families[family_id],'style':self.styles[cid]}
    def context(self,family_id,style_id,task_ids=(),risk_ids=()):
        selected=self.resolve(family_id,style_id)
        if len(task_ids)>3 or len(risk_ids)>8 or len(set(task_ids))!=len(task_ids) or len(set(risk_ids))!=len(risk_ids): raise KnowledgeError('CONTEXT_SELECTION_INVALID')
        if any(k not in self.tasks for k in task_ids) or any(k not in self.failures for k in risk_ids): raise KnowledgeError('UNKNOWN_CONTEXT_ID')
        value={**selected,'universal':self.universal['rules'],'tasks':{k:self.tasks[k] for k in task_ids},'safeguards':{k:self.failures[k] for k in risk_ids}}
        # Measured context is checked against actual author tokenizer before use.
        if len(json.dumps(value,ensure_ascii=False).encode())>32000: raise KnowledgeError('KNOWLEDGE_TRANSPORT_BOUND')
        return value
    def audit(self):
        issues=[];comparisons=[];texts={}
        banned=re.compile(r'/mnt/|/home/|AAG_ATLAS_|OBJECT_ONLY|benchmark scene|canonical prompt:',re.I)
        for cid,p in self.styles.items():
            text=' '.join([p['definition'],p['construction'],p['failure_prevention']])
            if len(text.split())<22 or not p['recognition_anchors'] or banned.search(text): issues.append([cid,'EMPTY_GENERIC_OR_LEAKED'])
            normalized=re.sub('[^a-z ]','',text.lower())
            if normalized in texts: issues.append([cid,'IDENTICAL_PROFILE',texts[normalized]])
            texts[normalized]=cid
        values=list(texts.items())
        for i,(a,aid) in enumerate(values):
            aw=set(a.split())
            for b,bid in values[i+1:]:
                bw=set(b.split());score=len(aw&bw)/len(aw|bw)
                if score>=.72:
                    comparisons.append({'left':aid,'right':bid,'word_jaccard':round(score,4)})
                    if difflib.SequenceMatcher(None,a,b).ratio()>.88: issues.append([aid,'NEAR_DUPLICATE',bid])
        return {'families':len(self.families),'styles':len(self.styles),'bijection':'PASS','issues':issues,'similarity_diagnostics':comparisons,'status':'PASS' if not issues else 'FAIL'}
if __name__=='__main__':
    report=Registry().audit();print(json.dumps(report,indent=2));raise SystemExit(bool(report['issues']))
