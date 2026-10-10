"""UI-independent backend. Importing this module never contacts OpenAI or Minecraft."""

from concurrent.futures import Future
from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Literal

from openai import OpenAIError
from pydantic import ValidationError

from .blueprint import english_label, expand_blueprint
from .build_from_plan import matches, read_plan, run as build_plan
from .check_mcpfabric import MCPFabricClient, check_connection
from .chat import ConversationStore, chat_reply, latest_design
from .builds import build_design, review_design
from .designs import generate_design, generate_design_image, revise_design
from .edits import selected_build, prepare_edit, apply_edit
from .modify_with_openai import current_blueprint, flatten, run as modify_build
from .operations import OperationCancelled, OperationContext, OperationEvent
from .plan_with_openai import ROOT, generate_plan, save_plan
from .preview_plan import preview as render_preview
from .preview_with_openai import IMAGE_MODEL, generate_preview
from .settings import stored

Operation = Literal['connection', 'generate_plan', 'voxel_preview', 'image_preview',
                    'survey', 'build', 'modify', 'verify', 'chat', 'design_plan', 'design_image', 'design_build', 'apply_edit']


@dataclass(frozen=True)
class TaskOutcome:
    status: Literal['completed', 'cancelled', 'failed']
    result: dict | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return dict(status=self.status, result=self.result, error=self.error)


def error_details(exc: Exception) -> dict:
    # Never forward SDK response bodies, credentials, or arbitrary validation input.
    if isinstance(exc, OpenAIError):
        status = getattr(exc, 'status_code', None)
        return dict(code='openai_error', message='OpenAI request failed. Check credentials, model access, credit, and connectivity.',
                    exception=type(exc).__name__, http_status=status)
    if isinstance(exc, ValidationError):
        return dict(code='validation_error', message='Blueprint validation failed.')
    if isinstance(exc, OSError):
        return dict(code='io_error', message='A local file or network operation failed.', exception=type(exc).__name__)
    if isinstance(exc, (ValueError, RuntimeError)):
        return dict(code='operation_error', message=english_label(str(exc), 'The operation could not complete.'))
    return dict(code='unexpected_error', message='The operation failed unexpectedly.', exception=type(exc).__name__)


class BackendTask:
    """A worker task with a queue the GUI can drain on its own UI thread."""

    def __init__(self, operation: str):
        self.events: Queue[OperationEvent] = Queue()
        self.context = OperationContext(operation, self.events.put)
        self._future: Future[TaskOutcome] = Future()

    @property
    def done(self) -> bool:
        return self._future.done()

    def cancel(self) -> bool:
        if self.done:
            return False
        self.context.cancel()
        self.context.report('Stop requested; waiting for the current request or safe checkpoint.', kind='status', phase='stopping')
        return True

    def drain_events(self) -> list[dict]:
        events = []
        while True:
            try:
                events.append(self.events.get_nowait().to_dict())
            except Empty:
                return events

    def result(self, timeout: float | None = None) -> TaskOutcome:
        """Wait outside the UI thread, or call only after done becomes True."""
        return self._future.result(timeout=timeout)


