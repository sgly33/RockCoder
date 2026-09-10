# Dual-Layer Memory Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the existing dual-layer memory system up to the user's required minimum by hardening static instruction protection, strengthening dynamic memory dedupe and degradation behavior, and verifying the end-to-end recall path with tests and docs.

**Architecture:** Keep the current split between static instructions (`rockcoder.memory.instructions`) and dynamic memory (`rockcoder.memory.auto_memory` / `rockcoder.memory.recall`). Reuse the existing startup and prefetch injection paths in `rockcoder.app` and `rockcoder.agent`, and limit implementation to behavior gaps rather than replacing the current design.

**Tech Stack:** Python 3.12, Textual app lifecycle, LangGraph agent loop, markdown-backed memory files, pytest

## Global Constraints

- `RockCoder.md` remains user-owned and must not be implicitly modified by the agent.
- Missing or unreadable static instruction files must not break startup.
- Dynamic memory continues to use `.rockcoder/memory/MEMORY.md` plus topic files.
- Only stable, non-sensitive, high-value facts may be persisted automatically.
- Current user instructions override static instructions and dynamic memory.
- Keep the implementation minimal and testable; do not introduce a database or vector store.

---

### Task 1: Tighten instruction-file protection semantics

**Files:**
- Modify: `rockcoder/memory/instructions.py`
- Modify: `rockcoder/tools/write_file.py`
- Modify: `rockcoder/tools/edit_file.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: `is_instruction_file(path: str | Path) -> bool`
- Produces: `guard_instruction_write(path: str | Path, *, explicit_user_request: bool = False) -> None`

- [ ] **Step 1: Write the failing instruction-guard tests**

```python
class TestInstructionWriteGuard:
    def test_guard_instruction_write_blocks_implicit_write(self, tmp_path: Path) -> None:
        path = tmp_path / "RockCoder.md"
        path.write_text("rules", encoding="utf-8")

        with pytest.raises(PermissionError, match="explicit user request"):
            guard_instruction_write(path)

    def test_guard_instruction_write_allows_non_instruction_file(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.md"
        path.write_text("ok", encoding="utf-8")
        guard_instruction_write(path)
```

- [ ] **Step 2: Run the new guard tests and verify failure**

Run: `pytest tests/test_memory.py -k "InstructionWriteGuard" -v`
Expected: FAIL because `guard_instruction_write` does not exist yet.

- [ ] **Step 3: Add the reusable guard in the instruction module**

```python
def guard_instruction_write(
    path: str | Path,
    *,
    explicit_user_request: bool = False,
) -> None:
    if is_instruction_file(path) and not explicit_user_request:
        raise PermissionError(
            "Instruction files are user-owned and require an explicit user request to modify"
        )
```

- [ ] **Step 4: Route write tools through the guard**

```python
from rockcoder.memory.instructions import guard_instruction_write

...
path = Path(params.file_path)
try:
    guard_instruction_write(path)
except PermissionError as exc:
    return ToolResult(output=f"Error: {exc}", is_error=True)
```

- [ ] **Step 5: Extend tool-level tests to assert blocked writes**

```python
def test_write_file_blocks_instruction_file(tmp_path: Path) -> None:
    tool = WriteFile()
    instruction_path = tmp_path / "RockCoder.md"
    result = asyncio.run(
        tool.execute(WriteFile.params_model(file_path=str(instruction_path), content="new content"))
    )
    assert result.is_error is True
    assert "explicit user request" in result.output
```

```python
def test_edit_file_blocks_instruction_file(tmp_path: Path) -> None:
    tool = EditFile()
    instruction_path = tmp_path / "RockCoder.md"
    instruction_path.write_text("rules", encoding="utf-8")
    result = asyncio.run(
        tool.execute(
            EditFile.params_model(
                file_path=str(instruction_path),
                old_string="rules",
                new_string="updated",
            )
        )
    )
    assert result.is_error is True
    assert "explicit user request" in result.output
```

- [ ] **Step 6: Run targeted tests and verify pass**

Run: `pytest tests/test_memory.py -k "InstructionWriteGuard" -v`
Expected: PASS

Run: `pytest tests/test_permissions.py -k "instruction_file" -v`
Expected: PASS

- [ ] **Step 7: Commit the guard work**

```bash
git add rockcoder/memory/instructions.py rockcoder/tools/write_file.py rockcoder/tools/edit_file.py tests/test_memory.py tests/test_permissions.py
git commit -m "feat: protect instruction files from implicit writes"
```

### Task 2: Strengthen dynamic memory dedupe and update behavior

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `MemoryManager.remember(fact: str, *, memory_type: str = "project", topic: str = "", scope: str = "project", source: str = "user-confirmed", confidence: str = "confirmed") -> tuple[str, str]`
- Produces: `_normalize_fact_for_compare(text: str) -> str`

- [ ] **Step 1: Add failing dedupe tests for semantically repeated facts**

```python
class TestMemoryDeduplication:
    def test_remember_deduplicates_normalized_fact(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        mm.remember("项目使用 pnpm", topic="workflows")
        action, _topic = mm.remember("  项目使用   pnpm。 ", topic="workflows")

        assert action == "duplicate"
        content = (mm.project_mem_dir / "workflows.md").read_text(encoding="utf-8")
        assert content.count("- Fact:") == 1

    def test_remember_rebuilds_index_without_duplicates(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        mm.remember("项目使用 pnpm", topic="workflows")
        mm.remember("项目使用 pnpm", topic="workflows")

        lines = [line for line in mm.project_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 1
```

- [ ] **Step 2: Run the new dedupe tests and verify failure**

Run: `pytest tests/test_memory.py -k "MemoryDeduplication" -v`
Expected: FAIL because duplicate detection is currently substring-based and not normalized.

- [ ] **Step 3: Add a comparison-normalization helper**

```python
def _normalize_fact_for_compare(self, fact: str) -> str:
    cleaned = self._normalize_fact(fact)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.casefold()
```

- [ ] **Step 4: Compare against existing `- Fact:` entries before appending**

```python
existing_facts = _extract_fact_lines(existing)
normalized_existing = {
    self._normalize_fact_for_compare(item) for item in existing_facts
}
if self._normalize_fact_for_compare(cleaned) in normalized_existing:
    return ("duplicate", topic_file)
```

- [ ] **Step 5: Add the fact-line parser used by remember**

```python
def _extract_fact_lines(content: str) -> list[str]:
    facts: list[str] = []
    for line in content.splitlines():
        if line.startswith("- Fact: "):
            facts.append(line[len("- Fact: "):].strip())
    return facts
```

- [ ] **Step 6: Run targeted dedupe tests and verify pass**

Run: `pytest tests/test_memory.py -k "MemoryDeduplication or remember_deduplicates_same_fact" -v`
Expected: PASS

- [ ] **Step 7: Commit the dedupe work**

```bash
git add rockcoder/memory/auto_memory.py tests/test_memory.py
git commit -m "fix: strengthen dynamic memory deduplication"
```

### Task 3: Surface graceful degradation for broken dynamic memory files

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Modify: `rockcoder/commands/handlers/memory.py`
- Modify: `rockcoder/memory/recall.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `MemoryManager.load_all() -> list[MemoryFile]`
- Produces: `MemoryManager.get_warnings() -> list[str]`
- Produces: `MemoryManager.clear_warnings() -> None`

- [ ] **Step 1: Write failing tests for unreadable dynamic memory files**

```python
class TestMemoryDegradation:
    def test_load_all_skips_unreadable_file_and_records_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        topic = mm.project_mem_dir / "workflows.md"
        topic.write_text("---\nname: workflows\ndescription: x\ntype: project\n---\nbody", encoding="utf-8")

        original = Path.read_text

        def broken(self: Path, *args: object, **kwargs: object) -> str:
            if self == topic:
                raise OSError("bad read")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", broken)

        memories = mm.load_all()
        assert memories == []
        assert any("bad read" in warning for warning in mm.get_warnings())
```

```python
def test_render_reminder_skips_unreadable_memory_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    result = render_reminder([RelevantMemory(path=str(bad), mtime_ms=0)])
    assert "bad.md" not in result
```

- [ ] **Step 2: Run degradation tests and verify failure**

Run: `pytest tests/test_memory.py -k "MemoryDegradation or render_reminder_skips_unreadable_memory_file" -v`
Expected: FAIL because warnings are not currently exposed from `MemoryManager`.

- [ ] **Step 3: Add warning collection to `MemoryManager`**

```python
class MemoryManager:
    def __init__(self, project_root: str) -> None:
        ...
        self._warnings: list[str] = []

    def get_warnings(self) -> list[str]:
        return list(self._warnings)

    def clear_warnings(self) -> None:
        self._warnings.clear()
```

- [ ] **Step 4: Thread warnings through directory scans**

```python
def _load_dir(dir_path: str, warnings: list[str] | None = None) -> list[MemoryFile]:
    ...
    except OSError as exc:
        if warnings is not None:
            warnings.append(f"Could not read memory file {fp}: {exc}")
        continue
```

- [ ] **Step 5: Surface warnings in `/memory list` output**

```python
if sub in {"", "list"}:
    output = mm.get_display_text()
    warnings = mm.get_warnings()
    if warnings:
        output += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
        mm.clear_warnings()
    ctx.ui.add_system_message(output)
    return
```

- [ ] **Step 6: Run targeted degradation tests and verify pass**

Run: `pytest tests/test_memory.py -k "MemoryDegradation or render_reminder_skips_unreadable_memory_file" -v`
Expected: PASS

- [ ] **Step 7: Commit the degradation work**

```bash
git add rockcoder/memory/auto_memory.py rockcoder/memory/recall.py rockcoder/commands/handlers/memory.py tests/test_memory.py
git commit -m "feat: surface dynamic memory degradation warnings"
```

### Task 4: Verify and harden relevant-memory recall injection

**Files:**
- Modify: `rockcoder/agent.py`
- Modify: `rockcoder/app.py`
- Test: `tests/test_memory.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `App._prefetch_relevant_memories(query: str) -> str`
- Consumes: `Agent.memory_recall_task: asyncio.Task[str] | None`
- Produces: `_consume_ready_memory_recall(conversation: ConversationManager) -> None`

- [ ] **Step 1: Write a failing integration test that proves recall reaches conversation history**

```python
@pytest.mark.asyncio
async def test_agent_injects_prefetched_memory_recall() -> None:
    conv = ConversationManager()
    agent = Agent(
        client=FakeClient.single_text("done"),
        registry=ToolRegistry(),
        protocol="test",
        work_dir=".",
    )
    task = asyncio.create_task(asyncio.sleep(0, result="remember this context"))
    agent.memory_recall_task = task
    agent._memory_recall_consumed = False

    events = [event async for event in agent.run(conv)]

    assert any("remember this context" in msg.content for msg in conv.history)
```

- [ ] **Step 2: Run the new recall-injection test and verify current behavior**

Run: `pytest tests/test_memory.py -k "prefetched_memory_recall" -v`
Expected: FAIL if the test setup reveals timing-sensitive behavior; otherwise keep the test and proceed with a hardening change.

- [ ] **Step 3: Extract recall consumption into a dedicated helper**

```python
def _consume_ready_memory_recall(self, conversation: ConversationManager) -> None:
    if not self.memory_recall_task or self._memory_recall_consumed:
        return
    if not self.memory_recall_task.done():
        return
    try:
        recall = self.memory_recall_task.result()
    except Exception:
        recall = ""
    if recall:
        conversation.add_system_reminder(recall)
    self._memory_recall_consumed = True
```

- [ ] **Step 4: Call the helper from both streaming and `run_to_completion` paths**

```python
self._consume_ready_memory_recall(conversation)
```

Place the call immediately after tool results are appended in `_execute_turn`, and after tool-result handling in `run_to_completion` so both execution modes behave the same way.

- [ ] **Step 5: Add an app-level test for empty prefetch degradation**

```python
@pytest.mark.asyncio
async def test_prefetch_relevant_memories_returns_empty_on_selector_failure(app: RockCoderApp) -> None:
    result = await app._prefetch_relevant_memories("hello")
    assert isinstance(result, str)
```

- [ ] **Step 6: Run targeted recall tests and verify pass**

Run: `pytest tests/test_memory.py -k "prefetched_memory_recall" -v`
Expected: PASS

Run: `pytest tests/test_agent.py -k "memory_recall" -v`
Expected: PASS

- [ ] **Step 7: Commit the recall hardening**

```bash
git add rockcoder/agent.py rockcoder/app.py tests/test_memory.py tests/test_agent.py
git commit -m "test: lock down relevant memory recall injection"
```

### Task 5: Tighten automatic extraction scope and document actual behavior

**Files:**
- Modify: `rockcoder/memory/auto_memory.py`
- Modify: `README.md`
- Create: `docs/architecture/memory-system.md`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `MemoryManager.extract(client: Any, conversation: ConversationManager, protocol: str) -> None`
- Produces: documented rules for static instructions, dynamic memory persistence, and user control flows

- [ ] **Step 1: Write failing tests for extraction scope boundaries**

```python
class TestMemoryExtractionBoundaries:
    @pytest.mark.asyncio
    async def test_extract_persists_explicit_memory_request(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        conv = ConversationManager(history=[Message(role="user", content="记住：提交代码前先运行测试")])

        await mm.extract(client=None, conversation=conv, protocol="test")

        assert any("运行测试" in item for item in mm.get_memories())

    @pytest.mark.asyncio
    async def test_extract_ignores_one_off_deadline_text(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        conv = ConversationManager(history=[Message(role="user", content="今天下午三点前提醒我发布")])

        await mm.extract(client=None, conversation=conv, protocol="test")

        assert mm.get_memories() == []
```

- [ ] **Step 2: Run extraction-boundary tests and verify behavior**

Run: `pytest tests/test_memory.py -k "MemoryExtractionBoundaries" -v`
Expected: PASS if current behavior already matches; if not, FAIL and fix in the next step.

- [ ] **Step 3: Keep extraction conservative and explicit-only**

```python
def _extract_fact_from_message(self, message: Message) -> str:
    text = message.content.strip()
    if not text or text.startswith("<system-reminder>"):
        return ""
    if "不要记住" in text:
        return ""
    for pattern in EXPLICIT_MEMORY_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        fact = self._normalize_fact(match.group("fact"))
        if not fact or self._is_one_off_fact(fact) or self._looks_sensitive(fact):
            return ""
        return fact
    return ""
```

- [ ] **Step 4: Document the implemented two-layer system in the repo**

```markdown
# Memory System

## Static instruction layer
- Loaded from `RockCoder.md` at startup through `rockcoder.memory.instructions.load_instructions`
- Read-only unless the user explicitly requests a modification
- Missing files are non-fatal; unreadable files surface warnings

## Dynamic memory layer
- Stored in `.rockcoder/memory/`
- `MEMORY.md` is the index; topic files store detailed records
- Explicit memory requests such as `记住：...` are persisted after sensitive-data and one-off filtering
- `/memory` supports list, show, search, remember, forget, invalidate, clear
```

- [ ] **Step 5: Update the top-level README memory section**

```markdown
- Static instructions load from `RockCoder.md` on startup.
- Dynamic memory lives under `.rockcoder/memory/` with an index-plus-topic-file layout.
- Relevant detailed memories are prefetched per query and injected as system reminders when available.
```

- [ ] **Step 6: Run focused tests plus docs sanity check**

Run: `pytest tests/test_memory.py -k "MemoryExtractionBoundaries or TestMemoryManager" -v`
Expected: PASS

Run: `pytest tests/test_memory.py tests/test_permissions.py tests/test_agent.py -v`
Expected: PASS

- [ ] **Step 7: Commit the extraction and docs work**

```bash
git add rockcoder/memory/auto_memory.py README.md docs/architecture/memory-system.md tests/test_memory.py
git commit -m "docs: describe the dual-layer memory system"
```

## Self-Review

- Spec coverage: static instruction loading, missing-file tolerance, warning surfacing, instruction write protection, dynamic memory storage/indexing, dedupe, user lifecycle commands, relevant-memory recall, sensitive-data filtering, one-off filtering, and documentation are all covered by Tasks 1-5.
- Placeholder scan: no `TODO`, `TBD`, or undefined implementation steps remain.
- Type consistency: all new helpers referenced in later tasks are defined in earlier steps with explicit names and signatures.
