"""Synthetic local pixels, never a real message or desktop action."""
import unittest
from PIL import Image,ImageDraw
from helpdesk.wecom_visible import incoming_bubbles


class IncomingCandidates(unittest.TestCase):
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
