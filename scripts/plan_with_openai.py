"""Generate and validate a building blueprint; this entry point never edits Minecraft."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import dotenv_values
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

if __package__:
    from .blueprint import Blueprint, expand_blueprint
    from .operations import OperationContext, operation_context
else:
    from blueprint import Blueprint, expand_blueprint
    from operations import OperationContext, operation_context

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS = """You design small vanilla Minecraft structures using high-level components.
Write every title, description, stage name, and stage description in English,
even if the user request or the existing blueprint uses another language.
Return a Blueprint matching the supplied schema, using local integer coordinates only.
X is east, Y is up, Z is south. Origin is the minimum corner of each component.
Plan ordered, visible construction stages such as foundation, frame, walls, roof.
Choose vanilla block IDs and explicit states using minecraft:block_name[property=value,...].
There is no fixed material whitelist. Use IDs/states valid for the target Minecraft version.
Do not output commands, scripts, block-entity NBT, or absolute world coordinates.
solid_box fills its inclusive volume. hollow_box places the six boundary faces only;
it does not remove existing blocks, and does not create openings automatically.
gable_roof has its ridge along Z; height is floor((width - 1) / 2) + 1.
Use solid_box with minecraft:air to clear space. Later components/stages replace earlier
coordinates. A 1x1x1 solid_box expresses any individual block with its exact states.
You are responsible for architecture and block semantics: openings, orientation,
multi-block parts, support, connectivity and placement order. Construct supports before
dependent blocks and keep multi-block assemblies together. Do not assume scripts invent
missing details. Express the requested design in these generic components.
For an enclosed habitable house or cabin, provide an accessible entrance with a door
and exterior windows unless the user explicitly asks otherwise. Do not seal the user
inside a hollow box. Keep existing geometry when revising only doors and windows.
Keep every component within bounds; each extent is at most 24 blocks. Stay below
4096 total component placements. Use compact components instead of one entry per block.
Describe only features represented in the actual components. Use concise stage
descriptions, not reasoning traces.
The program validates and expands your output before any later construction step.
"""


def read_settings() -> tuple[str, str]:
    # Project-local settings take precedence over another project's shell settings.
    settings = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    key = (settings.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip()
    model = (settings.get("OPENAI_MODEL") or os.environ.get("OPENAI_MODEL", "")).strip()
    if not key or key in ("your_api_key_here", "你的完整密钥"):
        raise ValueError("Set OPENAI_API_KEY in the project .env file.")
    if not model:
        raise ValueError("Set OPENAI_MODEL in the project .env file.")
    return key, model


def generate_plan(prompt: str, *, context: OperationContext | None = None,
                  history: list[dict] | None = None) -> dict:
    ctx = operation_context(context, 'plan')
    ctx.check()
    if not prompt.strip() or len(prompt) > 8000:
        raise ValueError("The building request must contain 1-8000 characters.")
    model_input = prompt
    if history is not None:
        if not history or any(item.get('role') not in ('user', 'assistant')
                              or not isinstance(item.get('content'), str) for item in history):
            raise ValueError('Provide a valid conversation history.')
        if sum(len(item['content']) for item in history) > 120000:
            raise ValueError('This conversation is too long to generate a plan. Start a new chat.')
        model_input = [*history, dict(role='user', content=prompt)]
    key, model = read_settings()
    ctx.report(f"Requesting a blueprint from {model}...", phase='requesting', model=model)
    # Fixed official endpoint, bounded wait, and no automatic duplicate requests.
    with OpenAI(
        api_key=key,
        base_url="https://api.openai.com/v1",
        timeout=60.0,
        max_retries=0,
    ) as client:
        options = {"reasoning": {"effort": "low"}} if model == "gpt-6-luna" else {}
        response = client.responses.parse(
            model=model,
            instructions=INSTRUCTIONS,
            input=model_input,
            text_format=Blueprint,
            max_output_tokens=4096,
            store=False,
            **options,
        )
    ctx.check()
    if response.status != "completed":
        raise ValueError("The model did not return a complete plan; nothing was saved or built.")
    for item in response.output:
        if item.type == "message" and any(part.type == "refusal" for part in item.content):
            raise ValueError("The model refused this request; revise the building requirements.")
    if response.output_parsed is None:
        raise ValueError("The model did not return a parseable blueprint.")
    blueprint = Blueprint.model_validate(response.output_parsed.model_dump())
    labels = [blueprint.title, blueprint.description]
    for stage in blueprint.stages:
        labels.extend((stage.name, stage.description))
    if any(any('\u3400' <= char <= '\u9fff' for char in label) for label in labels):
        raise ValueError("The model returned non-English labels; the plan was not saved or applied.")
    stages = expand_blueprint(blueprint)
    result = {
        "model": response.model,
        "response_id": response.id,
        "usage": response.usage.model_dump() if response.usage else None,
        "blueprint": blueprint.model_dump(),
        "expanded_stages": stages,
        "placement_count": sum(len(stage["blocks"]) for stage in stages),
    }
    ctx.report('Blueprint validated.', phase='validated', placements=result['placement_count'])
    return result


def save_plan(result: dict) -> Path:
    folder = ROOT / "data" / "plans"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = folder / f"{stamp}-{uuid4().hex[:8]}.json"
    with destination.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Building requirements; generates a plan without construction")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = generate_plan(args.prompt)
        destination = save_plan(result)
        print(f"Generated and validated: {result['blueprint']['title']}")
        for stage in result["expanded_stages"]:
            print(f"  {stage['name']}: {len(stage['blocks'])} blocks - {stage['description']}")
        print(f"Total placements: {result['placement_count']} blocks; plan: {destination}")
        print("This command did not connect to Minecraft or modify game blocks.")
        return 0
    except OpenAIError as exc:
        status = getattr(exc, "status_code", None)
        print(f"OpenAI request failed: {type(exc).__name__}" + (f" (HTTP {status})" if status else ""))
        print("Check API key permissions, available credit, model access, and network connectivity.")
        return 1
    except (ValueError, ValidationError, OSError) as exc:
        # Do not echo validation input, which may contain arbitrary user content.
        print("Plan validation failed." if isinstance(exc, ValidationError) else f"Operation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
