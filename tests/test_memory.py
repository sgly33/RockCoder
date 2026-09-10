 

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rockcoder.agent import Agent
from rockcoder.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from rockcoder.memory.auto_memory import MemoryManager
from rockcoder.memory.instructions import (
    MAX_INCLUDE_DEPTH,
    InstructionLoadResult,
    guard_instruction_write,
    is_instruction_file,
    load_instructions,
    process_includes,
)
from rockcoder.memory.session import (
    RecordType,
    ResumeResult,
    Session,
    SessionManager,
    SessionMeta,
    SessionRecord,

    make_compact_boundary,
    parse_compact_boundary,
    records_to_messages,
    validate_message_chain,
)
from rockcoder.tools import ToolRegistry
from rockcoder.tools.base import StreamEnd

# =========================================================================
# A. 指令文件（RockCoder.md / MEWCODE.md）
# =========================================================================

class TestProcessIncludes:
    def test_no_includes(self, tmp_path: Path) -> None:
        content = "line1\nline2\nline3"
        result = process_includes(content, tmp_path, tmp_path)
        assert result == content

    def test_basic_include(self, tmp_path: Path) -> None:
        child = tmp_path / "child.md"
        child.write_text("included content", encoding="utf-8")
        content = "before\n@include ./child.md\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "included content" in result
        assert "before" in result
        assert "after" in result

    def test_recursive_include(self, tmp_path: Path) -> None:
        grandchild = tmp_path / "grandchild.md"
        grandchild.write_text("deep content", encoding="utf-8")
        child = tmp_path / "child.md"
        child.write_text("@include ./grandchild.md", encoding="utf-8")
        content = "@include ./child.md"
        result = process_includes(content, tmp_path, tmp_path)
        assert "deep content" in result

    def test_depth_limit(self, tmp_path: Path) -> None:
        content = "should stop"
        result = process_includes(content, tmp_path, tmp_path, depth=MAX_INCLUDE_DEPTH)
        assert result == content

    def test_path_outside_project_not_found(self, tmp_path: Path) -> None:
        """项目外路径对齐 Go 版：不做路径限制，不存在的文件显示 file not found。"""
        content = "@include ../../etc/passwd"
        result = process_includes(content, tmp_path, tmp_path)
        assert "skipped: file not found" in result

    def test_file_not_found(self, tmp_path: Path) -> None:
        content = "@include ./nonexistent.md"
        result = process_includes(content, tmp_path, tmp_path)
        assert result == ""

    def test_cycle_detection(self, tmp_path: Path) -> None:
        """循环检测：A→B→A 不会无限递归（对齐 Go 版 seen 集合）。"""
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("start\n@include ./b.md\nend-a", encoding="utf-8")
        b.write_text("middle\n@include ./a.md\nend-b", encoding="utf-8")
        result = process_includes(
            "@include ./a.md", tmp_path, tmp_path
        )
        # a.md 被展开，b.md 也被展开，但 b 中再次 @include a 时被跳过
        assert "start" in result
        assert "middle" in result
        assert "end-b" in result
        # a.md 不会被第二次展开（循环检测生效）
        assert result.count("start") == 1

    def test_code_block_skip(self, tmp_path: Path) -> None:
        """代码块内的 @include 不展开（对齐 Go 版 inCode 检测）。"""
        child = tmp_path / "child.md"
        child.write_text("should not appear", encoding="utf-8")
        content = "before\n```\n@include ./child.md\n```\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "should not appear" not in result
        assert "@include ./child.md" in result
        assert "before" in result
        assert "after" in result

    def test_new_at_syntax(self, tmp_path: Path) -> None:
        """新格式 @./path 语法（对齐 Go 版 parseInclude）。"""
        child = tmp_path / "child.md"
        child.write_text("new syntax content", encoding="utf-8")
        content = "before\n@./child.md\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "new syntax content" in result

class TestInstructionFileDetection:
    def test_identifies_instruction_filenames(self, tmp_path: Path) -> None:
        assert is_instruction_file(tmp_path / "RockCoder.md") is True
        assert is_instruction_file(tmp_path / "RockCoder.local.md") is True

    def test_ignores_non_instruction_files(self, tmp_path: Path) -> None:
        assert is_instruction_file(tmp_path / "notes.md") is False
        assert is_instruction_file(tmp_path / ".rockcoder" / "notes.md") is False
        assert is_instruction_file(tmp_path / "INSTRUCTIONS.md") is False
        assert is_instruction_file(tmp_path / "MEWCODE.md") is False
        assert is_instruction_file(tmp_path / "MEWCODE.local.md") is False
        assert is_instruction_file(tmp_path / "AGENTS.md") is False
        assert is_instruction_file(tmp_path / ".rockcoder" / "INSTRUCTIONS.md") is False
        assert is_instruction_file(tmp_path / ".mewcode" / "INSTRUCTIONS.md") is False


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


