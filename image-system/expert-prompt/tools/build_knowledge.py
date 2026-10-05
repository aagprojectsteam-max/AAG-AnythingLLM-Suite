#!/usr/bin/env python3
"""Deterministically serialize reviewed reusable grammar; never read Atlas prompts."""
import hashlib,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
PROJECT=ROOT.parents[1]
TAXONOMY=ROOT.parent/'integrations/model-neutral-compatibility/composer/visual-taxonomy.json'
def rows(path,n):
    result={}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'): continue
        parts=[s.strip() for s in line.split('|')]
        assert len(parts)==n and parts[0] not in result,parts[0]
        result[parts[0]]=parts[1:]
    return result
def write(name,value):
    path=ROOT/'knowledge'/name
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
tax=json.loads(TAXONOMY.read_text())
notes=rows(ROOT/'knowledge/style-notes.tsv',4)
base=rows(ROOT/'knowledge/family-basis.tsv',5)
direction=rows(ROOT/'knowledge/family-direction.tsv',5)
families=[];styles=[]
for f in tax['families']:
    fid=f['id'];dna,light,material,render=base[fid];composition,edges,failures,budget=direction[fid]
    # Historical family hints are not mandates to invent magic or alter counts.
    if fid=='fantasy': dna='Coherent imaginative form governed by the supplied extraordinary premise';light='Consistent atmospheric or physical light; magical emission only when requested';render='Specified fantasy grammar without automatic narrative additions'
    if fid=='minimal-clean': render='Reduced incidental detail while retaining every user-required element'
    family={'canonical_id':fid,'label':f['label'],'definition':dna,'composition_viewpoint':composition,'lighting':light,'materials_textures':material,'color_edges_depth':edges,'rendering_detail':render,'failures_conflicts':failures,'budget_emphasis':budget,'inherits':['universal.v1'],'prompt_order':['P0 request','P1 spatial structure','P2 exact style grammar','P3 relevant art direction','P4 optional enrichment']}
    families.append(family)
    for s in f['subfamilies']:
        cid=fid+'/'+s['id'];anchor,construction,failure=notes[cid]
        styles.append({'canonical_id':cid,'family_id':fid,'style_id':s['id'],'label':s['label'],'definition':anchor,'recognition_anchors':[x.strip() for x in anchor.split(',')],'construction':construction,'failure_prevention':failure,'minimum_instruction':anchor+'. '+construction+'.','expanded_instruction':{'essential':[anchor,construction],'secondary':[family['composition_viewpoint'],family['lighting'],family['materials_textures'],family['color_edges_depth']],'avoid':[failure,family['failures_conflicts']],'optional':'Add only scene-relevant direction that preserves all explicit user constraints.'},'inherits':['universal.v1','family:'+fid],'token_priorities':{'P2':[anchor,construction],'P3':'Relevant inherited family and task guidance','P4':'Optional atmosphere or incidental surface refinement'},'subject_policy':'Apply this visual grammar to the requested scene; do not import subjects, counts, palette or objects from examples.'})
assert len(families)==28 and len(styles)==493
assert set(notes)=={s['canonical_id'] for s in styles}
write('families.json',{'schema':'aag.expert.families.v1','taxonomy_sha256':hashlib.sha256(TAXONOMY.read_bytes()).hexdigest(),'profiles':families})
write('styles.json',{'schema':'aag.expert.styles.v1','taxonomy_sha256':hashlib.sha256(TAXONOMY.read_bytes()).hexdigest(),'profiles':styles})
print(json.dumps({'families':len(families),'styles':len(styles)}))
