"""Authored translation fixtures; semantic judgments are tested at the model boundary in JS."""
import copy,json,pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT.parent/'integrations/model-neutral-compatibility')]
from neutral_context import validate_candidate,prepare_context,INSTRUCTION
from prompt_budget import KleinTokenCounter,BudgetError
from compiler import CompilerError
from compatibility import compose_request,composer_intent_from_message

CASES=[
 ('תעשה תמונה של בית מגורים בתוך מערה בטבע', [('subjects','בית מגורים','A lived-in home dwelling'),('relationships_contact','בתוך מערה','inside a cave'),('environment','בטבע','in a natural setting')], 'A lived-in home dwelling inside a cave in a natural setting. Rock walls enclose the rooms; trees grow around the cave entrance.'),
 ('Please make an image of three red balls on a white table. No people.', [('count','three red balls','Exactly three red balls'),('relationships_contact','on a white table','rest on a white table'),('exclusions','No people','No people')], 'Exactly three red balls rest on a white table. No people appear.'),
 ('שלושה כדורים אדומים על שולחן לבן', [('count','שלושה כדורים אדומים','בדיוק שלושה כדורים אדומים'),('relationships_contact','על שולחן לבן','על שולחן לבן')], 'בדיוק שלושה כדורים אדומים מונחים על שולחן לבן, נפרדים וברורים.'),
 ('Two blue cups ליד חלון; no logos. השלט אומר "שלום".', [('count','Two blue cups','Exactly two blue cups'),('environment','ליד חלון','beside a window'),('exclusions','no logos','No logos'),('text_requirements','"שלום"','A sign reads "שלום"')], 'Exactly two blue cups stand beside a window. A sign reads "שלום". No logos appear.'),
 ('שלושה כדורים אדומים על שולחן לבן, ללא אנשים', [('count','שלושה כדורים אדומים','Exactly three red balls'),('relationships_contact','על שולחן לבן','on a white table'),('exclusions','ללא אנשים','No people')], 'Exactly three red balls rest on a white table. No people appear.'),
 ('A child holds a yellow kite; no birds.', [('subjects','A child','One child'),('actions_poses','holds a yellow kite','holds a yellow kite'),('exclusions','no birds','No birds')], 'One child holds a yellow kite by its frame. No birds appear.'),
]
def fixture(case=0,automatic=False):
 request,rows,scene=CASES[case]
 payload={'mode':'advanced','free_text':request,'expert_mode':True,'visual_family':'auto' if automatic else 'photography','visual_subfamily':'auto' if automatic else 'photorealistic'}
 message,_=compose_request(payload);intent=composer_intent_from_message(message);label=intent['expert_prompt']['style_label']
 plan={'facts':[dict(dimension=d,classification='USER_REQUIRED',source_text=s,text=t) for d,s,t in rows], 'fragments':[{'id':'scene','priority':1,'text':scene},{'id':'style','priority':2,'text':label+' treatment with credible depth, natural material textures, coherent shadows and optical focus.'}],'conflicts':[]}
 prompt='\n'.join(f['text'] for f in plan['fragments'])
 return request,intent,prompt,plan,message
class SelfContainedTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.counter=KleinTokenCounter(pathlib.Path('/mnt/data/AI/ComfyUI'))
 def test_translated_and_same_language_requirements_protected_without_raw_prefix(self):
  for n in range(len(CASES)):
   for automatic in [True,False]:
    request,intent,prompt,plan,_=fixture(n,automatic);r=validate_candidate(request,intent,prompt,plan,self.counter)
    self.assertEqual(r['prompt'],prompt);self.assertEqual(r['authoring_contract'],'self-contained.v2');self.assertTrue(r['semantic_review_required']);self.assertFalse(prompt.startswith(request));self.assertLessEqual(r['tokens_including_template'],512)
    for f in plan['facts']:self.assertIn(f['text'],r['prompt'])
 def test_dropped_fact_with_raw_source_still_requires_semantic_review(self):
  for n in range(len(CASES)):
   request,intent,prompt,plan,_=fixture(n)
   for fact in plan['facts']:
    bad=copy.deepcopy(plan);bad['fragments'][0]['text']=bad['fragments'][0]['text'].replace(fact['text'],'REMOVED')
    self.assertTrue(validate_candidate(request,intent,request+'\n'+prompt,bad,self.counter)['semantic_review_required']) # Runtime semantic review rejects this; local validation never claims PASS.
 def test_dropped_requirements_cannot_claim_semantic_pass_at_structural_boundary(self):
  for n in range(len(CASES)):
   request,intent,prompt,plan,_=fixture(n)
   for fact in plan['facts']:
    self.assertTrue(validate_candidate(request,intent,prompt.replace(fact['text'],'REMOVED'),plan,self.counter)['semantic_review_required']) # Every mutation is rejected at the JS engine boundary.
 def test_fabricated_source_and_missing_facts_fail_closed(self):
  r,i,p,plan,_=fixture();plan['facts'][0]['source_text']='unrelated'
  with self.assertRaises(CompilerError):validate_candidate(r,i,p,plan,self.counter)
  plan['facts']=[]
  with self.assertRaises(CompilerError):validate_candidate(r,i,p,plan,self.counter)
 def test_context_contract_has_no_automatic_source_obligation(self):
  self.assertNotIn('Preserve the full original request verbatim',INSTRUCTION);self.assertIn('self-contained',INSTRUCTION)
  self.assertIn('exact',INSTRUCTION);self.assertIn('512',INSTRUCTION)
 def test_boundary_final_512_passes_513_fails_without_truncation(self):
  r,i,p,plan,_=fixture()
  while self.counter.count(p)<512:p+=' tree'
  self.assertEqual(self.counter.count(p),512);self.assertEqual(validate_candidate(r,i,p,plan,self.counter)['prompt'],p)
  with self.assertRaises(BudgetError) as e:validate_candidate(r,i,p+' tree',plan,self.counter)
  self.assertEqual(e.exception.code,'EXPERT_TOKEN_BUDGET_EXCEEDED')
 def test_source_tokens_are_not_double_charged_as_engine_prompt(self):
  r,i,p,plan,_=fixture();res=validate_candidate(r,i,p,plan,self.counter)
  self.assertEqual(res['tokens_including_template'],self.counter.count(p));self.assertNotIn('original-request',res['protected_fragment_ids'])
 def test_long_useful_prose_over_1800_and_source_4000_bound_unchanged(self):
  r,i,p,plan,_=fixture();p+=' '+('Soft directional illumination reveals weathered mineral surfaces and subtle natural variations. '*20)
  self.assertGreater(len(p),1800);self.assertLessEqual(self.counter.count(p),512);self.assertEqual(validate_candidate(r,i,p,plan,self.counter)['prompt'],p)
  with self.assertRaises(CompilerError):prepare_context('x'*4001,i)
if __name__=='__main__':unittest.main()
