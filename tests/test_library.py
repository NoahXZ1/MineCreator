"""Archive round trips, immutable versions, integrity and failure injection."""
import base64
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image

from scripts.library import BuildingLibrary
from scripts import designs
from scripts.chat import ConversationStore
import test_designs as fixture


class LibraryTests(unittest.TestCase):
    setUp = fixture.DesignTests.setUp
    complete_turn = fixture.DesignTests.complete_turn
    generate = fixture.DesignTests.generate

    def archive(self):
        self.plan,_=self.generate()
        self.library=BuildingLibrary(self.root,self.store)
        image=BytesIO();Image.new('RGB',(12,9),'green').save(image,format='PNG')
        self.library.reference(self.record['id'],uuid4().hex,'Garden reference.png',base64.b64encode(image.getvalue()).decode())
        return self.library.save(self.record['id'],self.plan['design_id'],uuid4().hex,'Lake cabin','Keep the garden natural.')

    def test_round_trip_includes_files_and_opens_without_originals_or_world_targets(self):
        m=self.archive()
        record=self.store.get(self.record['id'])
        record['builds']=[dict(id=uuid4().hex,status='completed',origin=[20,64,30],dimension='minecraft:overworld')]
        self.store._save(record)
        m=self.library.save(record['id'],self.plan['design_id'],uuid4().hex,'Lake cabin','Notes',m['library_id'])
        raw=self.library.export(m['id'])
        other_root=self.root/'other';other_store=ConversationStore(other_root/'data'/'conversations')
        other=BuildingLibrary(other_root,other_store)
        other.import_archive(raw)
        # Remove the original source plan and references; the imported archive is independent.
        Path(self.plan['plan_path']).unlink()
        for reference in record['references']:Path(reference['path']).unlink()
        opened=other.open(m['id'],uuid4().hex)
        self.assertNotIn('builds',opened)
        self.assertNotIn('edit_review',opened)
        self.assertNotIn('selected_build_id',opened)
        self.assertEqual(opened['archived_history']['builds'][0]['origin'],[20,64,30])
        self.assertTrue(Path(opened['references'][0]['path']).is_file())
        with patch.object(designs,'ROOT',other_root):
            self.assertEqual(designs.plan_summary(opened)['final_blocks'],62)
            next_saved=other.save(opened['id'],opened['designs'][0]['id'],uuid4().hex,'Lake cabin','New notes',m['library_id'])
        self.assertEqual(next_saved['version'],3)

    def test_versions_and_repeated_request_do_not_overwrite(self):
        first=self.archive();first_bytes=(self.library.folder/first['id']/'manifest.json').read_bytes()
        plan,_=self.generate('minecraft:white_wool');request=uuid4().hex
        second=self.library.save(self.record['id'],plan['design_id'],request,'Lake cabin','Wool roof',first['library_id'])
        again=self.library.save(self.record['id'],plan['design_id'],request,'Lake cabin','Wool roof',first['library_id'])
        self.assertEqual(second,again);self.assertEqual(second['version'],2)
        self.assertEqual((self.library.folder/first['id']/'manifest.json').read_bytes(),first_bytes)
        with self.assertRaises(ValueError):self.library.save(self.record['id'],plan['design_id'],request,'Other')
        new_id=uuid4().hex;opened=self.library.open(first['id'],new_id)
        self.assertEqual(self.library.open(first['id'],new_id)['id'],opened['id'])
        with self.assertRaises(ValueError):self.library.open(second['id'],new_id)

    def test_missing_or_corrupt_asset_is_visible_and_never_partially_opened(self):
        m=self.archive();asset=self.library.folder/m['id']/m['assets'][0]['file'];asset.write_bytes(b'broken')
        self.assertEqual(len(self.library.catalog()),1)
        with self.assertRaisesRegex(ValueError,'integrity'):self.library.open(m['id'],uuid4().hex)
        self.assertEqual(len(self.store.list()),1)

    def test_missing_source_prevents_incomplete_save(self):
        self.archive();Path(self.plan['plan_path']).unlink()
        request=uuid4().hex
        with self.assertRaises(ValueError):self.library.save(self.record['id'],self.plan['design_id'],request,'Missing')
        self.assertFalse((self.library.folder/request).exists())

    def test_import_rejects_path_traversal_duplicate_and_tampered_files(self):
        m=self.archive();raw=self.library.export(m['id'])
        for variant in ('traversal','tamper','external'):
            buffer=BytesIO()
            with ZipFile(BytesIO(raw)) as source,ZipFile(buffer,'w',ZIP_DEFLATED) as target:
                for name in source.namelist():
                    data=source.read(name)
                    if variant=='tamper' and name==m['assets'][0]['file']:data=b'changed'
                    if variant=='external' and name=='manifest.json':
                        manifest=json.loads(data);manifest['conversation']['designs'][0]['plan_path']='C:/secret.json';data=json.dumps(manifest)
                    target.writestr(name,data)
                if variant=='traversal':target.writestr('../escape.txt','bad')
            with self.subTest(variant=variant),self.assertRaises((ValueError,KeyError)):
                self.library.import_archive(buffer.getvalue())
        self.assertFalse((self.root/'escape.txt').exists())

    def test_interrupted_copy_publishes_no_version(self):
        self.archive();request=uuid4().hex
        with patch('scripts.library.atomic_json',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):self.library.save(self.record['id'],self.plan['design_id'],request,'Incomplete')
        self.assertFalse((self.library.folder/request).exists())
        self.assertFalse(list(self.library.folder.glob('.saving-*')))

    def test_invalid_reference_and_duplicates(self):
        self.archive();request=uuid4().hex
        with self.assertRaises(Exception):self.library.reference(self.record['id'],request,'bad.png',base64.b64encode(b'not image').decode())
        self.assertEqual(len(self.store.get(self.record['id'])['references']),1)
        ref=self.store.get(self.record['id'])['references'][0]
        raw=base64.b64encode(Path(ref['path']).read_bytes()).decode()
        self.library.reference(self.record['id'],ref['id'],ref['name'],raw)
        self.assertEqual(len(self.store.get(self.record['id'])['references']),1)

    def test_remove_every_image_then_upload_keeps_blueprint_and_round_trips(self):
        m=self.archive();original=(self.library.folder/m['id']/'manifest.json').read_bytes()
        selected=next(d for d in m['conversation']['designs'] if d['id']==m['design_id'])
        plan=(self.library.folder/m['id']/selected['plan_path']).read_bytes()
        images=[a['file'] for a in m['assets'] if a['file'].endswith('.png')]
        cleared=self.library.update_images(m['id'],uuid4().hex,remove=images)
        self.assertEqual(cleared['version'],2)
        self.assertFalse([a for a in cleared['assets'] if a['file'].endswith('.png')])
        self.assertFalse(cleared['conversation']['references'])
        self.assertNotIn('preview_path',cleared['conversation']['designs'][0])
        self.assertEqual((self.library.folder/cleared['id']/selected['plan_path']).read_bytes(),plan)
        self.assertEqual((self.library.folder/m['id']/'manifest.json').read_bytes(),original)
        uploads=[]
        for fmt in ('JPEG','PNG'):
            image=BytesIO();Image.new('RGB',(17,11),'blue').save(image,format=fmt)
            uploads.append(dict(name='Local.'+('jpg' if fmt=='JPEG' else 'png'),data=base64.b64encode(image.getvalue()).decode()))
        added=self.library.update_images(cleared['id'],uuid4().hex,images=uploads)
        self.assertEqual(len(added['conversation']['references']),2)
        exported=self.library.export(added['id'])
        other=BuildingLibrary(self.root/'imported',ConversationStore(self.root/'imported'/'data'/'conversations'))
        other.import_archive(exported)
        opened=other.open(added['id'],uuid4().hex)
        self.assertEqual(len(opened['references']),2)
        with patch.object(designs,'ROOT',other.root):
            resaved=other.save(opened['id'],opened['designs'][0]['id'],uuid4().hex,'Images retained')
        self.assertEqual(len([a for a in resaved['assets'] if a['file'].endswith('.png')]),2)

    def test_image_mutation_is_idempotent_and_rejects_non_images_or_bad_uploads(self):
        m=self.archive();request=uuid4().hex
        image=next(a['file'] for a in m['assets'] if a['file'].endswith('.png'))
        first=self.library.update_images(m['id'],request,remove=[image])
        self.assertEqual(self.library.update_images(m['id'],request,remove=[image]),first)
        plan=next(a['file'] for a in m['assets'] if a['file'].endswith('.json'))
        for removed in (plan,'../outside.png'):
            with self.assertRaises(ValueError):self.library.update_images(m['id'],uuid4().hex,remove=[removed])
        for name,data in [('bad.png',b'not an image'),('bad.svg',b'<svg/>')]:
            with self.assertRaises(ValueError):
                self.library.update_images(m['id'],uuid4().hex,images=[dict(name=name,data=base64.b64encode(data).decode())])
        self.assertEqual(len(self.library.catalog()),2)

    def test_failed_image_update_leaves_original_and_no_partial_version(self):
        m=self.archive();request=uuid4().hex
        image=next(a['file'] for a in m['assets'] if a['file'].endswith('.png'))
        with patch('scripts.library.atomic_json',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):self.library.update_images(m['id'],request,remove=[image])
        self.assertFalse((self.library.folder/request).exists());self.assertEqual(self.library.read(m['id']),m)
        self.assertFalse(list(self.library.folder.glob('.images-*')))

    def test_description_edit_survives_round_trip_and_resave_without_changing_assets(self):
        first=self.archive();request=uuid4().hex;description='A spacious lakeside cabin.\nKeep <windows> facing the water.'
        updated=self.library.update_description(first['id'],request,description)
        self.assertEqual(updated['version'],2);self.assertEqual(updated['description'],description)
        self.assertEqual(self.library.update_description(first['id'],request,description),updated)
        self.assertEqual(self.library.read(first['id']),first)
        self.assertEqual(updated['assets'],first['assets'])
        for asset in first['assets']:
            self.assertEqual((self.library.folder/first['id']/asset['file']).read_bytes(),
                             (self.library.folder/request/asset['file']).read_bytes())
        other=BuildingLibrary(self.root/'imported',ConversationStore(self.root/'imported'/'data'/'conversations'))
        imported=other.import_archive(self.library.export(request));self.assertEqual(imported['description'],description)
        opened=other.open(request,uuid4().hex);self.assertEqual(opened['archive_description'],description)
        with patch.object(designs,'ROOT',other.root):
            resaved=other.save(opened['id'],opened['designs'][0]['id'],uuid4().hex,'Cabin')
        self.assertEqual(resaved['description'],description)
        image=next(a['file'] for a in resaved['assets'] if a['file'].endswith('.png'))
        self.assertEqual(other.update_images(resaved['id'],uuid4().hex,remove=[image])['description'],description)

    def test_description_rejects_invalid_data_and_keeps_original_on_write_failure(self):
        first=self.archive();request=uuid4().hex
        for description in (None,42,'',' \n ','x'*8001):
            with self.assertRaises(ValueError):self.library.update_description(first['id'],request,description)
        self.assertEqual(self.library.update_description(first['id'],request,first['description']),first)
        with patch('scripts.library.atomic_json',side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):self.library.update_description(first['id'],request,'Updated cabin')
        self.assertFalse((self.library.folder/request).exists());self.assertFalse(list(self.library.folder.glob('.description-*')))
        self.assertEqual(self.library.read(first['id']),first)
        saved=self.library.update_description(first['id'],request,'x'*8000)
        with self.assertRaises(ValueError):self.library.update_description(first['id'],request,'Another description')
        self.assertEqual(len(saved['description']),8000)

    def test_archive_description_does_not_replace_a_new_blueprints_description(self):
        first=self.archive();updated=self.library.update_description(first['id'],uuid4().hex,'Custom archive description')
        opened=self.library.open(updated['id'],uuid4().hex)
        new_design,_=self.generate('minecraft:white_wool')
        current=self.store.get(self.record['id'])
        current.update(archive_description=opened['archive_description'],archive_description_design_id=opened['archive_description_design_id'])
        self.store._save(current)
        saved=self.library.save(current['id'],new_design['design_id'],uuid4().hex,'New roof')
        expected=json.loads(Path(new_design['plan_path']).read_text())['blueprint']['description']
        self.assertEqual(saved['description'],expected)

    def test_open_restores_paths_inside_execution_json_before_resaving(self):
        self.archive()
        log=self.root/'data'/'builds'/'execution.json';log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(json.dumps(dict(source_plan=self.plan['plan_path'],phase='completed')),encoding='utf-8')
        record=self.store.get(self.record['id']);record['builds']=[dict(id=uuid4().hex,status='completed',record_path=str(log))]
        self.store._save(record)
        saved=self.library.save(record['id'],self.plan['design_id'],uuid4().hex,'With execution record')
        opened=self.library.open(saved['id'],uuid4().hex)
        restored_log=Path(opened['archived_history']['builds'][0]['record_path'])
        restored_plan=Path(json.loads(restored_log.read_text(encoding='utf-8'))['source_plan'])
        self.assertTrue(restored_plan.is_absolute())
        self.assertTrue(restored_plan.is_file())
        again=self.library.save(opened['id'],opened['designs'][0]['id'],uuid4().hex,'Resaved')
        self.assertEqual(len(again['assets']),len(saved['assets']))

    def test_import_rejects_embedded_external_paths_and_windows_case_collisions(self):
        import hashlib
        from copy import deepcopy
        original=self.archive()
        for variant in ('embedded','case_collision'):
            manifest=deepcopy(original);files={a['file']:(self.library.folder/original['id']/a['file']).read_bytes() for a in original['assets']}
            name='assets/external.json' if variant=='embedded' else 'assets/PHOTO.png'
            raw=json.dumps(dict(source_plan='C:/outside/secret.json')).encode() if variant=='embedded' else b'one'
            files[name]=raw;manifest['assets'].append(dict(file=name,label=name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
            if variant=='case_collision':
                files['assets/photo.png']=b'two';manifest['assets'].append(dict(file='assets/photo.png',label='Other',sha256=hashlib.sha256(b'two').hexdigest(),size=3))
            manifest['id']=uuid4().hex
            buffer=BytesIO()
            with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json',json.dumps(manifest))
                for name,raw in files.items():archive.writestr(name,raw)
            with self.subTest(variant=variant),self.assertRaises(ValueError):self.library.import_archive(buffer.getvalue())
            self.assertFalse((self.library.folder/manifest['id']).exists())

    def test_malformed_catalog_entry_does_not_hide_valid_archives(self):
        saved=self.archive();broken=self.library.folder/uuid4().hex;broken.mkdir()
        (broken/'manifest.json').write_text('[]')
        rows=self.library.catalog()
        self.assertEqual(len(rows),2);self.assertTrue(any(row['id']==saved['id'] for row in rows))

    def test_working_copy_resave_retains_unlinked_image_metadata(self):
        self.archive();record=self.store.get(self.record['id'])
        preview=Path(record['designs'][0]['preview_path'])
        preview.with_suffix('.json').write_text(json.dumps(dict(source_plan=self.plan['plan_path'],quality='low')),encoding='utf-8')
        saved=self.library.save(record['id'],self.plan['design_id'],uuid4().hex,'With metadata')
        opened=self.library.open(saved['id'],uuid4().hex)
        again=self.library.save(opened['id'],opened['designs'][0]['id'],uuid4().hex,'Resaved metadata')
        self.assertEqual({a['label'] for a in again['assets']},{a['label'] for a in saved['assets']})


if __name__=='__main__':unittest.main()
