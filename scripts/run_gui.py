"""Run the local MineCreator chat interface in a desktop browser window."""

import argparse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
from threading import RLock, Thread
from time import monotonic, sleep
from urllib.parse import urlsplit, parse_qs
import webbrowser
import base64
import mimetypes

from .backend import MineCreatorBackend, error_details
from .chat import valid_id, validate_prompt
from .designs import plan_summary, preview_file, find_design
from .edits import selected_build
from .plan_with_openai import ROOT, read_settings
from .paths import ASSET_ROOT
from . import settings
from .library import BuildingLibrary

UI = ASSET_ROOT / 'ui'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/workbench.js': ('workbench.js', 'text/javascript; charset=utf-8'),
          '/icon.svg': ('icon.svg', 'image/svg+xml'),
          '/fonts/Silkscreen-Regular.ttf': ('fonts/Silkscreen-Regular.ttf', 'font/ttf')}


class ChatApplication:
    def __init__(self, backend=None):
        self.backend = backend or MineCreatorBackend()
        self.backend.conversations.recover()
        self.library = BuildingLibrary(ROOT, self.backend.conversations)
        self.token = secrets.token_hex(32)
        self.lock = RLock()
        self.jobs = OrderedDict()
        self.latest = None
        self.last_seen = monotonic()
        self.connected = False
        self.window_closed_at = None

    def state(self):
        configuration=dict(configured=False,model='gpt-6-luna',game_dir=str(self.backend.game_dir or ''),data_dir=str(ROOT))
        try:
            configuration.update(settings.public(ROOT))
            _, model = read_settings()
            configuration.update(configured=True, model=model)
        except ValueError as exc:
            configuration.update(configured=False, message=str(exc))
        with self.lock:
            return dict(configuration=configuration, conversations=self.backend.conversations.list(),
                        active=self.latest if self.latest and not self.jobs[self.latest['id']]['task'].done else None)

    def require_idle(self):
        if self.latest and not self.jobs[self.latest['id']]['task'].done:
            raise ValueError('Wait for the current operation to finish or stop it first.')

    def local_action(self, action, payload):
        with self.lock:
            self.require_idle()
            if action=='settings':
                return settings.save(ROOT,payload)
            if action=='pick-directory':
                import tkinter as tk
                from tkinter import filedialog
                window=tk.Tk();window.withdraw();window.attributes('-topmost',True)
                try:return dict(path=filedialog.askdirectory(parent=window,title='Select Minecraft game directory'))
                finally:window.destroy()
            if action=='library-save':
                m=self.library.save(valid_id(payload.get('conversation_id')),valid_id(payload.get('design_id')),
                                    valid_id(payload.get('request_id')),payload.get('name'),payload.get('notes',''),payload.get('library_id'))
                return dict(id=m['id'],name=m['name'],version=m['version'])
            if action=='library-open':
                return self.library.open(valid_id(payload.get('id')),valid_id(payload.get('request_id')))
            if action=='library-images':
                m=self.library.update_images(valid_id(payload.get('id')),valid_id(payload.get('request_id')),
                                             payload.get('remove'),payload.get('images'))
                return dict(id=m['id'],name=m['name'],version=m['version'])
            if action=='library-description':
                m=self.library.update_description(valid_id(payload.get('id')),valid_id(payload.get('request_id')),payload.get('description'))
                return dict(id=m['id'],name=m['name'],version=m['version'])
            if action=='library-import':
                m=self.library.import_archive(base64.b64decode(payload.get('data',''),validate=True))
                return dict(id=m['id'],name=m['name'],version=m['version'])
            if action=='reference':
                return self.library.reference(valid_id(payload.get('conversation_id')),valid_id(payload.get('request_id')),
                                              payload.get('name'),payload.get('data'))
            raise ValueError('Unsupported action.')

    def send(self, payload):
        prompt = validate_prompt(payload.get('prompt'))
        return self.start_operation(payload, 'chat', prompt=prompt)

    def generate_plan(self, payload):
        return self.start_operation(payload, 'design_plan')

    def generate_image(self, payload):
        design_id = valid_id(payload.get('design_id'))
        find_design(self.backend.conversations.get(valid_id(payload.get('conversation_id'))), design_id)
        return self.start_operation(payload, 'design_image', design_id=design_id)

    def build(self, payload):
        if payload.get('confirmed') is not True:
            raise ValueError('Use Build or explicitly ask to start construction in chat.')
        design_id = valid_id(payload.get('design_id'))
        find_design(self.backend.conversations.get(valid_id(payload.get('conversation_id'))), design_id)
        return self.start_operation(payload, 'design_build', design_id=design_id, confirmed=True)

    def conversation(self, conversation_id):
        record = self.backend.conversations.get(conversation_id)
        record['edit_target'] = selected_build(record)
        try:
            record['design'] = plan_summary(record)
        except Exception:
            record['design'] = None
            record['design_error'] = 'The saved plan could not be loaded. Generate a new plan from this conversation.'
        return record

    def select_target(self, payload):
        with self.lock:
            if self.latest and not self.jobs[self.latest['id']]['task'].done:
                raise ValueError('Wait for the current operation before changing the target.')
            self.backend.conversations.select_build(valid_id(payload.get('conversation_id')), valid_id(payload.get('build_id')))
        return self.conversation(payload['conversation_id'])

    def apply_edit(self, payload):
        if payload.get('confirmed') is not True:
            raise ValueError('Confirm the edit summary before applying it.')
        return self.start_operation(payload, 'apply_edit', review_id=valid_id(payload.get('review_id')), confirmed=True)

    def start_operation(self, payload, operation, **arguments):
        conversation_id = valid_id(payload.get('conversation_id'))
        request_id = valid_id(payload.get('request_id'))
        self.backend.conversations.get(conversation_id)
        signature = dict(operation=operation, conversation_id=conversation_id, **arguments)
        with self.lock:
            if request_id in self.jobs:
                job = self.jobs[request_id]
                if job['signature'] != signature:
                    raise ValueError('Request ID was already used for a different action.')
                return dict(id=request_id, conversation_id=conversation_id, operation=operation)
            task = self.backend.start(operation, conversation_id=conversation_id, request_id=request_id, **arguments)
            self.jobs[request_id] = dict(task=task, events=[], signature=signature)
            self.latest = dict(id=request_id, conversation_id=conversation_id, operation=operation)
            while len(self.jobs) > 100:
                self.jobs.popitem(last=False)
            return self.latest

    def poll(self, job_id, cursor):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('Request not found. Reload the conversation.')
            job = self.jobs[job_id]
            task = job['task']
            done = task.done
            job['events'].extend(task.drain_events())
            return dict(events=job['events'][cursor:], cursor=len(job['events']), done=done,
                        outcome=task.result().to_dict() if done else None)


