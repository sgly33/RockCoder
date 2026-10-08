from __future__ import annotations

from pathlib import Path

from rockcoder.commands.registry import Command, CommandContext, CommandType
from rockcoder.compat_paths import resolve_project_data_dir, resolve_user_data_dir
from rockcoder.config import AppConfig, MCPServerConfig, load_config, save_config


def _load_or_create_config(config_path: Path) -> AppConfig:
    """加载现有配置或创建新的空配置"""
    if config_path.exists():
        return load_config(config_path)
    return AppConfig()


def _parse_scope(args: str) -> tuple[str, str]:
    """解析作用域标志，返回 (scope, remaining_args)"""
    if args.startswith("--user "):
        return ("user", args[7:])
    elif args.startswith("--project "):
        return ("project", args[10:])
    else:
        return ("project", args)  # 默认项目级


async def handle_mcp_config(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "list":
        try:
            # 显示合并后的完整配置
            user_path = resolve_user_data_dir(Path.home()).path / "config.yaml"
            project_path = ctx.agent._project_data_dir / "config.yaml"

            user_servers = []
            project_servers = []

            if user_path.exists():
                user_config = load_config(user_path)
                user_servers = user_config.mcp_servers or []

            if project_path.exists():
                project_config = load_config(project_path)
                project_servers = project_config.mcp_servers or []

            if not user_servers and not project_servers:
                ctx.ui.add_system_message("未配置 MCP Server")
                return

            lines = ["MCP Server 配置：\n"]

            if user_servers:
                lines.append("[用户级配置]")
                for i, srv in enumerate(user_servers, 1):
                    lines.append(f"{i}. {srv.name}")
                    if srv.is_stdio:
                        lines.append(f"   类型: stdio")
                        lines.append(f"   命令: {srv.command} {' '.join(srv.args)}")
                        if srv.env:
                            lines.append(f"   环境变量: {', '.join(srv.env.keys())}")
                    else:
                        lines.append(f"   类型: HTTP")
                        lines.append(f"   URL: {srv.url}")
                        if srv.headers:
                            lines.append(f"   Headers: {', '.join(srv.headers.keys())}")
                lines.append("")

            if project_servers:
                lines.append("[项目级配置]")
                for i, srv in enumerate(project_servers, 1):
                    lines.append(f"{i}. {srv.name}")
                    if srv.is_stdio:
                        lines.append(f"   类型: stdio")
                        lines.append(f"   命令: {srv.command} {' '.join(srv.args)}")
                        if srv.env:
                            lines.append(f"   环境变量: {', '.join(srv.env.keys())}")
                    else:
                        lines.append(f"   类型: HTTP")
                        lines.append(f"   URL: {srv.url}")
                        if srv.headers:
                            lines.append(f"   Headers: {', '.join(srv.headers.keys())}")
                lines.append("")

            mcp_mgr = getattr(ctx.ui, "mcp_manager", None)
            if mcp_mgr and hasattr(mcp_mgr, "_clients"):
                lines.append("已连接工具：")
                for name, client in mcp_mgr._clients.items():
                    tool_names = [
                        t.name for t in ctx.agent.registry.list_tools()
                        if t.name.startswith(f"mcp_{name}_")
                    ]
                    lines.append(f"  {name}: {len(tool_names)} 个工具")
                    for tn in tool_names[:5]:
                        short = tn.replace(f"mcp_{name}_", "")
                        lines.append(f"    - {short}")
                    if len(tool_names) > 5:
                        lines.append(f"    … 还有 {len(tool_names) - 5} 个")

            ctx.ui.add_system_message("\n".join(lines))
        except Exception as e:
            ctx.ui.add_system_message(f"读取配置失败: {e}")

    else:
        ctx.ui.add_system_message(
            "用法: /mcp-config <子命令>\n"
            "\n"
            "子命令:\n"
            "  list  - 列出所有 MCP Server 配置（用户级+项目级）\n"
            "\n"
            "配置文件位置:\n"
            f"  用户级: ~/.rockcoder/config.yaml\n"
            f"  项目级: .rockcoder/config.yaml\n"
            "\n"
            "手动编辑配置文件后请重启应用"
        )


MCP_CONFIG_COMMAND = Command(
    name="mcp-config",
    description="MCP Server 配置管理 (支持用户级和项目级)",
    usage="/mcp-config <子命令>",
    type=CommandType.LOCAL,
    handler=handle_mcp_config,
)
