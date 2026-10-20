"""Local candidates in a reviewed incoming bubble lane, not platform messages.

No OCR of student text and no automatic sender/time/ACK inference. Clipboard
readback and the existing foreground/region guards must verify every candidate.
"""
from collections import deque
from datetime import datetime
import re


def advance_visible_view(previous, fingerprints, *, scope_key, started_at, completed_at,
                         max_gap_seconds=300, reset=False):
    """A durable visible-text boundary, never proof of original send time.

    First views establish history. Only unique suffix/prefix continuity yields
    appended candidates. A lost boundary stays paused until explicit rebaseline.
    """
    def stamp(value):
        result=datetime.fromisoformat(value)
        if result.utcoffset() is None:raise ValueError('VISIBLE_VIEW_CLOCK_INVALID')
        return result
    if (not isinstance(scope_key,str) or not scope_key
            or not isinstance(fingerprints,list) or not 1<=len(fingerprints)<=100
            or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in fingerprints)
            or type(reset) is not bool
            or type(max_gap_seconds) is not int or not 1<=max_gap_seconds<=900):
        raise ValueError('VISIBLE_VIEW_INPUT_INVALID')
    start,end=stamp(started_at),stamp(completed_at)
    if end<start:raise ValueError('VISIBLE_VIEW_CLOCK_INVALID')
    result={'status':'VIEW_REQUIRES_REVIEW','candidate_indices':[],
            'new_message_verified':False,'automatic_ack_eligible':False}
    if previous is None or reset:
        state={'version':1,'scope_key':scope_key,'fingerprints':list(fingerprints),
               'started_at':started_at,'completed_at':completed_at,'review_required':False}
        return state,{**result,'status':'BASELINE_ESTABLISHED'}
    if (not isinstance(previous,dict) or type(previous.get('version')) is not int or previous.get('version')!=1
            or not isinstance(previous.get('fingerprints'),list)
            or not 1<=len(previous['fingerprints'])<=100
            or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in previous['fingerprints'])
            or type(previous.get('review_required')) is not bool):
        return None,{**result,'reason':'BASELINE_CORRUPT'}
    reason=None
    try:
        old_start,old_end=stamp(previous['started_at']),stamp(previous['completed_at'])
        if old_end<old_start or start<old_end:reason='CLOCK_MOVED_BACKWARD'
        elif (end-old_start).total_seconds()>max_gap_seconds:reason='VIEW_GAP_EXCEEDED'
    except (ValueError,KeyError,TypeError):
        reason='BASELINE_CLOCK_INVALID'
    if previous.get('scope_key')!=scope_key:reason='VIEW_SCOPE_CHANGED'
    if previous['review_required']:reason=previous.get('reason','BOUNDARY_REVIEW_REQUIRED')
    old=previous['fingerprints']
    matches=[size for size in range(1,min(len(old),len(fingerprints))+1)
             if old[-size:]==fingerprints[:size]]
    if reason is None and fingerprints!=old:
        if len(matches)!=1:reason='VIEW_CONTINUITY_AMBIGUOUS_OR_LOST'
        elif matches[0]==len(fingerprints):reason='VIEW_MOVED_BACKWARD_OR_TRUNCATED'
        elif (any(v in old for v in fingerprints[matches[0]:])
              or len(set(fingerprints[matches[0]:]))!=len(fingerprints[matches[0]:])):
            reason='REPEATED_CONTENT_REQUIRES_REVIEW'
    if reason:
        return {**previous,'review_required':True,'reason':reason},{**result,'reason':reason}
    state={**previous,'fingerprints':list(fingerprints),'started_at':started_at,
           'completed_at':completed_at}
    return state,{**result,'status':'VIEW_UNCHANGED' if fingerprints==old else 'APPENDED_VISIBLE_CANDIDATES',
                  'candidate_indices':[] if fingerprints==old else list(range(matches[0],len(fingerprints))),
                  'observed_after':previous['started_at'],'observed_by':completed_at}