def make_server(app: ChatApplication, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            data = json.dumps(body, ensure_ascii=False).encode('utf-8') if isinstance(body, (dict, list)) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, api=False):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != origin.removeprefix('http://'):
                self.reply(403, dict(error='Invalid local host.'))
                return False
            if self.headers.get('Origin') not in (None, origin):
                self.reply(403, dict(error='Cross-origin requests are not allowed.'))
                return False
            if api and not secrets.compare_digest(self.headers.get('X-MineCreator-Token', ''), app.token):
                self.reply(403, dict(error='Reload MineCreator to reconnect.'))
                return False
            app.last_seen = monotonic()
            if self.path != '/api/window-close':
                app.window_closed_at = None
            if api:
                app.connected = True
            return True

        def do_GET(self):
            try:
                url = urlsplit(self.path)
                if not self.authorized(url.path.startswith('/api/')):
                    return
                if url.path in ASSETS:
                    filename, mime = ASSETS[url.path]
                    data = (UI / filename).read_bytes()
                    if url.path == '/':
                        data = data.replace(b'__APP_TOKEN__', app.token.encode('ascii'))
                    self.reply(200, data, mime)
                elif url.path == '/api/state':
                    self.reply(200, app.state())
                elif url.path == '/api/library':
                    self.reply(200, app.library.catalog())
                elif url.path in ('/api/library-detail','/api/library-asset','/api/library-export'):
                    query=parse_qs(url.query);version=valid_id(query.get('id',[''])[0])
                    if url.path.endswith('-detail'):
                        self.reply(200,app.library.read(version))
                    elif url.path.endswith('-export'):
                        self.reply(200,app.library.export(version),'application/zip')
                    else:
                        path=app.library.asset(version,query.get('file',[''])[0])
                        self.reply(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                elif url.path == '/api/reference':
                    query=parse_qs(url.query);record=app.backend.conversations.get(valid_id(query.get('conversation_id',[''])[0]))
                    ref=next((item for item in record.get('references',[]) if item['id']==query.get('id',[''])[0]),None)
                    if ref is None:raise ValueError('Reference not found.')
                    from .designs import local_file
                    path=local_file(ref['path'],(ROOT/'data'/'references',ROOT/'data'/'plans'))
                    self.reply(200,path.read_bytes(),'image/png')
                elif url.path.startswith('/api/conversations/'):
                    self.reply(200, app.conversation(url.path.rsplit('/', 1)[1]))
                elif url.path.startswith('/api/preview/'):
                    parts = url.path.split('/')
                    if len(parts) != 6:
                        raise ValueError('Invalid preview request.')
                    record = app.backend.conversations.get(valid_id(parts[3]))
                    image_id = parse_qs(url.query).get('image', [None])[0]
                    path = preview_file(record, valid_id(parts[4]), parts[5], image_id=image_id)
                    self.reply(200, path.read_bytes(), 'image/png')
                elif url.path == '/api/task':
                    query = parse_qs(url.query)
                    cursor = int(query.get('cursor', ['0'])[0])
                    if cursor < 0:
                        raise ValueError('Invalid event cursor.')
                    self.reply(200, app.poll(valid_id(query.get('id', [''])[0]), cursor))
                elif url.path == '/api/heartbeat':
                    self.reply(200, dict(ok=True))
                else:
                    self.reply(404, dict(error='Page not found.'))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.reply(400, dict(error=error_details(exc)['message']))

        def do_POST(self):
            try:
                if not self.authorized(True):
                    return
                size = int(self.headers.get('Content-Length', '0'))
                limit = 180*1024*1024 if self.path=='/api/library-import' else 48*1024*1024 if self.path=='/api/library-images' else 12*1024*1024 if self.path=='/api/reference' else 65536
                if not 0 < size <= limit:
                    raise ValueError('Invalid request size.')
                if self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Use JSON requests.')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid request body.')
                if self.path == '/api/conversations':
                    self.reply(200, app.backend.conversations.create())
                elif self.path == '/api/chat':
                    self.reply(202, app.send(payload))
                elif self.path == '/api/connection':
                    self.reply(202,app.start_operation(payload,'connection'))
                elif self.path in ('/api/settings','/api/pick-directory','/api/library-save','/api/library-open','/api/library-import','/api/library-images','/api/library-description','/api/reference'):
                    self.reply(200,app.local_action(self.path.removeprefix('/api/'),payload))
                elif self.path == '/api/plan':
                    self.reply(202, app.generate_plan(payload))
                elif self.path == '/api/image':
                    self.reply(202, app.generate_image(payload))
                elif self.path == '/api/build':
                    self.reply(202, app.build(payload))
                elif self.path == '/api/edit-target':
                    self.reply(200, app.select_target(payload))
                elif self.path == '/api/apply-edit':
                    self.reply(202, app.apply_edit(payload))
                elif self.path == '/api/cancel':
                    with app.lock:
                        job = app.jobs.get(valid_id(payload.get('id')))
                        if job is None:
                            raise ValueError('Request not found.')
                        self.reply(200, dict(stopping=job['task'].cancel()))
                elif self.path == '/api/window-close':
                    app.window_closed_at = monotonic()
                    self.reply(200, dict(ok=True))
                elif self.path == '/api/quit':
                    self.reply(200, dict(ok=True))
                    Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.reply(404, dict(error='Action not found.'))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.reply(400, dict(error=error_details(exc)['message']))

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def window_should_close(app, current_time):
    # Browsers throttle or suspend background tabs. A missing heartbeat is not
    # evidence that the user closed a window. Reload/new requests cancel closing.
    if app.window_closed_at is not None:
        return current_time - app.window_closed_at > 10
    return not app.connected and current_time - app.last_seen > 120


