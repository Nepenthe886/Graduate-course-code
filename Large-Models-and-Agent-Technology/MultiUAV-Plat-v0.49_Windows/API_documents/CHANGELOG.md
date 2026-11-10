# Changelog

All notable changes to this project should be documented in this file.

## [0.4.7] - 2026-10-26

### Student submission sample - 2026-10-28

- **Student submission sample (2026-10-28):** Added `guide/task template/提交样板/` with the two required directories `班级-序号-队员1-队员2-任务模板` and `班级-序号-队员1-队员2-场景文件`. Included one simple takeoff/hover template and a matching one-drone, clear-weather Session with one generated pending Task, plus naming/import/export instructions. Verified template import, Task parity, and in-process Server `POST /sessions` restoration (201); no live UAV commands were executed. **Server:** Release-note synchronization only; no runtime or API changes. **Agent:** No changes.

### Template reference refresh - 2026-10-28

- **Template reference refresh (2026-10-28):** Replaced the outdated template guide with a current 0.4.7 reference covering actual GUI entry points, configured storage, valid import JSON, endpoint/id binding, complete placeholder categories/default ranges, check trees, current built-in IDs, and a runnable offline example with entity names. Updated the student tutorial to document conflicting coordinate definitions, composite/scalar cache inconsistency, and best-effort collision checking; these runtime limitations remain unfixed. **Server:** Updated its companion reference and synchronized release notes. **Agent:** Documentation guidance only; no service or API changes.

### ZIP source packaging - 2026-10-28

- **Packaging configuration inclusion (2026-10-28):** Removed `settings.json` from both Controller and Server packaging exclusions. Existing settings files are included unchanged; absent files are not synthesized. Updated packaging documentation. **Agent:** No changes.
- **ZIP source packaging (2026-10-28):** Changed both Controller and Server `pack.sh` outputs from tar.gz to ZIP, using Python standard-library ZIP output to preserve Unicode names without macOS extended attributes or tar/PAX headers. Preserved source/resource inclusion, runtime-data exclusions, symlink storage, unique output names and failure cleanup; updated packaging documentation. **Agent:** No changes.

### Source packaging - 2026-10-27

- **Source packaging (2026-10-27):** Added executable `pack.sh` scripts to Controller and Server. Each archives the current source tree to its parent directory as a versioned, timestamped `.tar.gz`, including uncommitted source, docs, tests and bundled assets while excluding dependencies, caches, build outputs, logs, local settings and session data. Scripts locate their own project directory and clean temporary output on failure. Added usage documentation in Controller `guide/source-packaging.md` and Server `docs/SOURCE_PACKAGING.md`. **Agent:** No changes.

### Changed

- **Server / Controller:** Aligned `main.py` and `app_version.py` application versions at `0.4.7`. Moved the Task Template management, Session Manager layout, Settings/About, and student tutorial changes into this release; retained the earlier `0.4.6` history. Agent Service API versions are unchanged.

### Task template management - 2026-10-26

