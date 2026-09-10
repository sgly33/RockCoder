# Dual-Layer Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a complete dual-layer memory system with a read-only static instruction layer (`RockCoder.md`) and an automatically maintained dynamic memory layer with index-based recall, lifecycle operations, graceful degradation, and test coverage.

**Architecture:** Keep static instructions and dynamic memory separate at every boundary: discovery, loading, writing, recall, and user-facing management. Reuse the existing startup flow in `rockcoder/app.py` and `rockcoder/agent.py`, extend `rockcoder.memory` rather than adding a parallel subsystem, and make dynamic memory extraction a small, deterministic first version that only persists high-value, low-risk facts.

**Tech Stack:** Python 3.11, Textual app lifecycle, existing `rockcoder.memory` modules, pytest, markdown-based memory files under `.rockcoder/memory/`

---

### Task 1: Tighten static instruction loading semantics

**Files:**
- Modify: `rockcoder/memory/instructions.py`
- Modify: `rockcoder/conversation.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestLoadInstructions:
    def test_missing_rockcoder_md_is_nonfatal(self, tmp_path: Path) -> None:
        assert load_instructions(str(tmp_path)) == ""

    def test_load_instructions_reports_read_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        file = tmp_path / "RockCoder.md"
        file.write_text("rules", encoding="utf-8")

        original = Path.read_text

        def broken_read(self: Path, encoding: str = "utf-8") -> str:
            if self == file:
                raise OSError("boom")
            return original(self, encoding=encoding)

        monkeypatch.setattr(Path, "read_text", broken_read)
        result = load_instructions(str(tmp_path))
        assert "Failed to read" in result
        assert "RockCoder.md" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory.py -k "missing_rockcoder_md_is_nonfatal or load_instructions_reports_read_failure" -v`
Expected: FAIL because read failures are currently swallowed and not surfaced.

- [ ] **Step 3: Return structured load diagnostics from instruction loading**

```python
@dataclass
class InstructionLoadResult:
    content: str
    warnings: list[str]


def load_instructions(project_root: str) -> InstructionLoadResult:
    ...
    try:
        data = abs_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"Failed to read instruction file {abs_path}: {exc}")
        return
```

- [ ] **Step 4: Inject warnings into the long-term reminder without fabricating content**

```python
def inject_long_term_memory(self, instructions: str, memories: str, warnings: list[str] | None = None) -> None:
    ...
    if warnings:
        sections.append("# instructionWarnings\n" + "\n".join(f"- {w}" for w in warnings))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_memory.py -k "missing_rockcoder_md_is_nonfatal or load_instructions_reports_read_failure or inject" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/memory/instructions.py rockcoder/conversation.py tests/test_memory.py
git commit -m "feat: surface instruction loading warnings"
```

### Task 2: Make static instruction handling explicitly read-only

**Files:**
- Modify: `rockcoder/memory/instructions.py`
- Modify: `rockcoder/tools/edit_file.py`
- Modify: `rockcoder/tools/write_file.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_is_instruction_file_matches_rockcoder_md(tmp_path: Path) -> None:
    path = tmp_path / "RockCoder.md"
    assert is_instruction_file(path) is True


def test_instruction_file_is_protected_from_write(tmp_path: Path) -> None:
    path = tmp_path / "RockCoder.md"
    path.write_text("rules", encoding="utf-8")
    with pytest.raises(PermissionError):
        guard_instruction_write(path, explicit_user_request=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory.py -k "instruction_file" -v`
Expected: FAIL because no write guard exists.

- [ ] **Step 3: Add a small reusable guard in the instruction module**

```python
INSTRUCTION_FILENAMES = {"RockCoder.md", "RockCoder.local.md", "MEWCODE.md", "MEWCODE.local.md", "AGENTS.md"}


def is_instruction_file(path: str | Path) -> bool:
    return Path(path).name in INSTRUCTION_FILENAMES


def guard_instruction_write(path: str | Path, *, explicit_user_request: bool) -> None:
    if is_instruction_file(path) and not explicit_user_request:
        raise PermissionError("Instruction files are user-owned and require explicit user request to modify")
```

