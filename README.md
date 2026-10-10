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

Stage 1 is complete: blueprint generation, API image previews, gradual construction, natural-language modification, and readback worked. The user confirmed that the same save reopened normally in vanilla Minecraft. Stage 2 has multi-turn GUI chat, conversation-based blueprints, block and AI previews, construction, and reviewed in-game edits. The user has confirmed GUI construction and local edits in Minecraft.

Stage 2 now includes a versioned **Building library**, editable settings, and a standalone Windows build. The latest QA passed 86 unit/integration tests, browser workflows and source-process startup/restart checks. The EXE builds successfully, but Windows Application Control blocked its latest launch with WinError 4551; packaged runtime verification remains incomplete. See [the QA report](docs/testing-stage2.md) for coverage, fixes and limitations.

## Launch the GUI

Double-click `MineCreator.vbs` in the repository root. It uses the existing `.venv` and opens an Edge app window (or the default browser if Edge is unavailable). API calls happen only when you click Send, Generate plan, or Generate AI image. Minecraft does not need to be open for design.

The packaged build is `dist/MineCreator/MineCreator.exe`. Keep the entire folder together; it includes Python and dependencies. Its latest startup is blocked by local Windows policy, so use the repository launcher for now. The packaged app is configured to store data under `%LOCALAPPDATA%/MineCreator`, separately from the development repository. Building ZIP export/import transfers designs between data roots. Settings shows the active data directory. For development/testing, `MINECREATOR_HOME` can select a different data root.

For development, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_gui
```

Chat accepts Chinese or English requests and replies in English. Enter sends; Shift + Enter adds a line. Conversations are saved in the Git-ignored `data/conversations/` directory, with completed turns included in the next request. Stop preserves partial replies and excludes them from future context. The app never retries API requests automatically. A normal window close notifies the local server to exit after 10 seconds; Settings also has a Quit button. Backgrounding or minimizing the window does not shut down the server when browser timers pause.

Discuss a building, then click **Generate plan**. The complete conversation and any previous saved blueprint are used to generate a validated plan. The right panel shows one concept image and a short status. Expand **Blueprint details** in the conversation for dimensions, materials, stages and **View block preview**. Discuss revisions and click **Regenerate plan** to save a new version. Previous versions remain on disk, and the latest saved plan is restored when you switch conversations or reopen the app.

**Generate AI image** optionally requests one low-quality concept image through the existing API image tool. The image appears in the right panel and can be enlarged; the blueprint is authoritative for construction. Plans use generic box/gable-roof primitives, vanilla block IDs and states, extents up to 24 blocks, and a maximum of 4096 placements. The LLM designs architectural details using those primitives; the executor has no door/window-specific commands. Local block previews are voxel schematics: partial-block shapes and unrecognized textures are simplified. Minecraft validates whether a block/state exists in the running version during execution.

Ask in chat to revise the current design, for example **"Add a door and windows"**. MineCreator saves a revised plan and updates the block preview without changing the game, including when an earlier version was already built. Only an explicit request to edit placed blocks refers to live world modification. Revisions during a build review produce a fresh description and require confirmation again.

With a saved plan, you can also ask in chat: "Generate a Minecraft-style preview image of this building." The model can invoke the image tool directly in the chat request, with at most one low-quality image. The result appears in the Design panel and is linked from the reply. Ordinary discussion does not require image generation. These are AI illustrations, not live game screenshots, and image generation does not modify the world.

To construct the saved plan, enter your single-player world with Fabric + MCPFabric and world writes enabled. In chat, say **"Start build"**: MineCreator first describes the building, including its dimensions, materials and structure, and asks whether the description is accurate and construction should begin. Reply **"Yes, start building"** to proceed. If you request changes, MineCreator regenerates the actual blueprint through the API, describes the updated design, and asks again. Confirmation is tied to that exact saved version; changing the plan invalidates an older review. The first chat build request cannot place blocks.

Alternatively, **left-click or right-click Build** in the Design panel to start the displayed saved plan directly. The button needs no OpenAI request. Both paths use the same gradual construction pipeline. Chat uses model tool calling to interpret review, revision and confirmation; when a review is pending, it also attempts a read-only player-position snapshot before the model request to protect that original location.

Construction searches nearby once, then clears and levels the selected footprint if needed. The conversation shows the location, stage progress, and readback result, along with building selection and **Apply edit** controls. **Stop** preserves blocks already placed. A stopped or interrupted build does not resume automatically. Build uses the saved block blueprint; discuss and regenerate any geometry changes before starting. Build history stays with its conversation, and detailed progress records are stored in `data/builds/`.

## Building library

Click **Save to library**, select the blueprint version, give it a name and notes, and save as a new building or a new version of an existing building. Each version copies the conversation, linked blueprint/expanded block data, previews and their metadata, attached references, and construction/edit records into an independent archive. Add local reference images through **Upload images** in the Library. References are stored and viewable; uploading one does not automatically send it to the model.

Open **Building library** to search names/notes, inspect images and the design conversation, download individual files, or **Export ZIP** / **Import ZIP**. **Open working copy** starts a new conversation from the selected saved design with its references; the original archive remains unchanged. Old world coordinates and execution history remain archived for inspection and never become active edit targets. Save further edits as another version. Archive versions are integrity-checked; missing or corrupted assets are reported instead of silently dropped. Limits: 30 attached references per conversation, 8 MB and 25 million pixels per reference, 128 MB per archive.

Within an archive, **Upload images** adds local JPG/JPEG or PNG files; **Remove** below any image removes it from the new version, including generated previews. Each change saves a new archive version and preserves earlier versions. Deleted images stay absent when exporting or reopening that version. Upload up to 30 images, 8 MB each and 32 MB per batch; at most 30 uploaded references per version.

**Building description** appears at the top of the archive. Click **Edit**, change the text, then **Save** to keep it in a new version. The description follows exports and working copies; editing it does not regenerate the blueprint. **Blueprints and execution records** is at the bottom, collapsed until opened.

## OpenAI configuration

Use **Settings** to save your API key, model ID and Minecraft game directory. Browse opens a folder picker. **Check Minecraft** reads connection/world status without placing blocks or calling OpenAI. A blank key preserves the existing key; Remove saved key disables fallback credentials too. GUI-saved keys are encrypted for the current Windows user, kept out of responses and archives, and stored in `data/settings.json`. The settings override `OPENAI_API_KEY` and `OPENAI_MODEL` from `.env` or the environment; `.env` remains a development fallback. A configured key is not proof of remote model access; no paid request is made just to open Settings.

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

In the GUI, **Built structure** selects a completed build from the current conversation (the latest by default). Ask, for example: **"Change the roof of this building in Minecraft to white wool. Keep its shape and the other parts unchanged."** MineCreator shows the target coordinates and a summary of added, removed and replaced blocks. Nothing is placed yet. Confirm in chat or click **Apply edit** to execute that exact saved proposal; requesting revisions produces a new review. Ordinary design requests still update only the plan, and **Build** still starts a new construction directly.

Edits place only changed blocks progressively and refresh the saved design after success. Unrelated blocks may differ from the blueprint: their current IDs and states are read before work and preserved, including blocks the player removed. Readback checks edit targets against the proposal and other recorded positions against that pre-edit snapshot. Only conflicts at edit targets, blocked additions, player proximity or changes during execution stop the edit. Stop preserves changes already applied. A failure before placement keeps the reviewed proposal and displays its reason; after resolving it, confirm again or click Apply edit to retry the same proposal. Once placement has started, the proposal cannot be replayed. There are no automatic retries, backups or rollbacks. GUI edit integration is covered by offline model/game regression tests.

With the test world running, send a Chinese or English request directly from PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/modify_with_openai.py "Replace the roof with white wool. Keep its shape and size unchanged, and leave the floor and pillars untouched."
```