def match_sender_template(image, body, templates):
    """Match reviewed adjacent name pixels at this bubble, never infer continuation."""
    from .mcp_test_delivery import pixel_hash
    if not isinstance(templates,list) or len(templates)>200:
        raise ValueError('SENDER_TEMPLATES_INVALID')
    def rectangle(box):
        return (isinstance(box,list) and len(box)==4 and all(type(v) is int for v in box)
                and 0<=box[0]<box[2]<=image.width and 0<=box[1]<box[3]<=image.height)
    if not rectangle(body):raise ValueError('SENDER_TEMPLATE_BODY_INVALID')
    found={}
    for template in templates:
        if not isinstance(template,dict):raise ValueError('SENDER_TEMPLATES_INVALID')
        old=template.get('region_box');sender=template.get('sender_name')
        if not isinstance(sender,dict) or sender.get('reviewed') is not True:
            raise ValueError('SENDER_TEMPLATE_NOT_REVIEWED')
        box=sender.get('region_box');name=sender.get('display_name');digest=sender.get('region_hash')
        if (not rectangle(old) or not rectangle(box)
                or not isinstance(name,str) or not name.strip() or any(c in name for c in '\r\n\x00')
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
                or box[2]-box[0]>512 or box[3]-box[1]>48
                or box[0]<old[0]-16 or box[2]>old[2]+256
                or box[1]<old[1]-48 or box[3]>old[1]):
            raise ValueError('SENDER_TEMPLATE_INVALID')
        dx,dy=body[0]-old[0],body[1]-old[1]
        # UI line rounding may shift the name baseline by one pixel between
        # bubbles. Only exact approved pixels within this bounded header count.
        for adjustment in range(-2,3):
            current=[v+(dx if i%2==0 else dy+adjustment) for i,v in enumerate(box)]
            if (not rectangle(current) or current[2]>body[2]+256
                    or current[1]<body[1]-48 or current[3]>body[1]):continue
            crop=image.crop(current).convert('RGB')
            # A solid empty header is not a usable name template.
            if all(low==high for low,high in crop.getextrema()):continue
            if pixel_hash(image,current)==digest:
                key=(name.strip(),tuple(current),digest)
                found[key]={**sender,'region_box':current,'verification':'EXACT_REVIEWED_NAME_TEMPLATE'}
    if len(found)>1:raise ValueError('AMBIGUOUS_SENDER_NAME_TEMPLATE')
    return next(iter(found.values()),None)


def time_label_identity(label):
    """Auxiliary occurrence hint only; missing year stays missing."""
    if not isinstance(label,str):return None
    match=re.fullmatch(r'(?:(\d{4})[-/])?(\d{1,2})/(\d{1,2})(\d{2}):(\d{2}):(\d{2})',label)
    if not match:return None
    values=tuple(int(v) if v is not None else None for v in match.groups())
    try:datetime(2000 if values[0] is None else values[0],*values[1:])
    except ValueError:return None
    return values


def incoming_bubbles(image, config):
    box=config.get('viewport');anchor=config.get('incoming_left');color=config.get('bubble_color')
    tolerance=config.get('color_tolerance',2)
    if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
            or not 0<=box[0]<box[2]<=image.width or not 0<=box[1]<box[3]<=image.height
            or type(anchor) is not int or not box[0]<=anchor<box[2]-8
            or not isinstance(color,list) or len(color)!=3 or any(type(v) is not int or not 0<=v<=255 for v in color)
            or type(tolerance) is not int or not 0<=tolerance<=4):
        raise ValueError('INCOMING_DISCOVERY_CALIBRATION_REQUIRED')
    crop=image.crop(box).convert('RGB');width,height=crop.size
    if width*height>2_000_000:raise ValueError('INCOMING_DISCOVERY_REGION_TOO_LARGE')
    pixels=crop.load()
    mask=bytearray(width*height)
    for y in range(height):
        for x in range(width):
            pixel=pixels[x,y]
            mask[y*width+x]=all(abs(pixel[k]-color[k])<=tolerance for k in range(3))
    candidates=[]
    seed_x=anchor-box[0]+4
    for seed_y in range(height):
        seed=seed_y*width+seed_x
        if not mask[seed]:continue
        pending=deque([seed]);mask[seed]=0;count=0
        left=right=seed_x;top=bottom=seed_y
        while pending:
            item=pending.popleft();y,x=divmod(item,width);count+=1
            left=min(left,x);right=max(right,x);top=min(top,y);bottom=max(bottom,y)
            for neighbor in ((item-1 if x else -1),(item+1 if x+1<width else -1),
                             (item-width if y else -1),(item+width if y+1<height else -1)):
                if neighbor>=0 and mask[neighbor]:
                    mask[neighbor]=0;pending.append(neighbor)
        w=right-left+1;h=bottom-top+1
        if abs(left+box[0]-anchor)>3 or w<40 or not 20<=h<=400:
            continue
        if count/(w*h)<.60:
            # Selected text can cover most of a bubble in blue. Require its
            # reviewed grey frame on all four sides, never lower a confidence
            # threshold and treat a photo's sparse grey pixels as a message.
            edges=([(x,top+2) for x in range(left+8,right-7)],
                   [(x,bottom-2) for x in range(left+8,right-7)],
                   [(left+2,y) for y in range(top+6,bottom-5)],
                   [(right-2,y) for y in range(top+6,bottom-5)])
            if any(not edge or sum(all(abs(pixels[x,y][k]-color[k])<=tolerance for k in range(3))
                                  for x,y in edge)/len(edge)<.70 for edge in edges):
                continue
        candidates.append([left+box[0],top+box[1],right+box[0]+1,bottom+box[1]+1])
        if len(candidates)>20:raise ValueError('TOO_MANY_VISIBLE_CANDIDATES')
    return candidates
