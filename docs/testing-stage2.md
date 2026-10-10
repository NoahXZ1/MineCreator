# Stage 2 verification — 2026-10-09

## Current results

The latest QA round passed **86 unit/integration tests**, both existing browser suites, six browser fault scenarios, and an isolated source-process startup/restart test. Dependency validation and Python compilation passed. The Windows EXE rebuilt successfully, but Windows Application Control rejected its launch with **WinError 4551**, before application startup. Packaged runtime verification is therefore **blocked**, not passed. An earlier EXE smoke test passed before these changes; that result does not validate this build.

## Scope and methods

Tests use temporary workspaces, fake model responses and a fake Minecraft adapter. No paid inference, world writes, save backups or automatic rollback were performed in this test round. Existing user-confirmed live construction/edit results are separate evidence.

| Method | Scenarios | Result |
| --- | --- | --- |
| Unit and integration regression | Conversation state, blueprint/preview generation, construction, reviewed edits, target conflicts, unrelated player changes, cancellation, duplicate requests | 86 passed |
| Equivalence and boundary cases | Valid/invalid model IDs, game paths and image data; missing assets; invalid archive versions and paths | Pass |
| Fault injection | Disk write failure, corrupt file/hash, missing source, interrupted edits, disconnected Minecraft | Pass |
| State transitions | Review → confirmation → placement; failure before placement → explicit retry; immutable archive → working copy → new version | Pass |
| HTTP security | Missing token, cross-origin mutation, settings-file access, secrets omitted from responses, writes while a task is active | Pass |
| Browser end-to-end | Settings, reference uploads, archive round trips, image removal, description editing/save failure/retry/reload, records collapsed at the bottom | Pass |
| Simplified Design panel | Concept-only display, short state labels, block preview in chat, build/edit progress, retained Apply edit action, conversation reload | Pass; synthetic UI state and intercepted responses |
| Browser fault scenarios | Permanent task failure, temporary disconnect, interrupted task recovery, failed image generation, description draft retention, delayed preview after switching conversations | 6 passed |
| Source-process smoke | Independent temporary data directory, ZIP import, block preview, library image and description edits, Windows key encryption, exit and restart persistence | Pass |
| Existing local data | Read-only validation of 2 conversations and 11 archive versions | No validation errors |
| Dependency and syntax checks | pip check, Python compileall, build/edit CLI help imports | Pass |
| Windows packaging | PyInstaller bundle generation | Pass |
| Latest EXE startup | Independent process launch | Blocked: Windows Application Control, WinError 4551 |

The unittest suite contains 86 tests. Archive regression covers image and description changes across export/import/open/resave, unchanged blueprints and original versions, repeated requests, invalid inputs, non-image deletion rejection and interrupted writes. Description edits also cover the 8000-character boundary and isolation from newly generated blueprints. Browser checks use headless Microsoft Edge and wait for asynchronous dialogs/images before assertions. Earlier visual checks covered the archive browser, description editor, image controls and settings page. The existing disconnected-client regression remains covered.

## Bugs fixed in this round

Regression cases reproduced failures before fixes; the corrected behavior is now covered by automated checks.

- **Archive working copies:** nested paths in execution/metadata JSON were not restored, so opening and resaving could fail. JSON references now resolve to the copied assets.
- **Reference metadata:** unlinked image sidecars and original labels could disappear on resave. Working copies now retain the complete asset inventory and labels.
- **Invalid archives:** embedded external references and Windows case-colliding names could evade validation. A malformed catalog entry could disrupt the entire listing. Imports now validate nested references and names; bad catalog entries remain isolated.
- **Damaged local records:** malformed settings or conversation structures could prevent GUI startup. Valid conversations remain available; invalid settings produce a recoverable configuration error and can be replaced explicitly.
- **Execution-log writes:** construction and modification records used direct writes that could leave truncated JSON on interruption. Atomic replacement retains the previous complete record; injected write failures stop execution without marking it completed.
- **Task polling:** permanent request errors left the composer locked in an endless poll. They now stop polling and show a recovery message; temporary disconnection continues tracking the same task without resubmitting it.
- **Recovered status:** interrupted construction and failed image generation could show misleading status. The GUI now shows Stopped or Needs attention and retains the error in chat.
- **Description drafts:** reselecting an archive discarded unsaved text. Drafts now survive archive navigation within the current page session, including image-version changes. This is not restart/crash autosave.

## Reproduction

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q scripts desktop.py
# Start a fresh disposable server in a separate terminal:
.\.venv\Scripts\python.exe tests/workbench_fixture.py
# In another terminal with the provided Playwright runtime:
node tests/design_panel_browser.cjs
node tests/workbench_browser.cjs

# Start another fresh fixture for fault injection:
.\.venv\Scripts\python.exe tests/workbench_fixture.py
# In another terminal:
node tests/qa_fault_browser.cjs

# Source startup/restart verification:
.\.venv\Scripts\python.exe tests/packaged_smoke.py --source
# Actual EXE verification (currently blocked by local Windows policy):
.\.venv\Scripts\python.exe tests/packaged_smoke.py
```

The browser scripts reference the installed Codex Playwright runtime; adjust their `require` paths elsewhere. The main browser suite writes `data/browser-library-test.zip`, used by both startup smoke modes, and shuts down its fixture. Fault injection needs a fresh fixture because it creates archive data; stop that fixture with Ctrl+C afterward. Smoke tests launch and stop only their own isolated processes. `--source` explicitly tests the Python process and must not be reported as an EXE pass.

Local logs for this round include `data/qa-baseline.log`, `data/qa-library-red.log`, `data/qa-server-red.log`, `data/qa-regression.log` and `data/packaging.log`.

## Packaging

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller MineCreator.spec --noconfirm
```

The [PyInstaller manual](https://pyinstaller.org/en/stable/) describes the bundling workflow. This build uses Python 3.14.8 and PyInstaller 6.22.3 on Windows 11. Only the application, its dependencies and UI assets are bundled; project `.env`, conversations, Minecraft saves and API keys are excluded. Keep the `MineCreator.exe` and `_internal` directory together. Packaged data defaults to `%LOCALAPPDATA%/MineCreator`; the repository launcher continues using the existing project data.

## Practical limits

- **Distribution remains unverified:** the latest EXE is unsigned and Windows rejected execution with WinError 4551. The exact policy trigger has not been established; no security control was disabled. Clean-machine startup, signing and installer behavior remain untested. The repository source launcher is the verified local path.
- No new remote-model capability or live-Minecraft write test is claimed by this round.
- References are archived and viewable. Uploading a reference does not automatically give its pixels to the model.
- Imported or opened archives never restore an old world's active build/edit target. Historical logs remain available inside the archive.
- An edit that has started placing blocks cannot be replayed automatically. Failure before placement keeps its proposal for an explicit retry.
