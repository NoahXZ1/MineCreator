# MineCreator

An LLM-powered building design and construction tool for Minecraft.

## Architecture

Fabric + MCPFabric. Python controls a Minecraft Java single-player world through a local HTTP API. The LLM produces blueprints for the program to validate and execute. Preview and construction use the same expanded block data.

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

## Current status

Reading, building, modifying, and restoring blocks in a single-player test world have been verified through Python → MCPFabric. LLM API integration is pending.

## Project constraints

- Generate only vanilla blocks, block states, and block entities. Modified saves must pass a reopening test in the same vanilla Minecraft version without manual conversion.
- One-time mod installation and environment setup are acceptable. Routine construction, modification, and inspection must run automatically through the GUI.
- Keep the scope manageable for a short solo project without developing a custom Java mod. Version 26.3 is the current test version, not a permanent restriction.
- Build visibly in stages through gradual block placement, reading back and adjusting completed sections between stages.
- Protect the player's position when the request is made and keep checking their current position. Preserve ground support, movement space, and an exit route.
- First look nearby for natural terrain that suits the building. Make necessary local terrain changes only when a suitable site cannot be found.
