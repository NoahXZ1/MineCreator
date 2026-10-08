# MineCreator

An LLM-powered building design and construction tool for Minecraft.

## Architecture

Fabric + MCPFabric. Python controls a Minecraft Java single-player world through a local HTTP API. The LLM produces blueprints for the program to validate and execute. OpenAI generates concept previews from the blueprint and expanded block data; these illustrations may differ from the exact construction geometry.

## Confirmed environment

| Component | Version |
| --- | --- |
| Python | 3.14.8 (64-bit) |
| pip | 26.2.1 |
| Minecraft Java Edition client | 26.3 |
| Java bundled with Minecraft Launcher | Microsoft OpenJDK 25.0.1 |
| Fabric Loader | 0.19.5 |
| Fabric API | 0.161.0+26.3 |
| MCPFabric | 0.5.0+26.3 (Fabric) |
| OpenAI Python SDK | 3.24.0 |
| Pydantic | 2.13.5 |
| python-dotenv | 1.2.4 |

## Current status

Stage 1 is complete: blueprint generation, API image previews, gradual construction, natural-language modification, and readback worked. The user confirmed that the same save reopened normally in vanilla Minecraft. Stage 2 has multi-turn GUI chat, conversation-based blueprints, block and AI previews, and construction from chat or the Build button. GUI construction has passed offline checks; live game verification is pending.

## Launch the GUI

Double-click `MineCreator.vbs` in the repository root. It uses the existing `.venv` and opens an Edge app window (or the default browser if Edge is unavailable). API calls happen only when you click Send, Generate plan, or Generate AI image. Minecraft does not need to be open for design.

For development, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_gui
```

Chat accepts Chinese or English requests and replies in English. Enter sends; Shift + Enter adds a line. Conversations are saved in the Git-ignored `data/conversations/` directory, with completed turns included in the next request. Stop preserves partial replies and excludes them from future context. The app never retries API requests automatically. A normal window close notifies the local server to exit after 10 seconds; Settings also has a Quit button. Backgrounding or minimizing the window does not shut down the server when browser timers pause.

Discuss a building, then click **Generate plan**. The complete conversation and any previous saved blueprint are used to generate a validated plan. The right panel shows dimensions, final block counts, materials, construction stages, and a locally rendered block preview; click the preview to enlarge it. Discuss revisions and click **Regenerate plan** to save a new version. Previous versions remain on disk, and the latest saved plan is restored when you switch conversations or reopen the app.

**Generate AI image** optionally requests one low-quality concept image through the existing API image tool. Switch between **Blocks** and **AI concept**; the blueprint is authoritative for construction. Plans use generic box/gable-roof primitives, vanilla block IDs and states, extents up to 24 blocks, and a maximum of 4096 placements. The LLM designs architectural details using those primitives; the executor has no door/window-specific commands. Local block previews are voxel schematics: partial-block shapes and unrecognized textures are simplified. Minecraft validates whether a block/state exists in the running version during execution.

Ask in chat to revise the current design, for example **"Add a door and windows"**. MineCreator saves a revised plan and updates the block preview without changing the game, including when an earlier version was already built. Only an explicit request to edit placed blocks refers to live world modification. Revisions during a build review produce a fresh description and require confirmation again.

With a saved plan, you can also ask in chat: "Generate a Minecraft-style preview image of this building." The model can invoke the image tool directly in the chat request, with at most one low-quality image. The result appears in the Design panel and is linked from the reply. Ordinary discussion does not require image generation. These are AI illustrations, not live game screenshots, and image generation does not modify the world.

To construct the saved plan, enter your single-player world with Fabric + MCPFabric and world writes enabled. In chat, say **"Start build"**: MineCreator first describes the building, including its dimensions, materials and structure, and asks whether the description is accurate and construction should begin. Reply **"Yes, start building"** to proceed. If you request changes, MineCreator regenerates the actual blueprint through the API, describes the updated design, and asks again. Confirmation is tied to that exact saved version; changing the plan invalidates an older review. The first chat build request cannot place blocks.

Alternatively, **left-click or right-click Build** in the Design panel to start the displayed saved plan directly. The button needs no OpenAI request. Both paths use the same gradual construction pipeline. Chat uses model tool calling to interpret review, revision and confirmation; when a review is pending, it also attempts a read-only player-position snapshot before the model request to protect that original location.

Construction searches nearby once, then clears and levels the selected footprint if needed. The panel shows the location, stage progress, and readback result. **Stop** preserves blocks already placed. A stopped or interrupted build does not resume automatically. Build uses the saved block blueprint; discuss and regenerate any geometry changes before starting. Build history stays with its conversation, and detailed progress records are stored in `data/builds/`.

## OpenAI configuration

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in the project-local `.env` file (see `.env.example`). Local credentials and generated plans are excluded from Git.

To generate a plan manually, run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/plan_with_openai.py "Design a small 5-by-5 wooden pavilion."
```

