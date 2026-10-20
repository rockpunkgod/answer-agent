"""Local exact Copy-label suggestion, never a recipient or input authorization."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from datetime import datetime
import re

from .luna_navigation import ROOT,_source


def read_time_label(record_path,box,*,display_index=1,runner=None):
    """Read only a scoped UI timestamp; never invent a missing year."""
    image,width,height,_=_source(record_path,display_index)
    if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
            or not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height
            or box[2]-box[0]>512 or box[3]-box[1]>128):
        raise ValueError('TIME_LABEL_REGION_INVALID')
    from PIL import Image
    raw=image.read_bytes();digest=sha256(raw).hexdigest()
    directory=ROOT/'data/private/wecom-control-ocr';directory.mkdir(parents=True,exist_ok=True)
    crop=directory/('time-'+sha256((digest+json.dumps(box)).encode()).hexdigest()+'.png')
    size=[(box[2]-box[0])*4,(box[3]-box[1])*4]
    with Image.open(image) as bitmap:bitmap.crop(tuple(box)).resize(tuple(size)).save(crop)
    try:
        result=(runner or subprocess.run)(['powershell.exe','-NoProfile','-File',str(ROOT/'tools/ocr_window.ps1'),
            '-ImagePath',str(crop),'-TimeLabelOnly'],capture_output=True,timeout=15,shell=False,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=json.loads(result.stdout.decode('utf-8-sig'));labels=value.get('time_labels')
        if (result.returncode or value.get('available') is not True or value.get('image_size')!=size
                or not isinstance(labels,list) or len(labels)!=1):raise ValueError()
        label=labels[0]
        normalized=re.sub(r'\s','',label['raw']).replace('：',':')
        match=re.fullmatch(r'(?:(?P<year>\d{4})[-/])?(?P<month>\d{1,2})/(?P<day>\d{1,2})'
                           r'(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})',normalized)
        if not match or normalized!=label.get('normalized'):raise ValueError()
        parts=match.groupdict()
        if any((label.get(k) or None)!=(v or None) for k,v in parts.items()):raise ValueError()
        datetime(int(parts['year'] or 2000),*(int(parts[k]) for k in ('month','day','hour','minute','second')))
    except Exception:
        raise ValueError('TIME_LABEL_NOT_UNIQUE_OR_UNAVAILABLE') from None
    _source(record_path,display_index)
    if sha256(image.read_bytes()).hexdigest()!=digest:raise ValueError('TIME_LABEL_SOURCE_CHANGED')
    return {'raw_original_time_label':label['raw'],'normalized_original_time_label':normalized,
            'components':parts,'source_sent_at':None,
            'time_status':'REQUIRES_YEAR_EVIDENCE' if not parts['year'] else 'REQUIRES_TIME_CONFIRMATION',
            'formal_statistics_eligible':False,'source_record':str(record_path),
            'frame_sha256':digest,'crop_path':str(crop),'crop_sha256':sha256(crop.read_bytes()).hexdigest()}


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