- **Controller tutorial cleanup (2026-10-27):** Removed the redundant `guide/task template/examples/` files. The student tutorial now uses the exported `basic_takeoff` and `quick_checkpoint` default templates for its import workflow and exercises, with the checkpoint check coverage described accurately. Server and Agent runtime behavior is unchanged.
- **Controller documentation layout (2026-10-26):** Moved all 58 default-template JSON files and their index into `guide/task template/default_templates/` beside the student tutorial. Updated tutorial links, the index backlink/source link, and Agent prompt file paths. Server and Agent runtime behavior is unchanged.
- **Controller template resources and tutorial (2026-10-26):** Exported all 58 built-in templates from `default_templates.py` into individually importable JSON files in `default_templates/`, with a category index and import/source guidance. Extended the student tutorial with default-template usage, AI Agent template/Task generation instructions, a copyable prompt, offline validation steps, and the distinction from runtime Agent execution. Verified exact source parity, all 58 imports and persistence as custom copies, unchanged built-ins, and documentation links. **Server:** Synchronized release notes only; no API changes. **Agent:** No service or API changes.
- **Controller documentation (2026-10-26):** Added a Chinese student tutorial in `guide/task template/` covering TaskTem management, template fields and requirements, entity/custom/random/coordinate placeholders, command/check consistency, task generation, sharing, exercises, and troubleshooting. Included two importable JSON templates; validated 60 offline instantiations for parameter detection, numeric ranges, protocol normalization, and shared values across content/APIs/checks. Linked the tutorial from the guides and marked the older editing guide as historical. No Controller runtime, Server API, or Agent changes.
- **Controller label correction (2026-10-26):** Renamed the Session Manager template button and Tools menu entry from `TaskTemp` to `TaskTem`; updated the guide. Server and Agent runtime behavior is unchanged.
- **Controller layout (2026-10-26):** Arranged Session Manager actions in two rows of seven equal-width, column-aligned buttons; the second row ends with TaskTemp then TaskUI. Server and Agent runtime behavior is unchanged.
- **Controller naming (2026-10-26):** Shortened the Session Manager task-template button and Tools menu entry to `TaskTemp` / `TaskTemp…`; updated the user guide. Server and Agent runtime behavior is unchanged.
- **Controller UI follow-up (2026-10-26):** Moved the Session Manager About button into Settings, preserving the Help → About menu. Task Templates now appears before TaskUI. About opens above Settings and restores its modal focus when closed; no Server or Agent runtime changes.
- **Controller:** Added a `Task Templates` button and `Tools → Task Templates…` menu entry in Session Manager. They open one reusable, non-modal local template manager without session selection or server access.
- **Controller:** Reused the existing template browser/editor for search, source and task-type filters, source labels, preview, create/edit/duplicate/delete, and single-template JSON import/export. Management mode hides task creation; double-click edits custom templates or previews built-ins. Built-ins remain protected and can be copied for editing.
- **Controller:** Added unsaved-edit confirmation, selection of saved/imported templates, import structure validation and collision-safe IDs, atomic library writes with in-memory rollback, and reload preservation on malformed library files. TaskUI now uses the configured/shared template library; settings changes update the open manager.
- **Controller validation:** 35 focused regression tests passed, including import/export round trips, invalid data, failed-write rollback, window reuse, and existing template/session generation behavior. A real Tk window smoke check passed for management filters, action states, non-modal behavior, editor save, and legacy template selection.
- **Server:** Updated the application version to `0.4.7`; no API or runtime logic changes. Synchronized the release notes with Controller.
- **Agent:** No changes in this task.

## [0.4.6] - 2026-10-14

### Changed

- Saved the cross-project changelog synchronization and Server/Controller version-alignment requirements in both projects' `AGENTS.md` files.
- Aligned the server (`main.py`) and controller (`app_version.py`) application versions at `0.4.6`. Controller tag `0.4.5` remains on commit `e9cb7bc`; the subsequent controller changes belong to `0.4.6`.
- CheckUI now sends one stop request on task timeout or user Stop, stops polling the job, and evaluates task completion immediately. Stop requests use a two-second HTTP timeout; a failed stop request does not prevent Check.
- A timed-out task is checked and the queue proceeds to the next task. User Stop checks the current task and ends the batch; a later Run starts without querying the old job or asking for platform-stop confirmation.
- Removed CheckUI's `cancel_and_confirm`, `execution_confirmed_finished`, unresolved-job blocking, and platform confirmation flow. Cancelled or failed jobs no longer require `execution_finished_at` before Check; legacy Agents without that field remain compatible.
- Stopped batches no longer display a completion dialog. Closing the checker waits for its own checking worker and does not require Agent-exit or platform confirmation.
- Both companion Agent services (`agent4drone` and `agent4drone_ablation`) immediately mark a stopped job `cancelled` using an independent per-job stop flag. Model/tool boundaries, retry attempts and UAV requests check the flag; ablation navigation also checks it between steps. The ablation pending-cancellation state and global execution lock were removed.
- Agent runs now isolate their API clients, request capture, tools, blackboards and session context. Only runs that finish without being stopped retain session memory, so a late stopped response cannot overwrite a subsequent run or issue further requests at the next stop-check boundary.
- The main Agent now uses a default 180-second model-request timeout, configurable through `UAV_LLM_REQUEST_TIMEOUT_SECONDS`, with OpenAI-compatible SDK retries disabled (`max_retries=0`), matching ablation. Ollama receives the same timeout through `client_kwargs`. UAV HTTP requests in both Agents default to 30 seconds unless explicitly overridden.
- Agent exit timestamps remain diagnostic metadata; the checker no longer waits for model completion or recording cleanup before checking or starting the next task. UAV requests already sent before stopping may still complete.

