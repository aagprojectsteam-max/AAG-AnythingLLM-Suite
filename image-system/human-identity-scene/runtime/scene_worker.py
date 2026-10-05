#!/usr/bin/env python3
from __future__ import annotations
import argparse,gc,hashlib,json,os,resource,sys,time,traceback
from pathlib import Path
ROOT=Path('/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System');P7=ROOT/'image-agent/phase-7';RUNTIME_ROOT=Path(__file__).resolve().parents[1];STATE_ROOT=Path('/mnt/data/AI/Apps/AnythingLLM/storage/aag-human-identity-scene-state');OUTPUT_ROOT=Path('/mnt/data/AI/Outputs/.aag-human-identity-scene-private');ASSET=P7/'candidate/assets';SOURCE=ASSET/'source';sys.path.insert(0,str(SOURCE))
PULID=ASSET/'checkpoints/pulid_v1.1.safetensors';BASE=ASSET/'checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors';CONFIG=ASSET/'sdxl-config';TOKEN=ASSET/'tokenizers';EVA=ASSET/'vision/open_clip_model.safetensors';MODELS=ASSET/'insightface';FACEX=ASSET/'facexlib'
EXPECTED={PULID:('4cb8ceec1078e0165399b88332ab3c5971619111b8e1730e6bae64144aabae41',984405232),BASE:('c9e3e68f89b8e38689e1097d4be4573cf308de4e3fd044c64ca697bdb4aa8bca',7105348188),EVA:('f753bca0e8327f77e8845b0af2510d599c3e4614237007b48078c791f2cf391c',856239456)}
NEG='flaws in the eyes, flaws in the face, flaws, lowres, non-HDRi, low quality, worst quality,artifacts noise, text, watermark, glitch, deformed, mutated, ugly, disfigured, hands, low resolution, partially rendered objects,  deformed or partially rendered eyes, deformed, deformed eyeballs, cross-eyed,blurry'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8<<20),b''):h.update(b)
 return h.hexdigest()
