"""Immutable, self-contained building versions and portable archive files."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image, ImageOps, UnidentifiedImageError

from .chat import now, valid_id
from .designs import read_saved_plan, local_file
from .settings import atomic_json

MAX_BYTES = 128 * 1024 * 1024
PATH_KEYS = {'plan_path','preview_path','path','record_path','source_plan','source_build','build_path','prior_edit','modification_path'}
FOLDERS = ('plans','previews','builds','modifications','edit-proposals','references')
IMAGE_SUFFIXES = {'.png','.jpg','.jpeg','.webp'}


def uploaded_image(name, encoded):
    name=label(name,180)
    if Path(name).suffix.lower() not in {'.png','.jpg','.jpeg'}:
        raise ValueError('Upload a JPG or PNG image.')
    if not isinstance(encoded,str) or len(encoded)>12*1024*1024:
        raise ValueError('Each image must be at most 8 MB.')
    try:
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>8*1024*1024:raise ValueError('Each image must be at most 8 MB.')
        with Image.open(BytesIO(raw)) as img:
            if img.format not in {'JPEG','PNG'}:raise ValueError('Upload a JPG or PNG image.')
            if img.width*img.height>25_000_000:raise ValueError('The image exceeds 25 million pixels.')
            img.verify()
        with Image.open(BytesIO(raw)) as img:
            output=BytesIO()
            mode='RGBA' if 'A' in img.getbands() or 'transparency' in img.info else 'RGB'
            ImageOps.exif_transpose(img).convert(mode).save(output,format='PNG')
        return name, output.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise ValueError('This image is damaged or is not a supported JPG/PNG file.') from None


def label(value, maximum=120):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValueError(f'Enter a name with 1-{maximum} characters.')
    return value.strip()


class BuildingLibrary:
    def __init__(self, root, store):
        self.root, self.store = root, store
        self.folder = root / 'data' / 'library'

    def read(self, version_id):
        folder = self.folder / valid_id(version_id)
        try:
            manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            self.validate(manifest, lambda name:(folder/name).read_bytes())
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError('This archive is missing files or failed its integrity check. Import an intact copy.') from exc
        return manifest

    @staticmethod
    def validate(manifest, read):
        if not isinstance(manifest,dict) or manifest.get('format') != 'minecreator-library-v1':
            raise ValueError('Unsupported building archive format.')
        valid_id(manifest['id']);valid_id(manifest['library_id'])
        valid_id(manifest['design_id']);label(manifest['name'])
        if type(manifest['version']) is not int or manifest['version']<1:
            raise ValueError('Invalid archive version.')
        if not all(isinstance(manifest[k],str) for k in ('created_at','title','description')):
            raise ValueError('Invalid archive description.')
        if not isinstance(manifest['notes'],str) or len(manifest['notes'])>8000:
            raise ValueError('Invalid archive notes.')
        assets = manifest['assets']
        if not isinstance(assets,list) or len(assets)>2000:
            raise ValueError('Invalid archive asset list.')
        names=set();folded=set();documents=[];total=0
        for asset in assets:
            name=asset['file']; p=PurePosixPath(name)
            if not isinstance(name,str) or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or len(p.parts)!=2 or p.parts[0]!='assets' or name in names or p.suffix.lower() not in {'.json','.txt','.png','.jpg','.jpeg','.webp'}:
                raise ValueError('Invalid archive file path.')
            if name.casefold() in folded:raise ValueError('Archive has conflicting file names on Windows.')
            names.add(name);folded.add(name.casefold())
            raw=read(name);total+=len(raw)
            if total>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=asset['sha256']:
                raise ValueError('Archive integrity check failed.')
            if p.suffix.lower()=='.json':documents.append(json.loads(raw))
        def check(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in PATH_KEYS and item and (not isinstance(item,str) or item not in names):
                        raise ValueError('Archive references an external or missing file.')
                    check(item)
            elif isinstance(value,list):
                for item in value:check(item)
        check(manifest['conversation'])
        for document in documents:check(document)
        selected=next((item for item in manifest['conversation']['designs'] if item['id']==manifest['design_id']),None)
        if selected is None:raise ValueError('The selected blueprint is missing from this archive.')
        from .blueprint import Blueprint, expand_blueprint
        plan=json.loads(read(selected['plan_path']))
        if expand_blueprint(Blueprint.model_validate(plan['blueprint'])) != plan['expanded_stages']:
            raise ValueError('Archived blueprint and block data differ.')

    def catalog(self):
        rows=[]
        for path in self.folder.glob('*/manifest.json'):
            if path.parent.name.startswith('.'):
                continue
            try:
                m=json.loads(path.read_text(encoding='utf-8'))
                row={k:m[k] for k in ('id','library_id','name','version','created_at','notes','title')}
                valid_id(row['id']);valid_id(row['library_id'])
                if row['id']!=path.parent.name or type(row['version']) is not int or row['version']<1 or not all(isinstance(row[k],str) for k in ('name','created_at','notes','title')):
                    raise ValueError('Invalid archive metadata.')
                rows.append(row)
            except (OSError,KeyError,ValueError,TypeError):
                rows.append(dict(id=path.parent.name,name='Unreadable archive',version='?',created_at='',notes='Could not read this archive.',library_id='',title=''))
        return sorted(rows,key=lambda x:x['created_at'],reverse=True)

    def save(self, conversation_id, design_id, request_id, name, notes='', library_id=None):
        version_id=valid_id(request_id);name=label(name)
        if not isinstance(notes,str) or len(notes)>8000:
            raise ValueError('Notes must be at most 8000 characters.')
        signature=dict(conversation_id=conversation_id,design_id=design_id,name=name,notes=notes,library_id=library_id)
        if (self.folder/version_id).exists():
            result=self.read(version_id)
            if result.get('save_request')!=signature:raise ValueError('Request ID already used for another archive.')
            return result
        record=self.store.get(conversation_id)
        if any(t['status']=='pending' for t in record['turns']):raise ValueError('Wait for the current operation to finish.')
        selected=next((d for d in record.get('designs',[]) if d['id']==design_id and d.get('plan_path')),None)
        if selected is None:raise ValueError('Select a saved blueprint version first.')
        _,plan=read_saved_plan(selected['plan_path'])
        group=valid_id(library_id) if library_id else uuid4().hex
        versions=[m for m in self.catalog() if m['library_id']==group]
        if library_id and not versions:raise ValueError('The building archive no longer exists.')
        self.folder.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='.saving-',dir=self.folder) as temp:
            stage=Path(temp);(stage/'assets').mkdir()
            assets=[];mapped={};total=0
            def copy_file(value):
                nonlocal total
                path=local_file(value,tuple(self.root/'data'/f for f in FOLDERS)+(self.folder,))
                if path in mapped:return mapped[path]
                if path.suffix.lower() not in {'.json','.png','.jpg','.jpeg','.webp','.txt'}:
                    raise ValueError('Unsupported archive asset type.')
                destination=f'assets/{len(mapped):04d}{path.suffix.lower()}'
                mapped[path]=destination
                raw=path.read_bytes()
                if path.suffix.lower()=='.json':
                    raw=json.dumps(rewrite(json.loads(raw)),ensure_ascii=False,indent=2).encode('utf-8')
                total+=len(raw)
                if total>MAX_BYTES:raise ValueError('The archive exceeds 128 MB.')
                (stage/destination).write_bytes(raw)
                assets.append(dict(file=destination,label=path.name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
                if path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'} and path.with_suffix('.json').is_file():
                    copy_file(str(path.with_suffix('.json')))
                return destination
            def rewrite(value):
                if isinstance(value,dict):
                    return {k:copy_file(v) if k in PATH_KEYS and isinstance(v,str) and v else rewrite(v) for k,v in value.items()}
                if isinstance(value,list):return [rewrite(v) for v in value]
                return value
            # Save every linked plan, image, reference and execution record, including history.
            snapshot=rewrite(deepcopy(record))
            for reference in snapshot.get('archive_assets',[])+snapshot.get('references',[]):
                for asset in assets:
                    if asset['file']==reference['path']:asset['label']=reference['name']
            manifest=dict(format='minecreator-library-v1',id=version_id,library_id=group,
                          version=max((m['version'] for m in versions),default=0)+1,name=name,notes=notes,
                          title=plan['blueprint']['title'],
                          description=record['archive_description'] if record.get('archive_description_design_id')==design_id else plan['blueprint']['description'],
                          created_at=now(),design_id=design_id,conversation=snapshot,assets=assets,save_request=signature)
            self.validate(manifest,lambda name:(stage/name).read_bytes())
            atomic_json(stage/'manifest.json',manifest)
            stage.rename(self.folder/version_id)
        return manifest

    def asset(self, version_id, name):
        manifest=self.read(version_id)
        if name not in {a['file'] for a in manifest['assets']}:raise ValueError('Asset not found.')
        return self.folder/version_id/name

    def update_description(self, version_id, request_id, description):
        version_id=valid_id(version_id);request_id=valid_id(request_id)
        if not isinstance(description,str) or not description.strip() or len(description)>8000:
            raise ValueError('Enter a building description with 1-8000 characters.')
        signature=dict(source=version_id,description=description)
        if (self.folder/request_id).exists():
            result=self.read(request_id)
            if result.get('description_request')!=signature:raise ValueError('Request ID already used for another description change.')
            return result
        original=self.read(version_id)
        if description==original['description']:return original
        manifest=deepcopy(original)
        versions=[m['version'] for m in self.catalog() if m['library_id']==original['library_id']]
        for key in ('save_request','image_request'):manifest.pop(key,None)
        manifest.update(id=request_id,version=max(versions)+1,created_at=now(),parent_version=version_id,
                        description=description,description_request=signature)
        manifest['conversation'].update(archive_description=description,archive_description_design_id=manifest['design_id'])
        # Description edits are archive metadata; blueprint and execution records stay intact.
        with TemporaryDirectory(prefix='.description-',dir=self.folder) as temporary:
            stage=Path(temporary);(stage/'assets').mkdir()
            for asset in manifest['assets']:
                (stage/asset['file']).write_bytes((self.folder/version_id/asset['file']).read_bytes())
            self.validate(manifest,lambda name:(stage/name).read_bytes())
            atomic_json(stage/'manifest.json',manifest)
            stage.rename(self.folder/request_id)
        return manifest

    def update_images(self, version_id, request_id, remove=None, images=None):
        """Publish one complete new version; never leave dangling image references."""
        version_id=valid_id(version_id);request_id=valid_id(request_id)
        remove=[] if remove is None else remove;images=[] if images is None else images
        if not isinstance(remove,list) or not all(isinstance(v,str) for v in remove) or not isinstance(images,list) or len(images)>30:
            raise ValueError('Choose valid images to remove or upload (up to 30 per request).')
        if not remove and not images:raise ValueError('Choose at least one image change.')
        signature=hashlib.sha256(json.dumps(dict(source=version_id,remove=sorted(set(remove)),images=images),sort_keys=True).encode()).hexdigest()
        if (self.folder/request_id).exists():
            result=self.read(request_id)
            if result.get('image_request')!=signature:raise ValueError('Request ID already used for another image change.')
            return result
        original=self.read(version_id)
        image_files={a['file'] for a in original['assets'] if PurePosixPath(a['file']).suffix.lower() in IMAGE_SUFFIXES}
        removed=set(remove)
        if not removed<=image_files:raise ValueError('Only images in the selected archive can be removed.')
        uploads=[]
        for item in images:
            if not isinstance(item,dict):raise ValueError('Invalid image upload.')
            uploads.append(uploaded_image(item.get('name'),item.get('data')))
        def prune(value):
            if isinstance(value,dict):
                if isinstance(value.get('path'),str) and value['path'] in removed:return None
                result={k:prune(v) for k,v in value.items() if not (k in PATH_KEYS and isinstance(v,str) and v in removed)}
                if isinstance(value.get('preview_path'),str) and value['preview_path'] in removed:
                    result['preview_error']='Block preview removed from this archive version.'
                return result
            if isinstance(value,list):return [result for item in value if (result:=prune(item)) is not None]
            return value
        manifest=deepcopy(original)
        manifest['conversation']=prune(manifest['conversation'])
        refs=manifest['conversation'].setdefault('references',[])
        if len(refs)+len(uploads)>30:raise ValueError('An archive supports up to 30 uploaded references; remove one before adding more.')
        versions=[m['version'] for m in self.catalog() if m['library_id']==original['library_id']]
        manifest.update(id=request_id,version=max(versions)+1,created_at=now(),parent_version=version_id,image_request=signature)
        manifest.pop('save_request',None)
        manifest.pop('description_request',None)
        with TemporaryDirectory(prefix='.images-',dir=self.folder) as temporary:
            stage=Path(temporary);(stage/'assets').mkdir();assets=[]
            for asset in original['assets']:
                if asset['file'] in removed:continue
                raw=(self.folder/version_id/asset['file']).read_bytes()
                if asset['file'].endswith('.json'):
                    value=json.loads(raw);cleaned=prune(value)
                    if value!=cleaned:raw=json.dumps(cleaned,ensure_ascii=False,indent=2).encode('utf-8')
                (stage/asset['file']).write_bytes(raw)
                assets.append(dict(asset,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
            for name,raw in uploads:
                ref_id=uuid4().hex;file=f'assets/{ref_id}.png';digest=hashlib.sha256(raw).hexdigest()
                (stage/file).write_bytes(raw)
                assets.append(dict(file=file,label=name,sha256=digest,size=len(raw)))
                refs.append(dict(id=ref_id,name=name,path=file,sha256=digest))
            manifest['assets']=assets
            self.validate(manifest,lambda name:(stage/name).read_bytes())
            atomic_json(stage/'manifest.json',manifest)
            stage.rename(self.folder/request_id)
        return manifest

    def open(self, version_id, request_id):
        manifest=self.read(version_id);new_id=valid_id(request_id)
        try:
            existing=self.store.get(new_id)
        except ValueError:existing=None
        if existing:
            if existing.get('library_origin',{}).get('id')!=version_id:raise ValueError('Request ID already used.')
            return existing
        # Materialize independent copies. The archive is immutable; no old world binding is live.
        folder=self.root/'data'/'plans'/f'library-{new_id}'
        folder.mkdir(parents=True,exist_ok=True)
        def restore(value):
            if isinstance(value,dict):
                return {k:str((folder/Path(v).name).resolve()) if k in PATH_KEYS and v else restore(v) for k,v in value.items()}
            if isinstance(value,list):return [restore(v) for v in value]
            return value
        for asset in manifest['assets']:
            raw=(self.folder/version_id/asset['file']).read_bytes()
            if asset['file'].lower().endswith('.json'):
                raw=json.dumps(restore(json.loads(raw)),ensure_ascii=False,indent=2).encode('utf-8')
            (folder/Path(asset['file']).name).write_bytes(raw)
        archived=restore(deepcopy(manifest['conversation']))
        turns=[{k:t[k] for k in ('id','user','assistant','status','created_at')} for t in archived['turns'] if t['status']=='completed']
        chosen=next(d for d in archived['designs'] if d['id']==manifest['design_id'])
        chosen=deepcopy(chosen);chosen['source_turn_ids']=[t['id'] for t in turns]
        record=dict(id=new_id,title=manifest['name'],created_at=now(),updated_at=now(),turns=turns,designs=[chosen],
                    references=archived.get('references',[]),library_origin={k:manifest[k] for k in ('id','library_id','version','name')},
                    archived_history=archived,archive_notes=manifest['notes'],
                    archive_description=manifest['description'],archive_description_design_id=manifest['design_id'],
                    archive_assets=[dict(path=str((folder/Path(a['file']).name).resolve()),name=a['label']) for a in manifest['assets']])
        with self.store.lock:self.store._save(record)
        return record

    def export(self, version_id):
        manifest=self.read(version_id);buffer=BytesIO()
        with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            for asset in manifest['assets']:
                archive.writestr(asset['file'],(self.folder/version_id/asset['file']).read_bytes())
        return buffer.getvalue()

    def import_archive(self, raw):
        if len(raw)>MAX_BYTES:raise ValueError('Archive exceeds 128 MB.')
        with ZipFile(BytesIO(raw)) as archive:
            files=archive.infolist()
            if len(files)>2001 or sum(f.file_size for f in files)>MAX_BYTES or len({f.filename for f in files})!=len(files):
                raise ValueError('Archive is too large or has duplicate entries.')
            manifest=json.loads(archive.read('manifest.json'))
            self.validate(manifest,archive.read)
            allowed={'manifest.json'}|{a['file'] for a in manifest['assets']}
            if {f.filename for f in files}!=allowed:raise ValueError('Archive contains unexpected files.')
            destination=self.folder/manifest['id']
            if destination.exists():
                if self.read(manifest['id'])!=manifest:raise ValueError('A different archive already uses this ID.')
                return manifest
            self.folder.mkdir(parents=True,exist_ok=True)
            with TemporaryDirectory(prefix='.import-',dir=self.folder) as temp:
                stage=Path(temp);(stage/'assets').mkdir()
                for name in allowed:(stage/name).write_bytes(archive.read(name))
                stage.rename(destination)
        return manifest

    def reference(self, conversation_id, request_id, name, encoded):
        name=label(name,180);reference_id=valid_id(request_id)
        if not isinstance(encoded,str) or len(encoded)>12*1024*1024:raise ValueError('Reference image exceeds 8 MB.')
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>8*1024*1024:raise ValueError('Reference image exceeds 8 MB.')
        with Image.open(BytesIO(raw)) as img:
            if img.width*img.height>25_000_000:raise ValueError('Reference image is too large.')
            img.verify()
        with Image.open(BytesIO(raw)) as img:
            output=BytesIO();img.convert('RGB').save(output,format='PNG')
        folder=self.root/'data'/'references';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'{reference_id}.png'
        with self.store.lock:
            record=self.store.get(conversation_id)
            references=record.setdefault('references',[])
            existing=next((r for r in references if r['id']==reference_id),None)
            item=dict(id=reference_id,name=name,path=str(path.resolve()),sha256=hashlib.sha256(output.getvalue()).hexdigest())
            if existing:
                if existing!=item:raise ValueError('Reference request ID already used.')
                return existing
            if len(references)>=30:raise ValueError('A conversation supports up to 30 reference images.')
            path.write_bytes(output.getvalue());references.append(item);record['updated_at']=now();self.store._save(record)
        return item