### Fixed

- CheckUI always evaluates the task check tree after Agent failure, timeout, service interruption, missing responses or monitoring exceptions, provided the task has valid checks and the expected session is still current. A cancelled or failed execution alone does not determine whether the checks pass.
- Failed Agent execution now prints a one-line `Agent error: ...` summary immediately above the existing failure/timeout notice. It prefers the job error, then the result error/output, then the monitoring reason; whitespace is collapsed, the standard error prefix is removed, and the summary is capped at 200 characters. Quota errors such as `429 / AccountQuotaExceeded` are visible in the checker window.

### Validation

- Added or updated regression coverage for legacy job responses, immediate stop without further polling, failed stop requests, interrupted polling waits, task checks after Agent errors, timeout queue continuation, user Stop, restart without confirmation, and late-model-response isolation.
- Agent service/client/model/recording regression runs passed: main Agent 76 tests; ablation 96 tests plus 30 tool/navigation tests. The CheckUI stop/queue regression run passed 84 tests with two pre-existing candidate-export failures excluded; the stopped-batch dialog check passed in the nine-test progress suite. The subsequent error-summary regression run passed 40 cases.
- Changes were verified with simulated model/platform operations; no real UAV operations were run.

## [0.4.5] - 2026-09-25

### Added
- Added the vendored, dependency-free Related APIs v1 protocol (`related_api_protocol/`) with a shared POST command catalog, plan parsing and normalization, actual-value validation, and per-operation `parameters.id` binding; `tools/sync_related_api_protocol.py` copies the protocol into the sibling agent app or checks parity.
- Added `related_api_executor.py` for sequential plan execution: it polls asynchronous commands, honours cancellation and per-command timeouts, never retries writes, and distinguishes confirmed failures from unknown in-flight outcomes.
- Added Copy JSON and Export buttons to the task editor's related-APIs section, both emitting normalized v1 plans as JSONL.
- Added CheckUI task-check feedback reporting, which posts the final evaluation status to the Agent API and records whether that feedback was saved.

### Changed
- Load & Run now opens one non-modal window for pasted `related_apis`, task JSON, or JSONL content, validates and previews the resolved requests, and runs them on a background thread with an optional continue-after-confirmed-failure mode; task checks run only for complete task JSON.
- Agent candidates, legacy archive exports, and Load & Run now share the v1 converter instead of duplicating command matching in the checker.
- Failed or partially successful commands are excluded from exported plans so Load & Run does not replay them; their source indices remain listed in the manifest `issues`.
- Default templates now bind `move_to` target coordinates for target-tracking and multi-drone tasks, and template instantiation rejects related APIs that are not a valid v1 plan.
- Marking tasks done or pending, and CheckUI task completion, no longer mark the session as modified because they are runtime state rather than session definition edits.
- Candidate replay accepts flat run directories that contain `related_apis.jsonl` next to `manifest.json`, without snapshot restoration and without claiming equivalent task validation.
- The session editor renders like the drone UI: the window is sized from the desktop instead of a fixed 1280x800, drawing goes through SDL's renderer at the monitor's native pixel density, and text uses the bundled Alibaba PuHuiTi face, so labels are crisp on Retina and high-DPI displays.
- The session editor shows the logical zoom factor (for example `1.0x`) instead of the framebuffer-scaled one, and always opens at a fixed `0.4x` centered on the session, whichever interface launched it (GUI controller or Session Manager, own process or in-process). Fitting every item into view moved to the minimap double-click, which still uses the largest zoom level that fits.
- The session editor opens on the display the GUI window is on instead of jumping to the primary display: SDL centers a fresh window on the primary monitor, so the launcher now passes the parent window's screen (and the pointer decides when the editor runs as its own process) and the SDL window is moved there.

