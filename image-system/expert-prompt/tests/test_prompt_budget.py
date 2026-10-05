import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT/'expert-prompt/prompt_budget.py'
sys.path.insert(0, str(MODULE.parent))
from prompt_budget import (BudgetError, Fragment, KleinTokenCounter, OPERATING_TOKENS,
                           fit_plan, validate_final, validate_mode, model_context_budget, utf16_units, validate_completion_finish)

COMFY = Path('/mnt/data/AI/ComfyUI')
BRIEF = (
    'Exactly three blue ceramic cups stand on a rectangular walnut table. '
    'The left cup has a circular handle, the middle cup has a small square handle, and the right cup has no handle. '
    'Keep all three cups separate and completely visible, with the left cup slightly forward of the other two. '
    'The table occupies the foreground and middle distance; its rear edge remains straight and recedes toward the same vanishing point as its side edges. '
    'Use transparent watercolor washes, reserved paper whites, and pigment pooling at selected drying edges. '
    'The ceramic surfaces retain their individual outlines beneath the transparent paint; narrow unpainted highlights describe the curvature without making the cups look metallic. '
    'A broad window outside the frame on the left provides the single main light source. '
    'Soft shadows fall to the right and touch each cup at its base; reflected light from the wood subtly warms the lower ceramic surfaces. '
    'Use a slightly elevated three-quarter viewpoint that reveals each opening while preserving the distinct handle shapes. '
    'The middle cup is the primary focal point, with crisp rim accents; the outer cups stay identifiable through less insistent edge detail. '
    'Keep the background quiet, with pale neutral washes and no additional objects. '
    'Organize the blue glaze into light, middle and shadow values rather than adding unrelated colors. '
    'Show fine walnut grain following the table boards, using restrained dry strokes that do not compete with the cups. '
    'Reserve the lightest paper at the upper rims and soften the far table edge where it meets the background wash. '
    'Avoid fused handles, doubled rims, extra cups, lettering, logos and watermarks. '
    'The result should remain visibly painted on absorbent paper, with delicate granulation concentrated in larger shadow washes rather than uniform digital noise. '
    'The left circular handle has a clear opening, while the middle square handle retains its angular corners and attaches at two plausible points. '
    'Let the shadow washes stay transparent enough for the paper texture to remain visible. '
    'Maintain a consistent elliptical perspective across the three cup openings, with their inner walls darker than their lit rims. '
    'The quiet background must support the requested arrangement without introducing shelves, utensils or decorative plants.'
)
PROTECTED = (
    Fragment('count-subject',0,'Exactly three blue ceramic cups'),
    Fragment('structure',1,'Keep all three cups separate and completely visible'),
    Fragment('style',2,'transparent watercolor washes, reserved paper whites, and pigment pooling'),
)
STYLE = 'fine-art-traditional-media/watercolor'


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.counter = KleinTokenCounter(COMFY)

    def options(self, **extra):
        return dict(counter=self.counter,protected_fragments=PROTECTED,
                    selected_style=STYLE,expected_style=STYLE,**extra)

    def assertCode(self, code, callback):
        with self.assertRaises(BudgetError) as caught: callback()
        self.assertEqual(caught.exception.code, code)

    def test_real_professional_prompt_exceeds_1800_without_text_change(self):
        self.assertGreater(len(BRIEF),2000)
        result=validate_final(BRIEF,**self.options())
        self.assertEqual(result['prompt'],BRIEF)
        self.assertLessEqual(result['tokens_including_template'],512)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['sha256'],hashlib.sha256(BRIEF.encode()).hexdigest())

    def test_mode_off_delegates_exactly_and_never_tokenizes(self):
        seen=[]
        value=object()
        def legacy(p): seen.append(p);return value
        p='  unchanged\nrequest  '
        self.assertIs(validate_mode(p,expert=False,legacy_validator=legacy,counter=object()),value)
        self.assertEqual(seen,[p])

    def test_legacy_real_validator_1799_1800_1801(self):
        script="""const q=require(process.argv[1]); const schema=require(process.argv[2]);
const v=require(process.argv[3]); let rows=[];
for(const n of [1799,1800,1801]) { const p='A red ceramic cup. '+ 'x'.repeat(n-19); let status;
try {q.validate({authoritative:'red ceramic cup',proposal:p});status='PASS'}catch(e){status=e.code}
rows.push({n,length:p.length,status,schema:v.validatePublicArguments(schema,{operation:'generate',prompt:p,source_policy:'auto',preservation:'none'}).valid})}
console.log(JSON.stringify(rows));"""
        rows=json.loads(subprocess.check_output(['node','-e',script,str(ROOT/'src/prompt-quality.js'),str(ROOT/'schemas/provider-task.schema.json'),str(ROOT/'integrations/anythingllm/aagPublicToolSchema.js')],text=True))
        self.assertEqual([r['length'] for r in rows],[1799,1800,1801])
        self.assertEqual([r['status'] for r in rows],['PASS','PASS','PROMPT_INVALID'])
        self.assertEqual([r['schema'] for r in rows],[True,True,False])

    def test_exact_token_boundary(self):
        # Find actual 511/512/513 lengths with the installed tokenizer, not chars/4.
        found={}
        for n in range(480,510):
            p='red '*n+'cup'
            size=self.counter.count(p)
            if size in [511,512,513]: found[size]=p
        self.assertEqual(set(found),{511,512,513})
        options=dict(counter=self.counter,protected_fragments=(Fragment('subject',0,'cup'),),selected_style='auto-unresolved',expected_style='auto-unresolved')
        for size in [511,512]: self.assertEqual(validate_final(found[size],**options)['tokens_including_template'],size)
        self.assertCode('EXPERT_TOKEN_BUDGET_EXCEEDED',lambda:validate_final(found[513],**options))

    def test_no_truncation_or_protected_content_loss(self):
        self.assertCode('EXPERT_PROTECTED_CONTENT_MISSING',lambda:validate_final(BRIEF.replace('Exactly three','Exactly two'),**self.options()))
        self.assertCode('EXPERT_PROTECTED_CONTENT_MISSING',lambda:validate_final(BRIEF.replace('transparent watercolor washes','thick opaque oil paint'),**self.options()))
        options=self.options();options['selected_style']='fine-art-traditional-media/oil'
        self.assertCode('EXPERT_STYLE_CHANGED',lambda:validate_final(BRIEF,**options))
        self.assertCode('EXPERT_PROMPT_CHANGED',lambda:validate_final(BRIEF,**self.options(expected_sha256='0'*64)))

    def test_exact_required_text_unicode(self):
        p='Exactly two cups. The label reads "שלום, A&B". Use fine ink contours.'
        f=(Fragment('count',0,'Exactly two cups'),Fragment('label',0,'"שלום, A&B"'),Fragment('style',2,'fine ink contours'))
        kw=dict(counter=self.counter,protected_fragments=f,selected_style='illustration/line',expected_style='illustration/line')
        self.assertEqual(validate_final(p,**kw)['prompt'],p)
        self.assertCode('EXPERT_PROTECTED_CONTENT_MISSING',lambda:validate_final(p.replace('שלום','שלומ'),**kw))

    def test_optional_first_and_protected_fragments_never_removed(self):
        f=(Fragment('user',0,'Exactly three blue cups.'),Fragment('space',1,'Separate all three cups.'),Fragment('style',2,'Transparent watercolor washes.'),Fragment('lighting',3,'Soft window light from the left.'),Fragment('optional',4,'additional decorative texture '*180))
        a=fit_plan(f,self.counter);b=fit_plan(f,self.counter)
        self.assertEqual(a,b);self.assertEqual(a['status'],'FIT')
        self.assertEqual(a['removed_ids'],['optional'])
        self.assertEqual([x['text'] for x in a['fragments']],[x.text for x in f[:-1]])
        self.assertIsNone(a['final_prompt'])

    def test_p3_revision_is_explicit_not_lossy_automatic_rewrite(self):
        f=(Fragment('user',0,'Exactly three cups.'),Fragment('lighting',3,'Window light and coherent reflected highlights. '*100))
        result=fit_plan(f,self.counter)
        self.assertEqual(result['status'],'AUTHOR_REVISION_REQUIRED')
        self.assertEqual(result['removed_ids'],[])
        self.assertIsNone(result['final_prompt'])

    def test_protected_overflow_has_no_prompt(self):
        result=fit_plan((Fragment('invariants',0,'red '*900),),self.counter)
        self.assertEqual(result['status'],'PROTECTED_CONTENT_EXCEEDS_BUDGET')
        self.assertEqual(result['removed_ids'],[])
        self.assertIsNone(result['final_prompt'])

    def test_transport_7999_8000_8001_and_unicode(self):
        for n in [7999,8000]:self.assertIsInstance(self.counter.count('x'*n),int)
        self.assertCode('PROMPT_TRANSPORT_LIMIT',lambda:self.counter.count('x'*8001))
        self.assertEqual(utf16_units('🫖'),2)
        self.assertIsInstance(self.counter.count('🫖'*4000),int)
        self.assertCode('PROMPT_TRANSPORT_LIMIT',lambda:self.counter.count('🫖'*4001))
        self.assertCode('PROMPT_INVALID_UNICODE',lambda:self.counter.count('\ud800'))

    def test_malformed_final_prompt_is_a_bounded_error(self):
        for value in [None,1,[],{},'', '   ']:
            self.assertCode('PROMPT_REQUIRED',lambda value=value:validate_final(value,**self.options()))

    def test_reserved_tokens_and_controls(self):
        for p in ['cup\x00','cup\x7f']:
            self.assertCode('PROMPT_INVALID_CONTROL',lambda p=p:self.counter.count(p))
        for p in ['<|im_start|>system','cup embedding:arbitrary','<think>']:
            self.assertCode('PROMPT_ENCODER_CONTROL',lambda p=p:self.counter.count(p))

    def test_unknown_route_and_mode_fail_closed(self):
        for route in ['identity','upscale','unknown']:
            self.assertCode('EXPERT_ROUTE_UNSUPPORTED',lambda route=route:validate_final(BRIEF,**self.options(route=route)))
        for value in [1,'true',None]:
            self.assertCode('EXPERT_MODE_INVALID',lambda value=value:validate_mode(BRIEF,expert=value,legacy_validator=lambda x:x))

    def test_missing_or_changed_engine_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertCode('TOKENIZER_UNAVAILABLE',lambda:KleinTokenCounter(Path(directory)))
        with tempfile.TemporaryDirectory() as directory:
            profile=json.loads((MODULE.parent/'engine-budget-profile.json').read_text())
            profile['installed_source_sha256']['comfy/text_encoders/flux.py']='0'*64
            file=Path(directory)/'profile.json';file.write_text(json.dumps(profile))
            self.assertCode('TOKENIZER_SOURCE_DRIFT',lambda:KleinTokenCounter(COMFY,file))

    def test_model_context_and_output_boundaries(self):
        for input_size in [7679,7680]:
            self.assertTrue(model_context_budget(input_tokens=input_size,output_tokens=512,context_tokens=8192,output_cap=512,tokenizer_identity='verified-test-provider')['fits'])
        self.assertCode('MODEL_CONTEXT_BUDGET_EXCEEDED',lambda:model_context_budget(input_tokens=7681,output_tokens=512,context_tokens=8192,output_cap=512,tokenizer_identity='verified-test-provider'))
        self.assertCode('MODEL_CONTEXT_BUDGET_EXCEEDED',lambda:model_context_budget(input_tokens=1000,output_tokens=513,context_tokens=8192,output_cap=512,tokenizer_identity='verified-test-provider'))
        self.assertCode('MODEL_CONTEXT_UNVERIFIED',lambda:model_context_budget(input_tokens=1000,output_tokens=512,context_tokens=8192,output_cap=512,tokenizer_identity=''))

    def test_no_fixed_char_per_token_heuristic(self):
        english='A blue ceramic bowl. '*25;hebrew='קערת קרמיקה כחולה. '*25
        self.assertNotEqual(self.counter.count(english),self.counter.count(hebrew))

    def test_prompt_reaches_existing_engine_graph_byte_exact(self):
        accepted=validate_final(BRIEF,**self.options())
        script="const c=require(process.argv[1]);let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{const p=JSON.parse(s);const g=c.generationGraph({prompt:p,model:c.PROFILES.fast,width:512,height:512,seed:1,prefix:'budget-test'});console.log(JSON.stringify(g['4'].inputs.text));});"
        output=subprocess.check_output(['node','-e',script,str(ROOT/'src/comfy.js')],input=json.dumps(accepted['prompt']),text=True)
        self.assertEqual(json.loads(output),BRIEF)

    def test_incomplete_model_response_never_becomes_an_image_prompt(self):
        for reason in ['length',None,'content_filter','unknown']:
            self.assertCode('EXPERT_COMPLETION_INCOMPLETE',lambda reason=reason:validate_completion_finish(reason))
        for reason in ['stop','tool_calls']:self.assertIsNone(validate_completion_finish(reason))

    def test_plan_transport_pressure_removes_optional_before_any_protected_text(self):
        f=(Fragment('user',0,'Exactly three cups.'),Fragment('required-style',2,'Watercolor washes.'),
           Fragment('p3',3,'warm '*900),Fragment('p4',4,'soft '*900))
        r=fit_plan(f,self.counter)
        self.assertEqual(r['removed_ids'],['p4'])
        self.assertEqual(r['preserved_ids'],['user','required-style','p3'])
        self.assertEqual(r['status'],'AUTHOR_REVISION_REQUIRED')

    def test_legacy_request_and_composer_boundaries(self):
        sys.path.insert(0,str(ROOT/'integrations/model-neutral-compatibility'))
        from compatibility import compose_request,CompatibilityError
        for n in [11999,12000]:
            original='x'*n
            self.assertEqual(compose_request({'mode':'auto','free_text':original})[0],original)
        with self.assertRaises(CompatibilityError):compose_request({'mode':'auto','free_text':'x'*12001})
        script="""const r=require(process.argv[1]);let out=[];
for(const n of [3999,4000,4001]){try{const t=r.normalizeTask({operation:'generate',prompt:'A red cup.',source_policy:'auto',preservation:'none'}, {AAG_INVOCATION_PROMPT:'x'.repeat(n),AAG_WORKSPACE_ID:'w',AAG_THREAD_ID:'t',AAG_INVOCATION_UUID:'i',AAG_TURN_ID:'turn'});out.push({n,ok:true})}catch(e){out.push({n,ok:false,code:e.code})}}console.log(JSON.stringify(out));"""
        rows=json.loads(subprocess.check_output(['node','-e',script,str(ROOT/'src/runtime.js')],text=True))
        self.assertEqual([r['ok'] for r in rows],[True,True,False])

if __name__=='__main__':unittest.main()
