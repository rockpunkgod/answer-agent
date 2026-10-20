"""Explicit one-image evidence workflow; never teaches, delivers or awards work."""
from hashlib import sha256
import json
from pathlib import Path

from .domain import Question, compare
from .reference_lookup import student_snapshot, extract_document, verify_candidate
from .reference_resolution import ReferenceResolution
from .youdao_api import ApiError, image_bytes
from .storage import encode,now
from .youdao_handwriting_erase import HandwritingErase
from .youdao_photo_search import PhotoSearch


def current_snapshot(store, run_id, image, settings):
    from .question_matching import _current
    row = store.one('SELECT input_json FROM runs WHERE id=?',(run_id,))
    if not row:
        raise ApiError('ORIGINAL_RUN_REQUIRED')
    frozen = json.loads(row[0])
    _current(store, frozen)
    snapshot = student_snapshot(store, frozen['question_id'], frozen['question_version'], frozen['context_revision'])
    root,_ = settings.paths()
    raw,digest,_ = image_bytes(image,root,api='erase')
    path = Path(image).resolve(strict=True)
    if not any(Path(a['path']).resolve(strict=True)==path and a['sha256']==digest for a in frozen.get('attachments',[])):
        raise ApiError('ORIGINAL_IMAGE_NOT_BOUND_TO_TASK')
    return snapshot,digest


def verify_cleanup(snapshot, original_hash, cleaned, review):
    if not isinstance(review,dict) or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip():
        return False
    if any(review.get(k)!=v for k,v in dict(original_sha256=original_hash,
        processed_sha256=cleaned['processed_sha256'], question_version=snapshot['question_version'],
        context_revision=snapshot['context_revision']).items()):
        return False
    try:
        student = Question.from_dict(snapshot['student_question'])
        processed = Question.from_dict(review['question'])
        result = compare(cleaned['processed_sha256'], processed, review['material'],
                         snapshot['question_version'],student,snapshot['student_material'])
        # Erasure must preserve print, including printed numbers and letters.
        return result.resolution_status=='MATCH_CANDIDATE' and processed.number==student.number and all(
            a.label==b.label and a.verified_text==b.verified_text for a,b in zip(
                sorted(processed.options,key=lambda o:o.order),sorted(student.options,key=lambda o:o.order)))
    except (ValueError,KeyError,TypeError):
        return False