### Fixed
- PyInstaller builds now bundle `related_api_protocol/catalog.json`, which the protocol reads relative to its package; the frozen executable previously crashed at startup with `FileNotFoundError`.
- Windows notebook tabs use DPI-scaled padding, so the tab labels are no longer cramped on high-DPI displays.
- Moving a window between Windows monitors of different scale now keeps its logical size, like macOS: the pixel size follows the new monitor's scale factor (800x600 logical is 1600x1200 at 200% and 2400x1800 at 300%). Each top-level window is tracked separately, so dragging a dialog to another display is noticed on its own and the windows left behind are not re-sized. The new geometry is written from the stored logical size alone, never from the window's current pixels, so a crossing can no longer apply the scale twice when Windows has already resized the frame; the logical size is only re-read while a window sits still on one monitor, which is what remembers a size the user dragged.

### Controller 0.4.5 follow-up - 2026-10-14

- `Skip already checked tasks` now uses recorded Checker outcomes rather than Agent cancellation, interruption or monitoring metadata. Recorded failed/interrupted attempts remain checked and are skipped on restart.
- `Skip passed tasks` continues to use the final check-tree result; interruption alone does not invalidate a passed result.
- Starting Auto-Check moves keyboard focus off the button after it becomes Stop, preventing an accidental key press from stopping the run on Windows and macOS.
- Experiment notes remain read-only during an active or paused batch and become editable when the workflow ends.
- Removed CheckUI task-check feedback POSTs and the associated `feedback_saved` result field. Existing Agent run/job linkage and local check results are retained.
- Added macOS Dock reopen handling for Session Manager, Controller and Auto-Check windows. It restores minimized Toplevel windows even when the owning root is withdrawn and is a no-op on other platforms.
- Committed the controller changes as `e9cb7bc` and tagged the snapshot `0.4.5`; the controller working version was then advanced to `0.4.6`.

## [0.4.3] - 2026-09-22

### Changed
- CheckUI now reloads the session before each task by default and uses a default Agent timeout of 600 seconds.
- Agent progress updates in place in the log and clears when execution ends, without increasing API polling frequency. Timeout and execution failure messages are distinct.
- CheckUI supports Cmd/Ctrl+W to close, with confirmation during execution and shutdown after stopping. The centered confirmation defaults to No and shows the platform-specific shortcut.

## [0.4.2] - 2026-09-16

### Added
- Added task generation settings for single and batch session creation, with independent total and per-template counts and random or even allocation.
- Added template selection with Shift/Cmd/Ctrl, synchronized number ranges, and normalization of Chinese commas, whitespace, and extra separators.

### Changed
- Show all templates with consistent row numbers; templates unavailable for the current task type or automatic generation are gray and cannot be selected.
- Accelerated random-session collision checks with a per-session spatial hash and cached geometries.
- Added independent target/obstacle area limits before placement: at most 20% of map area per entity, at most three entities above 10%, and at most 40% combined for those large entities. Dimensions are floor-halved until compliant; processed sizes then enter the existing 40-round loop and define its shrinking floor.
- Targets and obstacles now keep their sampled types and shrink after failed random/grid placement using the smaller of floor(size × 0.94) and size − 8, with a 10%/minimum-2 dimension floor and at most 40 rounds. Drones retain fixed sizes and their existing retry budget.

## [0.4.1] - 2026-07-03

### Added
- Added an About button to the UI toolbar with version, copyright, license, clickable paper/project/website links, and click-outside dismissal.
- Added a `show_label` screenshot option to hide drone, target, and obstacle labels while preserving other screenshot content.
- Added UI details-panel vertex coordinate rows for selected polygon targets and obstacles; polygon target details now omit radius and label vertices with the target name.
- Added yellow mini-map selection highlighting for selected drones, targets, and obstacles.

### Changed
- Limited runtime request-history retention to the current session; non-current sessions discard request history by default, and switching the current session clears request history from other sessions.
- Changed request-history `limit` query defaults so omitted `limit` returns all retained records, while explicit `limit` values return that many recent records.
- Changed `python main.py --ui-drone-control` to start the UI directly without showing the startup UI prompt.
- Listed `httpx` in requirements so FastAPI `TestClient`-based tests run in fresh environments.
- Excluded `GET /sessions/current/data` from runtime request-history recording.
- Stopped storing full response bodies in structured API logs; logs now record response size, type, and an omission summary, and large response paths skip body capture.

