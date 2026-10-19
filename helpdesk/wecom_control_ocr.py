"""Local exact Copy-label suggestion, never a recipient or input authorization."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from .luna_navigation import ROOT,_source


def locate_copy(record_path,search_box,*,display_index=1,runner=None):
    # Reuse the native journal/display/freshness validator without calling Luna.
    image,width,height,region=_source(record_path,display_index)
    if (not isinstance(search_box,list) or len(search_box)!=4 or
            any(type(v) is not int for v in search_box)):
        raise ValueError('COPY_OCR_SEARCH_REGION_INVALID')
    left,top,right,bottom=search_box
    if not (0<=left<right<=width and 0<=top<bottom<=height and right-left<=512 and bottom-top<=256):
        raise ValueError('COPY_OCR_SEARCH_REGION_INVALID')
    from PIL import Image
    raw=image.read_bytes();digest=sha256(raw).hexdigest()
    directory=ROOT/'data/private/wecom-control-ocr';directory.mkdir(parents=True,exist_ok=True)
    key=sha256((digest+json.dumps(search_box)).encode()).hexdigest()
    crop=directory/(key+'.png')
    with Image.open(image) as bitmap:
        bitmap.crop(tuple(search_box)).resize(((right-left)*4,(bottom-top)*4)).save(crop)
    try:
        result=(runner or subprocess.run)(['powershell.exe','-NoProfile','-File',str(ROOT/'tools/ocr_window.ps1'),
            '-ImagePath',str(crop),'-CopyControlOnly'],capture_output=True,timeout=15,shell=False,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=json.loads(result.stdout.decode('utf-8-sig'))
        boxes=value.get('copy_control_boxes')
        if (result.returncode or value.get('available') is not True or value.get('control')!='copy_menu'
                or value.get('image_size')!=[(right-left)*4,(bottom-top)*4]
                or not isinstance(boxes,list) or len(boxes)!=1):
            raise ValueError()
        box=boxes[0]
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) for v in box)
                or not 0<=box[0]<box[2]<=(right-left)*4 or not 0<=box[1]<box[3]<=(bottom-top)*4):
            raise ValueError()
    except Exception:
        raise ValueError('COPY_OCR_NOT_UNIQUE_OR_UNAVAILABLE') from None
    _source(record_path,display_index)
    if sha256(image.read_bytes()).hexdigest()!=digest:raise ValueError('COPY_OCR_SOURCE_CHANGED')
    x=left+(box[0]+box[2])/8;y=top+(box[1]+box[3])/8
    dl,dt,dr,db=region
    return {'control':'copy_menu','source':'WINDOWS_OCR','authorizes_input':False,
            'source_record':str(Path(record_path).resolve()),'image_sha256':digest,
            'search_box':search_box,'loc':[dl+int(x*(dr-dl)/width),dt+int(y*(db-dt)/height)]}