class YoudaoReferencePipeline:
    def __init__(self, settings, *, transport=None):
        self.settings=settings
        kwargs={} if transport is None else {'transport':transport}
        self.erase=HandwritingErase(settings,**kwargs)
        self.search=PhotoSearch(settings,**kwargs)

    def run(self, store, run_id, image, *, needs_cleanup=False, cleanup_review=None):
        if type(needs_cleanup) is not bool:
            raise ApiError('EXPLICIT_CLEANUP_DECISION_REQUIRED')
        snapshot,original_hash=current_snapshot(store,run_id,image,self.settings)
        context={k:snapshot[k] for k in ('question_id','question_version','context_revision')}
        output=dict(status='MANUAL_REVIEW_REQUIRED', cleanup_status='CLEANUP_SKIPPED',
                    original_sha256=original_hash,candidates=[],actual_requests=0,delivery_completed=False,
                    performance_changed=False,**context)
        search_image=image
        if needs_cleanup:
            cleaned=self.erase.execute(image,context=context,store=store,run_id=run_id,expected_sha256=original_hash)
            output.update(cleanup_status=cleaned['status'],cleanup=cleaned)
            output['actual_requests']+=int(cleaned['actual_call'])
            current_snapshot(store,run_id,image,self.settings)
            if cleaned['status']!='CLEANUP_SUCCEEDED':
                return output | {'status':cleaned['status']}
            if not verify_cleanup(snapshot,original_hash,cleaned,cleanup_review):
                return output | {'error_code':'CLEANED_PRINT_REQUIRES_REVIEW'}
            review_evidence=encode({'original_sha256':original_hash,'processed_sha256':cleaned['processed_sha256'],
                'question_version':snapshot['question_version'],'context_revision':snapshot['context_revision'],
                'reviewer':cleanup_review['reviewer'],'review_sha256':sha256(encode(cleanup_review).encode()).hexdigest()})
            with store.transaction():
                store.execute('''INSERT INTO audit(run_id,question_id,event,details,created_at)
                    SELECT ?,?,'YOUDAO_CLEANUP_REVIEWED',?,? WHERE NOT EXISTS
                    (SELECT 1 FROM audit WHERE run_id=? AND event='YOUDAO_CLEANUP_REVIEWED' AND details=?)''',
                    (run_id,snapshot['question_id'],review_evidence,now(),run_id,review_evidence))
            output['cleanup']=cleaned | {'print_preservation_verified':True,
                                         'verification_method':'HUMAN_TRANSCRIPTION_AND_EXISTING_COMPARE'}
            search_image=cleaned['processed_path']
        found=self.search.execute(search_image,context=context,store=store,run_id=run_id,origin_sha256=original_hash,
                                  expected_sha256=cleaned['processed_sha256'] if needs_cleanup else original_hash)
        output.update(search={k:v for k,v in found.items() if k not in ('candidates','ocr_text')},search_status=found['status'])
        output['actual_requests']+=int(found['actual_call'])
        current_snapshot(store,run_id,image,self.settings)  # A late correction must stop application too.
        if found['status'] not in ('SEARCH_SUCCEEDED','SEARCH_NO_RESULTS'):
            return output | {'status':found['status']}
        if not found['candidates']:
            return output | {'status':'SEARCH_NO_RESULTS'}
        ranked=[]
        for index,candidate in enumerate(found['candidates']):
            document=extract_document(candidate['content'],url='https://openapi.youdao.com/ocrquestionapi',
                                      page_hash=sha256(candidate['content'].encode('utf-8')).hexdigest())
            if not document['questions']:
                ranked.append((2,index,{'status':'MANUAL_REVIEW_REQUIRED','source_question_id':candidate['source_question_id'],
                    'content_sha256':document['page_sha256'],'missing_fields':['question_structure']}))
            for item in document['questions']:
                checked=verify_candidate(snapshot,document,item)
                # Explicit allowlist: answer, analysis and OCR never become student facts or teaching input.
                evidence={k:checked[k] for k in ('reference_question','reference_material','resolution_status',
                    'comparison_result','option_mapping','missing_fields','reference_evidence')}
                evidence.update(status='MATCH_CANDIDATE' if checked['resolution_status']=='MATCH_CANDIDATE'
                                else 'MANUAL_REVIEW_REQUIRED',source_question_id=candidate['source_question_id'],
                                content_sha256=document['page_sha256'])
                ranked.append((0 if evidence['status']=='MATCH_CANDIDATE' else 1,index,evidence))
        selected=[e for _,_,e in sorted(ranked,key=lambda r:(r[0],r[1]))[:2]]
        for item in selected:
            if not item.get('reference_question') or not Question.from_dict(item['reference_question']).complete:
                continue
            current_snapshot(store,run_id,image,self.settings)
            candidate_id,comparison=ReferenceResolution(store).add(snapshot['question_version'],item['content_sha256'],
                Question.from_dict(item['reference_question']),item['reference_material'],'YOUDAO_PHOTO_API',
                expected_context_revision=snapshot['context_revision'],provenance={
                    'source_url':'https://openapi.youdao.com/ocrquestionapi',
                    'retrieved_at':found['retrieved_at'],'content_hash':item['content_sha256'],
                    'reference_evidence':{'api_response_sha256':found['raw_response_sha256'],
                                          'source_question_id':item['source_question_id']},
                    'source_policy':{'business_record_storage_allowed':True,
                                     'retention_seconds':self.settings.retention_seconds}})
            item['candidate_id']=candidate_id
        output.update(candidates=selected,status='MATCH_CANDIDATE' if any(
            c['status']=='MATCH_CANDIDATE' for c in selected) else 'MANUAL_REVIEW_REQUIRED')
        return output
