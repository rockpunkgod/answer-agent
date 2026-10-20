"""Youdao ocrquestionapi v2 mapping; HTML evidence is never a teaching rule."""
from hashlib import sha256
import json
import time
from uuid import uuid4

from .youdao_api import ApiError, Client


class PhotoSearch(Client):
    api, protocol = 'search', 'ocrquestionapi-v2'

    def parameters(self, parameters):
        if parameters:
            raise ApiError('SEARCH_PARAMETERS_INVALID')
        return {}

    def sign(self, q, app_key, secret, *, salt=None, curtime=None):
        salt, curtime = salt or str(uuid4()), curtime or str(int(time.time()))
        text = q if len(q)<=20 else q[:10]+str(len(q))+q[-10:]
        signature = sha256((app_key+text+salt+curtime+secret).encode('utf-8')).hexdigest()
        return dict(q=q,appKey=app_key,salt=salt,curtime=curtime,sign=signature,signType='v2',type='1',searchType='img')

    def parse(self, raw, directory, key):
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value,dict) or type(value.get('errorCode')) not in (str,int):
            raise ApiError('SEARCH_RESPONSE_INVALID')
        code = str(value['errorCode'])
        if code!='0':
            return {'status':'SEARCH_FAILED','error_code':code if code.isdigit() else 'BUSINESS_ERROR'}
        data = value.get('data')
        if not isinstance(data,dict) or not isinstance(data.get('questions'),list) or len(data['questions'])>100:
            raise ApiError('SEARCH_RESPONSE_INVALID')
        if data.get('text') is not None and not isinstance(data['text'],str):
            raise ApiError('SEARCH_RESPONSE_INVALID')
        candidates = []
        for item in data['questions']:
            if not isinstance(item,dict) or not isinstance(item.get('content'),str) or not item['content'].strip():
                raise ApiError('SEARCH_CANDIDATE_INVALID')
            if any(item.get(k) is not None and not isinstance(item[k],str) for k in ('id','answer','analysis')):
                raise ApiError('SEARCH_CANDIDATE_INVALID')
            candidates.append(dict(provider='YOUDAO_SEARCH',request_id=None,status='UNVERIFIED_CANDIDATE',
                source_question_id=item.get('id'),content=item['content'],stem=None,material=None,options=None,
                answer=item.get('answer'),explanation=item.get('analysis'),raw_response_sha256=sha256(raw).hexdigest()))
        return dict(status='SEARCH_SUCCEEDED' if candidates else 'SEARCH_NO_RESULTS',request_id=None,
                    ocr_text=data.get('text'),candidates=candidates)