- [ ] **Step 4: Call the guard from file-writing tools**

```python
guard_instruction_write(params.file_path, explicit_user_request=False)
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_memory.py -k "instruction_file" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/memory/instructions.py rockcoder/tools/edit_file.py rockcoder/tools/write_file.py tests/test_memory.py tests/test_tools.py
git commit -m "feat: protect project instruction files from implicit writes"
```

### Task 3: Implement minimal automatic memory extraction

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Modify: `rockcoder/agent.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_extract_persists_confirmed_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = MemoryManager(str(tmp_path))
    mgr.load()
    conv = ConversationManager(history=[
        Message(role="user", content="记住这件事：提交代码前先运行测试"),
        Message(role="assistant", content="已记录。"),
    ])

    await mgr.extract(client=None, conversation=conv, protocol="test")

    index = mgr.project_path.read_text(encoding="utf-8")
    assert "运行测试" in index or "test" in index
```

```python
@pytest.mark.asyncio
async def test_extract_ignores_one_off_task_text(tmp_path: Path) -> None:
    mgr = MemoryManager(str(tmp_path))
    mgr.load()
    conv = ConversationManager(history=[Message(role="user", content="帮我看下这个临时报错")])
    await mgr.extract(client=None, conversation=conv, protocol="test")
    assert mgr.load_all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory.py -k "extract_persists_confirmed_feedback or extract_ignores_one_off_task_text" -v`
Expected: FAIL because `extract()` is a stub.

- [ ] **Step 3: Add a deterministic extractor for explicit memory intents and repeated stable corrections**

```python
EXPLICIT_MEMORY_PATTERNS = (
    (re.compile(r"记住这件事[:：]?(.+)"), "feedback"),
    (re.compile(r"记住[:：]?(.+)"), "project"),
)

async def extract(self, client: Any, conversation: ConversationManager, protocol: str) -> None:
    new_messages = conversation.history[self._last_extraction_msg_count:]
    self._last_extraction_msg_count = len(conversation.history)
    for msg in new_messages:
        if msg.role != "user":
            continue
        fact = self._extract_explicit_memory_fact(msg.content)
        if fact and not self._looks_sensitive(fact):
            self.remember(fact, memory_type="feedback", topic="user-preferences", scope="project", source="explicit-user-memory")
```

- [ ] **Step 4: Keep the first version conservative**

```python
def _extract_explicit_memory_fact(self, text: str) -> str:
    if "不要记住" in text:
        return ""
    ...
    return cleaned_fact if len(cleaned_fact) >= 6 else ""
```

- [ ] **Step 5: Trigger extraction at the end of every completed turn, not every fifth loop**

```python
if self.memory_manager:
    asyncio.ensure_future(self._extract_memories(conversation))
```

- [ ] **Step 6: Run targeted tests**

Run: `pytest tests/test_memory.py -k "extract_" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add rockcoder/memory/auto_memory.py rockcoder/agent.py tests/test_memory.py
git commit -m "feat: add conservative automatic memory extraction"
```

### Task 4: Improve dynamic memory dedupe, update, and invalidation behavior

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_remember_updates_existing_topic_entry_without_duplicate_index(tmp_path: Path) -> None:
    mm = MemoryManager(str(tmp_path))
    mm.load()
    mm.remember("项目使用 pnpm", topic="workflows")
    mm.remember("项目使用 pnpm 包管理", topic="workflows")
    lines = mm.project_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
```

```python
def test_invalidate_marks_topic_metadata(tmp_path: Path) -> None:
    mm = MemoryManager(str(tmp_path))
    mm.load()
    mm.remember("旧部署流程已废弃", topic="workflows")
    mm.invalidate("workflows")
    assert "Status: invalid" in mm.show_topic("workflows")
