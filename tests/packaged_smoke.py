"""Launch the real Windows executable with isolated data and no paid/game calls."""
import base64
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.request

repo=Path(__file__).resolve().parents[1]
exe=repo/'dist'/'MineCreator'/'MineCreator.exe'
source_mode='--source' in sys.argv
with TemporaryDirectory(prefix='minecreator-packaged-') as temporary:
    root=Path(temporary)
    env=dict(os.environ,MINECREATOR_HOME=str(root))
    env.pop('OPENAI_API_KEY',None);env.pop('OPENAI_MODEL',None)
    def launch():
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        command=[sys.executable,'-m','scripts.run_gui'] if source_mode else [str(exe)]
        process=subprocess.Popen(command+['--no-open','--port',str(port)],env=env,cwd=repo if source_mode else temporary,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(50):
                if process.poll() is not None:raise AssertionError(f'EXE exited: {process.returncode}')
                try:
                    html=urllib.request.urlopen(base,timeout=.5).read().decode();break
                except OSError:time.sleep(.2)
            else:raise AssertionError('Packaged app did not start.')
            token=re.search('name="minecreator-token" content="([^"]+)"',html).group(1)
            def call(path,body=None,binary=False):
                request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','X-MineCreator-Token':token})
                raw=urllib.request.urlopen(request,timeout=10).read()
                return raw if binary else json.loads(raw)
            return process,call
        except Exception:
            process.terminate();process.wait(timeout=5);raise
    process,call=launch()
    try:
        state=call('/api/state');assert not state['configuration']['configured']
        assert b'Building library' in call('/',binary=True)
        assert b'open-library' in call('/workbench.js',binary=True)
        fixture=(repo/'data'/'browser-library-test.zip').read_bytes()
        saved=call('/api/library-import',dict(data=base64.b64encode(fixture).decode()))
        from uuid import uuid4
        opened=call('/api/library-open',dict(id=saved['id'],request_id=uuid4().hex))
        assert not opened.get('builds');design=opened['designs'][0]
        preview=call(f"/api/preview/{opened['id']}/{design['id']}/blocks",binary=True)
        assert preview.startswith(b'\x89PNG')
        manifest=call('/api/library-detail?id='+saved['id'])
        images=[asset['file'] for asset in manifest['assets'] if asset['file'].endswith('.png')]
        cleared=call('/api/library-images',dict(id=saved['id'],request_id=uuid4().hex,remove=images))
        added=call('/api/library-images',dict(id=cleared['id'],request_id=uuid4().hex,
            images=[dict(name='Packaged upload.png',data=base64.b64encode(preview).decode())]))
        assert added['version']==3
        manifest=call('/api/library-detail?id='+added['id'])
        assert len(manifest['conversation']['references'])==1
        described=call('/api/library-description',dict(id=added['id'],request_id=uuid4().hex,description='A revised library description.'))
        assert described['version']==4
        result=call('/api/settings',dict(model='offline-model',api_key='sk-fake-packaged',game_dir=str(root)))
        assert result['configured'] and 'key' not in result
        assert 'sk-fake-packaged' not in (root/'data'/'settings.json').read_text()
        call('/api/quit',{});process.wait(timeout=10);assert process.returncode==0
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
    process,call=launch()
    try:
        state=call('/api/state');assert state['configuration']['model']=='offline-model'
        assert state['configuration']['configured'];assert len(call('/api/library'))==4
        assert call('/api/library-detail?id='+described['id'])['description']=='A revised library description.'
        assert len(state['conversations'])==1
        call('/api/quit',{});process.wait(timeout=10)
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=5)
print(f"PASS: {'source process' if source_mode else 'actual EXE'} startup, library import/open/preview, image and description edits, encrypted settings and restart persistence; no API or Minecraft calls.")
