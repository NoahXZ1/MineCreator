"""Serve an isolated desktop test workspace; never call real API or Minecraft."""
import sys
import base64
from io import BytesIO
from PIL import Image
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import designs, plan_with_openai, preview_with_openai, run_gui, backend as backend_module
from scripts.backend import MineCreatorBackend
from scripts.chat import ConversationStore
from scripts.operations import OperationContext
from test_designs import sample_result

class OfflineBackend(MineCreatorBackend):
    def _execute(self, operation, context, **arguments):
        if operation=='connection':raise RuntimeError('Minecraft is disconnected. Open a single-player world.')
        raise AssertionError('Browser fixture must not call the model or change the world.')

with TemporaryDirectory() as temp:
    root=Path(temp)
    for module in (designs,plan_with_openai,preview_with_openai,run_gui,backend_module):module.ROOT=root
    backend=OfflineBackend();backend.conversations=ConversationStore(root/'data'/'conversations')
    record=backend.conversations.create();turn=uuid4().hex
    backend.conversations.begin(record['id'],turn,'Design a small pavilion.')
    backend.conversations.finish(record['id'],turn,'An oak pavilion with a gable roof.','completed')
    with patch.object(designs,'generate_plan',return_value=sample_result()):
        designs.generate_design(backend.conversations,record['id'],uuid4().hex,OperationContext('fixture',lambda _:None))
    app=run_gui.ChatApplication(backend)
    # Existing conversations can retain references created before uploads moved to Library.
    image=BytesIO();Image.new('RGB',(12,9),'green').save(image,format='PNG')
    app.library.reference(record['id'],uuid4().hex,'Existing reference.png',base64.b64encode(image.getvalue()).decode())
    server=run_gui.make_server(app,8790)
    print('Workbench fixture: http://127.0.0.1:8790',flush=True)
    try:server.serve_forever()
    finally:server.server_close();backend.close()
