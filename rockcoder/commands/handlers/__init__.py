

from __future__ import annotations

from rockcoder.commands.handlers.clear import CLEAR_COMMAND
from rockcoder.commands.handlers.compact import COMPACT_COMMAND
from rockcoder.commands.handlers.help import HELP_COMMAND
from rockcoder.commands.handlers.mcp import MCP_COMMAND
from rockcoder.commands.handlers.mcp_config import MCP_CONFIG_COMMAND
from rockcoder.commands.handlers.memory import MEMORY_COMMAND
from rockcoder.commands.handlers.permission import PERMISSION_COMMAND
from rockcoder.commands.handlers.plan import PLAN_COMMAND
from rockcoder.commands.handlers.review import REVIEW_COMMAND
from rockcoder.commands.handlers.sandbox import SANDBOX_COMMAND
from rockcoder.commands.handlers.session import RESUME_COMMAND, SESSION_COMMAND
from rockcoder.commands.handlers.skill import SKILL_COMMAND
from rockcoder.commands.handlers.rewind import REWIND_COMMAND
from rockcoder.commands.handlers.status import STATUS_COMMAND
from rockcoder.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    RESUME_COMMAND,
    MCP_COMMAND,
    MCP_CONFIG_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    REVIEW_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

