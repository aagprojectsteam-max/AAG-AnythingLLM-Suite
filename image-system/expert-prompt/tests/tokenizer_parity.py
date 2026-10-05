"""CPU-only comparison against installed Comfy token IDs, no weight loads."""
import sys,os,json,time,resource,hashlib,random
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'expert-prompt'))
from prompt_budget import KleinTokenCounter
sys.path.insert(0,str(Path(__file__).parent))
from test_prompt_budget import BRIEF
counter=KleinTokenCounter(Path('/mnt/data/AI/ComfyUI'))
start=time.monotonic()
# Counter-only evidence before importing Comfy and its torch dependency.
performance={'rss_before_comfy_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
for _ in range(100):counter.count(BRIEF)
performance['100_counts_seconds']=round(time.monotonic()-start,6)
performance['rss_after_100_counts_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
sys.path.insert(0,'/mnt/data/AI/ComfyUI');sys.argv=['tokenizer-parity','--cpu']
import comfy.options
comfy.options.enable_args_parsing()
from comfy.text_encoders.flux import KleinTokenizer,KleinTokenizer8B
samples=[BRIEF,'', 'café cafe\u0301',r'a \(ceramic\) cup (red:1.2)', 'שלושה ספלים כחולים עם הכיתוב "שלום".', '🫖🎨水彩画', 'line\n\tline', ' red '*1200]
rng=random.Random(493)
words=['light','glaze','反射','שלום','cafe\u0301','🫖','(',')','—','\\(','texture','\n','3','exactly','!']
for _ in range(100):samples.append(' '.join(rng.choices(words,k=rng.randrange(1,100))))
rows=[]
for cls,key in [(KleinTokenizer,'qwen3_4b'),(KleinTokenizer8B,'qwen3_8b')]:
 t=cls()
 for i,p in enumerate(samples):
  expected=[x[0] for x in t.tokenize_with_weights(p,min_length=1)[key][0]]
  actual=counter.token_ids_for_parity(p)
  if expected!=actual:raise AssertionError((key,i,len(expected),len(actual)))
  padded=t.tokenize_with_weights(p)[key][0]
  rows.append({'profile':key,'case':i,'characters':len(p),'utf8_bytes':len(p.encode()),'tokens':len(actual),'padded_tokens':len(padded),'token_ids_equal':True})
result={'status':'PASS','cases':len(rows),'profile_count':2,'prompt_example':{'characters':len(BRIEF),'tokens_including_template':counter.count(BRIEF),'sha256':hashlib.sha256(BRIEF.encode()).hexdigest()},'performance':performance,'rows':rows}
out=ROOT.parent/'evaluation/expert-prompt-mode-20261004/prompt-budget'
(out/'tokenizer-parity.json').write_text(json.dumps(result,indent=2)+'\n')
(out/'expert-prompt-example.txt').write_text(BRIEF)
print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