def native_key(k):return k.removeprefix('visual.trunk.').replace('.attn.q_proj.bias','.attn.q_bias').replace('.attn.v_proj.bias','.attn.v_bias').replace('.attn.norm.','.attn.inner_attn_ln.').replace('.mlp.fc1_g.','.mlp.w1.').replace('.mlp.fc1_x.','.mlp.w2.').replace('.mlp.norm.','.mlp.ffn_ln.').replace('.mlp.fc2.','.mlp.w3.')
class Cancelled(RuntimeError):pass
def main():
 a=argparse.ArgumentParser();a.add_argument('--request',required=True);a.add_argument('--report',required=True);a.add_argument('--output',required=True);a.add_argument('--cancel-file',required=True);a.add_argument('--stage-file',required=True);args=a.parse_args()
 request_path=Path(args.request).resolve();report=Path(args.report).resolve();output=Path(args.output).resolve();cancel=Path(args.cancel_file).resolve();stage_file=Path(args.stage_file).resolve();e=json.loads(request_path.read_text());partial=output.with_name('.'+output.name+'.partial.png')
 if STATE_ROOT not in report.parents or OUTPUT_ROOT not in output.parents:raise RuntimeError('production identity path boundary violation')
 contract_family,contract_identity='structured-scene-c','dynamic-scene'
 width=int(e['width']);height=int(e['height']);steps=int(e.get('steps',25));cfg=float(e.get('guidance_scale',7.0));id_scale=float(e.get('id_scale',0.8))
 def check_cancel():
  if cancel.exists():raise Cancelled('candidate request cancelled')
 def stage(value):
  tmp=stage_file.with_name('.'+stage_file.name+'.partial');tmp.write_text(json.dumps({'stage':value,'at':time.time()}));tmp.replace(stage_file)
 r={'schema_version':'aag.human-identity.scene.worker-report.v1','status':'FAIL','request_id':e['request_id'],'release':'0.9.0-preview.5','contract_id':'structured-scene-c','scene_contract_sha256':e['scene_contract_sha256'],'contract_b_unchanged_sha256':'d362463e47bed1622b52f7e928e07b92634133810d69785c7ff61bf0bad5e0b4','route':'pulid-v1.1-juggernaut-xl-v9-single-original-scene','reference_mode':'single-original','prompt_contract_id':contract_family,'prompt_contract_identity':contract_identity,'prompt':e['prompt'],'prompt_sha256':e['prompt_sha256'],'scene_profile':e['scene_profile'],'negative_prompt':NEG,'seed':e['seed'],'steps':steps,'guidance_scale':cfg,'id_scale':id_scale,'num_zero':20,'orthogonalization':'ORTHO_v2','height':height,'width':width,'original_pickle_opened':False,'runtime_network_expected':False};pipe=eva=idformer=helper=app=None;start=time.monotonic()
 try:
  if report.exists() or output.exists() or partial.exists():raise FileExistsError('refusing existing evidence/output')
  if os.environ.get('HF_HUB_OFFLINE')!='1' or os.environ.get('ORT_DISABLE_TELEMETRY')!='1':raise RuntimeError('offline guards required')
  if (width,height) not in {(1152,896),(896,1152)} or steps!=25 or id_scale!=1.2 or cfg!=7.0 or e.get('operation')!='transform':raise RuntimeError('scene production recipe mismatch')
  if hashlib.sha256(e['prompt'].encode()).hexdigest()!=e.get('prompt_sha256') or not 20<=len(e['prompt'])<=700:raise RuntimeError('scene prompt integrity mismatch')
  check_cancel()
  stage('LOADING_IDENTITY_RUNTIME')
  for asset,(digest,size) in EXPECTED.items():
   st=asset.lstat()
   if not st.st_mode or asset.is_symlink() or st.st_size!=size or sha(asset)!=digest:raise RuntimeError(f'candidate asset mismatch: {asset.name}')
  import cv2,numpy as np,torch
  from PIL import Image
  from basicsr.utils import img2tensor
  from diffusers import StableDiffusionXLPipeline
  from eva_clip import create_model_and_transforms
  from eva_clip.constants import OPENAI_DATASET_MEAN,OPENAI_DATASET_STD
  from facexlib.parsing import init_parsing_model
  from facexlib.utils.face_restoration_helper import FaceRestoreHelper
  from insightface.app import FaceAnalysis
  from pulid.encoders_transformer import IDFormer
  from pulid.utils import resize_numpy_image_long,sample_dpmpp_2m
  from safetensors.torch import load_file
  from torchvision.transforms import InterpolationMode
  from torchvision.transforms.functional import normalize,resize
  from transformers import CLIPTokenizer
  import pulid.attention_processor as ap
  from pulid.attention_processor import AttnProcessor2_0,IDAttnProcessor2_0
  dev=torch.device('xpu:0');state=load_file(str(PULID),device='cpu');ids={k[11:]:v for k,v in state.items() if k.startswith('id_adapter.')};attns={k[23:]:v for k,v in state.items() if k.startswith('id_adapter_attn_layers.')}
  stage('PREPROCESSING_REFERENCE')
  paths=[Path(e['reference_path'])]
  app=FaceAnalysis(name='antelopev2',root=str(MODELS),providers=['CPUExecutionProvider'],allowed_modules=['detection','recognition']);app.prepare(ctx_id=-1,det_size=(640,640))
  from unittest.mock import patch
  with patch('facexlib.utils.face_restoration_helper.init_parsing_model',return_value=None):helper=FaceRestoreHelper(upscale_factor=1,face_size=512,crop_ratio=(1,1),det_model='retinaface_resnet50',save_ext='png',device='cpu',model_rootpath=str(FACEX))
  helper.face_parse=init_parsing_model(model_name='bisenet',device='cpu',model_rootpath=str(FACEX));raw=load_file(str(EVA),device='cpu');mapped={native_key(k):v for k,v in raw.items() if k.startswith('visual.trunk.')};eva,_,_=create_model_and_transforms('EVA02-CLIP-L-14-336',None,precision='fp32',force_custom_clip=True,device='cpu');eva=eva.visual.eval();inc=eva.load_state_dict(mapped,strict=False);nonrope=[k for k in inc.missing_keys if 'freqs_cos' not in k and 'freqs_sin' not in k]
  if inc.unexpected_keys or nonrope or len(mapped)!=512:
   raise RuntimeError('EVA bridge mismatch')
  mean=getattr(eva,'image_mean',OPENAI_DATASET_MEAN);std=getattr(eva,'image_std',OPENAI_DATASET_STD);mean=mean if isinstance(mean,(list,tuple)) else (mean,)*3;std=std if isinstance(std,(list,tuple)) else (std,)*3;eva=eva.to(dev,dtype=torch.float32);idformer=IDFormer().float().eval();lr=idformer.load_state_dict(ids,strict=True)
  if lr.missing_keys or lr.unexpected_keys:
   raise RuntimeError('IDFormer mismatch')
  idformer=idformer.to(dev);conds=[];hidden_sets=[];refs=[]
  for path in paths:
   check_cancel();arr=resize_numpy_image_long(np.asarray(Image.open(path).convert('RGB')),1024);bgr=cv2.cvtColor(arr,cv2.COLOR_RGB2BGR);faces=app.get(bgr)
   if len(faces)!=1:raise RuntimeError(f'worker reference face count changed: {len(faces)}')
   face=faces[0];ante=np.asarray(face.embedding,dtype=np.float32).reshape(1,512);helper.clean_all();helper.read_image(bgr);helper.get_face_landmarks_5(only_center_face=True);helper.align_warp_face()
   if len(helper.cropped_faces)!=1:
    raise RuntimeError('facexlib alignment failed')
   aligned=helper.cropped_faces[0];inp=img2tensor(aligned,bgr2rgb=True).unsqueeze(0)/255.;parsed=helper.face_parse(normalize(inp,[.485,.456,.406],[.229,.224,.225]))[0].argmax(dim=1,keepdim=True);bg=sum(parsed==i for i in [0,16,18,7,8,9,14,15]).bool();gray=(.299*inp[:,0:1]+.587*inp[:,1:2]+.114*inp[:,2:3]).repeat(1,3,1,1);x=normalize(resize(torch.where(bg,torch.ones_like(inp),gray),eva.image_size,InterpolationMode.BICUBIC),mean,std).to(dev)
   with torch.inference_mode():vit,hidden=eva(x,return_all_features=False,return_hidden=True,shuffle=False)
   vit=vit/torch.norm(vit,2,1,True);conds.append(torch.cat([torch.from_numpy(ante).to(dev),vit],dim=-1));hidden_sets.append(hidden);refs.append({'path':str(path),'sha256':sha(path),'face_count':len(faces),'resized_shape':list(arr.shape),'selected_bbox':None if face is None else [float(v) for v in face.bbox]})
  cond=torch.stack(conds,dim=1);hidden=list(hidden_sets[0])
  for more in hidden_sets[1:]:hidden=[torch.cat([x,y],dim=1) for x,y in zip(hidden,more)]
  uncond=torch.zeros_like(conds[0]);unhidden=[torch.zeros_like(x) for x in hidden_sets[0]]
  with torch.inference_mode():id_token=idformer(cond,hidden).cpu();un_token=idformer(uncond,unhidden).cpu()
  if not torch.isfinite(id_token).all() or not torch.isfinite(un_token).all():raise RuntimeError('nonfinite identity token')
  r['references']=[{'sha256':x['sha256'],'face_count':x['face_count'],'resized_shape':x['resized_shape'],'selected_bbox':x['selected_bbox']} for x in refs];r['conditioning']={'id_shape':list(id_token.shape),'uncond_shape':list(un_token.shape),'finite':True,'raw_embeddings_written':False};del eva,idformer,helper,app,raw,mapped,state,ids;eva=idformer=helper=app=None;gc.collect();torch.xpu.empty_cache();check_cancel()
  tok=CLIPTokenizer.from_pretrained(str(TOKEN/'tokenizer'),local_files_only=True);tok2=CLIPTokenizer.from_pretrained(str(TOKEN/'tokenizer_2'),local_files_only=True);pipe=StableDiffusionXLPipeline.from_single_file(str(BASE),config=str(CONFIG),tokenizer=tok,tokenizer_2=tok2,local_files_only=True,torch_dtype=torch.bfloat16);procs={};cross=[]
  for n in pipe.unet.attn_processors:
   cd=None if n.endswith('attn1.processor') else pipe.unet.config.cross_attention_dim
   if n.startswith('mid_block'):w=pipe.unet.config.block_out_channels[-1]
   elif n.startswith('up_blocks'):w=list(reversed(pipe.unet.config.block_out_channels))[int(n[len('up_blocks.')])]
   else:w=pipe.unet.config.block_out_channels[int(n[len('down_blocks.')])]
   procs[n]=AttnProcessor2_0() if cd is None else IDAttnProcessor2_0(w,cd)
   if cd is not None:cross.append(n)
  pipe.unet.set_attn_processor(procs);al=torch.nn.ModuleList(pipe.unet.attn_processors.values()).load_state_dict(attns,strict=True)
  if al.missing_keys or al.unexpected_keys or len(cross)!=70:
   raise RuntimeError('attention strict load mismatch')
  ap.NUM_ZERO=20;ap.ORTHO=False;ap.ORTHO_v2=True;pipe.to(dev,dtype=torch.bfloat16);pipe.set_progress_bar_config(disable=True);id_token=id_token.to(dev,dtype=torch.bfloat16);un_token=un_token.to(dev,dtype=torch.bfloat16)
  betas=torch.linspace(.00085**.5,.012**.5,1000,dtype=torch.float64)**2;ac=torch.cumprod(1-betas,0).float();bs=((1-ac)/ac)**.5;logs=bs.log();ramp=torch.linspace(0,1,steps);sigmas=(bs[-1]**(1/7)+ramp*(bs[0]**(1/7)-bs[-1]**(1/7)))**7;sigmas=torch.cat([sigmas,sigmas.new_zeros(1)]).to(dev);pe,ne,pp,npool=pipe.encode_prompt(prompt=e['prompt'],negative_prompt=NEG);tids=torch.tensor([[height,width,0,0,height,width]],device=dev,dtype=torch.bfloat16);extra={'cfg_scale':cfg,'positive':{'encoder_hidden_states':pe,'added_cond_kwargs':{'text_embeds':pp,'time_ids':tids},'cross_attention_kwargs':{'id_embedding':id_token,'id_scale':id_scale}},'negative':{'encoder_hidden_states':ne,'added_cond_kwargs':{'text_embeds':npool,'time_ids':tids},'cross_attention_kwargs':{'id_embedding':un_token,'id_scale':id_scale}}}
  class Model:
   def __call__(self,x,sigma,**kw):
    check_cancel()
    z=x/(sigma[:,None,None,None]**2+1.)**.5;t=(sigma.log().to(logs.device)-logs[:,None]).abs().argmin(0).view(sigma.shape).to(dev);p=pipe.unet(z,t,return_dict=False,**kw['positive'])[0];n=pipe.unet(z,t,return_dict=False,**kw['negative'])[0];return x-(n+kw['cfg_scale']*(p-n))*sigma[:,None,None,None]
  noise=torch.randn((1,4,height//8,width//8),generator=torch.Generator(device='cpu').manual_seed(e['seed'])).to(dev,dtype=torch.bfloat16);torch.xpu.reset_peak_memory_stats(dev);t0=time.monotonic()
  stage('GENERATING')
  with torch.inference_mode():lat=sample_dpmpp_2m(Model(),noise*sigmas[0].to(noise),sigmas,extra_args=extra,disable=True);img=pipe.vae.decode(lat.to(pipe.vae.dtype)/pipe.vae.config.scaling_factor,return_dict=False)[0];image=pipe.image_processor.postprocess(img,output_type='pil')[0].convert('RGB')
  torch.xpu.synchronize();r['inference_seconds']=time.monotonic()-t0;check_cancel();image.save(partial,format='PNG',optimize=False)
  with partial.open('rb+') as f:f.flush();os.fsync(f.fileno())
  os.chmod(partial,0o640);os.replace(partial,output);r['artifact']={'path':str(output),'bytes':output.stat().st_size,'sha256':sha(output),'pixel_sha256_rgb8':hashlib.sha256(image.tobytes()).hexdigest(),'format':'PNG','dimensions':[width,height],'mode':'RGB','validated':True};r['xpu_memory']={'peak_allocated':int(torch.xpu.max_memory_allocated(dev)),'peak_reserved':int(torch.xpu.max_memory_reserved(dev))};r['strict_consumption']={'id_adapter':172,'attention_tensors':140,'cross_attention_processors':70};r['status']='PASS'
  del lat,img,image,noise,id_token,un_token,pe,ne,pp,npool,tids,extra,sigmas,logs,bs,ac,betas,ramp,cond,hidden,uncond,unhidden,conds,hidden_sets,procs,al,attns,Model
  pipe=None;gc.collect();torch.xpu.synchronize();torch.xpu.empty_cache()
 except Exception as ex:r.update(status='CANCELLED' if isinstance(ex,Cancelled) else 'FAIL',error_type=type(ex).__name__,error=str(ex),traceback=traceback.format_exc())
 finally:
  r['total_seconds']=time.monotonic()-start;r['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
  if pipe is not None:del pipe
  gc.collect()
  try:
   import torch
   if torch.xpu.is_available():torch.xpu.synchronize();torch.xpu.empty_cache();r['xpu_memory_after_cleanup']={'allocated':int(torch.xpu.memory_allocated()),'reserved':int(torch.xpu.memory_reserved())}
  except Exception:pass
  if r['status']!='PASS' and partial.exists():partial.unlink()
  report.parent.mkdir(parents=True,exist_ok=True)
  tmp=report.with_name('.'+report.name+'.partial');tmp.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');tmp.chmod(0o440);tmp.replace(report)
 print(json.dumps({'request_id':e['request_id'],'status':r['status'],'error':r.get('error')}));return 0 if r['status']=='PASS' else (2 if r['status']=='CANCELLED' else 1)
if __name__=='__main__':raise SystemExit(main())
