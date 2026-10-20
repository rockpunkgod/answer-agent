"""Self-authored images, synthetic credentials and mocked HTTPS only."""
from dataclasses import replace
from hashlib import sha256
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from PIL import Image
from helpdesk.youdao_api import ApiError,Settings,post
from helpdesk.youdao_handwriting_erase import HandwritingErase
from helpdesk.youdao_photo_search import PhotoSearch


class YoudaoApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.images=self.root/'data/private/youdao/test-images'
        self.images.mkdir(parents=True)
        self.image=self.images/'匿名试题.png';Image.new('RGB',(100,100),'white').save(self.image)
        self.settings=Settings(workspace=self.root,erase_enabled=True,search_enabled=True,
                               erase_reservation_cny='0.10',search_reservation_cny='0.10')
        self.env=patch.dict('os.environ',{'YOUDAO_ERASE_APP_KEY':'synthetic-erase-id',
            'YOUDAO_ERASE_APP_SECRET':'synthetic-erase-secret','YOUDAO_SEARCH_APP_KEY':'synthetic-search-id',
            'YOUDAO_SEARCH_APP_SECRET':'synthetic-search-secret'},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        self.context={'question_version':'synthetic-v1','context_revision':1}
        self.transport=Mock(return_value=(200,json.dumps({'errorCode':'0','data':{'text':'匿名','questions':[]}}).encode()))
        self.search=PhotoSearch(self.settings,transport=self.transport)

    def erase_response(self):
        return json.dumps({'errorCode':'0','requestId':'synthetic-response',
            'eraseEnhanceImg':base64.b64encode(self.image.read_bytes()).decode()}).encode()

    def test_separate_protocol_signatures_and_fields(self):
        for cls,version in ((HandwritingErase,'v3'),(PhotoSearch,'v2')):
            for q in ('short','x'*20,'x'*21,'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'):
                with self.subTest(client=cls.__name__,q=q):
                    fields=cls(self.settings).sign(q,'synthetic-key','synthetic-secret',salt='salt',curtime='1700000000')
                    shortened=q if len(q)<=20 else q[:10]+str(len(q))+q[-10:]
                    expected=sha256(('synthetic-key'+shortened+'salt1700000000synthetic-secret').encode()).hexdigest()
                    self.assertEqual(fields['sign'],expected);self.assertEqual(fields['signType'],version)
                    self.assertEqual(fields['q'],q)
                    self.assertEqual(fields.get('type'), '1' if cls is PhotoSearch else None)
                    self.assertEqual(fields.get('searchType'), 'img' if cls is PhotoSearch else None)

    def test_missing_credentials_disabled_and_dry_run_make_no_requests(self):
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(self.search.execute(self.image,context=self.context)['status'],'CREDENTIALS_NOT_CONFIGURED')
        disabled=PhotoSearch(replace(self.settings,search_enabled=False),transport=self.transport)
        self.assertEqual(disabled.execute(self.image,context=self.context)['status'],'PROVIDER_DISABLED')
        self.assertEqual(self.search.execute(self.image,context=self.context,dry_run=True)['status'],'DRY_RUN')
        with self.assertRaisesRegex(ApiError,'ERASE_PARAMETERS_INVALID'):
            HandwritingErase(self.settings,transport=self.transport).execute(self.image,context=self.context,dry_run=True,angle='2')
        self.transport.assert_not_called()

    def test_unicode_path_and_same_bytes_different_filename_hit_cache(self):
        original=self.image.read_bytes()
        first=self.search.execute(self.image,context=self.context)
        self.assertEqual(first['status'],'SEARCH_NO_RESULTS');self.assertTrue(first['actual_call'])
        renamed=self.images/'中文副本.png';renamed.write_bytes(original)
        second=self.search.execute(renamed,context=self.context)
        self.assertTrue(second['cache_hit']);self.assertFalse(second['actual_call'])
        self.assertIsNone(second['amount_cny']);self.assertEqual(self.image.read_bytes(),original)
        self.transport.assert_called_once()

    def test_same_filename_changed_bytes_not_reused(self):
        self.search.execute(self.image,context=self.context)
        Image.new('RGB',(100,100),'black').save(self.image)
        result=self.search.execute(self.image,context=self.context)
        self.assertFalse(result['cache_hit']);self.assertEqual(self.transport.call_count,2)

    def test_injected_cache_cannot_masquerade_as_real_transport(self):
        self.search.execute(self.image,context=self.context)
        with patch('helpdesk.youdao_api.http.client.HTTPSConnection') as factory:
            connection=factory.return_value
            connection.getresponse.return_value.status=200
            connection.getresponse.return_value.read.return_value=b'{"errorCode":"0","data":{"questions":[]}}'
            result=PhotoSearch(self.settings).execute(self.image,context=self.context)
            connection.request.assert_called_once()
        self.assertFalse(result['cache_hit']);self.assertFalse(result['injected'])

    def test_invalid_images_rejected_before_network(self):
        for name,raw in [('empty.png',b''),('fake.png',b'not image'),('too-big.png',b'x'*1000000)]:
            with self.subTest(name=name):
                path=self.images/name;path.write_bytes(raw)
                with self.assertRaises(ApiError):self.search.execute(path,context=self.context)
        disguised=self.images/'wrong.jpg';disguised.write_bytes(self.image.read_bytes())
        with self.assertRaisesRegex(ApiError,'IMAGE_FORMAT_INVALID'):self.search.execute(disguised,context=self.context)
        wide=self.images/'wide.png';Image.new('RGB',(2048,20),'white').save(wide)
        with self.assertRaisesRegex(ApiError,'IMAGE_DIMENSIONS_INVALID'):self.search.execute(wide,context=self.context)
        outside=self.root/'outside.png';outside.write_bytes(self.image.read_bytes())
        with self.assertRaisesRegex(ApiError,'PATH_OUTSIDE'):self.search.execute(outside,context=self.context)
        self.transport.assert_not_called()

    def test_http_business_errors_malformed_json_and_timeout_do_not_repeat(self):
        cases=[(403,b'{}','SEARCH_FAILED'),(429,b'{}','SEARCH_FAILED'),
               (200,b'{"errorCode":"202"}','SEARCH_FAILED'),(200,b'not-json','RESULT_UNKNOWN'),
               (500,b'{}','RESULT_UNKNOWN')]
        for index,(code,body,expected) in enumerate(cases):
            with self.subTest(code=code,body=body):
                config=replace(self.settings,private_root=f'data/private/case-{index}')
                transport=Mock(return_value=(code,body));client=PhotoSearch(config,transport=transport)
                first=client.execute(self.image,context=self.context)
                self.assertEqual(first['status'],expected)
                client.execute(self.image,context=self.context)
                transport.assert_called_once()
        transport=Mock(side_effect=TimeoutError('MUST_NOT_LEAK synthetic-secret'))
        client=HandwritingErase(self.settings,transport=transport)
        first=client.execute(self.image,context=self.context,angle='0')
        self.assertEqual(first['status'],'RESULT_UNKNOWN')
        self.assertNotIn('MUST_NOT_LEAK',json.dumps(first))
        other=client.execute(self.image,context=dict(self.context,context_revision=2),angle='1')
        self.assertEqual(other['status'],'RESULT_UNKNOWN');transport.assert_called_once()

    def test_erasure_output_and_corrupt_image_response(self):
        transport=Mock(return_value=(200,self.erase_response()))
        client=HandwritingErase(self.settings,transport=transport)
        original=self.image.read_bytes()
        result=client.execute(self.image,context=self.context)
        self.assertEqual(result['status'],'CLEANUP_SUCCEEDED')
        self.assertFalse(result['print_preservation_verified'])
        self.assertEqual(self.image.read_bytes(),original)
        self.assertNotEqual(Path(result['processed_path']),self.image)
        self.assertTrue(client.execute(self.image,context=self.context,angle='0')['cache_hit'])
        transport.assert_called_once()
        self.assertEqual(sha256(Path(result['processed_path']).read_bytes()).hexdigest(),result['processed_sha256'])
        Path(result['processed_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ApiError,'CACHE_DERIVATIVE_CHANGED'):client.execute(self.image,context=self.context)
        broken=Mock(return_value=(200,b'{"errorCode":"0","eraseEnhanceImg":"!!!"}'))
        cfg=replace(self.settings,private_root='data/private/broken')
        self.assertEqual(HandwritingErase(cfg,transport=broken).execute(self.image,context=self.context)['status'],'RESULT_UNKNOWN')

    def test_missing_candidate_fields_are_not_invented(self):
        body=json.dumps({'errorCode':'0','data':{'questions':[{'content':'<p>Only a fragment</p>'}]}}).encode()
        self.transport.return_value=(200,body)
        candidate=self.search.execute(self.image,context=self.context)['candidates'][0]
        for name in ('request_id','source_question_id','stem','material','options','answer','explanation'):
            self.assertIsNone(candidate[name])
        cache=self.root/self.settings.private_root
        self.assertFalse(any('Only a fragment' in p.read_text(encoding='utf-8') for p in cache.glob('*.json')))

    def test_budget_and_expiry_do_not_enable_new_post(self):
        client=PhotoSearch(replace(self.settings,budget_cny='0.05'),transport=self.transport)
        self.assertEqual(client.execute(self.image,context=self.context)['status'],'BUDGET_EXCEEDED')
        self.transport.assert_not_called()
        self.search.execute(self.image,context=self.context)
        with patch('helpdesk.youdao_api.time.time',return_value=100000000000):
            self.assertEqual(self.search.execute(self.image,context=self.context)['error_code'],'CACHE_EXPIRED_NO_AUTOMATIC_RECALL')
            self.search.purge_expired()
        self.assertFalse(list((self.root/self.settings.private_root).glob('*.response')))
        self.transport.assert_called_once()

    def test_transport_has_fixed_https_no_redirect_or_proxy_path(self):
        with patch('helpdesk.youdao_api.http.client.HTTPSConnection') as factory:
            connection=factory.return_value
            connection.getresponse.return_value.status=302
            connection.getresponse.return_value.read.return_value=b''
            status,_=post('search',{'q':'synthetic'},self.settings)
            self.assertEqual(status,302)
            factory.assert_called_once_with('openapi.youdao.com',443,timeout=5)
            self.assertEqual(connection.request.call_args.args[:2],('POST','/ocrquestionapi'))
            connection.request.assert_called_once();connection.close.assert_called_once()
        with self.assertRaisesRegex(ApiError,'ENDPOINT_NOT_ALLOWED'):post('foreign',{},self.settings)

    def test_bounded_http_response_and_connect_timeout_are_not_retried(self):
        with patch('helpdesk.youdao_api.http.client.HTTPSConnection') as factory:
            conn=factory.return_value
            conn.getresponse.return_value.read.return_value=b'x'*1025
            with self.assertRaisesRegex(ApiError,'RESPONSE_TOO_LARGE'):
                post('search',{},replace(self.settings,max_response_bytes=1024))
            conn.request.assert_called_once();conn.close.assert_called_once()
            conn.reset_mock();conn.connect.side_effect=TimeoutError('synthetic sensitive detail')
            with self.assertRaisesRegex(ApiError,'POST_RESULT_UNKNOWN'):post('search',{},self.settings)
            conn.request.assert_not_called();conn.close.assert_called_once()

    def test_cli_dry_run_is_offline_and_does_not_print_image_or_keys(self):
        from tools.test_youdao_apis import main
        with patch('tools.test_youdao_apis.Settings.load',return_value=self.settings), \
             patch('tools.test_youdao_apis.ROOT',self.root), \
             patch.object(PhotoSearch,'execute',autospec=True,side_effect=PhotoSearch.execute) as execute, \
             patch('helpdesk.youdao_api.http.client.HTTPSConnection') as network, redirect_stdout(io.StringIO()) as stdout:
            code=main(['--image',str(self.image),'--dry-run','--output-dir',str(self.root/'data/private/results')])
        self.assertEqual(code,0);network.assert_not_called()
        self.assertNotIn('synthetic_test',execute.call_args.kwargs['context'])
        result=json.loads(stdout.getvalue());self.assertEqual(result['status'],'DRY_RUN')
        self.assertNotIn('synthetic-search',stdout.getvalue())
        self.assertNotIn('q',result)

    def test_cli_rejects_external_output_or_missing_real_pipeline_task(self):
        from tools.test_youdao_apis import main
        with patch('tools.test_youdao_apis.Settings.load',return_value=self.settings), \
             patch('tools.test_youdao_apis.ROOT',self.root), \
             patch('helpdesk.youdao_api.http.client.HTTPSConnection') as network, redirect_stdout(io.StringIO()) as stdout:
            code=main(['--image',str(self.image),'--pipeline','--output-dir',str(self.root/'data/private/results')])
        self.assertEqual(code,2);network.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())['error_code'],'EXISTING_TASK_REQUIRED_FOR_PIPELINE')