## [0.4.0] - 2026-06-06

### Added
- Added `--ui-drone-control` to opt into UI drone controls, including the Take Off/Land button and map-click movement commands.
- Added `GET /sessions/current/command-history` as a convenience endpoint for retrieving the active session's recent command history.
- Added runtime-only HTTP request history with current-session and explicit-session query endpoints, sensitive-value redaction, and client/authentication audit context.
- Added filtered AGENT access to `GET /sessions/current/request-history` using the non-secret `X-Agent-ID` attribution header.
- Added SYSTEM/ADMIN `DELETE /sessions/current/request-history` and `DELETE /sessions/{id}/request-history` endpoints for clearing runtime request history without resetting sessions.
- Added `--request-history-limit` startup configuration and increased default per-session request-history retention to 5,000 records while keeping API responses capped at 1,000.

### Fixed
- Improved request-history response time by avoiding full response-body capture and normalizing only records needed for each request-history response.
- Preserved replayable request-history query parameters, including ordered repeated keys, sensitive-value redaction, failed requests, and legacy `{}` defaults.
- Excluded request history from serialized session objects, JSON exports, imports, and restores so it remains process-local runtime state.
- Standardized request-history `client_privilege` values on uppercase role names.

## [0.3.9] - 2026-05-23

### Fixed
- Kept the UI path trail length button selection unchanged when the current session reloads and drone-specific path settings are recreated.

## [0.3.8] - 2026-05-10

### Changed
- Added plain `move_along_path` response fields for successful and unsuccessful waypoint counts and `(x, y, z)` coordinate triples.
- Moved `move_along_path` point feedback fields to a dedicated response model so other command schemas do not advertise path-only fields.
- Kept `move_along_path` error responses focused on the error message without populated point feedback values.
- Updated `move_along_path` partial movement responses to return `partial_success` when `allow_partial_move=true` reaches at least one waypoint but stops before the requested endpoint.

## [0.3.6] - 2026-05-03

### Changed
- Updated `move_along_path` to accept 2D `{x, y}` waypoints by defaulting omitted `z` values to the calling drone's current altitude.
- Updated `move_along_path` partial movement so `allow_partial_move=true` stops at the last waypoint with sufficient battery instead of rejecting the whole path when the full route exceeds available battery.
- Updated `move_along_path` battery usage to remove the per-waypoint base cost, making path following charge only horizontal and vertical distance costs.
- Optimized `move_along_path` response time for large waypoint lists by batching internal coverage tracking, reusing path calculations, and avoiding full session history serialization during command recording.
- Updated `move_to` battery usage to remove the base movement cost, matching distance-only movement charging.
- Updated the startup UI prompt to use the same drone window icon as the main graphical dashboard.
- Updated target and obstacle canvas labels to show each object's ID under its type.

## [0.3.4] - 2026-04-25

### Added
- Added multiple hard-coded USER, SYSTEM, and ADMIN privilege keys for API authentication while preserving existing AGENT authentication behavior.
- Added optional `since_timestamp` scoping to task check endpoints so compatible history checks can validate work performed at or after a given timestamp.

### Changed
- Updated authentication documentation and examples to use API key placeholders instead of publishing concrete key values.
- Updated target reach and moving-target tracking check endpoints to support `since_timestamp` filtering.
- Corrected landing history check documentation to describe the implemented `min_count` parameter.
- Updated PyInstaller packaging to include project submodules as hidden imports so frozen Windows builds can import local modules such as `config.battery_config`.

## [0.3.3] - 2026-04-22

### Changed
- Extended ADMIN history check endpoint `GET /check/drone_has_taken_off` to support optional `max_altitude` and `tolerance` query parameters, enabling backward-compatible minimum-threshold checks, bounded altitude-range checks, and exact-height checks via `min_altitude == max_altitude`.
- Updated takeoff history check responses and documentation to include the applied altitude bounds and tolerance metadata.

## [0.3.2] - 2026-04-08