```

- [ ] **Step 2: Run tests to verify they fail where behavior is too weak**

Run: `pytest tests/test_memory.py -k "duplicate_index or invalidate_marks_topic_metadata" -v`
Expected: at least one FAIL if index rebuilding or update semantics are incomplete.

- [ ] **Step 3: Normalize facts before duplicate checks**

```python
def _normalize_fact(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
```

- [ ] **Step 4: Store stable per-entry ids and compare normalized facts before appending**

```python
normalized = self._normalize_fact(cleaned)
if normalized in {self._normalize_fact(item.fact) for item in existing_entries}:
    return ("duplicate", topic_file)
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_memory.py -k "remember_ or invalidate_" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/memory/auto_memory.py tests/test_memory.py
git commit -m "fix: strengthen dynamic memory deduplication"
```

### Task 5: Surface graceful degradation for broken memory files

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Modify: `rockcoder/memory/recall.py`
- Modify: `rockcoder/commands/handlers/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_skips_unreadable_memory_file_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mm = MemoryManager(str(tmp_path))
    mm.load()
    topic = mm.project_mem_dir / "workflows.md"
    topic.write_text("broken", encoding="utf-8")

    original = Path.read_text
    def broken(self: Path, encoding: str = "utf-8") -> str:
        if self == topic:
            raise OSError("bad read")
        return original(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", broken)
    text = mm.get_display_text()
    assert "warning" in text.lower() or "读取失败" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory.py -k "unreadable_memory_file" -v`
Expected: FAIL because current code silently skips read failures.

- [ ] **Step 3: Accumulate warnings during load and expose them in display text and reminder rendering**

```python
self._warnings.append(f"Failed to read memory file: {entry}")
```

- [ ] **Step 4: Make recall rendering include a short skip note for unreadable selected files**

```python
except OSError:
    parts.append(f"## Memory: {basename}\nSkipped: file could not be read.\n\n---\n")
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_memory.py -k "warning or unreadable" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/memory/auto_memory.py rockcoder/memory/recall.py rockcoder/commands/handlers/memory.py tests/test_memory.py
git commit -m "feat: report memory loading degradation"
```

### Task 6: Improve task-relevant recall and topic scoping

**Files:**
- Modify: `rockcoder/memory/recall.py`
- Modify: `rockcoder/memory/auto_memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_find_relevant_memories_prefers_matching_topic(tmp_path: Path) -> None:
    ...
    assert any("debugging.md" in mem.path for mem in result)
```

- [ ] **Step 2: Run tests to verify they fail or expose weak selection**

Run: `pytest tests/test_memory.py -k "relevant_memories_prefers_matching_topic" -v`
Expected: FAIL because topic metadata is not used strongly enough.

- [ ] **Step 3: Include topic and source metadata in recall manifests**

```python
lines.append(f"- [{m.scope}-scope] [{m.type}] {path} ({ts}) topic={m.topic}: {m.description}")
```

- [ ] **Step 4: Bias topic selection before LLM fallback**

```python
keyword_hits = [m for m in candidates if any(token in (m.description + " " + m.filename).lower() for token in query_tokens)]
if keyword_hits:
    candidates = keyword_hits[:20]
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_memory.py -k "relevant_memories" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/memory/recall.py rockcoder/memory/auto_memory.py tests/test_memory.py
git commit -m "feat: improve task-scoped memory recall"
```

### Task 7: Expand `/memory` lifecycle operations to match product requirements

**Files:**
- Modify: `rockcoder/commands/handlers/memory.py`
- Modify: `tests/test_commands.py`
- Modify: `tests/test_memory.py`

- [ ] **Step 1: Write the failing command tests**

```python
@pytest.mark.asyncio
async def test_memory_update_command_reports_success() -> None:
    ui = MockUI()
    mm = MagicMock()
    mm.update.return_value = True
    ctx = _make_context("update workflows 项目现在使用 uv", ui)
    ctx.memory_manager = mm
    await handle_memory(ctx)
    assert any("已更新记忆" in m for m in ui.messages)
```

```python
@pytest.mark.asyncio
async def test_memory_clear_requires_confirmation_token() -> None:
    ui = MockUI()
    mm = MagicMock()
    ctx = _make_context("clear", ui)
    ctx.memory_manager = mm
    await handle_memory(ctx)
    assert any("确认" in m for m in ui.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -k "memory_" -v`
Expected: FAIL because update/confirmation flows are missing.

- [ ] **Step 3: Add `update`, `delete`, and confirmation-based `clear` flows**

```python
if sub == "update":
    ...
if sub == "delete":
    ...
if sub == "clear" and parts[1:] != ["--yes"]:
    ctx.ui.add_system_message("用法: /memory clear --yes")
    return
```

- [ ] **Step 4: Keep command output transparent about writes, updates, and deletions**

```python
ctx.ui.add_system_message(f"已更新记忆: {topic}")
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/test_commands.py -k "memory_" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder/commands/handlers/memory.py tests/test_commands.py tests/test_memory.py
git commit -m "feat: expand memory lifecycle commands"
```

### Task 8: Document the architecture and user-facing usage

**Files:**
- Create: `docs/memory-system.md`
- Modify: `README.md`
- Test: none

- [ ] **Step 1: Write the documentation file**

```markdown
# Dual-Layer Memory System

## Static instruction layer
- `RockCoder.md` is user-owned.
- It is loaded at session start.
- Missing files are non-fatal.
- Read failures are surfaced as warnings.

## Dynamic memory layer
- Stored under `.rockcoder/memory/`.
- `MEMORY.md` is the index.
- Topic files store durable facts, scope, source, and update metadata.
- Detailed memory files are loaded only when recall determines they are relevant.
```

- [ ] **Step 2: Add a short README section pointing to the architecture doc and `/memory` commands**

```markdown
## Memory System

RockCoder uses a dual-layer memory model:
- static project instructions from `RockCoder.md`
- dynamic long-term memory under `.rockcoder/memory/`

See `docs/memory-system.md` for architecture and maintenance details.
```

- [ ] **Step 3: Review docs for consistency with actual paths and command names**

Run: `rg "RockCoder.md|/memory|.rockcoder/memory" docs/memory-system.md README.md`
Expected: output shows only the final names and paths used by code.

- [ ] **Step 4: Commit**

```bash
git add docs/memory-system.md README.md
git commit -m "docs: describe the dual-layer memory system"
```

### Task 9: Run full verification and close gaps against acceptance criteria

**Files:**
- Modify: `tests/test_memory.py`
- Modify: `tests/test_commands.py`
- Modify: any touched implementation file if verification exposes issues

- [ ] **Step 1: Add acceptance-style regression tests for the remaining scenarios**

```python
def test_new_session_loads_memory_index(...):
    ...

def test_current_user_request_overrides_memory(...):
    ...

def test_repeated_preference_does_not_create_duplicates(...):
    ...
```

- [ ] **Step 2: Run the focused test suites**

Run: `pytest tests/test_memory.py tests/test_commands.py -v`
Expected: PASS

- [ ] **Step 3: Run a broader regression slice for startup and agent injection**

Run: `pytest tests/test_agent.py tests/test_context.py tests/test_hooks.py -v`
Expected: PASS

- [ ] **Step 4: Fix any regressions with the smallest necessary change**

```python
# Update only the failing boundary, do not refactor unrelated modules.
```

- [ ] **Step 5: Re-run verification**

Run: `pytest tests/test_memory.py tests/test_commands.py tests/test_agent.py tests/test_context.py tests/test_hooks.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add rockcoder tests docs README.md
git commit -m "test: verify dual-layer memory behavior end to end"
```

## Self-Review

- Spec coverage: startup loading, missing-file tolerance, read-only static instructions, automatic memory capture, dedupe, scoped recall, lifecycle commands, graceful degradation, tests, and docs are all mapped to tasks.
- Placeholder scan: no `TBD`, `TODO`, or cross-task shorthand remains; every task contains explicit files, commands, and code snippets.
- Type consistency: the plan uses `InstructionLoadResult`, `is_instruction_file`, `guard_instruction_write`, and existing `MemoryManager` / `ConversationManager` entry points consistently across tasks.
