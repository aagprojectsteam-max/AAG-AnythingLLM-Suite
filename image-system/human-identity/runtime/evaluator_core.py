#!/usr/bin/env python3
"""Phase 7R evaluator core: frozen buffalo_l plus confirmed large-face scale recovery."""
from __future__ import annotations
from dataclasses import dataclass
import cv2, numpy as np

PAD_FACTORS=(1.5,2.0)
MIN_CONFIRMATION_IOU=0.80

@dataclass
class DetectionResult:
    faces:list
    path:str
    confirmations:list[dict]

def _padded(image:np.ndarray,factor:float):
    h,w=image.shape[:2];nh,nw=round(h*factor),round(w*factor)
    canvas=np.full((nh,nw,3),127,dtype=np.uint8);x=(nw-w)//2;y=(nh-h)//2
    canvas[y:y+h,x:x+w]=image
    return canvas,x,y

def _mapped(face,x:int,y:int):
    face.bbox=np.asarray(face.bbox,dtype=np.float32)-np.array([x,y,x,y],dtype=np.float32)
    if getattr(face,'kps',None) is not None:face.kps=np.asarray(face.kps,dtype=np.float32)-np.array([x,y],dtype=np.float32)
    return face

def _iou(a,b):
    x1=max(float(a[0]),float(b[0]));y1=max(float(a[1]),float(b[1]));x2=min(float(a[2]),float(b[2]));y2=min(float(a[3]),float(b[3]));inter=max(0.,x2-x1)*max(0.,y2-y1)
    aa=max(0.,float(a[2]-a[0]))*max(0.,float(a[3]-a[1]));bb=max(0.,float(b[2]-b[0]))*max(0.,float(b[3]-b[1]));return inter/max(1e-12,aa+bb-inter)

def detect_faces(app,image:np.ndarray)->DetectionResult:
    """Preserve canonical behavior; recover only a face confirmed at two padded scales."""
    direct=app.get(image)
    if direct:return DetectionResult(direct,'canonical-direct',[])
    probes=[]
    for factor in PAD_FACTORS:
        canvas,x,y=_padded(image,factor);faces=[_mapped(face,x,y) for face in app.get(canvas)]
        probes.append({'factor':factor,'faces':faces})
    counts=[len(p['faces']) for p in probes]
    public=[{'factor':p['factor'],'face_count':len(p['faces'])} for p in probes]
    if any(count>1 for count in counts):
        faces=max(probes,key=lambda p:len(p['faces']))['faces'];return DetectionResult(faces,'confirmed-padded-multiple',public)
    if counts!=[1,1]:return DetectionResult([],'padded-unconfirmed',public)
    left,right=probes[0]['faces'][0],probes[1]['faces'][0];iou=_iou(left.bbox,right.bbox);public[0]['cross_scale_iou']=iou;public[1]['cross_scale_iou']=iou
    if iou<MIN_CONFIRMATION_IOU:return DetectionResult([],'padded-inconsistent',public)
    return DetectionResult([left],'confirmed-padded-large-face',public)

