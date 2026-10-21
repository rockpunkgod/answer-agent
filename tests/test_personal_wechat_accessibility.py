"""Anonymous gate-memory fixtures only, no process mutation or real UIA."""
from types import SimpleNamespace
import unittest

from helpdesk.personal_wechat_accessibility import temporary_accessibility,current_chat_rows,locate_image_in_sequence


class AccessibilityScopeTests(unittest.TestCase):
    def fixture(self,*,failure=False,address=120,value=1):
        memory=SimpleNamespace(values={120:0},closed=False)
        memory.read=lambda address:memory.values.get(address)
        def write(address,value):memory.values[address]=value;return True
        memory.write=write
        memory.close=lambda:setattr(memory,'closed',True)
        class Gate:
            _pid_from_hwnd=staticmethod(lambda hwnd:7)
            _weixin_dll_module=staticmethod(lambda pid:(100,50,'fixture.dll'))
            @classmethod
            def _hot_activate_accessibility(cls,hwnd,*,verify,max_candidates):
                self.assertTrue(verify);self.assertEqual(max_candidates,1)
                cls._write_process_byte(None,address,value)
                if failure:raise RuntimeError('fixture failure after write')
                return True
        return SimpleNamespace(AccessibilityGate=Gate),memory

    def test_probe_restores_flag_and_never_claims_input_or_activation(self):
        module,memory=self.fixture()
        with temporary_accessibility(module,hwnd=42,pid=7,guard=lambda:None,
                                     memory_factory=lambda *_:memory) as result:
            self.assertEqual(memory.values[120],1)
            self.assertTrue(result['materialized'])
            self.assertFalse(result['activation_performed'])
            self.assertFalse(result['input_performed'])
        self.assertEqual(memory.values[120],0)
        self.assertTrue(result['restored']);self.assertTrue(memory.closed)

    def test_helper_or_consumer_exception_still_restores_byte(self):
        for helper_failure in (True,False):
            module,memory=self.fixture(failure=helper_failure)
            with self.subTest(helper_failure=helper_failure),self.assertRaises(RuntimeError):
                with temporary_accessibility(module,hwnd=42,pid=7,guard=lambda:None,
                                             memory_factory=lambda *_:memory):
                    raise RuntimeError('fixture consumer failed')
            self.assertEqual(memory.values[120],0);self.assertTrue(memory.closed)

    def test_wrong_process_or_out_of_bounds_write_stops(self):
        module,memory=self.fixture(address=900)
        with self.assertRaisesRegex(ValueError,'OUTSIDE_GATE_SCOPE'):
            with temporary_accessibility(module,hwnd=42,pid=7,guard=lambda:None,
                                         memory_factory=lambda *_:memory):pass
        self.assertEqual(memory.values,{120:0})
        with self.assertRaisesRegex(ValueError,'PROCESS_CHANGED'):
            with temporary_accessibility(module,hwnd=42,pid=8,guard=lambda:None,
                                         memory_factory=lambda *_:memory):pass

    def test_unverified_restoration_is_failure(self):
        module,memory=self.fixture()
        with self.assertRaisesRegex(RuntimeError,'RESTORE_UNVERIFIED'):
            with temporary_accessibility(module,hwnd=42,pid=7,guard=lambda:None,
                                         memory_factory=lambda *_:memory):
                memory.write=lambda *_:False
        self.assertTrue(memory.closed)


class Control:
    def __init__(self,aid='',name='',cls='',children=()):
        self.AutomationId,self.Name,self.ClassName,self.children=aid,name,cls,list(children)
    def GetChildren(self):return self.children


class ImageControlBindingTests(unittest.TestCase):
    rooms={'100@chatroom':'English test'}
    def root(self,*,caption='Image',title='English test'):
        def forbidden():raise AssertionError('Unrelated session list must not be traversed')
        sessions=Control('session_list');sessions.GetChildren=forbidden
        image=Control(name=caption,cls='mmui::ChatBubbleReferItemView')
        text=Control(name='Please explain B.',cls='mmui::ChatTextItemView')
        return Control(children=[sessions,Control('chat_input_field','English test长按鼠标语音输入'),
            Control('header.current_chat_name_label',title),Control('chat_message_list',children=[image,text])])
    def test_actual_english_image_caption_and_exact_db_sequence(self):
        for caption in ('Image','图片'):
            ui=current_chat_rows(self.root(caption=caption),room_id='100@chatroom',rooms=self.rooms)
            db=[{'kind':'image','text':'','message_id':'image-1'},
                {'kind':'text','text':'Please explain B.','message_id':'text-1'}]
            self.assertEqual(locate_image_in_sequence(ui,db,'image-1'),0)

    def test_ambiguous_or_truncated_text_never_guesses_image(self):
        ui=current_chat_rows(self.root(),room_id='100@chatroom',rooms=self.rooms)
        window=[{'kind':'image','text':'','message_id':'image-1'},
                {'kind':'text','text':'Please explain B.','message_id':'text-1'}]
        with self.assertRaisesRegex(ValueError,'NOT_UNIQUE'):
            locate_image_in_sequence(ui,window+window,'image-1')
        with self.assertRaisesRegex(ValueError,'NOT_UNIQUE'):
            locate_image_in_sequence(ui,[window[0],{**window[1],'text':'Please explain B. And C.'}],'image-1')
        with self.assertRaisesRegex(ValueError,'NOT_IN_CURRENT_VIEW'):
            locate_image_in_sequence(ui,window,'another-image')

    def test_quote_card_is_not_an_image_even_with_same_class(self):
        ui=current_chat_rows(self.root(caption='Quoted message'),room_id='100@chatroom',rooms=self.rooms)
        self.assertEqual([r['kind'] for r in ui],['text'])

    def test_wrong_or_nonunique_chat_stops_before_reading_messages(self):
        for rooms,title in ((self.rooms,'Other chat'),
                            ({**self.rooms,'200@chatroom':'English test'},'English test')):
            with self.subTest(title=title),self.assertRaises(ValueError):
                current_chat_rows(self.root(title=title),room_id='100@chatroom',rooms=rooms)

    def test_missing_or_duplicate_anchor_stops(self):
        root=self.root();root.children.append(Control('chat_input_field','English test'))
        with self.assertRaisesRegex(ValueError,'NOT_UNIQUE'):
            current_chat_rows(root,room_id='100@chatroom',rooms=self.rooms)


if __name__=='__main__':unittest.main()