class TestLoadInstructions:
    def test_single_layer_prefers_rockcoder_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rockcoder_md = tmp_path / "RockCoder.md"
        rockcoder_md.write_text("project instructions", encoding="utf-8")
        result = load_instructions(str(tmp_path))
        assert isinstance(result, InstructionLoadResult)
        assert "project instructions" in result.content
        assert result.warnings == []

    def test_ignores_legacy_instruction_files(self, tmp_path: Path) -> None:
        rockcoder_md = tmp_path / "RockCoder.md"
        rockcoder_md.write_text("rockcoder instructions", encoding="utf-8")
        (tmp_path / "MEWCODE.md").write_text("legacy instructions", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("agents instructions", encoding="utf-8")
        dotdir = tmp_path / ".rockcoder"
        dotdir.mkdir()
        (dotdir / "INSTRUCTIONS.md").write_text("legacy dot instructions", encoding="utf-8")

        result = load_instructions(str(tmp_path))

        assert "rockcoder instructions" in result.content
        assert "Contents of MEWCODE.md" not in result.content
        assert "Contents of AGENTS.md" not in result.content
        assert "Contents of .rockcoder/INSTRUCTIONS.md" not in result.content

    def test_multi_layer_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root_md = tmp_path / "RockCoder.md"
        root_md.write_text("root level", encoding="utf-8")
        subdir = tmp_path / "pkg"
        subdir.mkdir()
        nested_md = subdir / "RockCoder.md"
        nested_md.write_text("nested level", encoding="utf-8")

        monkeypatch.setattr("rockcoder.memory.instructions._find_git_root", lambda _start: tmp_path)
        result = load_instructions(str(subdir))

        assert result.content.index("root level") < result.content.index("nested level")
        assert "---" in result.content

    def test_missing_rockcoder_md_is_non_fatal(self, tmp_path: Path) -> None:
        result = load_instructions(str(tmp_path))
        assert result.content == ""
        assert result.warnings == []

    def test_read_failure_is_reported_as_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rockcoder_md = tmp_path / "RockCoder.md"
        rockcoder_md.write_text("project instructions", encoding="utf-8")
        original_read_text = Path.read_text

        def fail_for_rockcoder(self: Path, *args: object, **kwargs: object) -> str:
            if self == rockcoder_md:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_for_rockcoder)

        result = load_instructions(str(tmp_path))
        assert result.content == ""
        assert len(result.warnings) == 1
        assert str(rockcoder_md) in result.warnings[0]
        assert "permission denied" in result.warnings[0]

    def test_nested_include_read_failure_is_reported_as_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "RockCoder.md"
        child = tmp_path / "child.md"
        root.write_text("@include ./child.md", encoding="utf-8")
        child.write_text("nested content", encoding="utf-8")
        original_read_text = Path.read_text

        def fail_for_child(self: Path, *args: object, **kwargs: object) -> str:
            if self == child:
                raise OSError("nested permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_for_child)

        result = load_instructions(str(tmp_path))
        assert "nested content" not in result.content
        assert any(str(child) in warning for warning in result.warnings)
        assert any("nested permission denied" in warning for warning in result.warnings)

# =========================================================================
# B. 会话记录 SessionRecord
# =========================================================================

class TestMemoryManager:
    def test_load_creates_rockcoder_memory_dirs(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        prompt = mm.load()
        assert ".rockcoder" in prompt
        assert mm.project_mem_dir == tmp_path / ".rockcoder" / "memory"
        assert mm.project_path.exists()

    def test_remember_writes_topic_file_and_index(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        action, topic = mm.remember("提交代码前先运行测试", memory_type="feedback", topic="user-preferences")
        assert action == "created"
        topic_file = mm.project_mem_dir / topic
        assert topic_file.exists()
        assert "提交代码前先运行测试" in topic_file.read_text(encoding="utf-8")
        assert topic in mm.project_path.read_text(encoding="utf-8")

    def test_remember_deduplicates_same_fact(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        mm.remember("项目使用 pnpm", topic="workflows")
        action, _topic = mm.remember("项目使用 pnpm", topic="workflows")
        assert action == "duplicate"

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

    def test_forget_removes_topic_and_updates_index(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        _action, topic = mm.remember("项目使用 pnpm", topic="workflows")
        assert mm.forget("workflows") is True
        assert not (mm.project_mem_dir / topic).exists()
        assert topic not in mm.project_path.read_text(encoding="utf-8")

    def test_sensitive_memory_rejected(self, tmp_path: Path) -> None:
        mm = MemoryManager(str(tmp_path))
        mm.load()
        with pytest.raises(ValueError, match="sensitive"):
            mm.remember("OPENAI_API_KEY=sk-secret-value")

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
        assert all(Path(mem.path) != topic for mem in memories)
        assert any("bad read" in warning for warning in mm.get_warnings())


class TestSessionRecord:
    def test_user_message_roundtrip(self) -> None:
        msg = Message(role="user", content="hello world")
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert records[0].type == RecordType.USER
        assert records[0].content == "hello world"

        line = records[0].to_jsonl()
        restored = SessionRecord.from_jsonl(line)
        assert restored is not None
        assert restored.type == RecordType.USER
        assert restored.content == "hello world"

    def test_assistant_with_tool_uses(self) -> None:
        msg = Message(
            role="assistant",
            content="Let me check",
            tool_uses=[
                ToolUseBlock(tool_use_id="t1", tool_name="ReadFile", arguments={"path": "/a"})
            ],
        )
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert records[0].type == RecordType.ASSISTANT
        assert isinstance(records[0].content, list)
        assert records[0].content[0]["type"] == "text"
        assert records[0].content[1]["type"] == "tool_use"

    def test_tool_results_multiple_records(self) -> None:
        msg = Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock(tool_use_id="t1", content="result1"),
                ToolResultBlock(tool_use_id="t2", content="result2", is_error=True),
            ],
        )
        records = SessionRecord.from_message(msg)
        assert len(records) == 2
        assert records[0].type == RecordType.TOOL_RESULT
        assert records[0].tool_use_id == "t1"
        assert records[1].is_error is True

    def test_malformed_jsonl_returns_none(self) -> None:
        assert SessionRecord.from_jsonl("{bad json") is None
        assert SessionRecord.from_jsonl('{"type":"unknown","content":"x","timestamp":"2025-01-01T00:00:00"}') is None

    def test_plain_assistant_message(self) -> None:
        msg = Message(role="assistant", content="done")
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert records[0].content == "done"

# =========================================================================
# C. 会话 Session 与会话管理器 SessionManager
# =========================================================================

class TestSession:
    def test_append_writes_jsonl_and_updates_meta(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".mewcode" / "sessions"
        sessions_dir.mkdir(parents=True)
        meta = SessionMeta(id="test_session")
        meta.save(sessions_dir / "test_session.meta")
        jsonl_path = sessions_dir / "test_session.jsonl"

        with open(jsonl_path, "a", encoding="utf-8") as f:
            session = Session("test_session", f, meta, sessions_dir)
            session.append(Message(role="user", content="hello"))
            session.append(Message(role="assistant", content="hi"))

        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert meta.message_count == 2
        assert meta.title == "hello"

    def test_title_set_from_first_user_message(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".mewcode" / "sessions"
        sessions_dir.mkdir(parents=True)
        meta = SessionMeta(id="test_session")
        jsonl_path = sessions_dir / "test_session.jsonl"

        with open(jsonl_path, "a", encoding="utf-8") as f:
            session = Session("test_session", f, meta, sessions_dir)
            session.append(Message(role="assistant", content="welcome"))
            assert meta.title == ""
            session.append(Message(role="user", content="my first question"))
            assert meta.title == "my first question"

class TestSessionManager:

    def test_creates_new_project_dir_when_missing(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))

        assert mgr._sessions_dir == tmp_path / ".rockcoder" / "sessions"
        assert mgr._sessions_dir.exists()

    def test_create_and_list(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s1 = mgr.create()
        s1.append(Message(role="user", content="test"))
        s1.close()

        s2 = mgr.create()
        s2.append(Message(role="user", content="test2"))
        s2.close()

        metas = mgr.list()
        assert len(metas) == 2
        assert metas[0].last_active >= metas[1].last_active

    def test_empty_created_session_is_listed_until_app_layer_filters_it(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        empty_session = mgr.create()
        used_session = mgr.create()
        used_session.append(Message(role="user", content="real message"))
        empty_session.close()
        used_session.close()

        metas = mgr.list()
        assert len(metas) == 2
        assert {meta.message_count for meta in metas} == {0, 1}

    def test_delete(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.close()

        assert mgr.delete(sid) is True
        assert mgr.delete(sid) is False
        assert len(mgr.list()) == 0

    def test_cleanup_removes_old_sessions(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        s.meta.last_active = datetime.now(timezone.utc) - timedelta(days=31)
        s.meta.save(mgr._sessions_dir / f"{s.session_id}.meta")
        s.close()

        removed = mgr.cleanup(max_age_days=30)
        assert removed == 1
        assert len(mgr.list()) == 0

    def test_create_generates_valid_id(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        assert s.session_id.startswith("session_")
        assert len(s.session_id.split("_")) == 4
        s.close()

# =========================================================================
# D. 消息链校验与会话恢复
# =========================================================================

class TestValidateMessageChain:
    def test_complete_chain(self) -> None:
        now = datetime.now(timezone.utc)
        records = [
            SessionRecord(type=RecordType.USER, content="hi", timestamp=now),
            SessionRecord(
                type=RecordType.ASSISTANT,
                content=[
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "t1", "name": "ReadFile", "input": {}},
                ],
                timestamp=now,
            ),
            SessionRecord(
                type=RecordType.TOOL_RESULT,
                content="file content",
                timestamp=now,
                tool_use_id="t1",
            ),
            SessionRecord(type=RecordType.ASSISTANT, content="done", timestamp=now),
        ]
        assert validate_message_chain(records) == 4

    def test_truncate_at_missing_tool_result(self) -> None:
        now = datetime.now(timezone.utc)
        records = [
            SessionRecord(type=RecordType.USER, content="hi", timestamp=now),
            SessionRecord(type=RecordType.ASSISTANT, content="ok", timestamp=now),
            SessionRecord(
                type=RecordType.ASSISTANT,
                content=[
                    {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
                ],
                timestamp=now,
            ),
        ]
        assert validate_message_chain(records) == 2

    def test_empty_records(self) -> None:
        assert validate_message_chain([]) == 0

class TestRecordsToMessages:
    def test_basic_roundtrip(self) -> None:
        now = datetime.now(timezone.utc)
        records = [
            SessionRecord(type=RecordType.USER, content="hello", timestamp=now),
            SessionRecord(type=RecordType.ASSISTANT, content="world", timestamp=now),
        ]
        messages = records_to_messages(records)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_tool_result_grouping(self) -> None:
        now = datetime.now(timezone.utc)
        records = [
            SessionRecord(type=RecordType.USER, content="go", timestamp=now),
            SessionRecord(
                type=RecordType.ASSISTANT,
                content=[
                    {"type": "tool_use", "id": "t1", "name": "ReadFile", "input": {}},
                    {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
                ],
                timestamp=now,
            ),
            SessionRecord(
                type=RecordType.TOOL_RESULT, content="r1", timestamp=now, tool_use_id="t1"
            ),
            SessionRecord(
                type=RecordType.TOOL_RESULT, content="r2", timestamp=now, tool_use_id="t2"
            ),
            SessionRecord(type=RecordType.ASSISTANT, content="done", timestamp=now),
        ]
        messages = records_to_messages(records)
        assert len(messages) == 4
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"
        assert len(messages[1].tool_uses) == 2
        assert messages[2].role == "user"
        assert len(messages[2].tool_results) == 2
        assert messages[3].role == "assistant"

    def test_system_prompt_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        records = [
            SessionRecord(type=RecordType.SYSTEM_PROMPT, content="system", timestamp=now),
            SessionRecord(type=RecordType.USER, content="hi", timestamp=now),
        ]
        messages = records_to_messages(records)
        assert len(messages) == 1
        assert messages[0].content == "hi"

class TestSessionResume:
    def test_resume_restores_messages(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.append(Message(role="user", content="hello"))
        s.append(Message(role="assistant", content="hi"))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        assert len(result.messages) == 2
        assert result.messages[0].content == "hello"
        assert result.messages[1].content == "hi"
        result.session.close()

    def test_resume_nonexistent_returns_none(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        assert mgr.resume("nonexistent") is None

    def test_resume_truncates_incomplete_chain(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.append(Message(role="user", content="start"))
        s.append(Message(role="assistant", content="ok"))
        s.append(
            Message(
                role="assistant",
                content="checking",
                tool_uses=[
                    ToolUseBlock(tool_use_id="t1", tool_name="Bash", arguments={"command": "ls"})
                ],
            )
        )
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        assert len(result.messages) == 2
        result.session.close()

# =========================================================================
# D2. 压缩边界的持久化 + 恢复时重新加载压缩后的状态
# =========================================================================

class TestCompactBoundaryRoundTrip:
    def test_make_and_parse_boundary_text_only(self) -> None:
        keep = [
            Message(role="user", content="recent question"),
            Message(role="assistant", content="recent answer"),
        ]
        rec = make_compact_boundary("the summary", keep)
        assert rec.type == RecordType.COMPACT_BOUNDARY
        assert rec.content["summary"] == "the summary"

        # JSONL 往返序列化（content 是一个 dict，必须能完整地序列化/反序列化）
        line = rec.to_jsonl()
        restored = SessionRecord.from_jsonl(line)
        assert restored is not None
        assert restored.type == RecordType.COMPACT_BOUNDARY

        summary, keep_msgs = parse_compact_boundary(restored)
        assert summary == "the summary"
        assert len(keep_msgs) == 2
        assert keep_msgs[0].role == "user"
        assert keep_msgs[0].content == "recent question"
        assert keep_msgs[1].role == "assistant"
        assert keep_msgs[1].content == "recent answer"

    def test_boundary_preserves_tool_pairs_in_keep(self) -> None:
        # 保留的尾部消息中包含 tool_use ↔ tool_result 配对，必须完整保留
        keep = [
            Message(
                role="assistant",
                content="running",
                tool_uses=[
                    ToolUseBlock(tool_use_id="t9", tool_name="Bash", arguments={"command": "ls"})
                ],
            ),
            Message(
                role="user",
                content="",
                tool_results=[ToolResultBlock(tool_use_id="t9", content="file.txt")],
            ),
            Message(role="assistant", content="done"),
        ]
        rec = make_compact_boundary("sum", keep)
        restored = SessionRecord.from_jsonl(rec.to_jsonl())
        _, keep_msgs = parse_compact_boundary(restored)
        assert len(keep_msgs) == 3
        assert keep_msgs[0].tool_uses[0].tool_use_id == "t9"
        assert keep_msgs[1].tool_results[0].tool_use_id == "t9"
        assert keep_msgs[1].tool_results[0].content == "file.txt"
        assert keep_msgs[2].content == "done"

    def test_parse_malformed_boundary_degrades(self) -> None:
        bad = SessionRecord(
            type=RecordType.COMPACT_BOUNDARY, content="not a dict",
            timestamp=datetime.now(timezone.utc),
        )
        summary, keep_msgs = parse_compact_boundary(bad)
        assert summary == ""
        assert keep_msgs == []

    def test_resume_rebuilds_compacted_state(self, tmp_path: Path) -> None:
        """核心往返流程：原始前缀 + 边界（摘要 + 保留消息）+ 边界之后的消息。

        恢复时必须重建出「已压缩」的状态：摘要存在、保留的消息原样保留、
        边界之前的原始前缀不被重放，且边界之后追加的消息正常存在。
        """
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id

        # 已被摘要掉的原始前缀——不应被重放。
        s.append(Message(role="user", content="OLD raw question one"))
        s.append(Message(role="assistant", content="OLD raw answer one"))
        s.append(Message(role="user", content="OLD raw question two"))
        s.append(Message(role="assistant", content="OLD raw answer two"))

        # 边界内联了摘要 + 原样保留的尾部消息。
        keep = [
            Message(role="user", content="KEPT recent question"),
            Message(role="assistant", content="KEPT recent answer"),
        ]
        s.append_record(make_compact_boundary("SUMMARY OF OLD STUFF", keep))

        # 边界之后的续写。
        s.append(Message(role="user", content="NEW followup"))
        s.append(Message(role="assistant", content="NEW reply"))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        contents = [m.content for m in result.messages]

        # 摘要存在（以一条 user 消息的形式呈现）
        assert any("SUMMARY OF OLD STUFF" in c for c in contents)
        # 保留的尾部消息原样存在
        assert "KEPT recent question" in contents
        assert "KEPT recent answer" in contents
        # 边界之后的续写存在
        assert "NEW followup" in contents
        assert "NEW reply" in contents
        # 边界之前的原始前缀未被重放
        assert all("OLD raw" not in c for c in contents)

        # 结构顺序：先摘要，再保留消息，最后是边界之后的消息。
        summary_idx = next(i for i, c in enumerate(contents) if "SUMMARY OF OLD STUFF" in c)
        keep_idx = contents.index("KEPT recent question")
        post_idx = contents.index("NEW followup")
        assert summary_idx < keep_idx < post_idx
        result.session.close()

    def test_resume_uses_last_boundary_when_multiple(self, tmp_path: Path) -> None:
        """链式压缩：只有最后一个边界才决定恢复后的状态。"""
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id

        s.append(Message(role="user", content="gen0 raw"))
        s.append_record(make_compact_boundary("FIRST summary", [
            Message(role="user", content="gen1 kept"),
        ]))
        s.append(Message(role="assistant", content="between boundaries"))
        s.append_record(make_compact_boundary("SECOND summary", [
            Message(role="user", content="gen2 kept"),
        ]))
        s.append(Message(role="user", content="after second"))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        contents = [m.content for m in result.messages]
        assert any("SECOND summary" in c for c in contents)
        assert "gen2 kept" in contents
        assert "after second" in contents
        # 第一代压缩的所有内容都已消失。
        assert all("FIRST summary" not in c for c in contents)
        assert "gen1 kept" not in contents
        assert "between boundaries" not in contents
        assert all("gen0 raw" not in c for c in contents)
        result.session.close()

    def test_resume_legacy_compression_marker_is_boundary(self, tmp_path: Path) -> None:
        """Old ``compression`` markers must not replay compacted history."""
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.append(Message(role="user", content="OLD raw question"))
        s.append(Message(role="assistant", content="OLD raw answer"))
        s.append_record(SessionRecord(
            type=RecordType.COMPRESSION,
            content="LEGACY SUMMARY",
            timestamp=datetime.now(timezone.utc),
        ))
        s.append(Message(role="user", content="NEW followup"))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        contents = [m.content for m in result.messages]
        assert any("LEGACY SUMMARY" in content for content in contents)
        assert "NEW followup" in contents
        assert all("OLD raw" not in content for content in contents)
        result.session.close()

    def test_resume_no_boundary_full_replay(self, tmp_path: Path) -> None:
        """向后兼容：没有边界的会话仍然完整重放。"""
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.append(Message(role="user", content="q1"))
        s.append(Message(role="assistant", content="a1"))
        s.append(Message(role="user", content="q2"))
        s.append(Message(role="assistant", content="a2"))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        contents = [m.content for m in result.messages]
        assert contents == ["q1", "a1", "q2", "a2"]
        result.session.close()

    def test_resume_preserves_task_state_summary_sections(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id

        summary = """Current Goal: finish compact state support
Completed Work: added summary prompt fields
Key Decisions: keep summary+keep boundary schema
Active Files: rockcoder/context/manager.py, rockcoder/app.py
Test / Verification Status: tests not run yet
Unresolved Issues: wire compact state into recall selector
Next Steps: add recall query enrichment"""
        s.append_record(make_compact_boundary(summary, [
            Message(role="user", content="kept tail"),
        ]))
        s.close()

        result = mgr.resume(sid)
        assert result is not None
        contents = [m.content for m in result.messages]
        compact_message = next(c for c in contents if "Current Goal:" in c)
        assert "Completed Work:" in compact_message
        assert "Key Decisions:" in compact_message
        assert "Active Files:" in compact_message
        assert "Test / Verification Status:" in compact_message
        assert "Unresolved Issues:" in compact_message
        assert "Next Steps:" in compact_message
        result.session.close()

    def test_append_record_does_not_bump_message_count(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        s.append(Message(role="user", content="hi"))
        before = s.meta.message_count
        s.append_record(make_compact_boundary("x", []))
        assert s.meta.message_count == before  # 边界只是一个标记，不算一轮对话
        s.close()

# =========================================================================
# F. 会话元数据 SessionMeta
# =========================================================================

class TestSessionMeta:
    def test_save_and_load(self, tmp_path: Path) -> None:
        meta = SessionMeta(
            id="test_123",
            title="Test session",
            summary="A test",
            message_count=10,
            total_tokens=5000,
        )
        path = tmp_path / "test.meta"
        meta.save(path)

        loaded = SessionMeta.load(path)
        assert loaded is not None
        assert loaded.id == "test_123"
        assert loaded.title == "Test session"
        assert loaded.message_count == 10

    def test_load_invalid_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.meta"
        path.write_text("not json", encoding="utf-8")
        assert SessionMeta.load(path) is None

# =========================================================================
# G. 记忆管理器 MemoryManager
# =========================================================================

class TestMemoryManagerPromptAndListing:
    """对齐 Go 版 Manager：独立 .md 文件 + frontmatter + MEMORY.md 索引格式。"""

    def test_load_returns_prompt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """load() 返回完整的记忆系统提示（包含行为指令），不再是空字符串。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        result = mgr.load()
        # 即使没有记忆文件，也会返回记忆系统的行为指令
        assert "auto memory" in result
        assert "User-level" in result
        assert "Project-level" in result

    def test_load_includes_memory_index(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """MEMORY.md 索引内容被包含在 load() 返回的系统提示中。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # 创建项目级记忆目录和文件
        project_mem_dir = tmp_path / "project" / ".mewcode" / "memory"
        project_mem_dir.mkdir(parents=True)
        # 写一个记忆文件
        mem_file = project_mem_dir / "test_mem.md"
        mem_file.write_text(
            "---\nname: test\ndescription: a test memory\ntype: project\n---\n\ntest content\n",
            encoding="utf-8",
        )
        # 写 MEMORY.md 索引
        index_file = project_mem_dir / "MEMORY.md"
        index_file.write_text(
            "- [Test](test_mem.md) — a test memory\n", encoding="utf-8"
        )

        mgr = MemoryManager(str(tmp_path / "project"))
        result = mgr.load()
        assert "a test memory" in result

    def test_load_all(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_all() 扫描两个目录的 .md 文件并解析 frontmatter。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # 用户级记忆
        user_mem_dir = fake_home / ".mewcode" / "memory"
        user_mem_dir.mkdir(parents=True)
        (user_mem_dir / "user_pref.md").write_text(
            "---\nname: coding style\ndescription: prefers spaces\ntype: user\n---\n\nprefer spaces\n",
            encoding="utf-8",
        )

        # 项目级记忆
        project_mem_dir = tmp_path / "project" / ".mewcode" / "memory"
        project_mem_dir.mkdir(parents=True)
        (project_mem_dir / "proj_db.md").write_text(
            "---\nname: database\ndescription: uses PostgreSQL\ntype: project\n---\n\nuses PostgreSQL\n",
            encoding="utf-8",
        )

        mgr = MemoryManager(str(tmp_path / "project"))
        files = mgr.load_all()
        assert len(files) == 2
        names = [f.name for f in files]
        assert "coding style" in names
        assert "database" in names

    def test_clear(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear() 删除两个目录中所有 .md 文件。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # 创建记忆文件
        user_mem_dir = fake_home / ".mewcode" / "memory"
        user_mem_dir.mkdir(parents=True)
        (user_mem_dir / "test.md").write_text("content", encoding="utf-8")
        (user_mem_dir / "MEMORY.md").write_text("- index\n", encoding="utf-8")

        project_mem_dir = tmp_path / "project" / ".mewcode" / "memory"
        project_mem_dir.mkdir(parents=True)
        (project_mem_dir / "test.md").write_text("content", encoding="utf-8")

        mgr = MemoryManager(str(tmp_path / "project"))
        mgr.clear()
        assert mgr.load_all() == []

    def test_get_display_text_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        assert "当前没有任何动态记忆" in mgr.get_display_text()

    def test_get_memories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_memories() 返回单行摘要列表。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        project_mem_dir = tmp_path / "project" / ".mewcode" / "memory"
        project_mem_dir.mkdir(parents=True)
        (project_mem_dir / "db_info.md").write_text(
            "---\nname: db\ndescription: uses PostgreSQL\ntype: project\n---\n\ncontent\n",
            encoding="utf-8",
        )

        mgr = MemoryManager(str(tmp_path / "project"))
        summaries = mgr.get_memories()
        assert len(summaries) == 1
        assert "[project]" in summaries[0]
        assert "db" in summaries[0]

# =========================================================================
# H. 会话注入长期记忆 inject_long_term_memory
# =========================================================================

class TestConversationInjection:
    def test_inject_long_term_memory(self) -> None:
        conv = ConversationManager()
        conv.inject_environment("env info")
        conv.inject_long_term_memory("project rules", "user prefs")

        assert len(conv.history) == 2
        assert conv.history[0].content == "env info"
        assert "<system-reminder>" in conv.history[1].content
        assert "rockcoderMd" in conv.history[1].content
        assert "project rules" in conv.history[1].content
        assert "autoMemory" in conv.history[1].content
        assert "user prefs" in conv.history[1].content
        assert "currentDate" in conv.history[1].content
        assert conv.ltm_injected is True

    def test_inject_long_term_memory_includes_instruction_warnings(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory(
            "project rules",
            "user prefs",
            instruction_warnings=["Could not read /tmp/RockCoder.md: permission denied"],
        )

        assert len(conv.history) == 1
        assert "instructionWarnings" in conv.history[0].content
        assert "Could not read /tmp/RockCoder.md: permission denied" in conv.history[0].content

    def test_inject_idempotent(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory("rules", "mems")
        conv.inject_long_term_memory("rules2", "mems2")
        assert sum(1 for m in conv.history if "<system-reminder>" in m.content) == 1

    def test_inject_instructions_only(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory("rules", "")
        assert len(conv.history) == 1
        assert "<system-reminder>" in conv.history[0].content
        assert "rockcoderMd" in conv.history[0].content
        assert "rules" in conv.history[0].content

    def test_inject_memories_only(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory("", "mems")
        assert len(conv.history) == 1
        assert "<system-reminder>" in conv.history[0].content
        assert "autoMemory" in conv.history[0].content
        assert "mems" in conv.history[0].content

    def test_inject_nothing(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory("", "")
        assert len(conv.history) == 0
        assert conv.ltm_injected is False

    def test_replace_history_resets_ltm(self) -> None:
        conv = ConversationManager()
        conv.inject_long_term_memory("rules", "mems")
        assert conv.ltm_injected is True
        conv.replace_history([])
        assert conv.ltm_injected is False


@pytest.mark.asyncio
class TestAgentInstructionWarningInjection:
    async def test_run_to_completion_injects_warning_only_long_term_memory(
        self, tmp_path: Path
    ) -> None:
        class _NoOpClient:
            async def stream(self, conversation, system="", tools=None):
                yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        agent = Agent(
            client=_NoOpClient(),
            registry=ToolRegistry(),
            protocol="anthropic",
            work_dir=str(tmp_path),
            instructions_content="",
            instruction_warnings=["Could not read RockCoder.md"],
        )

        captured = {}

        async def _stream(conversation, system="", tools=None):
            captured["conversation"] = conversation
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        agent.client.stream = _stream

        await agent.run_to_completion("")

        conv = captured["conversation"]
        assert conv.ltm_injected is True
        assert len(conv.history) == 2
        assert "instructionWarnings" in conv.history[1].content
        assert "Could not read RockCoder.md" in conv.history[1].content

    async def test_run_to_completion_injects_warning_only_existing_conversation(
        self, tmp_path: Path
    ) -> None:
        class _NoOpClient:
            async def stream(self, conversation, system="", tools=None):
                yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        agent = Agent(
            client=_NoOpClient(),
            registry=ToolRegistry(),
            protocol="anthropic",
            work_dir=str(tmp_path),
            instructions_content="",
            instruction_warnings=["Could not read RockCoder.md"],
        )
        conversation = ConversationManager()

        await agent.run_to_completion("", conversation=conversation)

        assert conversation.ltm_injected is True
        assert any("instructionWarnings" in msg.content for msg in conversation.history)
        assert any("Could not read RockCoder.md" in msg.content for msg in conversation.history)

    async def test_manual_compact_reinjects_instruction_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = Agent(
            client=AsyncMock(),
            registry=ToolRegistry(),
            protocol="anthropic",
            work_dir=str(tmp_path),
            instructions_content="project rules",
            instruction_warnings=["Could not read RockCoder.md"],
        )
        conversation = ConversationManager()
        conversation.inject_environment("env")
        conversation.inject_long_term_memory("old rules", "old memory")
        conversation.replace_history([])

        from rockcoder.context.manager import CompactBoundary, CompactEvent

        async def _fake_auto_compact(*args, **kwargs):
            return CompactEvent(before_tokens=1234, boundary=CompactBoundary("summary", []))

        monkeypatch.setattr("rockcoder.agent.auto_compact", _fake_auto_compact)

        notification = await agent.manual_compact(conversation)

        assert notification.before_tokens == 1234
        assert conversation.ltm_injected is True
        assert len(conversation.history) == 2
        assert "project rules" in conversation.history[1].content
        assert "instructionWarnings" in conversation.history[1].content
        assert "Could not read RockCoder.md" in conversation.history[1].content

    async def test_run_to_completion_triggers_memory_extraction_on_completion(
        self, tmp_path: Path
    ) -> None:
        memory_manager = AsyncMock()
        memory_manager.load.return_value = ""

        class _NoOpClient:
            async def stream(self, conversation, system="", tools=None):
                yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        agent = Agent(
            client=_NoOpClient(),
            registry=ToolRegistry(),
            protocol="anthropic",
            work_dir=str(tmp_path),
            memory_manager=memory_manager,
        )

        conversation = ConversationManager()
        conversation.add_user_message("记住：这个项目使用 pnpm。")

        await agent.run_to_completion("", conversation=conversation)

        memory_manager.extract.assert_awaited_once()

# =========================================================================
# I. 记忆抽取 prompt 的构造
# =========================================================================

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

        project_entries = [mem for mem in mm.load_all() if Path(mem.path).parent == mm.project_mem_dir]
        assert project_entries == []


class TestMemoryExtraction:
    def test_memory_types_aligned_with_go(self, tmp_path: Path) -> None:
        """验证四种记忆类型与 Go 版一致。"""
        from rockcoder.memory.auto_memory import VALID_TYPES, _USER_LEVEL_TYPES, _PROJECT_LEVEL_TYPES

        assert VALID_TYPES == {"user", "feedback", "project", "reference"}
        assert _USER_LEVEL_TYPES == {"user", "feedback"}
        assert _PROJECT_LEVEL_TYPES == {"project", "reference"}

    @pytest.mark.asyncio
    async def test_extract_persists_explicit_remember_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        mgr.load()
        conversation = ConversationManager()
        conversation.add_user_message("记住这件事：我喜欢回答简洁一点。")
        conversation.add_assistant_message("好，我会注意。")

        await mgr.extract(None, conversation, "anthropic")

        memories = mgr.get_memories()
        assert len(memories) == 1
        assert any("我喜欢回答简洁一点" in summary for summary in memories)

    @pytest.mark.asyncio
    async def test_extract_ignores_one_off_task_chatter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        mgr.load()
        conversation = ConversationManager()
        conversation.add_user_message("帮我记一下今天下午三点开会。")
        conversation.add_assistant_message("好的，我会提醒你。")

        await mgr.extract(None, conversation, "anthropic")

        assert mgr.get_memories() == []

    @pytest.mark.asyncio
    async def test_extract_ignores_do_not_remember_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        mgr.load()
        conversation = ConversationManager()
        conversation.add_user_message("不要记住这件事：我的临时验证码是 123456。")
        conversation.add_assistant_message("收到。")

        await mgr.extract(None, conversation, "anthropic")

        assert mgr.get_memories() == []

    @pytest.mark.asyncio
    async def test_extract_does_not_duplicate_memories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        mgr = MemoryManager(str(tmp_path / "project"))
        mgr.load()
        conversation = ConversationManager()
        conversation.add_user_message("记住：这个项目使用 pnpm。")
        conversation.add_assistant_message("收到。")

        await mgr.extract(None, conversation, "anthropic")
        first_memories = mgr.get_memories()

        await mgr.extract(None, conversation, "anthropic")
        second_memories = mgr.get_memories()

        assert len(first_memories) == 1
        assert second_memories == first_memories
