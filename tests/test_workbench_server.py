"""HTTP validation and authorization for settings and archives."""
import json
from pathlib import Path
from unittest.mock import patch
import unittest
from scripts import run_gui, plan_with_openai
from scripts.library import BuildingLibrary
import test_gui_server as fixture

class WorkbenchServerTests(unittest.TestCase):
    request=fixture.ServerTests.request
    tearDown=fixture.ServerTests.tearDown
    def setUp(self):
        fixture.ServerTests.setUp(self)
        self.root=Path(self.temp.name)
        self.patcher=patch.object(run_gui,'ROOT',self.root);self.patcher.start();self.addCleanup(self.patcher.stop)
        settings_root=patch.object(plan_with_openai,'ROOT',self.root);settings_root.start();self.addCleanup(settings_root.stop)
        self.app.library=BuildingLibrary(self.root,self.backend.conversations)

    def test_new_mutations_require_auth_and_same_origin(self):
        for endpoint in ('settings','library-save','library-open','library-import','library-images','library-description','reference','pick-directory'):
            self.assertEqual(self.request('/api/'+endpoint,{}, {'Content-Type':'application/json'})[0],403)
            self.assertEqual(self.request('/api/'+endpoint,{}, {'Content-Type':'application/json','X-MineCreator-Token':self.app.token,'Origin':'https://example.com'})[0],403)

    def test_settings_never_return_secret_and_invalid_input_leaves_existing_value(self):
        payload=dict(model='offline-model',game_dir=str(self.root),api_key='sk-fake-http-only')
        code,raw=self.request('/api/settings',payload)
        self.assertEqual(code,200);self.assertNotIn(b'sk-fake-http-only',raw)
        self.assertEqual(self.request('/api/settings',dict(payload,game_dir='relative'))[0],400)
        self.assertEqual(json.loads(self.request('/api/state')[1])['configuration']['model'],'offline-model')
        self.assertEqual(self.request('/data/settings.json')[0],404)

    def test_settings_and_archive_save_blocked_during_running_task(self):
        from types import SimpleNamespace
        task=SimpleNamespace(done=False)
        self.app.latest=dict(id='active');self.app.jobs['active']=dict(task=task)
        for endpoint in ('settings','library-save','library-import','library-images','library-description','reference'):
            code,raw=self.request('/api/'+endpoint,{})
            self.assertEqual(code,400);self.assertIn(b'current operation',raw)
        self.app.latest=None;self.app.jobs.clear()

    def test_bad_settings_do_not_prevent_opening_gui_and_can_be_replaced(self):
        folder=self.root/'data';folder.mkdir(exist_ok=True)
        for raw in ('{broken','[]','{"model":null}'):
            (folder/'settings.json').write_text(raw,encoding='utf-8')
            code,body=self.request('/api/state')
            self.assertEqual(code,200,body)
            state=json.loads(body);self.assertFalse(state['configuration']['configured'])
            self.assertTrue(state['configuration']['message'])
            code,body=self.request('/api/settings',dict(model='offline',game_dir=str(self.root),api_key='sk-new-test-key'))
            self.assertEqual(code,200,body)
            self.assertNotIn(b'sk-new-test-key',body)

    def test_bad_conversation_does_not_break_state_or_recovery(self):
        from uuid import uuid4
        good=self.backend.conversations.create();folder=self.backend.conversations.folder
        (folder/f'{uuid4().hex}.json').write_text('[]')
        code,body=self.request('/api/state');self.assertEqual(code,200,body)
        self.assertTrue(any(c['id']==good['id'] for c in json.loads(body)['conversations']))
        self.backend.conversations.recover()

if __name__=='__main__':unittest.main()
