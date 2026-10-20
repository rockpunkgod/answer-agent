"""Synthetic OCR output and anonymous images; no desktop or model calls."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from helpdesk import wecom_control_ocr as controls


class ControlOcrTests(unittest.TestCase):
    def time_runner(self,labels):
        return Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps({
            'available':True,'image_size':[160,80],'time_labels':labels}).encode()))

    def time_label(self):
        return {'raw':'10 / 19 23 ： 12 ： 32','normalized':'10/1923:12:32','year':None,
                'month':'10','day':'19','hour':'23','minute':'12','second':'32'}

    def test_time_label_keeps_missing_year_and_never_uses_collection_time(self):
        result=controls.read_time_label('record',[10,20,50,40],runner=self.time_runner([self.time_label()]))
        self.assertEqual(result['time_status'],'REQUIRES_YEAR_EVIDENCE')
        self.assertIsNone(result['source_sent_at'])
        self.assertIsNone(result['components']['year'])
        self.assertFalse(result['formal_statistics_eligible'])
        self.assertTrue(Path(result['crop_path']).is_file())

    def test_time_label_missing_ambiguous_invalid_or_inconsistent_stops(self):
        label=self.time_label()
        for labels in ([],[label,label],[{**label,'month':'13'}],[{**label,'normalized':'10/1924:12:32'}]):
            with self.assertRaisesRegex(ValueError,'TIME_LABEL_NOT_UNIQUE_OR_UNAVAILABLE'):
                controls.read_time_label('record',[10,20,50,40],runner=self.time_runner(labels))

    def setUp(self):
        from PIL import Image
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.image=self.root/'source.png'
        Image.new('RGB',(200,100),'white').save(self.image)
        source=patch.object(controls,'_source',return_value=(self.image,200,100,(100,-100,500,100)))
        self.source=source.start();self.addCleanup(source.stop)
        root=patch.object(controls,'ROOT',self.root);root.start();self.addCleanup(root.stop)

    def runner(self,boxes):
        value={'available':True,'control':'copy_menu','image_size':[160,80],'copy_control_boxes':boxes}
        return Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value).encode()))

    def test_scoped_unique_label_maps_to_current_display_without_luna(self):
        runner=self.runner([[20,10,60,30]])
        result=controls.locate_copy(self.root/'record.json',[10,20,50,40],runner=runner)
        self.assertEqual(result['loc'],[140,-50])
        self.assertFalse(result['authorizes_input'])
        self.assertEqual(result['source'],'WINDOWS_OCR')
        self.assertEqual(self.source.call_count,2)
        self.assertFalse(runner.call_args.kwargs['shell'])
        self.assertEqual(runner.call_args.kwargs['timeout'],15)
        self.assertIn('-CopyControlOnly',runner.call_args.args[0])

    def test_ambiguous_missing_or_invalid_ocr_never_returns_coordinates(self):
        for boxes in ([],[[1,1,10,10],[20,20,30,30]],[[0,0,1000,40]],[[True,0,10,10]]):
            with self.subTest(boxes=boxes),self.assertRaisesRegex(ValueError,'NOT_UNIQUE_OR_UNAVAILABLE'):
                controls.locate_copy('record',[10,20,50,40],runner=self.runner(boxes))

    def test_invalid_region_and_untrusted_source_never_run_ocr(self):
        runner=self.runner([])
        with self.assertRaisesRegex(ValueError,'REGION_INVALID'):
            controls.locate_copy('record',[-1,0,50,40],runner=runner)
        self.source.side_effect=ValueError('NATIVE_JOURNAL_REQUIRED')
        with self.assertRaisesRegex(ValueError,'JOURNAL_REQUIRED'):
            controls.locate_copy('record',[10,20,50,40],runner=runner)
        runner.assert_not_called()