Generate an API image preview from the saved plan:

```powershell
.\.venv\Scripts\python.exe scripts/preview_with_openai.py data/plans/20261005T171301Z-215f92e3.json
```

This uses the same key and `OPENAI_MODEL`, forcing the `image_generation` tool with `gpt-image-2.5-flare`. It defaults to one 1024×1024 low-quality image, with no automatic retries. Use `--quality medium` or `--quality high` to change quality, or `--image-model` to specify another supported image model. PNGs and request metadata are saved under the Git-ignored `data/previews/` folder.

## Build a saved blueprint

Enter a single-player test world with Fabric + MCPFabric, then survey a site:

```powershell
.\.venv\Scripts\python.exe scripts/build_from_plan.py data/plans/20261005T171301Z-215f92e3.json
```

The default is read-only. It prints the selected `--origin X Y Z`; add those coordinates and `--execute` to construct at that site. Site selection searches nearby once, then falls back to clearing obstructions inside the building footprint and leveling its foundation with dirt. A specified origin also allows terrain preparation. Construction places blocks gradually and verifies IDs and requested states after each stage. It stops for player proximity, unexpected changes since the survey, or failed readback. Ctrl+C stops construction and leaves placed blocks in place. Progress is logged under `data/builds/`; no world backup or automatic rollback is performed.

## Modify a built structure

With the test world running, send a Chinese or English request directly from PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/modify_with_openai.py "Replace the roof with white wool. Keep its shape and size unchanged, and leave the floor and pillars untouched."
```

The command targets the most recently completed blueprint build, loads its latest verified modification, and sends that current blueprint with your request to OpenAI. It applies only the block diff, then checks changed and unchanged blocks. Use `--build data/builds/blueprint-4b27ca8fe96e.json` to select a specific build. Supported edits are limited to the current blueprint materials and primitives; new voxel positions must be empty. No image generation is requested.

White wool is supported. CLI messages and generated blueprint labels use English; requests may use Chinese or English.

## GUI backend

Import `MineCreatorBackend` from `scripts.backend`. `start(operation, **arguments)` returns a worker task; the GUI polls `task.drain_events()` for structured English status, progress, result, and error events. After `task.done`, read `task.result().to_dict()`. One shared backend instance runs one task at a time. Existing CLI commands remain available.

Operations: `chat`, `design_plan`, `design_image`, `design_build`, `connection`, `generate_plan`, `voxel_preview`, `image_preview`, `survey`, `build`, `modify`, and `verify`. Chat takes `conversation_id`, `request_id`, and `prompt`; design_plan takes `conversation_id` and `request_id`; design_image and design_build also take `design_id`. Conversation storage is available through `backend.conversations`. Preview/survey/build take `plan_path`; modify/verify require an explicit `build_path`. Build, design_build, and modify require `confirmed=True`; the GUI Build action supplies this, while an explicit chat build tool call dispatches the same bounded runner. `list_plans()` and `list_builds()` return local catalogs. No request or game edit runs when importing the backend.

`task.cancel()` stops at a safe checkpoint and preserves completed changes. A request already in flight may need to finish or time out; it is not automatically retried. On application exit, request cancellation and wait for task completion outside the UI thread.

The approved GUI direction is a dark deepslate Minecraft-inspired layout with green buttons and a simplified ivory architect icon with a green crossbar. The local reference is saved under `local-docs/gui-designs/approved/minecreator-gui-reference.png`. Interface text and program feedback remain English. Only the top-left MineCreator wordmark uses the pixel font. The bundled Silkscreen font is licensed under the SIL Open Font License; see `ui/fonts/OFL.txt`.

## Project constraints

- Generate only vanilla blocks, block states, and block entities. Modified saves must pass a reopening test in the same vanilla Minecraft version without manual conversion.
- One-time mod installation and environment setup are acceptable. Routine construction, modification, and inspection must run automatically through the GUI.
- Keep the scope manageable for a short solo project without developing a custom Java mod. Version 26.3 is the current test version, not a permanent restriction.
- Build visibly in stages through gradual block placement, reading back and adjusting completed sections between stages.
- Protect the player's position when the request is made and keep checking their current position. Preserve ground support, movement space, and an exit route.
- First look nearby for natural terrain that suits the building. Make necessary local terrain changes only when a suitable site cannot be found.
