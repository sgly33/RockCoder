from __future__ import annotations

from typing import TYPE_CHECKING

from rockcoder.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from rockcoder.agents.loader import AgentLoader
    from rockcoder.tools.agent_tool import AgentTool


_USAGE = "用法: /subagent [list|run <type> <prompt>]"


def create_subagent_handler(agent_tool: AgentTool, agent_loader: AgentLoader):
    async def handler(ctx: CommandContext) -> None:
        args = ctx.args.strip()
        parts = args.split(maxsplit=2) if args else []
        subcmd = parts[0].lower() if parts else "list"

        if subcmd == "list":
            catalog = agent_loader.list_agents()
            if not catalog:
                ctx.ui.add_system_message("当前没有可用的 sub-agent 类型")
                return

            lines = ["可用 sub-agent 类型:"]
            for agent_type, when_to_use in catalog:
                lines.append(f"  {agent_type:<20} {when_to_use}")
            ctx.ui.add_system_message("\n".join(lines))
            return

        if subcmd != "run":
            ctx.ui.add_system_message(_USAGE)
            return

        if len(parts) < 3:
            ctx.ui.add_system_message("用法: /subagent run <type> <prompt>")
            return

        subagent_type = parts[1].strip()
        prompt = parts[2].strip()
        if not prompt:
            ctx.ui.add_system_message("用法: /subagent run <type> <prompt>")
            return

        catalog = {name: desc for name, desc in agent_loader.list_agents()}
        if subagent_type not in catalog:
            available = ", ".join(sorted(catalog)) or "(none)"
            ctx.ui.add_system_message(
                f"未知 sub-agent 类型: {subagent_type}\n可用类型: {available}"
            )
            return

        result = await agent_tool.launch_subagent(
            prompt=prompt,
            description=prompt[:80],
            subagent_type=subagent_type,
            run_in_background=True,
            name=None,
        )
        ctx.ui.add_system_message(result.output)

    return handler


def create_subagent_command(agent_tool: AgentTool, agent_loader: AgentLoader) -> Command:
    return Command(
        name="subagent",
        description="启动或查看后台 sub-agent（/subagent list, /subagent run）",
        type=CommandType.LOCAL,
        handler=create_subagent_handler(agent_tool, agent_loader),
        aliases=["agent"],
        usage="/subagent [list|run <type> <prompt>]",
    )
