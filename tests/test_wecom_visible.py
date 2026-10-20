"""Synthetic local pixels, never a real message or desktop action."""
import unittest
from PIL import Image,ImageDraw
from helpdesk.wecom_visible import incoming_bubbles,time_label_identity


class IncomingCandidates(unittest.TestCase):
    def test_timestamp_hint_does_not_guess_year_and_validates_dates(self):
        self.assertEqual(time_label_identity('1/223:12:32'),(None,1,2,23,12,32))
        self.assertEqual(time_label_identity('01/0223:12:32'),(None,1,2,23,12,32))
        for label in ('0000/01/0223:12:32','10/1924:12:32','02/3023:12:32','invalid'):
            self.assertIsNone(time_label_identity(label))

    def setUp(self):
        self.image=Image.new('RGB',(600,400),(245,247,250))
        self.config={'viewport':[0,0,600,400],'incoming_left':20,
                     'bubble_color':[228,231,235],'color_tolerance':2}

    def bubble(self,box,selected=False):
        draw=ImageDraw.Draw(self.image)
        draw.rectangle(box,fill=tuple(self.config['bubble_color']))
        if selected:
            draw.rectangle((box[0]+8,box[1]+5,box[2]-8,box[3]-5),fill=(51,153,255))
        else:
            draw.text((box[0]+8,box[1]+8),'anonymous question',fill=(0,0,0))

    def test_new_visible_bubble_needs_no_predefined_message_recipe(self):
        self.bubble((20,40,220,75))
        self.assertEqual(len(incoming_bubbles(self.image,self.config)),1)
        self.bubble((20,150,240,190))
        self.assertEqual(len(incoming_bubbles(self.image,self.config)),2)

    def test_small_translation_returns_current_coordinates_not_old_recipe(self):
        self.bubble((22,38,222,73))
        self.assertEqual(incoming_bubbles(self.image,self.config),[[22,38,223,74]])
        self.assertNotIn([20,40,221,76],incoming_bubbles(self.image,self.config))

    def test_selected_text_frame_is_still_a_candidate(self):
        self.bubble((20,40,220,75),selected=True)
        self.assertEqual(incoming_bubbles(self.image,self.config),[[20,40,221,76]])

    def test_outgoing_and_sparse_image_pixels_not_incoming_bubbles(self):
        self.bubble((300,40,500,75))
        draw=ImageDraw.Draw(self.image)
        draw.line((24,150,180,190),fill=(228,231,235),width=2)
        self.assertEqual(incoming_bubbles(self.image,self.config),[])

    def test_empty_candidate_list_does_not_claim_empty_chat(self):
        self.assertEqual(incoming_bubbles(self.image,self.config),[])

    def test_unreviewed_geometry_and_overbroad_tolerance_rejected(self):
        for changes in ({'viewport':[-1,0,600,400]},{'color_tolerance':20},{'incoming_left':700}):
            with self.assertRaises(ValueError):incoming_bubbles(self.image,{**self.config,**changes})

    def test_candidate_limit_stops_instead_of_discarding_records(self):
        self.image=Image.new('RGB',(600,1000),(245,247,250))
        self.config['viewport']=[0,0,600,1000]
        for index in range(21):self.bubble((20,10+40*index,220,40+40*index))
        with self.assertRaisesRegex(ValueError,'TOO_MANY_VISIBLE_CANDIDATES'):
            incoming_bubbles(self.image,self.config)


