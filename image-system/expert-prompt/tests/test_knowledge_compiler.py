import copy,json,pathlib,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from knowledge import Registry,KnowledgeError
from compiler import Compiler,CompilerError
from prompt_budget import BudgetError,KleinTokenCounter

class AuthorFixture:
    """Deterministic contract fixture, explicitly not live model quality evidence."""
    def __init__(self,request,style):self.request=request;self.style=style;self.calls=[]
    def __call__(self,instruction,data):
        self.calls.append((instruction,data))
        if instruction.startswith('Classify'):return {'tasks':['product'],'risks':['count','style']}
        if instruction.startswith('Author a professional'):
            return {'facts':[{'dimension':'subjects','classification':'USER_REQUIRED','text':self.request,'source_text':self.request}],
                    'fragments':[{'id':'structure','priority':1,'text':self.request+' Keep every requested object separate, with coherent contact and readable spacing.'},{'id':'style','priority':2,'text':self.style+' treatment uses observed material response and controlled optical focus.'},{'id':'light','priority':3,'text':'A broad soft key reveals fine surface roughness while a weaker fill retains readable shadow detail.'}], 'conflicts':[]}
        if instruction.startswith('Synthesize'):return {'prompt':'\n'.join(f['text'] for f in data['fragments'])}
        if instruction.startswith('Audit'):return {'pass':self.request in data['prompt'] and self.style in data['prompt'],'issues':[],'scores':{k:2 for k in data['rubric']['dimensions']},'not_applicable':{}}
        if instruction.startswith('Choose one appropriate'):return {'family_id':'photography'}
        if instruction.startswith('Choose one exact'):return {'style_id':'product'}
        raise AssertionError(instruction)

class KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=Registry()
    def test_exact_bijection(self):
        r=self.registry;self.assertEqual((len(r.families),len(r.styles)),(28,493));self.assertEqual(r.audit()['status'],'PASS')
    def test_every_exact_manual_resolution(self):
        for cid,s in self.registry.styles.items():self.assertEqual(self.registry.resolve(s['family_id'],s['style_id'])['style']['canonical_id'],cid)
    def test_invalid_mismatched_and_traversal_ids_closed(self):
        for f,s in [('unknown','product'),('photography','holographic'),('../photography','product'),('photography',None)]:
            with self.assertRaises(KnowledgeError):self.registry.resolve(f,s)
    def test_selective_bounded_context_all_493(self):
        for s in self.registry.styles.values():
            c=self.registry.context(s['family_id'],s['style_id'],['product'],['geometry'])
            self.assertEqual(c['style']['canonical_id'],s['canonical_id']);self.assertNotIn('profiles',c)
            self.assertLess(len(json.dumps(c).encode()),32000)
    def test_unrelated_context_closed(self):
        for tasks,risks in [(['unknown'],[]),(['product']*4,[]),([],['count']*9)]:
            with self.assertRaises(KnowledgeError):self.registry.context('photography','product',tasks,risks)
    def test_duplicate_and_taxonomy_drift_closed(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(ROOT/'knowledge',pathlib.Path(d)/'knowledge');p=pathlib.Path(d)/'knowledge/styles.json';data=json.loads(p.read_text());data['profiles'].append(data['profiles'][0]);p.write_text(json.dumps(data))
            with self.assertRaises(KnowledgeError):Registry(p.parent)
    def test_identical_profile_gate(self):
        r=Registry();keys=list(r.styles);r.styles[keys[1]].update({k:r.styles[keys[0]][k] for k in ('definition','construction','failure_prevention')});self.assertEqual(r.audit()['status'],'FAIL')
    def test_no_runtime_benchmark_imports(self):
        source=(ROOT/'knowledge.py').read_text();self.assertNotIn('generation_prompt',source);self.assertNotIn('atlas-manifest',source)
    def test_profiles_validate_schema(self):
        import jsonschema
        schema=json.loads((ROOT/'schemas/style-profile.schema.json').read_text())
        for profile in self.registry.styles.values():jsonschema.validate(profile,schema)

class CompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=Registry();cls.counter=KleinTokenCounter(pathlib.Path('/mnt/data/AI/ComfyUI'))
    def setup_case(self):
        request='Exactly two ceramic cups, one blue and one white, beside each other on a table. No text.'
        style=self.registry.resolve('photography','product')['style']['label'];author=AuthorFixture(request,style)
        intent={'operation':'generate','preservation':'none','creative_direction':{'visual_family':'photography','visual_subfamily':'product'},'semantics':{'explicit_constraints':{}}}
        return request,author,intent,Compiler(author,self.counter,self.registry)
    def test_plan_synthesis_binding_and_exact_invariants(self):
        request,author,intent,c=self.setup_case();result=c.compile(request,intent)
        self.assertIn(request,result['prompt']);self.assertFalse(result['truncated']);self.assertEqual(result['selected_style'],'photography/product');self.assertLessEqual(result['tokens_including_template'],512)
        self.assertEqual(result['selection_method'],'manual');self.assertEqual(result['guidance_ids']['style'],'photography/product')
    def test_auto_uses_same_exact_resolver(self):
        request,author,intent,c=self.setup_case();intent['creative_direction']={'visual_family':'auto','visual_subfamily':'auto'}
        a=c.compile(request,intent);self.assertEqual(a['selected_style'],'photography/product');self.assertEqual(a['selection_method'],'auto-author-selection')
    def test_auto_canonical_retrieval_preserved(self):
        request,author,intent,c=self.setup_case();intent['creative_direction']={'visual_family':'auto','visual_subfamily':'auto'};intent['knowledge_modules']={'visual_atlas':{'selections':[{'family_id':'photography','subfamily_id':'product'}]}}
        result=c.compile(request,intent);self.assertEqual(result['selection_method'],'auto-canonical-retrieval')
    def test_unknown_manual_not_auto_fallback(self):
        request,author,intent,c=self.setup_case();intent['creative_direction']['visual_subfamily']='missing'
        with self.assertRaises(KnowledgeError):c.compile(request,intent)
        self.assertEqual(author.calls,[])
    def test_frozen_identity_and_upscale_closed(self):
        for op,p in [('transform','identity'),('upscale','none')]:
            request,author,intent,c=self.setup_case();intent.update(operation=op,preservation=p)
            with self.assertRaises(CompilerError):c.compile(request,intent)
            self.assertEqual(author.calls,[])
    def test_source_fact_fabrication_closed(self):
        request,author,intent,c=self.setup_case();style=c.registry.resolve('photography','product')['style'];plan=author('Author a professional',{});plan['facts'][0]['source_text']='not in the request'
        with self.assertRaises(CompilerError):c.validate_plan(plan,request,style)
    def test_wrong_style_protected_fragment_closed(self):
        request,author,intent,c=self.setup_case();style=c.registry.resolve('photography','product')['style'];plan=author('Author a professional',{});plan['fragments'][1]['text']='An unrelated style.'
        c.validate_plan(plan,request,style) # Wording is judged by the independent semantic audit.
        def wrong(instruction,data):
            if instruction.startswith('Author a professional'):return plan
            return author(instruction,data)
        c.author=wrong
        with self.assertRaises(CompilerError):c.compile(request,intent)
    def test_user_request_transport_and_token_overflow_closed(self):
        request,author,intent,c=self.setup_case()
        for text in ['x'*4001,'ceramic cup '*600]:
            with self.assertRaises(CompilerError):c.compile(text,intent)
        self.assertEqual(author.calls,[])
    def test_final_truncation_does_not_pass(self):
        request,author,intent,c=self.setup_case()
        def bad(instruction,data):
            if instruction.startswith('Synthesize'):return {'prompt':'A cup.'}
            return author(instruction,data)
        c.author=bad
        with self.assertRaises(CompilerError):c.compile(request,intent)
    def test_independent_review_failure_stops(self):
        request,author,intent,c=self.setup_case()
        def bad(instruction,data):
            if instruction.startswith('Audit'):return {'pass':False,'issues':['count changed'],'scores':{}}
            return author(instruction,data)
        c.author=bad
        with self.assertRaises(CompilerError):c.compile(request,intent)
if __name__=='__main__':unittest.main()
