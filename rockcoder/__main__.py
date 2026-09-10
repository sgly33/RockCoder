

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rockcoder.compat_paths import resolve_project_data_dir
from rockcoder.config import ConfigError, load_config
from rockcoder.hooks import HookConfigError, HookEngine, load_hooks
from rockcoder.permissions import PermissionMode


def main() -> None:
    project_data_dir = resolve_project_data_dir(Path.cwd()).path
    project_data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=str(project_data_dir / "debug.log"),
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="rockcoder", description="RockCoder AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    from rockcoder.app import RockCoderApp
    from rockcoder.driver import NoAltScreenDriver

    app = RockCoderApp(
        providers=config.providers,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        enable_coordinator_mode=config.enable_coordinator_mode,
        driver_class=NoAltScreenDriver,
        sandbox_config=config.sandbox,
    )
    app.run()


if __name__ == "__main__":
    main()

