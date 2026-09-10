 

"""Slash Command 框架测试——registry、parser、补全、handler。"""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import os

import pytest
from textual.app import App as TextualApp, ComposeResult
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import Static

from rockcoder.commands.parser import complete, parse_command
from rockcoder.conversation import Message
from rockcoder.commands.registry import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandType,
    UIController,
)

# ---------------------------------------------------------------------------
# 测试夹具（Fixtures）
# ---------------------------------------------------------------------------

def _make_command(
    name: str,
    aliases: list[str] | None = None,
    hidden: bool = False,
    handler: Any = None,
    arg_prompt: str = "",
) -> Command:
    return Command(
        name=name,
        aliases=aliases or [],
        description=f"Test {name}",
        type=CommandType.LOCAL,
        handler=handler or AsyncMock(),
        hidden=hidden,
        arg_prompt=arg_prompt,
    )

class MockUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sent_messages: list[str] = []
        self._plan_mode = False

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    def send_user_message(self, text: str) -> None:
        self.sent_messages.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        self._plan_mode = enabled

    def get_token_count(self) -> tuple[int, int]:
        return 10000, 5000

    def refresh_status(self) -> None:
        pass

def _make_context(args: str = "", ui: MockUI | None = None) -> CommandContext:
    return CommandContext(
        args=args,
        agent=None,
        conversation=None,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui or MockUI(),
        config={},
    )

# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_normal_command(self) -> None:
        name, args, is_cmd = parse_command("/help")
        assert is_cmd is True
        assert name == "help"
        assert args == ""

    def test_command_with_args(self) -> None:
        name, args, is_cmd = parse_command("/compact 保留数据库相关内容")
        assert is_cmd is True
        assert name == "compact"
        assert args == "保留数据库相关内容"

    def test_command_case_insensitive(self) -> None:
        name, args, is_cmd = parse_command("/HELP")
        assert name == "help"
        assert is_cmd is True

    def test_only_slash(self) -> None:
        name, args, is_cmd = parse_command("/")
        assert is_cmd is True
        assert name == ""
        assert args == ""

    def test_not_a_command(self) -> None:
        name, args, is_cmd = parse_command("hello world")
        assert is_cmd is False
        assert name == ""
        assert args == ""

    def test_empty_input(self) -> None:
        name, args, is_cmd = parse_command("")
        assert is_cmd is False

    def test_whitespace_input(self) -> None:
        name, args, is_cmd = parse_command("   ")
        assert is_cmd is False

    def test_command_with_leading_spaces(self) -> None:
        name, args, is_cmd = parse_command("  /help  ")
        assert is_cmd is True
        assert name == "help"

    def test_command_with_multiple_args(self) -> None:
        name, args, is_cmd = parse_command("/session resume abc123")
        assert name == "session"
        assert args == "resume abc123"

# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------

