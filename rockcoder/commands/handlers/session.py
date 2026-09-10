

from __future__ import annotations

from dataclasses import replace

from rockcoder.commands.registry import Command, CommandContext, CommandType
from rockcoder.conversation import ConversationManager


async def handle_session(ctx: CommandContext) -> None:
    sm = ctx.session_manager
    if sm is None:
        ctx.ui.add_system_message("会话管理器未初始化")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        if ctx.session:
            m = ctx.session.meta
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            ctx.ui.add_system_message(
                f"当前会话: {m.id}\n"
                f"  标题: {m.title or '(未命名)'}\n"
                f"  消息: {m.message_count} 条\n"
                f"  Token: {m.total_tokens:,}\n"
                f"  最后活跃: {ts}"
            )
        else:
            ctx.ui.add_system_message("当前没有活跃会话")
        return

    if sub == "list":
        metas = sm.list()
        if not metas:
            ctx.ui.add_system_message("没有已保存的会话。")
            return
        lines: list[str] = ["会话列表："]
        for m in metas[:10]:
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            title = m.title or "(未命名)"
            lines.append(f"  {m.id}  {title}  [{m.message_count} msgs, {ts}]")
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "resume":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            metas = sm.list()
            if not metas:
                ctx.ui.add_system_message("没有已保存的会话。")
                return
            lines: list[str] = ["可恢复的会话（使用 /session resume <id> 或 /session resume <序号>）："]
            for i, m in enumerate(metas[:15], 1):
                ts = m.last_active.strftime("%Y-%m-%d %H:%M")
                title = m.title or "(未命名)"
                lines.append(f"  {i}. {m.id}  {title}  ({m.message_count} msgs, {ts})")
            ctx.ui.add_system_message("\n".join(lines))
            ctx.config["_resume_candidates"] = [m.id for m in metas[:15]]
            return
        candidates = ctx.config.get("_resume_candidates", [])
        if session_id.isdigit():
            if not candidates:
                candidates = [m.id for m in sm.list()[:15]]
            idx = int(session_id) - 1
            if 0 <= idx < len(candidates):
                session_id = candidates[idx]
        result = sm.resume(session_id)
        if result is None:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")
            return
        if ctx.session:
            ctx.session.close()
        ctx.config["set_session"](result.session)
        conv = ConversationManager()
        for msg in result.messages:
            conv.history.append(msg)
        ctx.config["set_conversation"](conv)
        if ctx.agent:
            ctx.agent._loop_count = 0
        await ctx.config["render_restored"](result.messages)
        ctx.ui.add_system_message(
            f"会话已恢复: {session_id} ({result.session.meta.message_count} msgs)"
        )


    elif sub == "new":
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
        ctx.config["clear_chat"]()
        ctx.ui.add_system_message("已切换到新会话，已进入草稿状态")

    elif sub == "delete":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            ctx.ui.add_system_message("用法: /session delete <id>")
            return
        if ctx.session and ctx.session.session_id == session_id:
            ctx.ui.add_system_message("不能删除当前活跃的会话。")
            return
        if sm.delete(session_id):
            ctx.ui.add_system_message(f"会话已删除: {session_id}")
        else:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")


    else:
        ctx.ui.add_system_message(
            "用法: /session [list | resume <id> | new | delete <id>]"
        )


async def handle_resume(ctx: CommandContext) -> None:
    forwarded_args = f"resume {ctx.args}".strip()
    await handle_session(replace(ctx, args=forwarded_args))


SESSION_COMMAND = Command(
    name="session",
    description="会话管理",
    usage="/session [list | resume <id> | new | delete <id>]",
    type=CommandType.LOCAL,
    handler=handle_session,
)


RESUME_COMMAND = Command(
    name="resume",
    description="恢复已保存会话",
    usage="/resume [id|index]",
    type=CommandType.LOCAL,
    handler=handle_resume,
)