def open_window(url):
    candidates = [Path(os.environ.get(variable, '')) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
                  for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')]
    edge = next((path for path in candidates if path.is_file()), None)
    if edge:
        subprocess.Popen([str(edge), f'--app={url}', '--window-size=1440,960', '--no-first-run',
                          f'--user-data-dir={ROOT / "data" / "gui" / "browser"}'],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-open', action='store_true', help='Serve the UI without opening a window')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    # Hold a local lock for the lifetime of the app to avoid concurrent writers.
    lock_file = None
    if os.name == 'nt':
        import msvcrt
        folder = ROOT / 'data' / 'gui'
        folder.mkdir(parents=True, exist_ok=True)
        lock_file = (folder / 'app.lock').open('a+b')
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            raise ValueError('MineCreator is already running. Use Settings > Quit, or close its window and wait 10 seconds.') from None
    app = ChatApplication()
    server = make_server(app, args.port)
    url = f'http://127.0.0.1:{server.server_port}'
    if not args.no_open:
        open_window(url)

        def watch_window():
            while True:
                sleep(2)
                if window_should_close(app, monotonic()):
                    server.shutdown()
                    return

        Thread(target=watch_window, daemon=True).start()
    if hasattr(__import__('sys').stdout, 'write'):
        print(f'MineCreator: {url}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.backend.close()
        if lock_file:
            lock_file.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import sys
        if sys.stdout is None and os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'MineCreator could not start. Check that it is not already running and the project environment is installed. Run python -m scripts.run_gui from the project folder for diagnostics.', 'MineCreator', 0x10)
        else:
            raise
