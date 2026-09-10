# RockCoder Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the app from MewCode to RockCoder across branding, package/CLI namespace, and default storage directory behavior while preserving compatibility with existing `.mewcode` data.

**Architecture:** Introduce a single path-resolution compatibility utility first, then move all storage-path consumers onto it before renaming the package/CLI namespace. After storage compatibility is stable, mechanically rename the Python package and imports, then update user-facing branding strings, docs, and tests.

**Tech Stack:** Python 3.11, Textual, pytest, Hatch packaging

---

## File Structure

### New files
- `rockcoder/compat_paths.py` — centralized resolver for project/user data directories and legacy `.mewcode` fallback behavior
- `docs/superpowers/plans/2026-07-20-rockcoder-rename.md` — this implementation plan

### Renamed directory
- `mewcode/` → `rockcoder/`

### Files to modify early (path compatibility)
- `mewcode/__main__.py`
- `mewcode/app.py`
- `mewcode/memory/session.py`
- `mewcode/memory/instructions.py`
- `mewcode/filehistory/history.py`
- `mewcode/agents/loader.py`
- `mewcode/context/manager.py`
- `mewcode/permissions/checker.py`
- `mewcode/permissions/sandbox.py`
- `.gitignore`

### Files to modify later (package/branding)
- `pyproject.toml`
- `rockcoder/__main__.py`
- `rockcoder/app.py`
- `rockcoder/prompts.py`
- `rockcoder/plan_dialog.py`
- `README.md`
- tests that import `mewcode`, assert `MewCode`, or assert `.mewcode` paths

---

### Task 1: Add storage compatibility utility

**Files:**
- Create: `rockcoder/compat_paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from rockcoder.compat_paths import (
    resolve_project_data_dir,
    resolve_user_data_dir,
)


def test_project_dir_prefers_new_namespace(tmp_path: Path) -> None:
    (tmp_path / ".rockcoder").mkdir()
    (tmp_path / ".mewcode").mkdir()

    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.is_legacy is False


def test_project_dir_falls_back_to_legacy_namespace(tmp_path: Path) -> None:
    (tmp_path / ".mewcode").mkdir()

    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".mewcode"
    assert result.is_legacy is True


def test_project_dir_creates_new_namespace_when_missing(tmp_path: Path) -> None:
    result = resolve_project_data_dir(tmp_path)

    assert result.path == tmp_path / ".rockcoder"
    assert result.path.exists()
    assert result.is_legacy is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_paths.py"`
Expected: FAIL because `rockcoder.compat_paths` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResolvedDataDir:
    path: Path
    is_legacy: bool


def _resolve_dir(base: Path, new_name: str, legacy_name: str) -> ResolvedDataDir:
    new_path = base / new_name
    legacy_path = base / legacy_name
    if new_path.exists():
        return ResolvedDataDir(new_path, False)
    if legacy_path.exists():
        return ResolvedDataDir(legacy_path, True)
    new_path.mkdir(parents=True, exist_ok=True)
    return ResolvedDataDir(new_path, False)


def resolve_project_data_dir(work_dir: str | Path) -> ResolvedDataDir:
    return _resolve_dir(Path(work_dir), ".rockcoder", ".mewcode")


def resolve_user_data_dir(home_dir: str | Path) -> ResolvedDataDir:
    return _resolve_dir(Path(home_dir), ".rockcoder", ".mewcode")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_paths.py"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_paths.py rockcoder/compat_paths.py
git commit -m "feat: add rockcoder path compatibility resolver"
```

### Task 2: Move storage-path consumers to the compatibility utility

**Files:**
- Modify: `mewcode/__main__.py`
- Modify: `mewcode/app.py`
- Modify: `mewcode/memory/session.py`
- Modify: `mewcode/memory/instructions.py`
- Modify: `mewcode/filehistory/history.py`
- Modify: `mewcode/agents/loader.py`
- Modify: `mewcode/context/manager.py`
- Modify: `mewcode/permissions/checker.py`
- Modify: `mewcode/permissions/sandbox.py`
- Modify: `.gitignore`
- Test: `tests/test_memory.py`
- Test: `tests/test_permissions.py`
- Test: `tests/test_skills.py`
- Test: `tests/test_subagent.py`

- [ ] **Step 1: Write failing compatibility tests for one representative consumer**

```python
from pathlib import Path