class TestCommandRegistry:
    def test_register_and_find(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("help", aliases=["h", "?"])
        registry.register_sync(cmd)
        assert registry.find("help") is cmd

    def test_find_by_alias(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("help", aliases=["h", "?"])
        registry.register_sync(cmd)
        assert registry.find("h") is cmd
        assert registry.find("?") is cmd

    def test_find_unknown(self) -> None:
        registry = CommandRegistry()
        assert registry.find("nonexistent") is None

    def test_list_commands_excludes_hidden(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("visible"))
        registry.register_sync(_make_command("secret", hidden=True))
        cmds = registry.list_commands()
        assert len(cmds) == 1
        assert cmds[0].name == "visible"

    def test_alias_conflict_raises(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h"]))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("hints", aliases=["h"]))

    def test_name_conflict_raises(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help"))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("help"))

    def test_name_alias_cross_conflict(self) -> None:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h"]))
        with pytest.raises(ValueError, match="conflicts"):
            registry.register_sync(_make_command("h"))

    @pytest.mark.asyncio
    async def test_async_register(self) -> None:
        registry = CommandRegistry()
        cmd = _make_command("test")
        await registry.register(cmd)
        assert registry.find("test") is cmd

    @pytest.mark.asyncio
    async def test_async_register_conflict(self) -> None:
        registry = CommandRegistry()
        await registry.register(_make_command("test"))
        with pytest.raises(ValueError, match="conflicts"):
            await registry.register(_make_command("test"))

# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

class TestComplete:
    def _build_registry(self) -> CommandRegistry:
        registry = CommandRegistry()
        registry.register_sync(_make_command("help", aliases=["h", "?"]))
        registry.register_sync(_make_command("compact", aliases=["c"]))
        registry.register_sync(_make_command("session"))
        registry.register_sync(_make_command("status", aliases=["s"]))
        registry.register_sync(_make_command("secret", hidden=True))
        return registry

    @staticmethod
    def _values(matches: list[tuple[str, str]]) -> list[str]:
        return [v for _, v in matches]

    def test_empty_prefix(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/")
        values = self._values(matches)
        assert "/help" in values
        assert "/compact" in values
        assert "/secret" not in values

    def test_prefix_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/com")
        assert self._values(matches) == ["/compact"]

    def test_multiple_matches(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/s")
        values = self._values(matches)
        assert "/session" in values
        assert "/status" in values

    def test_alias_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/h")
        values = self._values(matches)
        assert "/help" in values

    def test_no_match(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/xyz")
        assert matches == []

    def test_hidden_excluded(self) -> None:
        registry = self._build_registry()
        matches = complete(registry, "/sec")
        assert matches == []

# ---------------------------------------------------------------------------
# Handler 测试
# ---------------------------------------------------------------------------

class TestHelpHandler:
    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        from rockcoder.commands.handlers import register_all_commands
        from rockcoder.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert len(ui.messages) == 1
        assert "可用命令" in ui.messages[0]
        assert "/help" in ui.messages[0]
        assert "/compact" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_help_specific_command(self) -> None:
        from rockcoder.commands.handlers import register_all_commands
        from rockcoder.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="compact", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert len(ui.messages) == 1
        assert "compact" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_help_unknown_command(self) -> None:
        from rockcoder.commands.handlers import register_all_commands
        from rockcoder.commands.handlers.help import handle_help

        registry = CommandRegistry()
        register_all_commands(registry)
        ui = MockUI()
        ctx = _make_context(args="nonexistent", ui=ui)
        ctx.config = {"registry": registry}
        await handle_help(ctx)
        assert "未知命令" in ui.messages[0]

class TestPlanDoHandlers:

    @pytest.mark.asyncio
    async def test_plan_switches_mode(self) -> None:
        from rockcoder.commands.handlers.plan import handle_plan

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        await handle_plan(ctx)
        assert ui._plan_mode is True
        assert "Plan 模式" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_plan_with_args_sends_message(self) -> None:
        from rockcoder.commands.handlers.plan import handle_plan

        ui = MockUI()
        ctx = _make_context(args="设计登录模块", ui=ui)
        await handle_plan(ctx)
        assert ui._plan_mode is True
        assert "设计登录模块" in ui.sent_messages

class TestSkillHandler:
    @pytest.mark.asyncio
    async def test_skill_list_no_loader(self) -> None:
        from rockcoder.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="list", ui=ui)
        await handle_skill(ctx)
        assert "未初始化" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_skill_list_with_loader(self) -> None:
        from rockcoder.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="list", ui=ui)
        loader = MagicMock()
        loader.get_catalog.return_value = [("commit", "分析 git diff")]
        loader.get_source_label.return_value = "builtin"
        ctx.config = {"skill_loader": loader}
        await handle_skill(ctx)
        assert "commit" in ui.messages[0]
        assert "builtin" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_skill_unknown_subcmd(self) -> None:
        from rockcoder.commands.handlers.skill import handle_skill

        ui = MockUI()
        ctx = _make_context(args="foobar", ui=ui)
        loader = MagicMock()
        ctx.config = {"skill_loader": loader}
        await handle_skill(ctx)
        assert "未知子命令" in ui.messages[0]

class TestStatusHandler:

    @pytest.mark.asyncio
    async def test_status_output(self) -> None:
        from rockcoder.commands.handlers.status import handle_status

        ui = MockUI()
        agent = MagicMock()
        agent.permission_mode = MagicMock()
        agent.permission_mode.value = "default"
        agent.context_window = 200_000
        agent.registry = MagicMock()
        agent.registry.list_tools.return_value = []
        agent.registry.is_enabled.return_value = True
        agent.work_dir = "/test"

        ctx = _make_context(args="", ui=ui)
        ctx.agent = agent
        ctx.memory_manager = MagicMock()
        ctx.memory_manager.load.return_value = ""

        await handle_status(ctx)
        assert "RockCoder 状态" in ui.messages[0]
        assert "default" in ui.messages[0]

class TestSessionHandler:
    @pytest.mark.asyncio
    async def test_session_no_manager(self) -> None:
        from rockcoder.commands.handlers.session import handle_session

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.session_manager = None
        await handle_session(ctx)
        assert "未初始化" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_session_new_resets_to_draft_without_creating_session(self) -> None:
        from rockcoder.commands.handlers.session import handle_session

        ui = MockUI()
        sm = MagicMock()
        current_session = MagicMock()
        set_session = MagicMock()
        set_conversation = MagicMock()
        clear_chat = MagicMock()
        agent = MagicMock()
        ctx = _make_context(args="new", ui=ui)
        ctx.session_manager = sm
        ctx.session = current_session
        ctx.agent = agent
        ctx.config = {
            "set_session": set_session,
            "set_conversation": set_conversation,
            "clear_chat": clear_chat,
            "reset_session": set_session,
        }

        await handle_session(ctx)

        current_session.close.assert_called_once()
        sm.create.assert_not_called()
        set_session.assert_called_once_with()
        set_conversation.assert_called_once()
        clear_chat.assert_called_once()
        assert agent._loop_count == 0

    @pytest.mark.asyncio
    async def test_clear_resets_to_draft_without_creating_session(self) -> None:
        from rockcoder.commands.handlers.clear import handle_clear

        ui = MockUI()
        sm = MagicMock()
        current_session = MagicMock()
        set_session = MagicMock()
        set_conversation = MagicMock()
        clear_chat = MagicMock()
        agent = MagicMock()
        ctx = _make_context(args="", ui=ui)
        ctx.session_manager = sm
        ctx.session = current_session
        ctx.agent = agent
        ctx.config = {
            "set_session": set_session,
            "set_conversation": set_conversation,
            "clear_chat": clear_chat,
            "reset_session": set_session,
        }

        await handle_clear(ctx)

        current_session.close.assert_called_once()
        sm.create.assert_not_called()
        set_session.assert_called_once_with()
        set_conversation.assert_called_once()
        clear_chat.assert_called_once()
        assert "新会话草稿状态" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_session_list_empty(self) -> None:
        from rockcoder.commands.handlers.session import handle_session

        ui = MockUI()
        sm = MagicMock()
        sm.list.return_value = []
        ctx = _make_context(args="list", ui=ui)
        ctx.session_manager = sm
        await handle_session(ctx)
        assert "没有已保存的会话" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_session_unknown_sub(self) -> None:
        from rockcoder.commands.handlers.session import handle_session

        ui = MockUI()
        ctx = _make_context(args="foobar", ui=ui)
        ctx.session_manager = MagicMock()
        await handle_session(ctx)
        assert "用法" in ui.messages[0]


class TestResumeHandler:
    def test_restored_ui_hides_process_messages_but_keeps_chat_messages(self) -> None:
        from rockcoder.app import _is_hidden_restored_message
        from rockcoder.conversation import Message, ToolResultBlock

        messages = [
            Message(role="user", content="real question"),
            Message(role="user", content="<system-reminder>internal context</system-reminder>"),
            Message(role="user", content="本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\nold summary"),
            Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content="tool output")]),
            Message(role="assistant", content="real answer"),
        ]

        visible = [msg for msg in messages if not _is_hidden_restored_message(msg)]

        assert [msg.content for msg in visible] == ["real question", "real answer"]
        assert len(messages) == 5

    @pytest.mark.asyncio
    async def test_resume_no_manager(self) -> None:
        from rockcoder.commands.handlers.session import handle_resume

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.session_manager = None
        await handle_resume(ctx)
        assert "未初始化" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_resume_lists_candidates_without_arg(self) -> None:
        from rockcoder.commands.handlers.session import handle_resume

        ui = MockUI()
        sm = MagicMock()
        meta = MagicMock()
        meta.id = "session_abc123"
        meta.title = "Resume me"
        meta.message_count = 12
        from datetime import datetime
        meta.last_active = datetime(2026, 7, 20, 12, 0)
        sm.list.return_value = [meta]
        ctx = _make_context(args="", ui=ui)
        ctx.session_manager = sm
        await handle_resume(ctx)
        assert "可恢复的会话" in ui.messages[0]
        assert "session_abc123" in ui.messages[0]
        assert ctx.config["_resume_candidates"] == ["session_abc123"]

    @pytest.mark.asyncio
    async def test_resume_restores_session_by_id(self) -> None:
        from rockcoder.commands.handlers.session import handle_resume

        ui = MockUI()
        sm = MagicMock()
        current_session = MagicMock()
        restored_session = MagicMock()
        restored_session.meta.message_count = 3
        result = MagicMock()
        result.session = restored_session
        result.messages = [MagicMock(), MagicMock()]
        sm.resume.return_value = result

        set_session = MagicMock()
        set_conversation = MagicMock()
        render_restored = AsyncMock()
        agent = MagicMock()
        agent._loop_count = 7

        ctx = _make_context(args="session_abc123", ui=ui)
        ctx.session_manager = sm
        ctx.session = current_session
        ctx.agent = agent
        ctx.config = {
            "set_session": set_session,
            "set_conversation": set_conversation,
            "render_restored": render_restored,
        }

        await handle_resume(ctx)

        sm.resume.assert_called_once_with("session_abc123")
        current_session.close.assert_called_once()
        set_session.assert_called_once_with(restored_session)
        assert set_conversation.call_count == 1
        restored_conv = set_conversation.call_args.args[0]
        assert len(restored_conv.history) == 2
        render_restored.assert_awaited_once_with(result.messages)
        assert agent._loop_count == 0
        assert "会话已恢复" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_resume_restores_session_by_candidate_index(self) -> None:
        from rockcoder.commands.handlers.session import handle_resume

        ui = MockUI()
        sm = MagicMock()
        restored_session = MagicMock()
        restored_session.meta.message_count = 1
        result = MagicMock()
        result.session = restored_session
        result.messages = []
        sm.resume.return_value = result

        ctx = _make_context(args="1", ui=ui)
        ctx.session_manager = sm
        ctx.config = {
            "_resume_candidates": ["session_abc123"],
            "set_session": MagicMock(),
            "set_conversation": MagicMock(),
            "render_restored": AsyncMock(),
        }

        await handle_resume(ctx)

        sm.resume.assert_called_once_with("session_abc123")

    @pytest.mark.asyncio
    async def test_resume_restores_session_by_index_without_candidates(self) -> None:
        from rockcoder.commands.handlers.session import handle_resume

        ui = MockUI()
        sm = MagicMock()
        meta = MagicMock()
        meta.id = "session_abc123"
        meta.title = "Resume me"
        meta.message_count = 12
        from datetime import datetime
        meta.last_active = datetime(2026, 7, 20, 12, 0)
        sm.list.return_value = [meta]

        restored_session = MagicMock()
        restored_session.meta.message_count = 1
        result = MagicMock()
        result.session = restored_session
        result.messages = []
        sm.resume.return_value = result

        ctx = _make_context(args="1", ui=ui)
        ctx.session_manager = sm
        ctx.config = {
            "set_session": MagicMock(),
            "set_conversation": MagicMock(),
            "render_restored": AsyncMock(),
        }

        await handle_resume(ctx)

        sm.list.assert_called_once()
        sm.resume.assert_called_once_with("session_abc123")

class TestMemoryHandler:
    @pytest.mark.asyncio
    async def test_memory_display(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        mm.get_display_text.return_value = "记忆内容"
        ctx = _make_context(args="", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        assert "记忆内容" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_remember(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        mm.remember.return_value = ("created", "user-preferences.md")
        ctx = _make_context(args="remember 提交前先跑测试", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        mm.remember.assert_called_once()
        assert "已记录记忆" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_forget(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        mm.forget.return_value = True
        ctx = _make_context(args="forget workflows", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        assert "已删除记忆" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_show_missing_topic(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        mm.show_topic.side_effect = FileNotFoundError("x")
        ctx = _make_context(args="show x", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        assert "未找到记忆主题" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_clear(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        mm = MagicMock()
        ctx = _make_context(args="clear", ui=ui)
        ctx.memory_manager = mm
        await handle_memory(ctx)
        mm.clear.assert_called_once()
        assert "清空" in ui.messages[0]

    @pytest.mark.asyncio
    async def test_memory_no_manager(self) -> None:
        from rockcoder.commands.handlers.memory import handle_memory

        ui = MockUI()
        ctx = _make_context(args="", ui=ui)
        ctx.memory_manager = None
        await handle_memory(ctx)
        assert "未初始化" in ui.messages[0]

# ---------------------------------------------------------------------------
# 集成测试：register_all_commands
# ---------------------------------------------------------------------------

class TestRegisterAllCommands:
    def test_all_commands_registered(self) -> None:
        from rockcoder.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)
        cmds = registry.list_commands()
        names = {c.name for c in cmds}
        expected = {
            "help", "compact", "clear", "plan",
            "session", "resume", "mcp", "memory", "permission",
            "sandbox", "rewind", "status", "skill", "review",
        }
        assert names == expected

    def test_review_command_registered(self) -> None:
        from rockcoder.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)
        review = registry.find("review")
        assert review is not None
        assert review.name == "review"

    def test_no_alias_conflicts(self) -> None:
        from rockcoder.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)

    def test_aliases_work(self) -> None:
        from rockcoder.commands.handlers import register_all_commands

        registry = CommandRegistry()
        register_all_commands(registry)
        assert registry.find("h") is not None
        assert registry.find("h").name == "help"
        assert registry.find("c").name == "compact"
        assert registry.find("p").name == "plan"
        assert registry.find("s").name == "status"
        assert registry.find("?").name == "help"


class _FocusRecoveryHarness(TextualApp[None]):
    def compose(self) -> ComposeResult:
        from rockcoder.app import ChatInput, ToolCallBlock, ToolGroupSummary, SubAgentBlock

        with Vertical():
            yield Static("plain message", id="plain-static")
            yield ChatInput(id="chat-input")
            yield ToolCallBlock("EditFile", {"file_path": "/tmp/demo.txt"}, id="tool-block")
            yield ToolGroupSummary(2, 0.2, id="tool-group")
            yield SubAgentBlock("agent", "demo", id="subagent-block")

    def on_mount(self) -> None:
        self.query_one("#tool-block").set_result(
            "Successfully edited /tmp/demo.txt\n[Preview] edited: /tmp/demo.txt\n--- before\n+++ after\n@@ -1 +1 @@\n-hello\n+world",
            is_error=False,
            elapsed=0.1,
        )
        self.query_one("#subagent-block").set_result("done", is_error=False, elapsed=0.1)
        self.query_one("#chat-input").focus()


class _MewLayoutHarness(TextualApp[None]):
    def compose(self) -> ComposeResult:
        from rockcoder.app import ChatInput

        yield Static("title", id="title-bar")
        yield VerticalScroll(Static("plain message", id="chat-static"), id="chat-area")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("default", id="mode-label")
                yield Static("", id="teammates-label")
                yield Static("", id="model-label")

    def on_mount(self) -> None:
        self.query_one("#chat-input").focus()


class _RockCoderAppHarness:
    @staticmethod
    def build():
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig
        from rockcoder.permissions import PermissionMode

        class TestableRockCoderApp(RockCoderApp):
            CSS_PATH = str(Path(__file__).resolve().parents[1] / "rockcoder" / "styles.tcss")

            def _select_provider(self, provider: ProviderConfig) -> None:
                from textual.containers import Vertical
                from textual.widgets import Markdown

                self._selected_provider = provider
                self.query_one("#model-label", Static).update(provider.model)
                select = self.query("#provider-select")
                if select:
                    select.first().display = False
                self.query_one("#chat-area").display = True
                self.query_one("#input-area").display = True
                chat = self.query_one("#chat-area")
                chat.mount(Static("plain message", id="chat-static"))
                user_row = Vertical(classes="user-row", id="user-row")
                chat.mount(user_row)
                user_row.mount(Static("user bubble", classes="message user-message", id="user-bubble"))
                ai_row = Vertical(classes="ai-row", id="ai-row")
                chat.mount(ai_row)
                ai_row.mount(Markdown("assistant text", classes="message ai-message", id="ai-markdown"))
                chat_input = self.query_one("#chat-input")
                chat_input.placeholder = "Send a message..."
                chat_input.focus()

        provider = ProviderConfig(
            name="test",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-test",
        )
        return TestableRockCoderApp(
            providers=[provider],
            permission_mode=PermissionMode.DEFAULT,
        )


class TestUserCommandLoader:
    def test_prefers_rockcoder_commands_when_both_exist(self, tmp_path: Path) -> None:
        from rockcoder.commands.loader import load_user_commands

        home = tmp_path / "home"
        home.mkdir()
        project_new = tmp_path / ".rockcoder" / "commands"
        project_legacy = tmp_path / ".mewcode" / "commands"
        project_new.mkdir(parents=True)
        project_legacy.mkdir(parents=True)
        (project_new / "custom.md").write_text(textwrap.dedent("""
            ---
            description: New command
            ---
            New body
        """), encoding="utf-8")
        (project_legacy / "custom.md").write_text(textwrap.dedent("""
            ---
            description: Legacy command
            ---
            Legacy body
        """), encoding="utf-8")

        with patch("rockcoder.commands.loader.Path.home", return_value=home):
            commands = load_user_commands(str(tmp_path))

        custom = next(cmd for cmd in commands if cmd.name == "custom")
        assert custom.description == "New command"

    def test_falls_back_to_legacy_commands_dir(self, tmp_path: Path) -> None:
        from rockcoder.commands.loader import load_user_commands

        home = tmp_path / "home"
        home.mkdir()
        project_legacy = tmp_path / ".mewcode" / "commands"
        project_legacy.mkdir(parents=True)
        (project_legacy / "legacy.md").write_text(textwrap.dedent("""
            ---
            description: Legacy command
            ---
            Legacy body
        """), encoding="utf-8")

        with patch("rockcoder.commands.loader.Path.home", return_value=home):
            commands = load_user_commands(str(tmp_path))

        assert any(cmd.name == "legacy" for cmd in commands)


class TestAppCommandIntegration:
    def test_make_banner_uses_current_version(self) -> None:
        from rockcoder.app import RockCoderApp

        banner = RockCoderApp._make_banner(model="test-model", work_dir="/tmp")
        assert "RockCoder v0.2.0" in banner.plain

    def test_make_banner_uses_wordmark_and_preserves_context(self) -> None:
        from rockcoder.app import RockCoderApp

        banner = RockCoderApp._make_banner(model="test-model", work_dir="/tmp")

        assert ".----------------." in banner.plain
        assert "[ R O C K C O D E R ]" in banner.plain
        assert "./build/code/." in banner.plain
        assert "test-model" in banner.plain
        assert "/tmp" in banner.plain
        assert len(banner.plain.splitlines()) == 13

    def test_app_registers_user_commands_on_init(self, tmp_path: Path) -> None:
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig
        from rockcoder.permissions import PermissionMode

        provider = ProviderConfig(
            name="test",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-test",
        )

        custom_command = _make_command("custom")
        with patch("rockcoder.app.load_user_commands", return_value=[custom_command]) as mocked_loader:
            app = RockCoderApp(
                providers=[provider],
                permission_mode=PermissionMode.DEFAULT,
            )

        mocked_loader.assert_called_once()
        assert mocked_loader.call_args.args[0] == str(Path.cwd())
        registered = app.command_registry.find("custom")
        assert registered is custom_command

    @pytest.mark.asyncio
    async def test_app_does_not_create_session_before_first_message(self) -> None:
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig
        from rockcoder.permissions import PermissionMode

        provider = ProviderConfig(
            name="test",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-test",
        )

        with patch("rockcoder.app.create_client") as mocked_client_factory, \
             patch("rockcoder.app.SessionManager") as mocked_session_manager_cls, \
             patch.object(RockCoderApp, "run_worker") as mocked_run_worker, \
             patch("rockcoder.app.start_stale_cleanup_task", new=AsyncMock()):
            mocked_client_factory.return_value = MagicMock()
            mocked_run_worker.side_effect = lambda coro, **kwargs: coro.close()
            app = RockCoderApp(
                providers=[provider],
                permission_mode=PermissionMode.DEFAULT,
            )

            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.session is None
                assert app.agent is not None
                assert app.agent.session_id == ""

        mocked_session_manager = mocked_session_manager_cls.return_value
        mocked_session_manager.create.assert_not_called()
        mocked_run_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_creates_session_on_first_real_input(self) -> None:
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig
        from rockcoder.permissions import PermissionMode
        from rockcoder.tools.base import StreamEnd

        provider = ProviderConfig(
            name="test",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-test",
        )

        class _NoOpClient:
            async def stream(self, conversation, system="", tools=None):
                yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        session = MagicMock()
        session.session_id = "session_test"
        session.meta = MagicMock()
        session._sessions_dir = Path("D:/tmp")

        with patch("rockcoder.app.create_client", return_value=_NoOpClient()), \
             patch("rockcoder.app.SessionManager") as mocked_session_manager_cls, \
             patch.object(RockCoderApp, "run_worker") as mocked_run_worker, \
             patch("rockcoder.app.start_stale_cleanup_task", new=AsyncMock()), \
             patch("rockcoder.filehistory.FileHistory") as mocked_file_history:
            mocked_session_manager_cls.return_value.create.return_value = session
            mocked_run_worker.side_effect = lambda coro, **kwargs: coro.close()
            app = RockCoderApp(
                providers=[provider],
                permission_mode=PermissionMode.DEFAULT,
            )

            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.session is None
                await app._send_message("hello")

        mocked_session_manager_cls.return_value.create.assert_called_once()
        session.append.assert_called()
        first_call = session.append.call_args_list[0].args[0]
        assert first_call.role == "user"
        assert first_call.content == "hello"
        mocked_file_history.assert_called_once()
        assert app.agent.session_id == "session_test"

    @pytest.mark.asyncio
    async def test_prefetch_relevant_memories_includes_compact_state_in_query(self) -> None:
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig
        from rockcoder.permissions import PermissionMode

        provider = ProviderConfig(
            name="test",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-test",
        )

        captured = {}

        async def fake_find_relevant_memories(**kwargs):
            captured.update(kwargs)
            return []

        with patch("rockcoder.app.create_client", return_value=MagicMock()), \
             patch.object(RockCoderApp, "run_worker") as mocked_run_worker, \
             patch("rockcoder.app.start_stale_cleanup_task", new=AsyncMock()), \
             patch("rockcoder.app.find_relevant_memories", side_effect=fake_find_relevant_memories):
            mocked_run_worker.side_effect = lambda coro, **kwargs: coro.close()
            app = RockCoderApp(
                providers=[provider],
                permission_mode=PermissionMode.DEFAULT,
            )

            async with app.run_test() as pilot:
                await pilot.pause()
                app.conversation.history.append(
                    Message(
                        role="user",
                        content=(
                            "本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n"
                            "Current Goal: finish compact state support\n"
                            "Completed Work: summary fields added\n"
                            "Next Steps: enrich recall selector"
                        ),
                    )
                )

                result = await app._prefetch_relevant_memories("What should I do next?")

        assert result == ""
        assert "Current compacted session state:" in captured["query"]
        assert "Current Goal: finish compact state support" in captured["query"]
        assert "Completed Work: summary fields added" in captured["query"]
        assert "Next Steps: enrich recall selector" in captured["query"]

    @pytest.mark.asyncio
    async def test_clicking_tool_block_keeps_chat_input_focus(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            assert app.focused is chat_input
            await pilot.click("#tool-block")

            assert app.focused is chat_input

    @pytest.mark.asyncio
    async def test_clicking_tool_block_shows_change_preview(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            block = app.query_one("#tool-block")

            await pilot.click("#tool-block")

            assert block._collapsed is False
            assert "[Preview] edited: /tmp/demo.txt" in block._full_output
            assert "--- before" in block._full_output
            assert "+world" in block._full_output

    @pytest.mark.asyncio
    async def test_clicking_tool_group_keeps_chat_input_focus(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            assert app.focused is chat_input
            await pilot.click("#tool-group")

            assert app.focused is chat_input

    @pytest.mark.asyncio
    async def test_clicking_subagent_block_keeps_chat_input_focus(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            assert app.focused is chat_input
            await pilot.click("#subagent-block")

            assert app.focused is chat_input

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_focus_returns(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            await pilot.click("#tool-group")
            assert app.focused is chat_input

            await pilot.click("#chat-input")
            await pilot.press("a", "b", "c")

            assert app.focused is chat_input
            assert chat_input.text == "abc"

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_clicking_plain_static(self) -> None:
        async with _FocusRecoveryHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            await pilot.click("#plain-static")
            await pilot.click("#chat-input")
            await pilot.press("x", "y")

            assert app.focused is chat_input
            assert chat_input.text == "xy"

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_clicking_chat_area_in_mew_layout(self) -> None:
        async with _MewLayoutHarness().run_test() as pilot:
            app = pilot.app
            chat_input = app.query_one("#chat-input")

            await pilot.click("#chat-static")
            await pilot.click("#chat-input")
            await pilot.press("m", "e", "w")

            assert app.focused is chat_input
            assert chat_input.text == "mew"

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_clicking_chat_area_in_rockcoder_app(self) -> None:
        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input")

            await pilot.pause()
            await pilot.click("#chat-static")
            await pilot.click("#chat-input")
            await pilot.press("r", "e", "t", "u", "r", "n")

            assert app.focused is chat_input
            assert chat_input.text == "return"

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_clicking_user_bubble_in_rockcoder_app(self) -> None:
        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input")

            await pilot.pause()
            await pilot.click("#user-bubble")
            await pilot.click("#chat-input")
            await pilot.press("u", "s", "e", "r")

            assert app.focused is chat_input
            assert chat_input.text == "user"

    @pytest.mark.asyncio
    async def test_chat_input_accepts_typing_after_clicking_markdown_in_rockcoder_app(self) -> None:
        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input")

            await pilot.pause()
            await pilot.click("#ai-markdown")
            await pilot.click("#chat-input")
            await pilot.press("a", "i")

            assert app.focused is chat_input
            assert chat_input.text == "ai"

    @pytest.mark.asyncio
    async def test_clicking_chat_area_restores_chat_input_when_focus_is_none(self) -> None:
        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input")

            await pilot.pause()
            app.screen.set_focus(None)
            assert app.focused is None

            await pilot.click("#chat-static")

            assert app.focused is chat_input

    @pytest.mark.asyncio
    async def test_typing_after_chat_area_click_goes_into_chat_input(self) -> None:
        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input")

            await pilot.pause()
            await pilot.click("#chat-static")
            await pilot.press("o", "k")

            assert app.focused is chat_input
            assert chat_input.text == "ok"

    @pytest.mark.asyncio
    async def test_ask_user_widget_mounts_before_tool_future_completes(self) -> None:
        from rockcoder.askuser_dialog import InlineAskUserWidget
        from rockcoder.tools.ask_user import AskUserParams, AskUserTool, QuestionItem

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            tool = AskUserTool()
            task = asyncio.create_task(tool.execute(AskUserParams(questions=[
                QuestionItem(type="radio", name="choice", message="Pick one", options=["A"]),
            ])))
            for _ in range(10):
                await pilot.pause()
                if tool._pending_event is not None:
                    break

            assert tool._pending_event is not None
            await app._handle_askuser(tool._pending_event)
            assert app.query_one("#askuser-inline", InlineAskUserWidget)
            assert not task.done()
            tool._pending_event.future.set_result({"choice": "A"})
            await task

    @pytest.mark.asyncio
    async def test_permission_widget_mounts_while_permission_future_waits(self) -> None:
        from rockcoder.permission_dialog import InlinePermissionWidget
        from rockcoder.agent import PermissionRequest, PermissionResponse

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            future = asyncio.get_running_loop().create_future()
            request = PermissionRequest("Bash", "run command", future)
            await app._handle_permission_request(request)
            assert app.query_one("#perm-inline", InlinePermissionWidget)
            assert not future.done()
            future.set_result(PermissionResponse.DENY)
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_ask_user_widget_renders_basic_question(self) -> None:
        from rockcoder.askuser_dialog import InlineAskUserWidget

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-area")
            await chat.mount(
                InlineAskUserWidget([
                    {
                        "type": "radio",
                        "name": "choice",
                        "message": "Pick one",
                        "options": ["A"],
                    }
                ])
            )
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_provider_selection_focuses_chat_input(self) -> None:
        from rockcoder.app import ChatInput
        from rockcoder.config import ProviderConfig

        provider_a = ProviderConfig(
            name="a",
            protocol="openai",
            base_url="https://example.com",
            model="model-a",
        )
        provider_b = ProviderConfig(
            name="b",
            protocol="openai",
            base_url="https://example.com",
            model="model-b",
        )

        from rockcoder.app import RockCoderApp
        from rockcoder.permissions import PermissionMode

        class ProviderSelectHarness(RockCoderApp):
            CSS_PATH = str(Path(__file__).resolve().parents[1] / "rockcoder" / "styles.tcss")

        app = ProviderSelectHarness(
            providers=[provider_a, provider_b],
            permission_mode=PermissionMode.DEFAULT,
        )

        with patch("rockcoder.app.create_client", return_value=MagicMock()):
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.click("#provider-list", offset=(2, 1))
                await pilot.pause()

                chat_input = app.query_one("#chat-input", ChatInput)
                assert app._selected_provider is provider_a
                assert app.focused is chat_input
                assert app.query_one("#chat-area").display is True
                assert app.query_one("#input-area").display is True

    @pytest.mark.asyncio
    async def test_slash_menu_escape_restores_chat_input_focus(self) -> None:
        from rockcoder.app import ChatInput
        from rockcoder.commands.completion import CompletionPopup

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.insert("/")
            await pilot.pause()

            popup = app.query_one(CompletionPopup)
            assert popup.is_visible

            app.screen.set_focus(None)
            await pilot.press("escape")
            await pilot.pause()

            assert popup.is_visible is False
            assert app.focused is chat_input

    @pytest.mark.asyncio
    async def test_permission_widget_handles_keyboard_navigation(self) -> None:
        from rockcoder.agent import PermissionRequest, PermissionResponse
        from rockcoder.permission_dialog import InlinePermissionWidget

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            future = asyncio.get_running_loop().create_future()
            request = PermissionRequest("Bash", "run command", future)
            await app._handle_permission_request(request)
            widget = app.query_one("#perm-inline", InlinePermissionWidget)
            assert app.focused is widget

            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert future.done()
            assert future.result() == PermissionResponse.DENY

    @pytest.mark.asyncio
    async def test_permission_widget_keeps_keyboard_navigation_after_focus_is_lost(self) -> None:
        from rockcoder.agent import PermissionRequest, PermissionResponse
        from rockcoder.permission_dialog import InlinePermissionWidget

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            future = asyncio.get_running_loop().create_future()
            request = PermissionRequest("Bash", "run command", future)
            await app._handle_permission_request(request)
            widget = app.query_one("#perm-inline", InlinePermissionWidget)

            app.screen.set_focus(None)
            await pilot.press("down")
            await pilot.pause()
            assert widget._cursor == 1

            await pilot.press("enter")
            await pilot.pause()

            assert future.done()
            assert future.result() == PermissionResponse.ALLOW_ALWAYS

    @pytest.mark.asyncio
    async def test_permission_widget_accepts_number_shortcuts(self) -> None:
        from rockcoder.agent import PermissionRequest, PermissionResponse
        from rockcoder.permission_dialog import InlinePermissionWidget

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            future = asyncio.get_running_loop().create_future()
            request = PermissionRequest("Bash", "run command", future)
            await app._handle_permission_request(request)
            widget = app.query_one("#perm-inline", InlinePermissionWidget)
            assert app.focused is widget

            await pilot.press("3")
            await pilot.pause()

            assert future.done()
            assert future.result() == PermissionResponse.DENY

    @pytest.mark.asyncio
    async def test_permission_mode_command_persists_to_config(self, tmp_path: Path) -> None:
        from rockcoder.app import RockCoderApp
        from rockcoder.config import ProviderConfig, load_config
        from rockcoder.permissions import PermissionMode

        project = tmp_path / "project"
        project.mkdir()
        data_dir = project / ".rockcoder"
        data_dir.mkdir()
        (data_dir / "config.yaml").write_text(textwrap.dedent("""\
            providers:
              - name: base
                protocol: openai
                base_url: https://example.com
                model: gpt-4o
                api_key: sk-base
            permission_mode: default
        """), encoding="utf-8")

        class PermissionModeHarness(RockCoderApp):
            CSS_PATH = str(Path(__file__).resolve().parents[1] / "rockcoder" / "styles.tcss")

        provider = ProviderConfig(
            name="base",
            protocol="openai",
            base_url="https://example.com",
            model="gpt-4o",
        )
        app = PermissionModeHarness(
            providers=[provider],
            permission_mode=PermissionMode.DEFAULT,
        )

        with patch("rockcoder.app.create_client", return_value=MagicMock()), patch("rockcoder.commands.handlers.permission.Path.cwd", return_value=project):
            async with app.run_test() as pilot:
                await app._dispatch_command("/permission mode bypassPermissions")
                await pilot.pause()

        reloaded = load_config(data_dir / "config.yaml")
        assert reloaded.permission_mode == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_tool_use_event_mounts_ask_user_widget_before_tool_result(self) -> None:
        from rockcoder.agent import LoopComplete, ToolUseEvent
        from rockcoder.askuser_dialog import InlineAskUserWidget
        from rockcoder.tools.ask_user import AskUserEvent, AskUserTool

        app = _RockCoderAppHarness.build()
        async with app.run_test() as pilot:
            ask_tool = AskUserTool()
            app.registry.register(ask_tool)
            app.agent = MagicMock()
            app.agent.work_dir = "/test"
            app.agent.memory_recall_task = None
            app.agent._memory_recall_consumed = False
            app.agent.total_input_tokens = 0
            app.agent.total_output_tokens = 0
            app.agent.plan_mode = False

            loop = asyncio.get_running_loop()
            ask_tool._pending_event = AskUserEvent(
                questions=[
                    {
                        "type": "radio",
                        "name": "choice",
                        "message": "Pick one",
                        "options": ["A"],
                    }
                ],
                future=loop.create_future(),
            )

            async def fake_run(_conversation):
                yield ToolUseEvent(
                    tool_name="AskUserQuestion",
                    tool_id="tool_1",
                    arguments={
                        "questions": [
                            {
                                "type": "radio",
                                "name": "choice",
                                "message": "Pick one",
                                "options": ["A"],
                            }
                        ]
                    },
                )
                yield LoopComplete(total_turns=1)

            app.agent.run = fake_run
            app.session_manager = MagicMock()
            app.session = MagicMock()
            await app._send_message("trigger ask")
            await pilot.pause()

            assert app.query_one("#askuser-inline", InlineAskUserWidget)
            assert app._pending_askuser_event is ask_tool._pending_event
