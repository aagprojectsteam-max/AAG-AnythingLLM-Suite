import copy,hashlib,json,pathlib,sys,unittest
from unittest.mock import Mock,patch
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT.parent/'integrations/model-neutral-compatibility')]
from neutral_context import prepare_context,validate_candidate
from knowledge import Registry
from compiler import CompilerError
from compatibility import compose_request,composer_intent_from_message,CompatibilityError
from server import Application
from prompt_budget import KleinTokenCounter,BudgetError
class NeutralTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.counter=KleinTokenCounter(pathlib.Path('/mnt/data/AI/ComfyUI'))
 def example(self):
  request='Exactly two ceramic cups, one blue and one white, on a plain table. No text.'
  payload={'mode':'advanced','free_text':request,'visual_family':'photography','visual_subfamily':'product','expert_mode':True}
  message,_=compose_request(payload);intent=composer_intent_from_message(message);label=intent['expert_prompt']['style_label']
  plan={'facts':[{'dimension':'count','classification':'USER_REQUIRED','text':'Exactly two ceramic cups','source_text':'Exactly two ceramic cups'}],'fragments':[{'id':'structure','priority':1,'text':request+' Separate both silhouettes with a clear gap and stable contact shadows.'},{'id':'style','priority':2,'text':label+' uses optical focus and realistic ceramic surface response.'}],'conflicts':[]}
  prompt='\n'.join(f['text'] for f in plan['fragments'])
  args={'operation':'generate','prompt':prompt,'source_policy':'auto','preservation':'none','expert_plan':plan}
  return payload,message,intent,args
 def test_missing_runtime_schema_is_structured_expert_failure(self):
  p,m,i,a=self.example();app=object.__new__(Application);app.auth_token='test-only-secret'*3;app.audit=Mock();signed=app._sign_composer_message(m)
  with patch('expert_schema.provider_schema',side_effect=FileNotFoundError('/private/schema.json')):
   with self.assertRaises(CompatibilityError) as error:app.validate_expert({'envelope':signed,'request':p['free_text'],'arguments':a})
  self.assertEqual(error.exception.code,'EXPERT_VALIDATOR_UNAVAILABLE');self.assertNotIn('/private',str(error.exception))
 def test_off_exact_bytes_no_context_no_model_calls(self):
  p={'mode':'advanced','free_text':'One ceramic cup.'};a=compose_request(p);b=compose_request({**p,'expert_mode':False});self.assertEqual(a,b);self.assertNotIn('expert_prompt',a[0])
  with patch('socket.socket',side_effect=AssertionError('network forbidden')):self.assertEqual(compose_request(p),a)
 def test_on_has_one_selected_style_no_provider_metadata(self):
  p,m,i,a=self.example();c=i['expert_prompt'];self.assertEqual(c['selected_style'],'photography/product');self.assertEqual(c['knowledge']['style']['canonical_id'],'photography/product')
  raw=json.dumps(c).lower()
  for banned in ('gemini','google','openai','anthropic','api_key','/mnt/','/home/','provider_selector','base_url'):self.assertNotIn(banned,raw)
  self.assertNotIn('profiles',c['knowledge']);self.assertLess(len(raw.encode()),32000)
 def test_model_execution_not_present(self):
  for file in ['neutral_context.py','compiler.py','knowledge.py']:
   code=(ROOT/file).read_text().lower()
   for banned in ('urllib','requests.post','http.client','localauthor','gemini','openai','anthropic','google'):self.assertNotIn(banned,code)
 def test_approved_native_request_validated_with_real_tokenizer(self):
  p,m,i,a=self.example();result=validate_candidate(p['free_text'],i,a['prompt'],a['expert_plan'],self.counter);self.assertEqual(result['author'],'current-active-chat-llm');self.assertFalse(result['truncated'])
 def test_signed_validation_and_tamper_fail_closed(self):
  p,m,i,a=self.example();app=object.__new__(Application);app.auth_token='test-only-secret'*3;app.audit=Mock();signed=app._sign_composer_message(m)
  result=app.validate_expert({'envelope':signed,'request':p['free_text'],'arguments':a});self.assertEqual(result['results'][0]['prompt'],a['prompt'])
  for changed in [signed.replace('Exactly two','Exactly three'),signed[:-1]+'z']:
   with self.assertRaises(CompatibilityError):app.validate_expert({'envelope':changed,'request':p['free_text'],'arguments':a})
 def test_manual_unknown_not_silently_replaced(self):
  p,m,i,a=self.example();p['visual_subfamily']='not-a-style'
  with self.assertRaises(CompatibilityError):compose_request(p)
 def test_auto_and_manual_resolve_identical_guidance(self):
  p,m,i,a=self.example();manual=i['expert_prompt'];j=copy.deepcopy(i);j['creative_direction']={'visual_family':'auto','visual_subfamily':'auto'};j['knowledge_modules']={'visual_atlas':{'selections':[{'family_id':'photography','subfamily_id':'product'}]}}
  automatic=prepare_context(p['free_text'],j);self.assertEqual(manual['knowledge'],automatic['knowledge'])
 def test_counter_overflow_never_truncated(self):
  p,m,i,a=self.example()
  with self.assertRaises(BudgetError):validate_candidate(p['free_text'],i,a['prompt']+' ceramic'*700,a['expert_plan'],self.counter)
 def test_missing_plan_or_removed_user_request_fails(self):
  p,m,i,a=self.example()
  for plan in [None,{}]:
   with self.assertRaises(CompilerError):validate_candidate(p['free_text'],i,a['prompt'],plan,self.counter)
  self.assertTrue(validate_candidate(p['free_text'],i,'A cup.',a['expert_plan'],self.counter)['semantic_review_required'])
 def test_auto_ui_mode_cannot_silently_ignore_expert(self):
  with self.assertRaises(CompatibilityError):compose_request({'mode':'auto','free_text':'One cup.','expert_mode':True})
 def test_existing_adapter_retains_off_cap_and_active_on_budget(self):
  from compatibility import build_adapter_payload
  for limit in [None,256,2048]:
   payload={'messages':[{'role':'user','content':'One cup.'}]}
   if limit is not None:payload['max_tokens']=limit
   tools=[{'type':'function','function':{'name':'example','description':'Example','parameters':{'type':'object','properties':{},'additionalProperties':False}}}]
   off=build_adapter_payload(payload,tools);on=build_adapter_payload(payload,tools,expert_context=True)
   self.assertEqual(off['max_tokens'],min(limit or 512,512))
   self.assertEqual(on.get('max_tokens'),limit)
 def test_subject_transform_safeguard_is_part_of_final_token_budget(self):
  from neutral_context import SUBJECT_REFERENCE_RULE
  p,m,i,a=self.example();i['operation']='transform';i['preservation']='subject'
  with self.assertRaises(CompilerError):validate_candidate(p['free_text'],i,a['prompt'],a['expert_plan'],self.counter)
  a['expert_plan']['fragments'].append({'id':'source-safeguard','priority':1,'text':SUBJECT_REFERENCE_RULE});a['prompt']+=' '+SUBJECT_REFERENCE_RULE
  result=validate_candidate(p['free_text'],i,a['prompt'],a['expert_plan'],self.counter)
  self.assertEqual(result['tokens_including_template'],self.counter.count(a['prompt']));self.assertEqual(result['prompt'],a['prompt'])
 def test_style_display_case_is_not_a_different_canonical_style(self):
  p,m,i,a=self.example();label=i['expert_prompt']['style_label']
  a['expert_plan']['fragments'][1]['text']=a['expert_plan']['fragments'][1]['text'].replace(label,label.lower());a['prompt']=a['prompt'].replace(label,label.lower())
  result=validate_candidate(p['free_text'],i,a['prompt'],a['expert_plan'],self.counter);self.assertEqual(result['selected_style'],'photography/product');self.assertEqual(result['prompt'],a['prompt'])
  a['expert_plan']['fragments'][1]['text']='Unrelated watercolor treatment.'
  self.assertTrue(validate_candidate(p['free_text'],i,a['prompt'],a['expert_plan'],self.counter)['semantic_review_required'])
if __name__=='__main__':unittest.main()
