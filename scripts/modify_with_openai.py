"""Accept a natural-language edit, request a revised blueprint, and apply its block diff."""

import argparse
from datetime import datetime, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import sys
from uuid import uuid4

from openai import OpenAIError
from pydantic import ValidationError

if __package__:
    from .blueprint import Blueprint, MAX_PLACEMENTS, expand_blueprint
    from .build_from_plan import matches, protect_player, read_plan
    from .check_mcpfabric import MCPFabricClient
    from .plan_with_openai import ROOT, generate_plan
    from .operations import OperationContext, operation_context
    from .settings import atomic_json
else:
    from blueprint import Blueprint, MAX_PLACEMENTS, expand_blueprint
    from build_from_plan import matches, protect_player, read_plan
    from check_mcpfabric import MCPFabricClient
    from plan_with_openai import ROOT, generate_plan
    from operations import OperationContext, operation_context
    from settings import atomic_json


def flatten(stages: list[dict]) -> dict:
    return {(b["x"], b["y"], b["z"]): b["block"]
            for stage in stages for b in stage["blocks"]}


def blueprint_hash(blueprint: Blueprint) -> str:
    return hashlib.sha256(json.dumps(blueprint.model_dump(), sort_keys=True).encode()).hexdigest()


def block_diff(old_blocks: dict, new_blocks: dict) -> dict:
    return {local: new_blocks.get(local, 'minecraft:air')
            for local in old_blocks.keys() | new_blocks.keys()
            if old_blocks.get(local, 'minecraft:air') != new_blocks.get(local, 'minecraft:air')}


def edit_request(prompt: str, blueprint: Blueprint) -> str:
    return ('Edit this EXISTING Minecraft building; keep its local coordinate anchor fixed.\n'
            'User request:\n' + prompt + '\nCurrent blueprint (data):\n' + blueprint.model_dump_json()
            + '\nReturn the complete revised Blueprint. Make the smallest changes needed. '
              'Preserve all unspecified geometry, materials and states. Use generic vanilla '
              'block IDs and states. The application will apply only the block diff.')