The command targets the most recently completed blueprint build, loads its latest verified modification, and sends that current blueprint with your request to OpenAI. It applies only the block diff, then checks changed and unchanged blocks. Use `--build data/builds/blueprint-4b27ca8fe96e.json` to select a specific build. Supported edits are limited to the current blueprint materials and primitives; new voxel positions must be empty. No image generation is requested.

White wool is supported. CLI messages and generated blueprint labels use English; requests may use Chinese or English.

## GUI backend

Import `MineCreatorBackend` from `scripts.backend`. `start(operation, **arguments)` returns a worker task; the GUI polls `task.drain_events()` for structured English status, progress, result, and error events. After `task.done`, read `task.result().to_dict()`. One shared backend instance runs one task at a time. Existing CLI commands remain available.

Operations: `chat`, `design_plan`, `design_image`, `design_build`, `apply_edit`, `connection`, `generate_plan`, `voxel_preview`, `image_preview`, `survey`, `build`, `modify`, and `verify`. Chat takes `conversation_id`, `request_id`, and `prompt`; design_plan takes `conversation_id` and `request_id`; design_image and design_build also take `design_id`. apply_edit takes `conversation_id`, `request_id`, `review_id`, and `confirmed=True`. Conversation storage is available through `backend.conversations`. Preview/survey/build take `plan_path`; modify/verify require an explicit `build_path`. Build, design_build, and modify require `confirmed=True`; the GUI Build action supplies this, while an explicit chat build tool call dispatches the same bounded runner. `list_plans()` and `list_builds()` return local catalogs. No request or game edit runs when importing the backend.

`task.cancel()` stops at a safe checkpoint and preserves completed changes. A request already in flight may need to finish or time out; it is not automatically retried. On application exit, request cancellation and wait for task completion outside the UI thread.

The approved GUI direction is a dark deepslate Minecraft-inspired layout with green buttons and a simplified ivory architect icon with a green crossbar. The local reference is saved under `local-docs/gui-designs/approved/minecreator-gui-reference.png`. Interface text and program feedback remain English. Only the top-left MineCreator wordmark uses the pixel font. The bundled Silkscreen font is licensed under the SIL Open Font License; see `ui/fonts/OFL.txt`.

## Project constraints

- Generate only vanilla blocks, block states, and block entities. Modified saves must pass a reopening test in the same vanilla Minecraft version without manual conversion.
- One-time mod installation and environment setup are acceptable. Routine construction, modification, and inspection must run automatically through the GUI.
- Keep the scope manageable for a short solo project without developing a custom Java mod. Version 26.3 is the current test version, not a permanent restriction.
- Build visibly in stages through gradual block placement, reading back and adjusting completed sections between stages.
- Protect the player's position when the request is made and keep checking their current position. Preserve ground support, movement space, and an exit route.
- First look nearby for natural terrain that suits the building. Make necessary local terrain changes only when a suitable site cannot be found.