from rockcoder.memory.session import SessionManager


def test_session_manager_uses_legacy_project_dir_when_new_missing(tmp_path: Path) -> None:
    legacy = tmp_path / ".mewcode"
    legacy.mkdir()

    mgr = SessionManager(str(tmp_path))

    assert mgr._sessions_dir == legacy / "sessions"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_memory.py" -k "legacy_project_dir"`
Expected: FAIL because `SessionManager` still hardcodes `.mewcode`

- [ ] **Step 3: Replace hardcoded `.mewcode` roots with resolved directories**

Use this pattern in each consumer:

```python
from rockcoder.compat_paths import resolve_project_data_dir, resolve_user_data_dir

project_data_dir = resolve_project_data_dir(work_dir).path
user_data_dir = resolve_user_data_dir(Path.home()).path
```

Examples to rewrite:

```python
Path(".mewcode").mkdir(parents=True, exist_ok=True)
filename=".mewcode/debug.log"
```

becomes

```python
project_data_dir = resolve_project_data_dir(Path.cwd()).path
project_data_dir.mkdir(parents=True, exist_ok=True)
filename=str(project_data_dir / "debug.log")
```

and

```python
self._sessions_dir = Path(work_dir) / ".mewcode" / "sessions"
```

becomes

```python
self._sessions_dir = resolve_project_data_dir(work_dir).path / "sessions"
```

- [ ] **Step 4: Update `.gitignore` to ignore both directories**

```gitignore
.mewcode/
.rockcoder/
```

- [ ] **Step 5: Run focused tests to verify compatibility behavior**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_memory.py" "D:/py_project/mewcode1/tests/test_permissions.py" "D:/py_project/mewcode1/tests/test_skills.py" "D:/py_project/mewcode1/tests/test_subagent.py"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .gitignore mewcode/__main__.py mewcode/app.py mewcode/memory/session.py mewcode/memory/instructions.py mewcode/filehistory/history.py mewcode/agents/loader.py mewcode/context/manager.py mewcode/permissions/checker.py mewcode/permissions/sandbox.py tests/test_memory.py tests/test_permissions.py tests/test_skills.py tests/test_subagent.py
git commit -m "feat: support rockcoder data dir with legacy fallback"
```

### Task 3: Rename package and CLI namespace

**Files:**
- Modify: `pyproject.toml`
- Rename: `mewcode/` → `rockcoder/`
- Test: import-bearing tests under `tests/`

- [ ] **Step 1: Write the failing packaging/import checks**

```python
import importlib


def test_rockcoder_package_imports() -> None:
    module = importlib.import_module("rockcoder")
    assert module is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_imports.py"`
Expected: FAIL because package `rockcoder` does not exist yet

- [ ] **Step 3: Rename package directory and rewrite imports**

Apply these mechanical changes:

```toml
[project]
name = "rockcoder"

[project.scripts]
rockcoder = "rockcoder.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["rockcoder"]
```

Rewrite imports like:

```python
from mewcode.app import MewCodeApp
```

to

```python
from rockcoder.app import RockCoderApp
```

and:

```python
import mewcode.prompts
```

to

```python
import rockcoder.prompts
```

- [ ] **Step 4: Update `__main__.py` CLI identity**

```python
parser = argparse.ArgumentParser(
    prog="rockcoder",
    description="RockCoder AI coding assistant",
)
```

and:

```python
from rockcoder.app import RockCoderApp
from rockcoder.driver import NoAltScreenDriver
```

- [ ] **Step 5: Run package/import tests**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_imports.py"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml rockcoder tests/test_imports.py tests
 git commit -m "refactor: rename package and cli namespace to rockcoder"
```

### Task 4: Rename app classes, theme identifiers, prompts, and visible branding

**Files:**
- Modify: `rockcoder/app.py`
- Modify: `rockcoder/prompts.py`
- Modify: `rockcoder/plan_dialog.py`
- Modify: `README.md`
- Test: `tests/test_commands.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_teams.py`

- [ ] **Step 1: Write the failing branding tests**

```python
def test_make_banner_uses_rockcoder_brand() -> None:
    from rockcoder.app import RockCoderApp

    banner = RockCoderApp._make_banner(model="test-model", work_dir="/tmp")
    assert "RockCoder v0.2.0" in banner.plain