def select_build(requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    completed = []
    for path in (ROOT / "data" / "builds").glob("blueprint-*.json"):
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if record.get("phase") == "completed":
            completed.append((record["created_at"], path))
    if not completed:
        raise ValueError("No completed build record found. Build first, or specify a record with --build.")
    return max(completed, key=lambda item: item[0])[1].resolve()


def current_blueprint(build_path: Path, build: dict) -> tuple[Blueprint, str, str | None]:
    original, _, source_hash = read_plan(Path(build["source_plan"]))
    if source_hash != build["source_sha256"]:
        raise ValueError("The original plan does not match the build record.")
    history = []
    for path in (ROOT / "data" / "modifications").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if (record.get("phase") == "verified" and record.get("source_build")
                and Path(record["source_build"]).resolve() == build_path):
            if record["source_sha256"] != source_hash:
                raise ValueError("Modification history does not match the original build record.")
            history.append((record["created_at"], path, record))
    if not history:
        return original, source_hash, None
    _, path, record = max(history, key=lambda item: item[0])
    result = record["model_result"]
    latest = Blueprint.model_validate(result["blueprint"])
    if result["expanded_stages"] != expand_blueprint(latest):
        raise ValueError("The latest modification blueprint does not match its block data.")
    return latest, source_hash, str(path.resolve())


def run(prompt: str, requested_build: Path | None = None, game_dir: Path | None = None,
        delay: float = 0.15, *, context: OperationContext | None = None,
        prepared: dict | None = None, client=None, request_player=None) -> dict:
    ctx = operation_context(context, 'modify')
    ctx.check()
    if not math.isfinite(delay) or not 0.03 <= delay <= 2:
        raise ValueError('--delay must be between 0.03 and 2 seconds.')
    if not prompt.strip() or len(prompt) > 8000:
        raise ValueError("The edit request must contain 1-8000 characters.")
    build_path = select_build(requested_build)
    build = json.loads(build_path.read_text(encoding="utf-8-sig"))
    if build["phase"] != "completed":
        raise ValueError("The selected build is not complete.")
    blueprint, source_hash, prior_edit = current_blueprint(build_path, build)
    if prepared is not None and (prepared['source_sha256'] != source_hash
                                or prepared['prior_edit'] != prior_edit
                                or prepared['baseline_sha256'] != blueprint_hash(blueprint)):
        raise ValueError('The building record changed after review. Prepare a new edit summary.')
    old_blocks = flatten(expand_blueprint(blueprint))
    origin, dimension = build["origin"], build["dimension"]
    if client is None and game_dir is None:
        if "APPDATA" not in os.environ:
            raise ValueError("Specify the Minecraft game directory with --game-dir.")
        game_dir = Path(os.environ["APPDATA"]) / ".minecraft"
    client = client or MCPFabricClient(game_dir, timeout=5)
    status = client.call("info.status")
    if not status.get("integratedServer") or "world_write" not in status.get("capabilities", []):
        raise RuntimeError("Enter a single-player test world with MCPFabric world writes enabled.")
    request_player = request_player or client.call("player.getState")
    if request_player["dimension"] != dimension:
        raise RuntimeError("The player and target building are in different dimensions.")

    def params(local):
        return dict(x=origin[0]+local[0], y=origin[1]+local[1], z=origin[2]+local[2], dimension=dimension)

    ctx.report(f"Target build at {origin}; record: {build_path}", phase='target_selected', origin=origin, build_path=str(build_path))
    ctx.report(f"Reading the current building: {len(old_blocks)} blocks...", phase='reading', total=len(old_blocks))
    before = {}
    for index, local in enumerate(old_blocks, 1):
        ctx.check()
        actual = client.call("world.getBlock", params(local))
        # The blueprint defines edit intent, not ownership of every current block.
        # Snapshot unrelated player changes instead of demanding the old design.
        before[local] = actual
        if index % 25 == 0:
            ctx.report(f"  Read {index}/{len(old_blocks)} blocks", kind='progress', phase='reading', current=index, total=len(old_blocks))
    if prepared is None:
        ctx.report('Sending edit request to OpenAI...', phase='requesting')
        revised = generate_plan(prompt, history=[dict(role='user', content=edit_request(prompt, blueprint))], context=ctx)
    else:
        revised = prepared['model_result']
        validated = Blueprint.model_validate(revised['blueprint'])
        if expand_blueprint(validated) != revised['expanded_stages']:
            raise ValueError('The reviewed edit differs from its blueprint. Prepare a new review.')
    new_blocks = flatten(revised["expanded_stages"])
    changed = block_diff(old_blocks, new_blocks)
    if len(changed) > MAX_PLACEMENTS:
        raise ValueError("The edit exceeds 4096 changed blocks; stopped.")
    if not changed:
        ctx.report("The API returned the same block data; no changes to apply.", phase='unchanged')
        return dict(phase='unchanged', changed=0, build_path=str(build_path))

    positions_changed = set(old_blocks) != set(new_blocks)
    combined = old_blocks.keys() | new_blocks.keys()
    area_start = tuple(origin[i] + min(pos[i] for pos in combined) for i in range(3))
    area_end = tuple(origin[i] + max(pos[i] for pos in combined) for i in range(3))

    def guard(local):
        ctx.check()
        world = params(local)
        current = client.call("player.getState")
        for player in (request_player, current):
            if player["dimension"] != dimension:
                raise RuntimeError("The player changed dimensions; edit stopped.")
            if positions_changed:
                protect_player(player, dimension, area_start, area_end)
            elif (abs(world["x"] - player["x"]) < 1.5
                  and abs(world["z"] - player["z"]) < 1.5
                  and math.floor(player["y"]) - 1 <= world["y"] <= math.ceil(player["y"]) + 2):
                raise RuntimeError("The edit is too close to the player or their support block; stopped.")

    # New voxels may occupy air, but never overwrite an unrelated
    # structure. Only edit targets must still match the current building blueprint.
    for local in changed:
        guard(local)
        actual = client.call("world.getBlock", params(local))
        if local in old_blocks:
            if not matches(actual, old_blocks[local]):
                raise RuntimeError(f"Edit target conflicts with its latest record at {params(local)}: "
                                   f"expected {old_blocks[local]}, got {actual['id']} {actual.get('properties', {})}.")
        elif actual["id"] not in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}:
            raise RuntimeError(f"New block position is obstructed at {params(local)}: {actual['id']}.")
        before.setdefault(local, actual)

    folder = ROOT / "data" / "modifications"
    folder.mkdir(parents=True, exist_ok=True)
    record_path = folder / f"edit-{uuid4().hex[:12]}.json"
    record = dict(created_at=datetime.now(timezone.utc).isoformat(), request=prompt,
                  source_build=str(build_path), source_sha256=source_hash, prior_edit=prior_edit,
                  origin=origin, dimension=dimension, model_result=revised,
                  change_count=len(changed), phase="validated", applied=0)

    def save():
        atomic_json(record_path,record)

    save()
    ctx.report(f"API plan validated. Applying {len(changed)} changed blocks gradually.", phase='applying', total=len(changed), record_path=str(record_path))
    ordered = sorted(changed.items(), key=lambda item: (
        0 if item[1] == "minecraft:air" else 1,
        -item[0][1] if item[1] == "minecraft:air" else item[0][1], item[0][2], item[0][0]))
    try:
        for index, (local, material) in enumerate(ordered, 1):
            actual = client.call("world.getBlock", params(local))
            guard(local)
            if actual["id"] != before[local]["id"] or actual.get("properties", {}) != before[local].get("properties", {}):
                raise RuntimeError(f"Target block changed during construction at {params(local)}; stopped.")
            try:
                client.call("world.setBlock", {**params(local), "blockId": material})
            except (OSError, RuntimeError):
                if not matches(client.call("world.getBlock", params(local)), material):
                    raise
            if not matches(client.call("world.getBlock", params(local)), material):
                raise RuntimeError(f"Write readback failed at {params(local)}.")
            record.update(phase="applying", applied=index)
            save()
            if index % 5 == 0 or index == len(changed):
                ctx.report(f"Edit progress: {index}/{len(changed)}", kind='progress', phase='applying', current=index, total=len(changed))
            ctx.pause(delay)
        untouched = 0
        ctx.report("Verifying changed blocks and the rest of the building...", phase='verifying')
        for index, local in enumerate(sorted(combined), 1):
            ctx.check()
            actual = client.call("world.getBlock", params(local))
            if local in changed and not matches(actual, new_blocks.get(local, "minecraft:air")):
                raise RuntimeError(f"Final readback mismatch at {params(local)}.")
            if local not in changed:
                if actual["id"] != before[local]["id"] or actual.get("properties", {}) != before[local].get("properties", {}):
                    raise RuntimeError(f"An untouched block changed state at {params(local)}.")
                untouched += 1
            if index % 25 == 0:
                ctx.report(f"  Checked {index}/{len(combined)} blocks", kind='progress', phase='verifying', current=index, total=len(combined))
        ctx.check()
        record.update(phase="verified", verified_changed=len(changed), verified_unchanged=untouched)
        save()
        ctx.report(f"Verified: {len(changed)} changed blocks; {untouched} other blocks unchanged.", phase='verified', changed=len(changed), unchanged=untouched)
        ctx.report(f"Request and execution record: {record_path}", phase='verified', record_path=str(record_path))
        return dict(phase='verified', changed=len(changed), unchanged=untouched, record_path=str(record_path), build_path=str(build_path))
    except (Exception, KeyboardInterrupt) as exc:
        record.update(phase="stopped", error=type(exc).__name__)
        save()
        ctx.report(f"Edit stopped; applied changes remain. Record: {record_path}", phase='stopped', record_path=str(record_path), applied=record['applied'])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Natural-language edit request in any language; applies changes directly to the game")
    parser.add_argument("--build", type=Path, help="Build record JSON; defaults to the most recently completed build")
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    if not math.isfinite(args.delay) or not 0.03 <= args.delay <= 2:
        parser.error("--delay must be between 0.03 and 2 seconds.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        run(args.prompt, args.build, args.game_dir, args.delay)
        return 0
    except OpenAIError as exc:
        print(f"API request failed: {type(exc).__name__}; HTTP {getattr(exc, 'status_code', None)}", flush=True)
        return 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print("Blueprint validation failed." if isinstance(exc, ValidationError) else f"Edit stopped: {exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        print("Edit interrupted; applied changes remain.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
