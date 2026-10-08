"""Verify construction, edits, readback, and restoration in a new test world; keep the test wall."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from uuid import uuid4

from check_mcpfabric import MCPFabricClient


def positions(origin: dict) -> list[dict]:
    return [
        {"x": origin["x"] + x, "y": origin["y"] + y, "z": origin["z"]}
        for y in range(3) for x in range(5)
    ]


def read_wall(client: MCPFabricClient, origin: dict, dimension: str) -> list[dict]:
    return [client.call("world.getBlock", {**pos, "dimension": dimension}) for pos in positions(origin)]


def find_empty_area(client: MCPFabricClient, player: dict) -> tuple[dict, list[dict]]:
    x, y, z = (math.floor(player[axis]) for axis in ("x", "y", "z"))
    for height in (0, 4):
        for dx, dz in ((6, 0), (-10, 0), (0, 6), (0, -6)):
            origin = {"x": x + dx, "y": y + height, "z": z + dz}
            blocks = read_wall(client, origin, player["dimension"])
            if all(block["id"] == "minecraft:air" for block in blocks):
                return origin, blocks
    raise RuntimeError("No empty 5-by-3 area found nearby; move to an open area.")


def fill(client: MCPFabricClient, origin: dict, dimension: str, block_id: str, top_only: bool = False) -> dict:
    start = {**origin, "y": origin["y"] + 2} if top_only else origin
    end = {"x": origin["x"] + 4, "y": origin["y"] + 2, "z": origin["z"]}
    result = client.call("world.fill", {
        "from": start, "to": end, "blockId": block_id, "dimension": dimension,
    })
    if result.get("success") is not True:
        raise RuntimeError(f"Region write failed: {result.get('output', [])}")
    return result


def verify(blocks: list[dict], origin: dict, phase: str) -> None:
    for block in blocks:
        expected = "minecraft:air" if phase == "restored" else (
            "minecraft:glass" if phase == "modified" and block["y"] == origin["y"] + 2
            else "minecraft:stone"
        )
        if block["id"] != expected or block.get("properties", {}) != {}:
            raise RuntimeError(f"Readback mismatch: ({block['x']}, {block['y']}, {block['z']}) should be {expected}.")


def run_test(client: MCPFabricClient) -> dict:
    status = client.call("info.status")
    if not status.get("integratedServer") or "world_write" not in status.get("capabilities", []):
        raise RuntimeError("Enter a single-player test world with world writes enabled.")
    player = client.call("player.getState")
    dimension = player["dimension"]
    origin, original = find_empty_area(client, player)
    record_dir = Path(__file__).resolve().parent.parent / "data" / "smoke-tests"
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / f"wall-{uuid4().hex}.json"
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "minecraft": status["minecraftVersion"], "mcpfabric": status["modVersion"],
        "dimension": dimension, "origin": origin, "size": [5, 3, 1],
        "original_blocks": original, "phase": "prepared", "checks": [],
    }

    def save() -> None:
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()  # Record the original air region before sending writes.
    try:
        fill(client, origin, dimension, "minecraft:stone")
        verify(read_wall(client, origin, dimension), origin, "built")
        record["checks"].append("stone_wall_15_blocks")
        record["phase"] = "built"
        save()

        fill(client, origin, dimension, "minecraft:glass", top_only=True)
        verify(read_wall(client, origin, dimension), origin, "modified")
        record["checks"].append("glass_top_5_blocks")
        record["phase"] = "modified"
        save()

        fill(client, origin, dimension, "minecraft:air")
        verify(read_wall(client, origin, dimension), origin, "restored")
        record["checks"].append("original_air_restored")
        record["phase"] = "restored"
        save()

        # Rebuild after restoration checks so the player can inspect the wall.
        fill(client, origin, dimension, "minecraft:stone")
        fill(client, origin, dimension, "minecraft:glass", top_only=True)
        record["final_blocks"] = read_wall(client, origin, dimension)
        verify(record["final_blocks"], origin, "modified")
        record["checks"].append("visible_wall_verified")
        record["phase"] = "passed"
        save()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        record["phase"] = "failed"
        record["error"] = str(exc)
        save()
        raise RuntimeError(f"Check failed; original state and progress saved: {record_path}; {exc}") from None
    return {"check_passed": True, "dimension": dimension, "wall_origin": origin, "size": "5×3×1", "checks": record["checks"], "record": str(record_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_dir = Path(os.environ["APPDATA"]) / ".minecraft" if "APPDATA" in os.environ else None
    parser.add_argument("--game-dir", type=Path, default=default_dir)
    args = parser.parse_args()
    if args.game_dir is None:
        parser.error("Specify the Minecraft game directory with --game-dir.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = run_test(MCPFabricClient(args.game_dir))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"Connection check failed: {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