class VisibleBoundary(unittest.TestCase):
    def step(self, old, values, second=2, **options):
        from helpdesk.wecom_visible import advance_visible_view
        from hashlib import sha256
        return advance_visible_view(old,[sha256(v.encode()).hexdigest() for v in values],
            scope_key=options.pop('scope_key','group'),
            started_at=f'2026-10-20T12:00:{second:02d}+08:00',
            completed_at=f'2026-10-20T12:00:{second+1:02d}+08:00',**options)

    def test_history_restart_and_unique_append_are_candidate_only(self):
        import json
        state,result=self.step(None,['a','b'])
        self.assertEqual(result['status'],'BASELINE_ESTABLISHED')
        self.assertEqual(result['candidate_indices'],[])
        state,result=self.step(json.loads(json.dumps(state)),['a','b'],4)
        self.assertEqual(result['status'],'VIEW_UNCHANGED')
        state,result=self.step(state,['b','c'],6)
        self.assertEqual(result['candidate_indices'],[1])
        self.assertFalse(result['new_message_verified'])
        self.assertFalse(result['automatic_ack_eligible'])
        self.assertNotIn('source_sent_at',result)

    def test_lost_boundary_latches_until_explicit_history_reset(self):
        state,_=self.step(None,['a','b'])
        held,result=self.step(state,['x','y'],4)
        self.assertEqual(result['status'],'VIEW_REQUIRES_REVIEW')
        held,result=self.step(held,['b','c'],6)
        self.assertEqual(result['candidate_indices'],[])
        self.assertTrue(held['review_required'])
        reset,result=self.step(held,['b','c'],8,reset=True)
        self.assertEqual(result['status'],'BASELINE_ESTABLISHED')
        self.assertFalse(reset['review_required'])
        self.assertEqual(result['candidate_indices'],[])

    def test_scope_clock_gap_truncation_and_repetition_stop(self):
        state,_=self.step(None,['a','b'])
        cases=[(['a','b'],4,{'scope_key':'other'}),
               (['b','c'],1,{}),(['b','c'],8,{'max_gap_seconds':3}),
               (['b'],4,{}),(['b','a'],4,{}),(['b','c','c'],4,{})]
        for values,second,options in cases:
            with self.subTest(values=values,options=options):
                held,result=self.step(state,values,second,**options)
                self.assertTrue(held['review_required'])
                self.assertEqual(result['candidate_indices'],[])
        repeated,_=self.step(None,['a','a'])
        _,result=self.step(repeated,['a','a','b'],4)
        self.assertEqual(result['reason'],'VIEW_CONTINUITY_AMBIGUOUS_OR_LOST')

    def test_corrupt_state_is_not_silently_replaced(self):
        state,result=self.step({'version':0},['a'])
        self.assertIsNone(state)
        self.assertEqual(result['reason'],'BASELINE_CORRUPT')
        with self.assertRaises(ValueError):self.step(None,[])


class SenderTemplates(unittest.TestCase):
    def setUp(self):
        from helpdesk.mcp_test_delivery import pixel_hash
        self.image=Image.new('RGB',(250,200),'white')
        tile=Image.new('RGB',(50,14),'white')
        ImageDraw.Draw(tile).text((1,1),'Alice',fill=(80,80,80))
        self.image.paste(tile,(22,100))
        self.template={'region_box':[20,60,120,90],'sender_name':{
            'reviewed':True,'display_name':'Alice','region_box':[20,40,70,54],
            'region_hash':pixel_hash(self.image,[22,100,72,114])}}

    def test_known_name_follows_new_bubble_without_reusing_original_position(self):
        from helpdesk.wecom_visible import match_sender_template
        result=match_sender_template(self.image,[22,120,150,150],[self.template,self.template])
        self.assertEqual(result['display_name'],'Alice')
        self.assertEqual(result['region_box'],[22,100,72,114])
        self.assertEqual(result['verification'],'EXACT_REVIEWED_NAME_TEMPLATE')
        self.assertEqual(self.template['sender_name']['region_box'],[20,40,70,54])

    def test_one_pixel_name_baseline_rounding_still_requires_exact_pixels(self):
        from helpdesk.wecom_visible import match_sender_template
        tile=self.image.crop((22,100,72,114))
        self.image=Image.new('RGB',self.image.size,'white')
        self.image.paste(tile,(22,99))
        result=match_sender_template(self.image,[22,120,150,150],[self.template])
        self.assertEqual(result['region_box'],[22,99,72,113])
        self.image=Image.new('RGB',self.image.size,'white')
        self.image.paste(tile,(22,94))
        self.assertIsNone(match_sender_template(self.image,[22,120,150,150],[self.template]))

    def test_missing_header_unknown_name_and_blank_template_do_not_infer_sender(self):
        from helpdesk.wecom_visible import match_sender_template
        from helpdesk.mcp_test_delivery import pixel_hash
        self.assertIsNone(match_sender_template(self.image,[22,160,150,190],[self.template]))
        blank={**self.template,'sender_name':{**self.template['sender_name'],
            'region_hash':pixel_hash(self.image,[22,140,72,154])}}
        self.assertIsNone(match_sender_template(self.image,[22,160,150,190],[blank]))

    def test_conflicting_names_and_unreviewed_templates_stop(self):
        from helpdesk.wecom_visible import match_sender_template
        other={**self.template,'sender_name':{**self.template['sender_name'],'display_name':'Bob'}}
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS_SENDER_NAME_TEMPLATE'):
            match_sender_template(self.image,[22,120,150,150],[self.template,other])
        bad={**self.template,'sender_name':{**self.template['sender_name'],'reviewed':False}}
        with self.assertRaisesRegex(ValueError,'NOT_REVIEWED'):
            match_sender_template(self.image,[22,120,150,150],[bad])
        for templates in ([None],[{**self.template,'sender_name':{**self.template['sender_name'],
                            'region_box':[20,70,70,84]}}]):
            with self.assertRaises(ValueError):
                match_sender_template(self.image,[22,120,150,150],templates)
