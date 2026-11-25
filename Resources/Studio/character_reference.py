"""Derive a head-scale reference from the same full-body photograph."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image


def head_reference(source,output):
    image=Image.open(source).convert('RGB');scale=min(1.,1600/max(image.size))
    small=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
    detector=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
    faces=detector.detectMultiScale(cv2.cvtColor(np.asarray(small),cv2.COLOR_RGB2GRAY),1.1,5,minSize=(40,40))
    candidates=[f for f in faces if f[1]<small.height*.35]
    if not candidates:raise ValueError('Não foi possível localizar o rosto para a geração em duas escalas.')
    x,y,w,h=np.asarray(sorted(candidates,key=lambda f:(f[1],-f[2]*f[3]))[0],dtype=float)/scale
    box=(max(0,int(x-w*.45)),max(0,int(y-h*.75)),min(image.width,int(x+w*1.45)),min(image.height,int(y+h*1.3)))
    Path(output).parent.mkdir(parents=True,exist_ok=True);image.crop(box).save(output)
    return dict(face_box=[x,y,w,h],crop_box=list(box),original_size=list(image.size))
