 

from __future__ import annotations

from rockcoder.commands.registry import Command, CommandContext, CommandType


async def handle_memory(ctx: CommandContext) -> None:
    mm = ctx.memory_manager
    if mm is None:
        ctx.ui.add_system_message("记忆管理器未初始化")
        return

    parts = ctx.args.split(None, 2)
    sub = parts[0] if parts else ""

    if sub in {"", "list"}:
        output = mm.get_display_text()
        warnings = mm.get_warnings()
        if warnings:
            output += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
            mm.clear_warnings()
        ctx.ui.add_system_message(output)
        return

    if sub == "show":
        if len(parts) < 2:
            ctx.ui.add_system_message("用法: /memory show <topic>")
            return
        try:
            ctx.ui.add_system_message(mm.show_topic(parts[1]))
        except FileNotFoundError:
            ctx.ui.add_system_message(f"未找到记忆主题: {parts[1]}")
        return

    if sub == "search":
        query = parts[1] if len(parts) > 1 else ""
        results = mm.search(query)
        if not results:
            ctx.ui.add_system_message("没有找到匹配的记忆。")
            return
        lines = [f"[{m.type or '?'}] {m.topic or m.name} — {m.description or m.name}" for m in results]
        ctx.ui.add_system_message("\n".join(lines))
        return

    if sub == "remember":
        if len(parts) < 2 or not parts[1].strip():
            ctx.ui.add_system_message("用法: /memory remember <fact>")
            return
        try:
            action, topic = mm.remember(parts[1])
        except ValueError as e:
            ctx.ui.add_system_message(f"无法保存记忆: {e}")
            return
        verb = "已记录记忆" if action == "created" else ("已更新记忆" if action == "updated" else "记忆已存在")
        ctx.ui.add_system_message(f"{verb}: {topic}")
        return

    if sub == "forget":
        if len(parts) < 2:
            ctx.ui.add_system_message("用法: /memory forget <topic>")
            return
        if mm.forget(parts[1]):
            ctx.ui.add_system_message(f"已删除记忆: {parts[1]}")
        else:
            ctx.ui.add_system_message(f"未找到记忆: {parts[1]}")
        return

    if sub == "invalidate":
        if len(parts) < 2:
            ctx.ui.add_system_message("用法: /memory invalidate <topic>")
            return
        if mm.invalidate(parts[1]):
            ctx.ui.add_system_message(f"已标记失效: {parts[1]}")
        else:
            ctx.ui.add_system_message(f"未找到记忆: {parts[1]}")
        return

    if sub == "clear":
        mm.clear()
        ctx.ui.add_system_message("所有动态记忆已清空。")
        return

    if sub == "edit":
        ctx.ui.add_system_message(
            f"编辑记忆文件：\n"
            f"  用户级目录: {mm.user_mem_dir}\n"
            f"  项目级目录: {mm.project_mem_dir}"
        )
        return

    ctx.ui.add_system_message(
        "用法: /memory [list | show <topic> | search <query> | remember <fact> | forget <topic> | invalidate <topic> | clear | edit]"
    )


MEMORY_COMMAND = Command(
    name="memory",
    description="记忆管理",
    usage="/memory [list | clear | edit]",
    type=CommandType.LOCAL,
    handler=handle_memory,
)

