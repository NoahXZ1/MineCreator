"""Validated local building primitives for the first OpenAI planning pipeline."""

from itertools import product
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PLACEMENTS = 4096


def english_label(value: str, fallback: str) -> str:
    """Keep legacy plan labels out of English program feedback."""
    return fallback if any('\u3400' <= char <= '\u9fff' for char in value) else value


Coordinate = Annotated[int, Field(strict=True, ge=0, le=23)]
Extent = Annotated[int, Field(strict=True, ge=1, le=24)]
# Vanilla identifiers and optional block states, not a hand-maintained material list.
# Minecraft remains authoritative about which IDs/states exist in its version.
Block = Annotated[str, Field(min_length=11, max_length=300,
    pattern=r'^minecraft:[a-z0-9_]+(?:\[[a-z0-9_]+=[a-z0-9_-]+(?:,[a-z0-9_]+=[a-z0-9_-]+)*\])?$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(StrictModel):
    x: Coordinate
    y: Coordinate
    z: Coordinate


class Size(StrictModel):
    x: Extent
    y: Extent
    z: Extent


class Component(StrictModel):
    kind: Literal["solid_box", "hollow_box", "gable_roof"]
    origin: Position
    size: Size
    block: Block

    @model_validator(mode="after")
    def check_roof(self):
        if self.kind == "gable_roof" and self.size.y != (self.size.x - 1) // 2 + 1:
            raise ValueError("gable_roof height must be floor((width - 1) / 2) + 1")
        if '[' in self.block:
            keys = [state.split('=')[0] for state in self.block.split('[', 1)[1][:-1].split(',')]
            if len(set(keys)) != len(keys):
                raise ValueError('Block states must not contain duplicate properties.')
        return self


class Stage(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(min_length=1, max_length=200)]
    components: Annotated[list[Component], Field(min_length=1, max_length=16)]


class Blueprint(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(min_length=1, max_length=400)]
    bounds: Size
    stages: Annotated[list[Stage], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def check_geometry(self):
        count = 0
        for stage_index, stage in enumerate(self.stages, 1):
            for c in stage.components:
                for axis in ("x", "y", "z"):
                    if getattr(c.origin, axis) + getattr(c.size, axis) > getattr(self.bounds, axis):
                        raise ValueError(f"component in stage {stage_index} exceeds the {axis} bound")
                if c.kind == "gable_roof":
                    count += c.size.x * c.size.z
                elif c.kind == "hollow_box":
                    count += c.size.x * c.size.y * c.size.z - (
                        max(0, c.size.x - 2) * max(0, c.size.y - 2) * max(0, c.size.z - 2)
                    )
                else:
                    count += c.size.x * c.size.y * c.size.z
                if count > MAX_PLACEMENTS:
                    raise ValueError(f"plan exceeds the {MAX_PLACEMENTS}-placement limit")
        return self


def expand_blueprint(blueprint: Blueprint) -> list[dict]:
    """Expand stages deterministically, retaining placement order and local coordinates.

    Hollow boxes place only their shell; they do not clear their interior.
    A gable roof has a ridge along Z and rises one block for each step toward
    the center of X. These initial primitives do not perform terrain clearing.
    """
    stages = []
    for stage in blueprint.stages:
        blocks = {}
        for c in stage.components:
            if c.kind == "gable_roof":
                coordinates = (
                    (x, min(x, c.size.x - 1 - x), z)
                    for x, z in product(range(c.size.x), range(c.size.z))
                )
            else:
                coordinates = product(range(c.size.x), range(c.size.y), range(c.size.z))
            for x, y, z in coordinates:
                if c.kind == "hollow_box" and not (
                    x in (0, c.size.x - 1)
                    or y in (0, c.size.y - 1)
                    or z in (0, c.size.z - 1)
                ):
                    continue
                xyz = c.origin.x + x, c.origin.y + y, c.origin.z + z
                blocks[xyz] = c.block
        stages.append({
            "name": stage.name,
            "description": stage.description,
            "blocks": [dict(x=x, y=y, z=z, block=block) for (x, y, z), block in blocks.items()],
        })
    return stages