class MineCreatorBackend:
    """Use one shared instance per application; only one task runs at a time."""

    OPERATIONS = {'connection', 'generate_plan', 'voxel_preview', 'image_preview',
                  'survey', 'build', 'modify', 'verify', 'chat', 'design_plan', 'design_image', 'design_build', 'apply_edit'}

    def __init__(self, game_dir: Path | None = None):
        self._explicit_game_dir = game_dir is not None
        self.game_dir = Path(game_dir).resolve() if game_dir is not None else (
            Path(os.environ['APPDATA']) / '.minecraft' if 'APPDATA' in os.environ else None)
        self._lock = Lock()
        self._active: BackendTask | None = None
        self.conversations = ConversationStore()

    def start(self, operation: Operation, **arguments) -> BackendTask:
        if operation not in self.OPERATIONS:
            raise ValueError('Unsupported backend operation.')
        with self._lock:
            if self._active is not None and not self._active.done:
                raise RuntimeError('Another operation is still running; wait for it to finish or stop.')
            task = BackendTask(operation)
            self._active = task
            Thread(target=self._work, args=(task, operation, arguments),
                   name=f'minecreator-{operation}', daemon=False).start()
            return task

    def close(self, timeout: float | None = None) -> TaskOutcome | None:
        """Request stop and wait outside the UI thread before closing the app."""
        with self._lock:
            task = self._active
        if task is None:
            return None
        task.cancel()
        return task.result(timeout)

    def _work(self, task: BackendTask, operation: str, arguments: dict) -> None:
        ctx = task.context
        try:
            ctx.check()
            ctx.report('Operation started.', phase='started')
            result = self._execute(operation, ctx, **arguments)
            # Mutating operations check cancellation before committing their final
            # phase. Do not relabel an already committed result as cancelled here.
            outcome = TaskOutcome('completed', result=result)
            ctx.report('Operation completed.', kind='result', **result)
        except OperationCancelled:
            outcome = TaskOutcome('cancelled', error=dict(code='cancelled', message='Operation cancelled; completed work remains in place.'))
            ctx.report(outcome.error['message'], kind='cancelled')
        except Exception as exc:
            details = error_details(exc)
            outcome = TaskOutcome('failed', error=details)
            ctx.report(details['message'], kind='error', **{k:v for k,v in details.items() if k != 'message'})
        task._future.set_result(outcome)

    def _client(self) -> MCPFabricClient:
        if not self._explicit_game_dir:
            configured = stored(ROOT).get('game_dir')
            if configured:
                self.game_dir = Path(configured)
        if self.game_dir is None:
            raise ValueError('Set the Minecraft game directory first.')
        return MCPFabricClient(self.game_dir, timeout=5)

    @staticmethod
    def _require_confirmation(confirmed: bool) -> None:
        if confirmed is not True:
            raise ValueError('Explicit confirmation is required before changing game blocks.')

    def _execute(self, operation: str, context: OperationContext, *, prompt: str | None = None,
                 plan_path: str | Path | None = None, build_path: str | Path | None = None,
                 origin: list[int] | None = None, confirmed: bool = False,
                 delay: float = 0.12, quality: str = 'low', image_model: str = IMAGE_MODEL,
                 conversation_id: str | None = None, request_id: str | None = None,
                 design_id: str | None = None, review_id: str | None = None) -> dict:
        ctx = context
        if operation == 'chat':
            record = self.conversations.get(conversation_id)
            target = selected_build(record)
            pending_edit = record.get('edit_review')
            request_player = None
            if record.get('build_review') or pending_edit:
                # Capture the request-time position before the model can take time
                # deciding on a build. An unavailable game must not prevent chat.
                ctx.report('Checking the player position for possible construction.', phase='request_position')
                try:
                    request_player = self._client().call('player.getState')
                except (OSError, ValueError, RuntimeError):
                    pass
            def start_build(selected_id):
                return build_design(self.conversations, conversation_id, selected_id, request_id,
                                    self._client(), ctx, request_player=request_player)
            def review_build(revise_plan):
                return review_design(self.conversations, conversation_id, request_id, revise_plan, ctx)
            def revise_plan():
                return revise_design(self.conversations, conversation_id, request_id, ctx)
            def review_edit():
                return prepare_edit(self.conversations, conversation_id, request_id, prompt, ctx, prior_review=pending_edit)
            def confirm_edit(review):
                return apply_edit(self.conversations, conversation_id, request_id, review, self._client(), ctx,
                                  request_player=request_player)
            return chat_reply(self.conversations, conversation_id, request_id, prompt, ctx,
                              build_handler=start_build, review_handler=review_build, revision_handler=revise_plan,
                              edit_target={key:target.get(key) for key in ('id','origin','dimension','design_id')} if target else None,
                              edit_review_handler=review_edit if target else None,
                              edit_apply_handler=confirm_edit if target else None)
        if operation == 'apply_edit':
            self._require_confirmation(confirmed)
            review = self.conversations.get(conversation_id).get('edit_review')
            if not review or review['id'] != review_id or review.get('change_count', 0) <= 0:
                raise ValueError('Review the proposed edit before applying it.')
            return apply_edit(self.conversations, conversation_id, request_id, review, self._client(), ctx)
        if operation == 'design_build':
            self._require_confirmation(confirmed)
            return build_design(self.conversations, conversation_id, design_id, request_id, self._client(), ctx)
        if operation == 'design_plan':
            return generate_design(self.conversations, conversation_id, request_id, ctx)
        if operation == 'design_image':
            return generate_design_image(self.conversations, conversation_id, design_id, request_id, ctx)
        if operation == 'connection':
            ctx.report('Checking the local Minecraft connection.', phase='connecting')
            result = check_connection(self._client())
            ctx.check()
            return result
        if operation == 'generate_plan':
            if not isinstance(prompt, str):
                raise ValueError('Provide a building request.')
            result = generate_plan(prompt, context=ctx)
            ctx.check()
            path = save_plan(result)
            return dict(plan_path=str(path), blueprint=result['blueprint'], placement_count=result['placement_count'])
        if operation in {'modify', 'verify'}:
            if build_path is None:
                raise ValueError('Select a build record first.')
            selected = Path(build_path).resolve()
            if operation == 'verify':
                return self._verify(selected, ctx)
            self._require_confirmation(confirmed)
            if not isinstance(prompt, str):
                raise ValueError('Provide an edit request.')
            if self.game_dir is None:
                raise ValueError('Set the Minecraft game directory first.')
            return modify_build(prompt, selected, self.game_dir, delay, context=ctx)
        if plan_path is None:
            raise ValueError('Select a saved blueprint first.')
        selected = Path(plan_path).resolve()
        if operation == 'voxel_preview':
            path = render_preview(selected, context=ctx)
            return dict(preview_path=str(path.resolve()), preview_kind='voxel')
        if operation == 'image_preview':
            path = generate_preview(selected, quality, image_model, context=ctx)
            return dict(preview_path=str(path.resolve()), preview_kind='concept')
        if operation == 'build':
            self._require_confirmation(confirmed)
        if origin is not None and (len(origin) != 3 or any(type(v) is not int for v in origin)):
            raise ValueError('Origin must contain three integer coordinates.')
        return build_plan(selected, self._client(), origin, operation == 'build', delay, context=ctx)

    def _verify(self, build_path: Path, ctx: OperationContext) -> dict:
        build = json.loads(build_path.read_text(encoding='utf-8-sig'))
        if build.get('phase') != 'completed':
            raise ValueError('The selected build is not complete.')
        blueprint, source_hash, edit_path = current_blueprint(build_path, build)
        expected = flatten(expand_blueprint(blueprint))
        # Include removed voxels from the original plan and verified history.
        # Otherwise a removed wall left in the world could go undetected.
        _, original_stages, _ = read_plan(Path(build['source_plan']))
        occupied = set(flatten(original_stages))
        for path in (ROOT / 'data' / 'modifications').glob('*.json'):
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if (record.get('phase') == 'verified' and record.get('source_build')
                    and Path(record['source_build']).resolve() == build_path
                    and record.get('source_sha256') == source_hash):
                occupied.update(flatten(record['model_result']['expanded_stages']))
        for local in occupied:
            expected.setdefault(local, 'minecraft:air')
        client = self._client()
        status = client.call('info.status')
        if not status.get('integratedServer'):
            raise RuntimeError('Enter the single-player world first.')
        dimension = build['dimension']
        if client.call('player.getState')['dimension'] != dimension:
            raise RuntimeError('The player and selected build are in different dimensions.')
        origin = build['origin']
        mismatches = []
        ctx.report('Reading back the selected building.', phase='verifying', total=len(expected))
        for index, (local, material) in enumerate(expected.items(), 1):
            ctx.check()
            position = dict(x=origin[0]+local[0], y=origin[1]+local[1], z=origin[2]+local[2], dimension=dimension)
            actual = client.call('world.getBlock', position)
            if not matches(actual, material):
                mismatches.append(dict(position=position, expected=material, actual=actual['id'], properties=actual.get('properties', {})))
            if index % 25 == 0 or index == len(expected):
                ctx.report(f'Checked {index}/{len(expected)} blocks.', kind='progress', phase='verifying', current=index, total=len(expected))
        ctx.check()
        return dict(verified=not mismatches, checked=len(expected), mismatches=mismatches,
                    build_path=str(build_path), modification_path=edit_path)

    def list_plans(self) -> list[dict]:
        """Local catalog only; opening the UI never triggers generation or game reads."""
        plans = []
        for path in sorted((ROOT / 'data' / 'plans').glob('*.json'), reverse=True):
            blueprint, stages, _ = read_plan(path)
            plans.append(dict(path=str(path.resolve()), title=english_label(blueprint.title, 'Building plan'),
                              bounds=blueprint.bounds.model_dump(), placement_count=sum(len(s['blocks']) for s in stages)))
        return plans

    def list_builds(self) -> list[dict]:
        builds = []
        for path in (ROOT / 'data' / 'builds').glob('blueprint-*.json'):
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if record.get('phase') == 'completed':
                builds.append(dict(path=str(path.resolve()), title=english_label(record['title'], 'Building'),
                                   origin=record['origin'], dimension=record['dimension'], created_at=record['created_at']))
        return sorted(builds, key=lambda b:b['created_at'], reverse=True)
