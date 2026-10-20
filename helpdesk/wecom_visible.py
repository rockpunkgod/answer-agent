"""Local candidates in a reviewed incoming bubble lane, not platform messages.

No OCR of student text and no automatic sender/time/ACK inference. Clipboard
readback and the existing foreground/region guards must verify every candidate.
"""
from collections import deque


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