### Added
- Added SVG and EPS export support for session screenshots.
- Added a `show_status` screenshot option that includes path traces, area coverage, reached/tracked target state, and UI-style status bar details.
- Added ADMIN check endpoint `GET /check/drone_group_distance` for validating pairwise distance constraints across a group of drones, with `all_pairs` and `any_pair` modes.
- Added ADMIN check endpoint `GET /check/drone_has_sent_message_content` for validating whether a drone sent message text containing a requested substring, including broadcasts.

### Changed
- Refactored screenshot rendering to use a shared scene model for raster and vector exports.
- Updated screenshot API documentation to cover the new formats and `show_status` query parameter.
- Updated PyInstaller packaging to use the compact executable version format, so app version `0.3.2` builds as `MultiUAV-Plat.Server.v0.32`.
- Updated API reference, API documentation, and authentication docs to describe the new drone-group distance check and repeated-query `drone_ids` usage.
- Updated message-check documentation to describe the new content-matching endpoint and its broadcast-inclusive behavior.
- Updated `move_along_path` to accept `allow_partial_move=false` by default, with optional partial completion that stops at the last safe waypoint before an obstacle and reports the early stop in the success message.
- Updated `move_along_path` to accept a single waypoint, so one-point path requests are handled as a valid path move instead of being rejected.

## [0.3.1] - 2026-04-06

### Added
- Added a cross-platform PyInstaller spec file at `multiuav_plat.spec` for reproducible standalone builds on Windows, macOS, and Linux.
- Added native PyInstaller icon assets at `ui/img/drone.ico` and `ui/img/drone.icns` generated from the existing project icon.

### Changed
- Removed the session `time_mode` attribute from the backend session model, session APIs, example session payloads, and API documentation.
- Updated the standalone deployment documentation in `README.md` to use the shared spec file instead of platform-specific inline PyInstaller commands.
- Corrected the documented UI startup flag for compiled binaries to `--ui`.
- Updated the PyInstaller spec to set the executable icon automatically on Windows and macOS.
- Standardized target and obstacle API payloads on the canonical `type` field instead of legacy `target_type` and `obstacle_type` names across the server contract, tests, README, and API docs.
- Added a toolbar toggle in the UI to show or hide canvas labels for drones, targets, and obstacles.

## [0.3.0] - 2026-04-01

### Added
- Added explicit moving-target runtime fields to target responses:
  - `movement_mode`
  - `last_motion_update`
  - `tracking_status`
  - `last_tracked_at`
- Added backend-owned moving-target tracking state in session history via `moving_target_tracking`.
- Added ADMIN check endpoint `/check/moving_target_tracked` for validating retained moving-target tracking duration for any drone or a specific drone.
- Added dedicated regression tests for moving-target creation, validation, motion stepping, and tracking behavior.
- Added initial project changelog in `docs/CHANGELOG.md`.

### Changed
- Refactored moving-target handling so live target objects are the authoritative runtime source for movement state.
- Refactored session synchronization to use explicit snapshot sync APIs instead of mutating on session reads.
- Updated moving-target motion handling to use explicit internal movement modes:
  - `velocity`
  - `path`
  - `stationary`
- Updated path-based movement to progress deterministically across larger time steps.
- Updated collision behavior for moving targets to apply mode-specific reversal logic.
- Updated `target_tracking` task progress so moving targets use backend freshness-based tracking semantics instead of UI-local timeout logic.
- Enriched `moving_target_tracking` with compact per-drone recent periods so specific-drone duration checks are possible without storing an unbounded raw event log.
- Updated target retrieval paths and UI rendering to consume canonical backend tracking and movement state.
- Updated API documentation and reference docs to describe the new moving-target runtime fields and session tracking payloads.

### Fixed
- Fixed stale moving-target state issues caused by session snapshots lagging behind live simulation state.
- Fixed duplicated movement-mode inference across backend and UI layers.
- Fixed repeated session write/snapshot side effects triggered from drone target-reach loops.
- Fixed path validation gaps by rejecting degenerate moving paths and obstacle-conflicting path segments.
- Fixed inconsistency between historical target reach and current moving-target tracking display.

### Compatibility
- Existing target request payloads remain supported.
- Legacy target response fields such as `is_reached` and `reached_by` remain available for compatibility, but moving-target freshness is now derived from backend tracking state.
