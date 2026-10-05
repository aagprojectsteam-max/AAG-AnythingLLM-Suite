"""Opt-in professional authoring with exact encoder budgeting and protected prose.

The model authors the plan and final prose. The compiler never invents creative
sentences, truncates a response or raises the legacy provider prompt limit.
"""
from __future__ import annotations
import hashlib,json,pathlib,re
from knowledge import Registry,KnowledgeError
from prompt_budget import BudgetError,Fragment,KleinTokenCounter,fit_plan,validate_final,validate_completion_finish,model_context_budget,utf16_units
ROOT=pathlib.Path(__file__).resolve().parent
class CompilerError(ValueError):
    def __init__(self,code,message): super().__init__(message);self.code=code

def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(text): return hashlib.sha256(text.encode()).hexdigest()

class Compiler:
    def __init__(self,author,counter,registry=None): self.author=author;self.counter=counter;self.registry=registry or Registry()
    def selection(self,request,intent):
        creative=intent.get('creative_direction',{});family=creative.get('visual_family','auto');style=creative.get('visual_subfamily','auto')
        if family!='auto' and style!='auto': return self.registry.resolve(family,style),'manual'
        selections=intent.get('knowledge_modules',{}).get('visual_atlas',{}).get('selections',[])
        for selected in selections:
            if family=='auto' or selected['family_id']==family:
                return self.registry.resolve(selected['family_id'],selected['subfamily_id']),'auto-canonical-retrieval'
        if family=='auto':
            answer=self.author('Choose one appropriate canonical visual family for this request. Return {"family_id": exact listed ID}. Do not alter the requested content.',{'request':request,'families':{k:v['label'] for k,v in self.registry.families.items()}})
            family=answer.get('family_id')
        if family not in self.registry.families: raise CompilerError('INVALID_STYLE_ID','Author selected an unknown family.')
        choices={s['style_id']:s['label'] for s in self.registry.styles.values() if s['family_id']==family}
        answer=self.author('Choose one exact style ID within the selected family. Return {"style_id": exact listed ID}. Respect explicit style intent.',{'request':request,'family_id':family,'styles':choices})
        return self.registry.resolve(family,answer.get('style_id')),'auto-author-selection'
    def compile(self,request,intent):
        if not isinstance(request,str) or not request.strip() or utf16_units(request)>4000: raise CompilerError('EXPERT_REQUEST_BOUND','The unchanged authoritative request boundary is 4000 UTF-16 units; shorten the request explicitly, never silently.')
        route='generate' if intent.get('operation')=='generate' else 'transform-subject' if intent.get('operation')=='transform' and intent.get('preservation')=='subject' else None
        if route is None: raise CompilerError('EXPERT_ROUTE_UNSUPPORTED','Expert authoring supports generation and ordinary subject transforms; frozen identity/upscale recipes remain unchanged.')
        selected,method=self.selection(request,intent);style=selected['style'];cid=style['canonical_id']
        classification=self.author('Classify the actual scene for selective prompt guidance. Return {"tasks": up to 3 exact task IDs, "risks": up to 8 exact risk IDs}. Select only relevant entries; no generic anatomy for inanimate subjects.',{'request':request,'controls':intent.get('semantics',{}),'style':cid,'tasks':list(self.registry.tasks),'risks':{k:v['trigger'] for k,v in self.registry.failures.items()}})
        tasks=classification.get('tasks',[]);risks=classification.get('risks',[])
        if not isinstance(tasks,list) or not isinstance(risks,list): raise CompilerError('PLAN_INVALID','Task/risk selections must be lists.')
        context=self.registry.context(style['family_id'],style['style_id'],tasks,risks)
        instruction=('Author a professional VisualPlan and compact production prose for the supplied scene, maximizing useful visual specification. '
          'Return {"facts":[{"dimension":exact dimension ID,"classification":exact classification,"text":specific fact,"source_text":exact request substring or empty for inference}],'
          '"fragments":[{"id":unique short ID,"priority":1 or 2 or 3 or 4,"text":complete authored prose}],"conflicts":[]}. '
          'P0 protects semantic user invariants, not the raw source string. Translate every material user fact into image-facing prose in fact.text; represent its meaning in protected P1/P2 prose. Equivalent translation or paraphrase is valid; verbatim fact or source echo is not required. The original request remains separate source evidence. Do not mechanically prepend it or echo a source wrapper. Preserve requested visible text exactly in its original alphabet. P1 contains essential scene structure; P2 contains exact style-defining visual decisions, including the supplied style label verbatim. '
          'P3 supplies relevant composition/viewpoint/light/palette/material/texture/rendering details. P4 is truly optional enrichment. '
          'Use the selected style grammar on THIS subject. Do not copy generic advice into the image prompt. '
          'Facts must cover every materially useful dimension; omit irrelevant dimensions. Required facts must cite source_text exactly. '
          'Do not invent events, objects, identities, counts, text or exclusions. Distinguish safe art direction from required facts. '
          'Never put required content into P3/P4. Record genuine contradictory user constraints in conflicts. '
          'The self-contained final prose must fit 512 image-encoder tokens including its chat envelope. Aim for rich useful detail, not length. '
          'No internal IDs, JSON, paths, workflow language or empty quality slogans in prose.')
        data={'request':request,'controls':intent.get('semantics',{}),'selected_style':cid,'style_label':style['label'],'knowledge':context,'dimensions':self.registry.universal['dimensions'],'classifications':self.registry.universal['classification']}
        plan=self.author(instruction,data);self.validate_plan(plan,request,style)
        fragments=self.protected_fragments(plan)
        budget=fit_plan(fragments,self.counter,route=route);revisions=0
        if budget['status']=='PROTECTED_CONTENT_EXCEEDS_BUDGET': raise CompilerError(budget['status'],'P0-P2 exceed the validated image budget. Nothing was removed.')
        while budget['status']=='AUTHOR_REVISION_REQUIRED' and revisions<2:
            p3=[f for f in budget['fragments'] if f['priority']==3]
            compact=self.author('Compact ONLY these supporting P3 prose fragments. Return {"fragments":[same IDs and priority 3 with shorter complete prose]}. Preserve their meaning; remove redundant wording first. Do not edit protected content or introduce new facts. No string truncation.',{'supporting':p3,'protected':[f for f in budget['fragments'] if f['priority']<=2],'image_token_overage':None if budget['tokens'] is None else budget['tokens']-512})
            revised=compact.get('fragments',[])
            if not isinstance(revised,list) or {f.get('id') for f in revised}!={f['id'] for f in p3} or len(revised)!=len(p3) or any(f.get('priority')!=3 for f in revised): raise CompilerError('PLAN_INVALID','Author changed protected revision structure.')
            byid={f['id']:f for f in revised}
            fragments=tuple(Fragment(**byid.get(f['id'],f)) for f in budget['fragments'])
            budget=fit_plan(fragments,self.counter,route=route);revisions+=1
        if budget['status']!='FIT': raise CompilerError('EXPERT_BUDGET_UNRESOLVED','Supporting prose could not fit without losing protected content; no image submitted.')
        kept=tuple(Fragment(**f) for f in budget['fragments'])
        # Final synthesis remains a separate model-authored prose operation.
        # Exact protected spans must survive; the returned text is never sliced.
        final=self.author('Synthesize the supplied authored VisualPlan prose into a coherent natural-language image prompt. Return {"prompt": complete prose}. Include every protected authored P0/P1/P2 text EXACTLY and in order; never prepend the raw source request. Output self-contained image prose, not source wrappers, analysis or explanations. Preserve their wording and punctuation. Integrate P3 naturally; omit no useful details. Do not add P4 or new content. Stay within 512 image-encoder tokens including envelope; no headings, internal IDs or JSON in the prompt.',{'fragments':budget['fragments'],'style_label':style['label']})
        result=validate_final(final.get('prompt'),counter=self.counter,protected_fragments=kept,selected_style=cid,expected_style=cid,route=route,semantic_protection=True)
        # Independent semantic audit; fails closed on ambiguity rather than
        # claiming that substring checks prove natural-language correctness.
        review=self.author('Audit request, selected style, VisualPlan and final prompt independently. Return {"pass":boolean,"issues":[short factual reasons],"scores":{each requested rubric dimension:0,1,2 or null},"not_applicable":{dimension:reason}}. Fail if any invariant/count/ownership/action/text changes, contradictory additions, unsupported facts, wrong style, or important omissions occur. Also fail source-request wrapper/echo, analysis, headings, fences or leaked plan JSON unless required as literal visible text. Confirm the final prompt stands alone and retains exclusions, colors and spatial relations, including translated requirements. Do not reward verbosity.',{'request':request,'controls':intent.get('semantics',{}),'style':selected,'plan':plan,'prompt':result['prompt'],'rubric':self.registry.rubric})
        dimensions=self.registry.rubric['dimensions'];scores=review.get('scores',{})
        if review.get('pass') is not True or review.get('issues')!=[] or set(scores)!=set(dimensions) or any(type(v) is not int and v is not None or v is not None and v not in (0,1,2) for v in scores.values()): raise CompilerError('EXPERT_SEMANTIC_REVIEW_FAILED','Independent author review did not confirm fidelity: '+canonical(review.get('issues',[])))
        if any(v==0 for v in scores.values()) or any(v is None and not review.get('not_applicable',{}).get(k) for k,v in scores.items()): raise CompilerError('EXPERT_SEMANTIC_REVIEW_FAILED','Incomplete or failed rubric evidence.')
        return {**result,'semantic_review_required':False,'schema':'aag.expert.compiled.v1','request_sha256':digest(request),'request':request,'intent':intent,'visual_plan':plan,'selected_family':style['family_id'],'selection_method':method,'guidance_ids':{'family':style['family_id'],'style':cid,'tasks':tasks,'risks':risks},'guidance_sha256':digest(canonical(context)),'budget':budget,'author_revisions':revisions,'review':review,'author_measurements':getattr(self.author,'measurements',[])}
    def validate_plan(self,plan,request,style):
        import jsonschema
        try: jsonschema.validate(plan,json.loads((ROOT/'schemas/visual-plan.schema.json').read_text()))
        except jsonschema.ValidationError as exc: raise CompilerError('PLAN_INVALID','VisualPlan schema validation failed.') from exc
        if not isinstance(plan.get('facts'),list) or not plan['facts'] or not isinstance(plan.get('fragments'),list) or not plan['fragments'] or len(plan['facts'])>64 or len(plan['fragments'])>24 or plan.get('conflicts')!=[]: raise CompilerError('PLAN_INVALID','Plan is malformed or has unresolved conflicts.')
        for f in plan['facts']:
            if not isinstance(f,dict) or f.get('dimension') not in self.registry.universal['dimensions'] or f.get('classification') not in self.registry.universal['classification'] or not isinstance(f.get('text'),str) or not f['text'].strip(): raise CompilerError('PLAN_INVALID','Invalid classified fact.')
            source=f.get('source_text')
            if not isinstance(source,str) or source and source not in request or f['classification'] in ('USER_REQUIRED','USER_PREFERRED') and not source: raise CompilerError('INVARIANT_SOURCE_INVALID','User facts require exact source evidence.')
        required=[f for f in plan['facts'] if f['classification'] in ('USER_REQUIRED','USER_PREFERRED') or f['classification']=='DO_NOT_INVENT' and f['source_text']]
        if not required: raise CompilerError('EXPERT_INVARIANT_EVIDENCE_MISSING','At least one source-bound user invariant is required.')
        # Meaning, translation and completeness are checked by the mandatory
        # semantic review, with evidence bound to protected prose and final text.
        ids=set();priorities=set();p2=[]
        for f in plan['fragments']:
            if not isinstance(f,dict) or set(f)!={'id','priority','text'} or not re.fullmatch('[a-z][a-z0-9-]{0,63}',str(f.get('id',''))) or f['id'] in ids or type(f['priority']) is not int or f['priority'] not in (1,2,3,4) or not isinstance(f['text'],str) or not f['text'].strip(): raise CompilerError('PLAN_INVALID','Invalid authored fragment.')
            ids.add(f['id']);priorities.add(f['priority'])
            if f['priority']==2:p2.append(f['text'])
        if not {1,2}.issubset(priorities): raise CompilerError('EXPERT_STYLE_CHANGED','Plan must contain protected scene structure and selected-style prose.')


    def protected_fragments(self,plan):
        # P1 prose carries the independently audited P0 facts. Elevate that
        # prose to P0 for budgeting, without concatenating duplicate source
        # or fact strings. P1 and P2 remain unconditionally protected.
        return tuple(Fragment(f['id'],0 if f['priority']==1 else f['priority'],f['text']) for f in plan['fragments'])
