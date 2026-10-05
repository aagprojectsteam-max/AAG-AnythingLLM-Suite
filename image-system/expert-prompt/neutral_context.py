"""Provider-free Expert context and local validation. Never executes a model."""
import hashlib,json,pathlib,sys
from knowledge import Registry
from prompt_budget import Fragment,KleinTokenCounter,fit_plan,validate_final,utf16_units
from compiler import Compiler,CompilerError
ROOT=pathlib.Path(__file__).resolve().parent
SUBJECT_REFERENCE_RULE="Use the supplied authorized reference as the visual source for the main subject. Preserve the subject's recognizable shape, proportions, colors, markings, materials, texture, design, and distinctive details unless explicitly requested otherwise. Apply the requested action, scene, setting, and composition faithfully."
INSTRUCTION='''EXPERT DETAILED PROMPT MODE. You are the existing active chat author; use no other model or provider. Author one useful professional image prompt through the normal image tool. Use ONLY the selected knowledge below. Author ONE complete, self-contained final image prompt. Preserve every material user requirement semantically as protected P0 facts. Keep the original request only as separate provenance and source_text evidence; never prepend or quote it mechanically in the engine-facing prompt. A factual source phrase may naturally recur, and requested visible text must remain exact in its original alphabet. No source wrappers, analysis, explanations, headings, VisualPlan JSON or Markdown fences in the prompt unless explicitly requested as visible image text. Assign fragment priority by necessity, not by visual dimension. P1 must preserve EVERY explicit user requirement, including requested lighting, background, framing, material, color and exclusions as well as scene structure. P2 must preserve the selected style-defining visual treatment. P3 supporting art direction and P4 optional enrichment may contain only compatible additions that the user did not require; they must never be the sole coverage of a required fact. Before calling the tool, mentally remove P3/P4 and verify that every required user fact remains expressed in complete P1/P2 prose. Correct the plan before submission if any required meaning disappears. The final prompt may exceed 1800 characters but must fit 512 exact image-encoder tokens including its envelope. This is an operating budget, not an engine maximum. Maximize useful specification, not length. Remove optional P4 first, compact redundant P3 next; never drop P0-P2 or slice a string. If protected content cannot fit, explain the conflict instead of calling the image tool.
Supply expert_plan alongside prompt (per item for a batch). It contains facts [{dimension,classification,text,source_text}], fragments [{id,priority,text}], and conflicts: []. Facts use the supplied dimension/classification vocabulary; USER_REQUIRED and USER_PREFERRED facts must cite a nonempty exact substring of USER_CREATIVE_DIRECTION only. Never cite a style label, control value, or guidance passage as source_text unless that literal text appears in the original user request. Put selected Composer style guidance in P2 fragments, not in request-derived facts. Inferred facts use source_text: "". Do not elevate inferred attributes to USER_REQUIRED. Fragments are complete prose P1-P4 only, with unique short lowercase-hyphen IDs. Prefer one complete P1 paragraph covering the ENTIRE explicit user request and one P2 paragraph for the selected style. Do not split requested content into topical optional fragments. Include at least P1 and P2. Any P3/P4 hints in selected knowledge concern additions only and never demote a user requirement. P2 must express the selected canonical style through scene-specific observable traits; wording may vary. Translate each user fact into complete image-facing prose in text and represent its meaning inside protected P1/P2 prose. Equivalent translation or paraphrase is valid. Cover subjects, counts, attributes, actions, relations, environment, composition, requested visible text and exclusions; never promote inference to a user fact. The final coherent prose must preserve P1/P2 meaning; translated or paraphrased prose is valid. The original source string is NOT a required prompt fragment. An independent audit through the current active chat model must confirm semantic preservation and a self-contained prompt; ambiguity is a typed failure, never a reason to append the source. Translate the selected guidance into concrete scene-specific visual decisions. Every fragment must describe the finished image, not give instructions to a prompt writer: never copy meta-guidance such as "describe how", "describe brush behavior", "use the supplied concept", or "preserve requested content". Develop relevant composition, viewpoint, depth, light, palette, material, edge and medium behavior as concrete observable features. Do not pad a simple scene with irrelevant detail or empty quality adjectives. Never output internal IDs, paths, JSON or workflow instructions in the image prompt. Self-check invariants, counts, ownership, action/contact, spatial structure, composition, medium/style, light/color/material coherence, exact text, contradictions, invention and unnecessary detail. No unrequested text, logos or watermarks. Style guidance never authorizes changing requested content. Frozen identity and upscale routes retain their own contracts and do not accept Expert expansion.'''

