"""Survey a site and gradually construct a validated saved blueprint through MCPFabric."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from pydantic import ValidationError

if __package__:
    from .paths import ROOT
    from .blueprint import Blueprint, MAX_PLACEMENTS, english_label, expand_blueprint
    from .check_mcpfabric import MCPFabricClient
    from .operations import OperationContext, operation_context
    from .settings import atomic_json
else:
    from paths import ROOT
    from blueprint import Blueprint, MAX_PLACEMENTS, english_label, expand_blueprint
    from check_mcpfabric import MCPFabricClient
    from operations import OperationContext, operation_context
    from settings import atomic_json

AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
FLUIDS = {"minecraft:water", "minecraft:lava"}
PLANTS = {"minecraft:" + name for name in (
    "short_grass", "tall_grass", "fern", "large_fern", "dead_bush",
    "dandelion", "poppy", "blue_orchid", "allium", "azure_bluet",
    "red_tulip", "orange_tulip", "white_tulip", "pink_tulip",
    "oxeye_daisy", "cornflower", "lily_of_the_valley",
)}
GROUND = {"minecraft:" + name for name in (
    "grass_block", "dirt", "coarse_dirt", "rooted_dirt", "podzol", "mycelium",
    "moss_block", "stone", "andesite", "diorite", "granite", "deepslate",
    "sandstone", "red_sandstone", "terracotta", "clay", "snow_block",
)}


def read_plan(path: Path) -> tuple[Blueprint, list[dict], str]:
    raw = path.read_bytes()
    record = json.loads(raw.decode("utf-8-sig"))
    blueprint = Blueprint.model_validate(record["blueprint"])
    stages = expand_blueprint(blueprint)
    if record.get("expanded_stages") != stages:
        raise ValueError("Saved block data differs from the blueprint; regenerate the plan.")
    return blueprint, stages, hashlib.sha256(raw).hexdigest()


def bounds(origin: tuple[int, int, int], blueprint: Blueprint) -> tuple[tuple, tuple]:
    end = tuple(origin[i] + getattr(blueprint.bounds, axis) - 1
                for i, axis in enumerate(("x", "y", "z")))
    return origin, end


def protect_player(player: dict, dimension: str, origin: tuple, end: tuple) -> None:
    if player["dimension"] != dimension:
        raise RuntimeError("The player changed dimensions; construction stopped.")
    # Keep the whole horizontal building footprint away from the player, including
    # their support block and exit space, even while they stand below the roof.
    if (origin[0] - 2 <= player["x"] <= end[0] + 3
            and origin[2] - 2 <= player["z"] <= end[2] + 3):
        raise RuntimeError("The construction area is too close to the player or request position; stand outside it.")


def read_region(client: MCPFabricClient, dimension: str, start: tuple, end: tuple, *,
                context: OperationContext | None = None) -> dict:
    ctx = operation_context(context, 'survey')
    blocks = {}
    width = end[0] - start[0] + 1
    depth = end[2] - start[2] + 1
    # Bound each RPC to 4096 cells and reject partial results instead of treating
    # missing or unloaded cells as air.
    slab_height = max(1, 4096 // (width * depth))
    for y in range(start[1], end[1] + 1, slab_height):
        ctx.check()
        upper = min(end[1], y + slab_height - 1)
        if end[1] - start[1] + 1 > slab_height:
            ctx.report(f"  Reading terrain at heights {y}-{upper}...", phase='terrain_read', from_y=y, to_y=upper)
        result = client.call("world.getBlocks", {
            "dimension": dimension,
            "from": dict(x=start[0], y=y, z=start[2]),
            "to": dict(x=end[0], y=upper, z=end[2]),
            "includeAir": True,
            "maxBlocks": 4096,
        })
        if result.get("truncated"):
            raise RuntimeError("Terrain read was incomplete; stopped without modifying the game.")
        cells = {(b["x"], b["y"], b["z"]): b["id"] for b in result["blocks"]}
        expected = {(x, yy, z) for x in range(start[0], end[0] + 1)
                    for yy in range(y, upper + 1) for z in range(start[2], end[2] + 1)}
        if set(cells) != expected:
            raise RuntimeError("Terrain read is missing blocks; construct within loaded chunks.")
        blocks.update(cells)
    return blocks


def check_site(blocks: dict, origin: tuple, end: tuple) -> list[dict]:
    for x in range(origin[0], end[0] + 1):
        for z in range(origin[2], end[2] + 1):
            if blocks[x, origin[1] - 1, z] not in GROUND:
                raise ValueError("The ground is uneven or lacks stable natural support.")
    plants = []
    for x in range(origin[0], end[0] + 1):
        for y in range(origin[1], end[1] + 1):
            for z in range(origin[2], end[2] + 1):
                block = blocks[x, y, z]
                if block in PLANTS:
                    plants.append(dict(x=x, y=y, z=z, block="minecraft:air"))
                elif block not in AIR:
                    raise ValueError(f"Obstruction {block} at {x},{y},{z}.")
    return plants


def prepare_terrain(blocks: dict, origin: tuple, end: tuple) -> list[dict]:
    foundation = []
    clearing = []
    bottom = min(position[1] for position in blocks)
    for x in range(origin[0], end[0] + 1):
        for z in range(origin[2], end[2] + 1):
            for y in range(origin[1] - 1, bottom - 1, -1):
                existing = blocks[x, y, z]
                if existing in GROUND or (
                    y < origin[1] - 1 and existing not in AIR | PLANTS | FLUIDS
                ):
                    break
                foundation.append(dict(x=x, y=y, z=z, block="minecraft:dirt", before_id=existing))
            for y in range(end[1], origin[1] - 1, -1):
                existing = blocks[x, y, z]
                if existing not in AIR:
                    clearing.append(dict(x=x, y=y, z=z, block="minecraft:air", before_id=existing))
    foundation.sort(key=lambda b: (b["y"], b["z"], b["x"]))
    clearing.sort(key=lambda b: (-b["y"], b["z"], b["x"]))
    return [dict(name=name, blocks=targets, terrain=True) for name, targets in
            (("Level foundation", foundation), ("Clear construction area", clearing)) if targets]


def terrain_floor(blocks: dict, x: int, z: int, blueprint: Blueprint, py: int) -> int:
    surfaces = []
    waterline = py - 6
    for xx in range(x, x + blueprint.bounds.x):
        for zz in range(z, z + blueprint.bounds.z):
            heights = [y for y in range(py - 6, py + 5) if blocks[xx, y, zz] in GROUND]
            if heights:
                surfaces.append(max(heights) + 1)
            fluids = [y for y in range(py - 6, py + 5) if blocks[xx, y, zz] in FLUIDS]
            if fluids:
                waterline = max(waterline, max(fluids) + 1)
    # Prefer the local ground level, and keep the building above existing fluids.
    ground_level = sorted(surfaces)[len(surfaces) // 2] if surfaces else py
    return max(ground_level, waterline)


def choose_site(client: MCPFabricClient, blueprint: Blueprint, player: dict,
                requested_origin: list[int] | None, *,
                context: OperationContext | None = None) -> tuple[tuple, list[dict], str]:
    ctx = operation_context(context, 'survey')
    ctx.check()
    dimension = player["dimension"]
    if requested_origin is not None:
        origin, end = bounds(tuple(requested_origin), blueprint)
        protect_player(player, dimension, origin, end)
        start = (origin[0], origin[1] - 6, origin[2])
        blocks = read_region(client, dimension, start, end, context=ctx)
        try:
            plants = check_site(blocks, origin, end)
        except ValueError:
            return origin, prepare_terrain(blocks, origin, end), "prepared terrain"
        return origin, [dict(name="Clear small plants", blocks=plants)] if plants else [], "natural clearing"
    px, py, pz = (math.floor(player[axis]) for axis in ("x", "y", "z"))
    distance = max(blueprint.bounds.x, blueprint.bounds.z) // 2 + 7
    started = time.monotonic()
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1),
                  (1, 1), (-1, 1), (1, -1), (-1, -1))
    fallback = None
    ctx.report("Searching once; if no natural clearing is found, prepare the terrain.", phase='searching')
    for index, (dx, dz) in enumerate(directions, 1):
        ctx.check()
        if time.monotonic() - started > 15 and fallback is not None:
            break
        x = px + dx * distance - blueprint.bounds.x // 2
        z = pz + dz * distance - blueprint.bounds.z // 2
        ctx.report(f"Site {index}/8: checking nearby ({x}, {z})...", kind='progress', phase='searching', current=index, total=8, x=x, z=z)
        start = (x, py - 6, z)
        end = (x + blueprint.bounds.x - 1, py + 5 + blueprint.bounds.y - 1,
               z + blueprint.bounds.z - 1)
        blocks = read_region(client, dimension, start, end, context=ctx)
        forced_origin, forced_top = bounds((x, terrain_floor(blocks, x, z, blueprint, py), z), blueprint)
        protect_player(player, dimension, forced_origin, forced_top)
        preparation = prepare_terrain(blocks, forced_origin, forced_top)
        cost = sum(len(stage["blocks"]) for stage in preparation)
        if fallback is None or cost < fallback[0]:
            fallback = (cost, forced_origin, preparation)
        for y in sorted(range(py - 5, py + 6), key=lambda yy: abs(yy - py)):
            origin, top = bounds((x, y, z), blueprint)
            try:
                protect_player(player, dimension, origin, top)
                plants = check_site(blocks, origin, top)
            except (ValueError, RuntimeError):
                continue
            return origin, [dict(name="Clear small plants", blocks=plants)] if plants else [], "natural clearing"
    if fallback is None:
        raise RuntimeError("No complete nearby terrain data available; cannot determine the construction area.")
    ctx.report(f"No natural clearing found; preparing {fallback[1]}, approximately {fallback[0]} block changes.", phase='terrain_preparation', origin=fallback[1], blocks=fallback[0])
    return fallback[1], fallback[2], "prepared terrain"


def matches(actual: dict, expected: str) -> bool:
    name, _, states = expected.partition("[")
    props = dict(pair.split("=", 1) for pair in states.rstrip("]").split(",")) if states else {}
    return actual["id"] == name and all(actual.get("properties", {}).get(k) == v
                                         for k, v in props.items())


def placement_matches(actual: dict, expected: str, *, terrain: bool = False) -> bool:
    # Only our terrain fill tolerates dirt becoming grass. Blueprint blocks stay strict.
    return matches(actual, expected) or (
        terrain and expected == 'minecraft:dirt' and actual['id'] == 'minecraft:grass_block')


def run(plan_path: Path, client: MCPFabricClient, requested_origin: list[int] | None,
        execute: bool, delay: float, *, context: OperationContext | None = None,
        request_player: dict | None = None) -> dict:
    ctx = operation_context(context, 'build' if execute else 'survey')
    ctx.check()
    if not math.isfinite(delay) or not 0.03 <= delay <= 2:
        raise ValueError('--delay must be between 0.03 and 2 seconds.')
    blueprint, stages, source_hash = read_plan(plan_path)
    status = client.call("info.status")
    if not status.get("integratedServer"):
        raise RuntimeError("Enter a single-player test world with MCPFabric first.")
    if execute and "world_write" not in status.get("capabilities", []):
        raise RuntimeError("MCPFabric world writes are not enabled.")
    current_player = client.call("player.getState")
    request_player = request_player or current_player
    dimension = request_player["dimension"]
    if current_player['dimension'] != dimension:
        raise RuntimeError('The player changed dimensions after the request; construction stopped.')
    ctx.report('Minecraft connected. Selecting a construction site.', phase='connected', dimension=dimension)
    origin, preparation, site_mode = choose_site(client, blueprint, request_player, requested_origin, context=ctx)
    _, end = bounds(origin, blueprint)
    placement_count = sum(len(stage["blocks"]) for stage in stages)
    if placement_count > MAX_PLACEMENTS:
        raise ValueError("The blueprint exceeds 4096 placements; stopped.")
    # Terrain edits are separately bounded by the surveyed building footprint;
    # they do not consume the blueprint's 4096-placement allowance.
    terrain_count = sum(len(stage["blocks"]) for stage in preparation)
    world_stages = list(preparation)
    for stage_index, stage in enumerate(stages, 1):
        translated = [{"x": origin[0] + b["x"], "y": origin[1] + b["y"],
                       "z": origin[2] + b["z"], "block": b["block"]}
                      for b in stage["blocks"]]
        translated.sort(key=lambda b: (b["y"], b["z"], b["x"]))
        name = english_label(stage["name"], f"Construction stage {stage_index}")
        world_stages.append(dict(name=name, blocks=translated))
    summary = dict(origin=list(origin), end=list(end), dimension=dimension, site_mode=site_mode,
                   placement_count=placement_count, terrain_placements=terrain_count,
                   stages=[dict(name=s['name'], blocks=len(s['blocks'])) for s in world_stages])
    ctx.report(f"Build in {dimension}; origin: {origin}; site: {site_mode}; total placements: {placement_count + terrain_count}", phase='site_selected', **summary)
    for stage in world_stages:
        ctx.report(f"  {stage['name']}: {len(stage['blocks'])} blocks", phase='stage_planned', stage=stage['name'], total=len(stage['blocks']))
    if not execute:
        ctx.report("Read-only survey complete; no blocks modified. Construction arguments:", phase='survey_completed')
        ctx.report(f"--origin {origin[0]} {origin[1]} {origin[2]} --execute", phase='survey_completed')
        return dict(phase='survey_completed', **summary)

    folder = ROOT / "data" / "builds"
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / f"blueprint-{uuid4().hex[:12]}.json"
    record = dict(created_at=datetime.now(timezone.utc).isoformat(), source_plan=str(plan_path.resolve()),
                  source_sha256=source_hash, title=blueprint.title, dimension=dimension,
                  origin=origin, site_mode=site_mode, terrain_placements=terrain_count,
                  request_position={k: request_player[k] for k in ("x", "y", "z")},
                  phase="ready", completed_stages=[], changed=0)
    owned = {}
    terrain_owned = set()

    def save():
        # Progress only: no world copy, old block snapshot, or automatic rollback.
        atomic_json(log_path,record)

    def params(position):
        return dict(x=position[0], y=position[1], z=position[2], dimension=dimension)

    def guard():
        ctx.check()
        protect_player(request_player, dimension, origin, end)
        protect_player(client.call("player.getState"), dimension, origin, end)

    save()
    ctx.report(f"Starting gradual construction; progress record: {log_path}", phase='building', record_path=str(log_path))
    try:
        for stage in world_stages:
            guard()
            record["phase"] = stage["name"]
            save()
            ctx.report(f"Starting {stage['name']}...", phase='placing', stage=stage['name'])
            for index, block in enumerate(stage["blocks"], 1):
                position = (block["x"], block["y"], block["z"])
                target = block["block"]
                terrain = bool(stage.get('terrain'))
                record["current_position"] = position
                old = client.call("world.getBlock", params(position))
                guard()
                needs_change = not placement_matches(old, target, terrain=terrain)
                if needs_change:
                    previous = owned.get(position)
                    permitted = (old["id"] in AIR | PLANTS or
                                 previous is not None and placement_matches(old, previous, terrain=position in terrain_owned) or
                                 stage.get("terrain") and old["id"] == block["before_id"])
                    if not permitted:
                        raise RuntimeError(f"New obstruction or manual edit at {position}: {old['id']}; stopped.")
                    try:
                        client.call("world.setBlock", {**params(position), "blockId": target})
                    except (OSError, RuntimeError):
                        # A write may have reached the game before a timeout. Read once;
                        # never send a duplicate write or automatically roll back.
                        if not placement_matches(client.call("world.getBlock", params(position)), target, terrain=terrain):
                            raise
                    actual = client.call("world.getBlock", params(position))
                    if not placement_matches(actual, target, terrain=terrain):
                        raise RuntimeError(f"Placement readback mismatch at {position}: expected {target}, got {actual['id']}")
                    record["changed"] += 1
                owned[position] = target
                if terrain:
                    terrain_owned.add(position)
                else:
                    terrain_owned.discard(position)
                # Save each committed placement before observing cancellation.
                save()
                if index % 10 == 0 or index == len(stage["blocks"]):
                    ctx.report(f"  {stage['name']}: {index}/{len(stage['blocks'])}", kind='progress', phase='placing', stage=stage['name'], current=index, total=len(stage['blocks']), changed=record['changed'])
                if needs_change:
                    ctx.pause(delay)
            ctx.report(f"Verifying {stage['name']}...", phase='verifying', stage=stage['name'])
            for index, (position, target) in enumerate(owned.items(), 1):
                if index % 25 == 1:
                    guard()
                    ctx.report(f"  Checked {index - 1}/{len(owned)} blocks", kind='progress', phase='verifying', current=index - 1, total=len(owned))
                actual = client.call("world.getBlock", params(position))
                if not placement_matches(actual, target, terrain=position in terrain_owned):
                    raise RuntimeError(f"Stage readback mismatch at {position}: expected {target}, got {actual['id']}")
            record["completed_stages"].append(stage["name"])
            save()
            ctx.report(f"{stage['name']} complete; {len(owned)} blocks verified.", phase='stage_verified', stage=stage['name'], verified=len(owned))
        ctx.check()
        record["phase"] = "completed"
        record.pop("current_position", None)
        save()
        ctx.report(f"Construction complete at {origin}.", phase='completed', record_path=str(log_path), verified=len(owned))
        return dict(phase='completed', record_path=str(log_path), changed=record['changed'], verified=len(owned), **summary)
    except (Exception, KeyboardInterrupt) as exc:
        record["phase"] = "stopped"
        record["error"] = "Interrupted by user" if isinstance(exc, KeyboardInterrupt) else type(exc).__name__
        save()
        ctx.report(f"Construction stopped; placed blocks remain. Progress record: {log_path}", phase='stopped', record_path=str(log_path), changed=record['changed'])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Saved and approved blueprint JSON")
    game_dir = Path(os.environ["APPDATA"]) / ".minecraft" if "APPDATA" in os.environ else None
    parser.add_argument("--game-dir", type=Path, default=game_dir)
    parser.add_argument("--origin", nargs=3, type=int, metavar=("X", "Y", "Z"))
    parser.add_argument("--execute", action="store_true", help="Construct gradually; default is a read-only survey")
    parser.add_argument("--delay", type=float, default=0.12, help="Pause after each change in seconds, between 0.03 and 2")
    args = parser.parse_args()
    if args.game_dir is None:
        parser.error("Specify the Minecraft game directory with --game-dir.")
    if not math.isfinite(args.delay) or not 0.03 <= args.delay <= 2:
        parser.error("--delay must be between 0.03 and 2 seconds.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        run(args.plan, MCPFabricClient(args.game_dir, timeout=5), args.origin, args.execute, args.delay)
        return 0
    except KeyboardInterrupt:
        print("Construction stopped.", flush=True)
        return 130
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print("Blueprint validation failed." if isinstance(exc, ValidationError) else f"Operation failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