```

```python
def test_system_prompt_mentions_rockcoder() -> None:
    ...
    assert "RockCoder" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_commands.py" -k "banner"`
Expected: FAIL because branding still says `MewCode`

- [ ] **Step 3: Rename app class and theme identifiers together**

Apply this coordinated update:

```python
_ROCKCODER_THEME = Theme(..., name="rockcoder")

class RockCoderApp(App):
    TITLE = "RockCoder"
    theme = "rockcoder"
```

Update banner text:

```python
t.append(f"RockCoder v{_get_app_version()}\n", style="color(242)")
```

- [ ] **Step 4: Update prompts, dialog copy, and README branding**

Examples:

```python
"You are RockCoder..."
```

```python
"Tell RockCoder what to change"
```

```markdown
# RockCoder
```

- [ ] **Step 5: Update tests to import `RockCoderApp` and assert new branding**

Representative rewrites:

```python
from rockcoder.app import RockCoderApp
```

```python
assert "RockCoder" in sp
```

- [ ] **Step 6: Run focused branding tests**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_commands.py" "D:/py_project/mewcode1/tests/test_agent.py" "D:/py_project/mewcode1/tests/test_teams.py"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add rockcoder/app.py rockcoder/prompts.py rockcoder/plan_dialog.py README.md tests/test_commands.py tests/test_agent.py tests/test_teams.py
git commit -m "feat: rename app branding to rockcoder"
```

### Task 5: Update remaining docs, path assertions, and full regression coverage

**Files:**
- Modify: tests asserting `.mewcode` or `MewCode`
- Modify: remaining docs that reference old names/paths
- Test: `tests/test_memory.py`
- Test: `tests/test_permissions.py`
- Test: `tests/test_skills.py`
- Test: `tests/test_subagent.py`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write or update final compatibility assertions**

```python
def test_new_project_dir_preferred_over_legacy(tmp_path: Path) -> None:
    (tmp_path / ".rockcoder").mkdir()
    (tmp_path / ".mewcode").mkdir()
    assert resolve_project_data_dir(tmp_path).path.name == ".rockcoder"
```

- [ ] **Step 2: Run targeted tests for docs/path fallout**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_memory.py" -k "rockcoder or mewcode"`
Expected: FAIL where old path assumptions remain

- [ ] **Step 3: Update remaining tests and docs to match final contract**

Final contract to encode in tests/docs:
- package name is `rockcoder`
- CLI is `rockcoder`
- display name is `RockCoder`
- new default data dir is `.rockcoder`
- legacy `.mewcode` is still accepted when the new dir does not exist

- [ ] **Step 4: Run the full regression suite for the touched areas**

Run: `"D:/py_project/mewcode1/.venv/Scripts/python.exe" -m pytest -q "D:/py_project/mewcode1/tests/test_commands.py" "D:/py_project/mewcode1/tests/test_memory.py" "D:/py_project/mewcode1/tests/test_permissions.py" "D:/py_project/mewcode1/tests/test_skills.py" "D:/py_project/mewcode1/tests/test_subagent.py" "D:/py_project/mewcode1/tests/test_agent.py" "D:/py_project/mewcode1/tests/test_teams.py"`
Expected: PASS

- [ ] **Step 5: Manual smoke verification**

Run the app and verify:
- banner says `RockCoder`
- CLI launches via `rockcoder`
- with only `.mewcode` present, old sessions/config still load
- with no data dir present, `.rockcoder` is created

- [ ] **Step 6: Commit**

```bash
git add README.md docs tests
git commit -m "test: finalize rockcoder rename coverage"
```

## Self-Review

- Spec coverage: this plan covers branding rename, package/CLI rename, `.rockcoder` defaulting, `.mewcode` fallback compatibility, tests, and manual verification.
- Placeholder scan: removed TBD/TODO language and provided concrete files, code snippets, and commands per task.
- Type consistency: the plan consistently uses `rockcoder.compat_paths`, `resolve_project_data_dir`, `resolve_user_data_dir`, and `RockCoderApp` as the target names across tasks.
