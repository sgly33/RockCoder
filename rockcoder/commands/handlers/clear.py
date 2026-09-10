

from __future__ import annotations

from rockcoder.commands.registry import Command, CommandContext, CommandType
from rockcoder.conversation import ConversationManager


async def handle_clear(ctx: CommandContext) -> None:
    if ctx.session:
        ctx.session.close()

    reset_session = ctx.config.get("reset_session")
    if reset_session is not None:
        reset_session()
    else:
        ctx.config["set_session"](None)

    ctx.config["set_conversation"](ConversationManager())

    if ctx.agent:
        ctx.agent._loop_count = 0
        ctx.agent.clear_active_skills()

    ctx.config["clear_chat"]()
    ctx.ui.refresh_status()
    ctx.ui.add_system_message("对话已清除，已进入新会话草稿状态")


CLEAR_COMMAND = Command(
    name="clear",
    description="清除对话历史",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)

