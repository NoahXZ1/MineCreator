"""Settings validation, secret isolation, encryption and atomic write failures."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from scripts import settings

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.payload=dict(model='test-model',game_dir=str(self.root),api_key='sk-fake-test-only')

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_secret_round_trip_masking_and_clear_does_not_fall_back_to_env(self):
        result=settings.save(self.root,self.payload)
        raw=(self.root/'data'/'settings.json').read_text()
        self.assertNotIn('sk-fake-test-only',raw);self.assertNotIn('key',result)
        self.assertEqual(settings.effective(self.root)['key'],'sk-fake-test-only')
        settings.save(self.root,dict(self.payload,api_key='',model='another-model'))
        self.assertEqual(settings.effective(self.root)['key'],'sk-fake-test-only')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'sk-env-test-only'}):
            settings.save(self.root,dict(self.payload,api_key='',clear_key=True))
            self.assertFalse(settings.public(self.root)['configured'])

    def test_validation_does_not_write_partial_settings(self):
        for override in (dict(model='x\nAPI_KEY=bad'),dict(game_dir='relative'),dict(api_key='bad\nkey')):
            with self.subTest(override=override),self.assertRaises(ValueError):settings.save(self.root,dict(self.payload,**override))
        self.assertFalse((self.root/'data'/'settings.json').exists())

    def test_env_fallback_and_atomic_failure(self):
        (self.root/'.env').write_text('OPENAI_API_KEY=sk-test\nOPENAI_MODEL=test-model\n')
        self.assertEqual(settings.effective(self.root)['key'],'sk-test')
        path=self.root/'data'/'settings.json';settings.atomic_json(path,dict(model='before'))
        with patch.object(Path,'replace',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):settings.atomic_json(path,dict(model='after'))
        self.assertEqual(json.loads(path.read_text())['model'],'before')
        self.assertFalse(list(path.parent.glob('*.tmp')))

if __name__=='__main__':unittest.main()