def prepare_context(request,intent,atlas=None,registry=None):
    r=registry or Registry()
    operation=intent.get('operation');preservation=intent.get('preservation')
    if operation!='generate' and not(operation=='transform' and preservation=='subject'):
        raise CompilerError('EXPERT_ROUTE_UNSUPPORTED','Expert supports generation and subject transforms; identity and upscale retain their existing recipes.')
    if not isinstance(request,str) or not request.strip() or utf16_units(request)>4000:
        raise CompilerError('EXPERT_REQUEST_BOUND','The authoritative request must fit its unchanged 4000 UTF-16-unit boundary.')
    creative=intent.get('creative_direction',{});family=creative.get('visual_family','auto');style=creative.get('visual_subfamily','auto')
    if family!='auto' and style!='auto': selected=r.resolve(family,style);method='manual'
    else:
        plan=intent.get('knowledge_modules',{}).get('visual_atlas',{})
        matches=[x for x in plan.get('selections',[]) if family=='auto' or x['family_id']==family]
        if matches:selected=r.resolve(matches[0]['family_id'],matches[0]['subfamily_id']);method='auto-canonical-retrieval'
        elif family!='auto':
            if family not in r.families:raise CompilerError('INVALID_STYLE_ID','Unknown canonical family.')
            first=next(s for s in r.styles.values() if s['family_id']==family)
            selected=r.resolve(family,first['style_id']);method='auto-family-default'
        elif plan.get('reason') in ('ambiguous_match','no_reliable_match'):
            raise CompilerError('EXPERT_STYLE_UNRESOLVED','Select an exact Atlas style when automatic style intent is ambiguous.')
        else:selected=r.resolve('photography','photorealistic');method='auto-unconstrained-default'
    s=selected['style']
    # No request/secret/path is inferred from local files. Task knowledge is
    # selected by canonical family, structural safeguards by current text only.
    family_tasks={'photography':'photography','cinematic-film-still':'photography','illustration':'illustration','fine-art-traditional-media':'painting','3d-cgi':'3d-cgi','concept-art':'illustration','architecture-interior':'architecture','product-commercial':'product','infographic-educational':'infographic','coloring-page-line-art':'drawing','icon-ui-asset':'icon','map-spatial-illustration':'map','abstract-experimental':'abstract'}
    tasks=[family_tasks[s['family_id']]] if s['family_id'] in family_tasks else []
    import re
    risks=['style','media']
    for risk,pattern in [('count',r'\b(?:exactly|two|three|four|[2-9])\b|בדיוק|שני|שתי|שלוש'),('contact',r'\b(?:hold|holding|pass|passing|touch|riding)\b|מחזיק|נוגע'),('geometry',r'\b(?:building|room|mechanism|architecture)\b|מבנה|חדר'),('text',r'["“”]|\btext\b|טקסט'),('reflection',r'\b(?:mirror|reflective|glass)\b|מראה|זכוכית')]:
        if re.search(pattern,request,re.I):risks.append(risk)
    context=r.context(s['family_id'],s['style_id'],tasks,risks)
    # Strip bookkeeping/provenance from model-facing selected guidance.
    knowledge={'universal':context['universal'],'family':context['family'],'style':context['style'],'tasks':context['tasks'],'safeguards':context['safeguards']}
    return {'schema':'aag.expert.context.v1','authoring_contract':'self-contained.v2','selected_style':s['canonical_id'],'style_label':s['label'],'selection_method':method,'knowledge':knowledge,'dimensions':r.universal['dimensions'],'classifications':r.universal['classification'],'instructions':INSTRUCTION,'operating_token_budget':512, **({'required_source_safeguard':SUBJECT_REFERENCE_RULE,'source_safeguard_instruction':'Include this exact safeguard in one P1 fragment and in the final prompt; it is included in the 512-token budget.'} if operation=='transform' else {})}

def validate_candidate(request,intent,prompt,plan,counter=None):
    context=intent.get('expert_prompt')
    if not isinstance(context,dict) or context.get('schema')!='aag.expert.context.v1':raise CompilerError('EXPERT_CONTEXT_REQUIRED','Missing signed Expert context.')
    if context.get('authoring_contract')!='self-contained.v2':raise CompilerError('EXPERT_AUTHORING_CONTRACT_REQUIRED','Refresh the signed Composer request to use self-contained authoring.')
    cid=context['selected_style'];family,style=cid.split('/',1);r=Registry();selected=r.resolve(family,style)['style']
    c=Compiler(None,None,r);c.validate_plan(plan,request,selected)
    route='generate' if intent.get('operation')=='generate' else 'transform-subject' if intent.get('operation')=='transform' and intent.get('preservation')=='subject' else 'unsupported'
    if route=='transform-subject' and (SUBJECT_REFERENCE_RULE not in prompt or not any(f['priority']==1 and SUBJECT_REFERENCE_RULE in f['text'] for f in plan['fragments'])):
        raise CompilerError('EXPERT_SOURCE_SAFEGUARD_MISSING','The reference safeguard must be authored verbatim in P1 and counted within the final budget.')
    count=counter or KleinTokenCounter(pathlib.Path('/mnt/data/AI/ComfyUI'))
    fragments=c.protected_fragments(plan)
    budget=fit_plan(fragments,count,route=route)
    if budget['status']!='FIT':raise CompilerError(budget['status'],'Author must remove optional enrichment or compact supporting direction; protected content was not truncated.')
    result=validate_final(prompt,counter=count,protected_fragments=fragments,selected_style=cid,expected_style=cid,route=route,semantic_protection=True)
    return {**result,'plan':plan,'request_sha256':hashlib.sha256(request.encode()).hexdigest(),'context_sha256':hashlib.sha256(json.dumps(context,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'author':'current-active-chat-llm','contract':'aag.expert.prompt.v1','authoring_contract':'self-contained.v2','semantic_review_required':True,'review_style':{'canonical_id':cid,'label':selected['label'],'recognition_anchors':selected['recognition_anchors']}}
if __name__=='__main__':
    try:
        d=json.load(sys.stdin);result=validate_candidate(d['request'],d['intent'],d['prompt'],d['plan']);print(json.dumps({'ok':True,'result':result},ensure_ascii=False))
    except Exception as e:print(json.dumps({'ok':False,'code':getattr(e,'code','EXPERT_VALIDATION_FAILED'),'message':str(e)[:700]}));sys.exit(1)
