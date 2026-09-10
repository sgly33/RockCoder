# RockCoder Rename Design

## Goal

Rename the project from `MewCode` to `RockCoder` across user-facing branding, runtime identifiers, Python package/CLI names, and the local data directory, while preserving existing user data under `.mewcode` through backward-compatible startup behavior.

## Scope

This change includes:
- displayed product name (`MewCode` → `RockCoder`)
- internal runtime identifiers tied to the product name
- Python package namespace (`mewcode` → `rockcoder`)
- CLI entrypoint name (`mewcode` → `rockcoder`)
- default local data/config directory (`.mewcode` → `.rockcoder`)
- tests and docs that reference the old name or paths

This change does not include:
- redesigning memory/session formats
- changing feature behavior unrelated to naming/path resolution
- one-shot destructive migration that deletes the old `.mewcode` directory

## Chosen Approach

Use a three-layer rename with compatibility on the storage layer.

### 1. Branding and runtime identifier rename

Update all user-visible and internal product-name identifiers from `MewCode` / `mewcode` to `RockCoder` / `rockcoder` where they represent branding or runtime identity rather than persisted compatibility contracts.

Examples:
- app title and banner
- prompt identity text
- dialog copy
- README and design/docs prose
- app class names
- theme identifiers

### 2. Python package and CLI rename

Rename the package directory from `mewcode/` to `rockcoder/`, rewrite in-repo imports, and update packaging metadata so the installed command becomes `rockcoder`.

Examples:
- `pyproject.toml` package name and script entrypoint
- `rockcoder/__main__.py`
- all `from mewcode...` / `import mewcode...` references
- tests that import the package or reference app class names

### 3. Storage path compatibility layer

Adopt `.rockcoder` as the new default project/user data directory, but keep backward compatibility for existing `.mewcode` data.

Preferred runtime behavior:
1. If `.rockcoder` exists, use it.
2. Else if `.mewcode` exists, read from it and treat it as the active legacy store.
3. Else create `.rockcoder`.

This keeps the rename safe for existing local histories, permissions, skills, agents, sessions, plans, and memory files without forcing a destructive migration step.

## Why This Approach

### Recommended: full rename with legacy directory fallback

Pros:
- product name becomes consistent everywhere users can see or invoke it
- package and CLI names match the public product name
- existing local data remains usable
- avoids forcing users to manually move configuration and history files

Cons:
- broader code churn than a branding-only rename
- path resolution code must handle both old and new directories carefully
- tests need coordinated updates across imports, strings, and storage paths

### Rejected: branding-only rename

Reason:
- leaves package/CLI/storage identity split across `RockCoder` and `mewcode`
- weakens the rename by keeping the most visible technical surfaces old

### Rejected: hard cut to `.rockcoder` with no compatibility

Reason:
- breaks existing local sessions, permissions, skills, agents, and memory unexpectedly
- creates avoidable migration pain and makes the rename feel brittle

## Design Details

### A. Naming rules

Use these target forms consistently:
- product/display name: `RockCoder`
- Python package/import namespace: `rockcoder`
- CLI command: `rockcoder`
- local storage directory: `.rockcoder`

Old forms (`MewCode`, `mewcode`, `.mewcode`) should remain only where explicitly needed for compatibility reads or migration logic.

### B. Compatibility contract for storage

Compatibility should be centralized rather than duplicated ad hoc.

Create or adapt a small path-resolution utility used by session, memory, permissions, skills, agents, file history, and related features.

That utility should answer:
- active project data dir
- active user-global data dir
- whether the active dir is legacy `.mewcode`

Behavior should be deterministic:
- prefer `.rockcoder` if present
- otherwise fall back to `.mewcode`
- otherwise create `.rockcoder`

The system should not silently copy or delete user files during normal startup in this phase.

### C. Package rename boundaries

Package rename should be mechanical and complete:
- directory rename
- import rewrite
- script entrypoint update
- test import update
- documentation path update where it refers to the package

Do not leave mixed `mewcode` and `rockcoder` imports in normal runtime code.

### D. Documentation and prompt alignment

Docs and prompts should reflect the renamed product consistently:
- README title/body
- in-app prompt identity
- MEWCODE/agent-facing branding references that describe the app
- tests asserting visible product strings

Compatibility-only path references may still mention `.mewcode` where the code explicitly supports legacy directories.

## Critical Files

High-priority rename anchors:
- `pyproject.toml`
- `mewcode/__main__.py` → `rockcoder/__main__.py`
- `mewcode/app.py`
- `mewcode/prompts.py`
- `mewcode/plan_dialog.py`
- `README.md`
- `tests/test_commands.py`
- `tests/test_agent.py`
- `tests/test_teams.py`

High-priority storage compatibility files:
- `mewcode/memory/session.py`
- `mewcode/memory/instructions.py`
- `mewcode/filehistory/history.py`
- `mewcode/agents/loader.py`
- `mewcode/context/manager.py`
- `mewcode/permissions/checker.py`
- `mewcode/permissions/sandbox.py`
- `.gitignore`
- tests that assert `.mewcode/...` paths

## Verification

### 1. Import/package verification
- run the rename-affected test suites
- confirm imports succeed under `rockcoder`, not `mewcode`
- confirm the CLI entrypoint resolves as `rockcoder`

### 2. Branding verification
- launch the TUI and confirm title/banner/dialog branding shows `RockCoder`
- confirm prompt/system identity strings use `RockCoder`
- confirm README and visible docs no longer describe the app as `MewCode`

### 3. Legacy data compatibility
- with only `.mewcode` present, confirm the app still finds sessions/config/history/memory
- with `.rockcoder` present, confirm it is preferred
- with neither present, confirm `.rockcoder` is created

### 4. Resume/session regression
- confirm existing saved sessions remain resumable from legacy `.mewcode`
- confirm new sessions are stored in the selected active directory

### 5. Path-sensitive regression
- verify permissions, skills, agents, plans, file history, and memory instruction loading still work when legacy paths are the only existing paths

## Main Risk

The main risk is treating `.mewcode` as a cosmetic string when it is actually a persistence contract. If any feature is left on the old hardcoded path while others switch to `.rockcoder`, the rename will create inconsistent behavior that is harder to debug than a clean failure.

The compatibility layer is therefore the most important part of this design.