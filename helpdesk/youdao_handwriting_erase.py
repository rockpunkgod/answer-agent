"""Youdao ocr_writing_erase v3 protocol; original images are never overwritten."""
import base64
from hashlib import sha256
import io
import json
import time
from uuid import uuid4

from .youdao_api import ApiError, Client, checked_path


class HandwritingErase(Client):
    api, protocol = 'erase', 'ocr_writing_erase-v3'

    def parameters(self, parameters):
        if set(parameters)-{'angle'} or parameters.get('angle','0') not in ('0','1'):
            raise ApiError('ERASE_PARAMETERS_INVALID')
        return {'angle':parameters.get('angle','0')}

    def sign(self, q, app_key, secret, *, angle='0', salt=None, curtime=None):
        # Official v3 demo confirms curtime, despite the parameter table typo.
        salt, curtime = salt or str(uuid4()), curtime or str(int(time.time()))
        text = q if len(q)<=20 else q[:10]+str(len(q))+q[-10:]
        signature = sha256((app_key+text+salt+curtime+secret).encode('utf-8')).hexdigest()
        return dict(q=q,appKey=app_key,salt=salt,curtime=curtime,sign=signature,signType='v3',angle=angle)

    def parse(self, raw, directory, key):
        from PIL import Image
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value,dict) or type(value.get('errorCode')) not in (str,int):
            raise ApiError('ERASE_RESPONSE_INVALID')
        code = str(value['errorCode'])
        if code!='0':
            return {'status':'CLEANUP_FAILED','error_code':code if code.isdigit() else 'BUSINESS_ERROR'}
        try:
            encoded = value['eraseEnhanceImg']
            if not isinstance(encoded,str) or len(encoded)>8000000:
                raise ValueError()
            content = base64.b64decode(encoded,validate=True)
            with Image.open(io.BytesIO(content)) as image:
                size = list(image.size)
                if image.format not in ('PNG','JPEG','BMP') or size[0]*size[1]>20000000:
                    raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                output = io.BytesIO(); image.save(output,format='PNG')
            # Save a derivative only, never the caller's original path.
            output = output.getvalue()
            checked_path(directory/(key+'.png'),directory,exists=False).write_bytes(output)
        except Exception:
            raise ApiError('ERASE_IMAGE_INVALID') from None
        request_id = value.get('requestId')
        if request_id is not None and (not isinstance(request_id,str) or len(request_id)>256):
            raise ApiError('ERASE_RESPONSE_INVALID')
        return dict(status='CLEANUP_SUCCEEDED',request_id=request_id,processed_path=str(directory/(key+'.png')),
                    processed_sha256=sha256(output).hexdigest(),processed_dimensions=size,
                    print_preservation_verified=False)
