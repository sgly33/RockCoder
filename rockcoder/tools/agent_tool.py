 
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from rockcoder.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from rockcoder.agent import Agent
    from rockcoder.agents.loader import AgentLoader
    from rockcoder.agents.task_manager import TaskManager
    from rockcoder.agents.trace import TraceManager
    from rockcoder.client import LLMClient

log = logging.getLogger(__name__)


class AgentToolParams(BaseModel):
    prompt: str
    description: str
    subagent_type: str | None = None
    model: str | None = None
    run_in_background: bool = False
    name: str | None = None
    isolation: str | None = None
    team_name: str | None = Field(
        default=None,
        description=(
            "REQUIRED when creating team members. Spawns the agent as a long-running "
            "teammate under this team (created via TeamCreate). Unlike regular sub-agents, "
            "team members run in their own terminal, persist after the lead returns, and "
            "communicate with each other via SendMessage. Without team_name the agent "
            "runs as a one-shot sub-agent that blocks and returns inline."
        ),
    )


PERMISSION_MODE_MAP = {
    "default": "DEFAULT",
    "acceptEdits": "ACCEPT_EDITS",
    "bypassPermissions": "BYPASS",
}


@dataclass
class SubAgentLaunchResult:
    task_id: str | None
    agent_name: str
    agent_type: str
    output: str
    is_background: bool


TEAMMATE_ADDENDUM = (
    "\n\nIMPORTANT: You are running as an agent in a team.\n"
    "Just writing a response in text is not visible to others\n"
    "on your team - you MUST use the SendMessage tool.\n"
    "The user interacts primarily with the team lead.\n"
    "Your work is coordinated through the task system\n"
    "and teammate messaging.\n\n"
    "You are working in an isolated Git worktree. "
    "All file paths you use MUST be relative to your current working directory. "
    "Do NOT use absolute paths from the original project — they are outside your sandbox and will be rejected."
)


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Launch a sub-agent to handle a task in an isolated context. "
        "Use subagent_type to select a predefined agent type (e.g. Explore, Plan, general-purpose), "
        "or leave it empty to fork the current conversation. "
        "Use team_name to spawn a teammate in an existing team."
    )
    params_model = AgentToolParams
    category = "command"
    is_concurrency_safe = False


    def __init__(
        self,
        agent_loader: AgentLoader,
        task_manager: TaskManager,
        trace_manager: TraceManager,
        parent_agent: Agent,
        enable_fork: bool = False,
        provider_config: Any = None,
        worktree_manager: Any = None,
        team_manager: Any = None,
    ) -> None:
        self._agent_loader = agent_loader
        self._task_manager = task_manager
        self._trace_manager = trace_manager
        self._parent_agent = parent_agent
        self._enable_fork = enable_fork
        self._provider_config = provider_config
        self._worktree_manager = worktree_manager
        self._team_manager = team_manager

    async def execute(self, params: BaseModel) -> ToolResult:
        p: AgentToolParams = params  # type: ignore[assignment]

        if p.team_name:
            return await self._execute_as_teammate(p)

        isolation = ""
        if p.subagent_type:
            defn = self._agent_loader.get(p.subagent_type)
            if defn and defn.isolation:
                isolation = defn.isolation

        if isolation == "worktree":
            return await self._execute_with_worktree(p)

        launch_result = await self.launch_subagent(
            prompt=p.prompt,
            description=p.description,
            subagent_type=p.subagent_type,
            model=p.model,
            run_in_background=p.run_in_background,
            name=p.name,
        )
        return ToolResult(output=launch_result.output)

    async def launch_subagent(
        self,
        *,
        prompt: str,
        description: str,
        subagent_type: str | None,
        model: str | None = None,
        run_in_background: bool = True,
        name: str | None = None,
    ) -> SubAgentLaunchResult:
        from rockcoder.agents.fork import ForkError, build_forked_messages
        from rockcoder.agents.parser import AgentDef
        from rockcoder.agents.tool_filter import resolve_agent_tools
        from rockcoder.agent import Agent as AgentClass
        from rockcoder.conversation import ConversationManager
        from rockcoder.permissions import (
            DangerousCommandDetector,
            PathSandbox,
            PermissionChecker,
            PermissionMode,
            RuleEngine,
        )

        definition: AgentDef | None = None
        conversation: ConversationManager

        if subagent_type:
            definition = self._agent_loader.get(subagent_type)
            if definition is None:
                available = ", ".join(t for t, _ in self._agent_loader.list_agents())
                raise ValueError(
                    f"Unknown agent type: '{subagent_type}'. Available types: {available}"
                )
            conversation = ConversationManager()
        else:
            if not self._enable_fork:
                raise ValueError(
                    "Fork mode is not enabled. Set 'enable_fork: true' in config.yaml to use fork, or specify a subagent_type parameter."
                )
            try:
                parent_conv = getattr(self._parent_agent, '_current_conversation', None)
                if parent_conv is None:
                    raise ValueError("Cannot fork: no active conversation in parent agent.")
                conversation = build_forked_messages(parent_conv, prompt)
            except ForkError as e:
                raise ValueError(str(e)) from e

            definition = AgentDef(
                agent_type="fork",
                when_to_use="Forked from parent agent",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        params = AgentToolParams(
            prompt=prompt,
            description=description,
            subagent_type=subagent_type,
            model=model,
            run_in_background=run_in_background,
            name=name,
        )

        client = self._select_llm(params, definition)

        is_background = run_in_background or definition.background
        if self._enable_fork and subagent_type is None:
            is_background = True

        _base_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        filtered_registry = resolve_agent_tools(
            _base_registry, definition, is_background
        )

        pm_str = definition.permission_mode
        pm_enum = getattr(
            PermissionMode,
            PERMISSION_MODE_MAP.get(pm_str, "DEFAULT"),
            PermissionMode.DEFAULT,
        )
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(self._parent_agent.work_dir),
            rule_engine=RuleEngine(),
            mode=pm_enum,
        )

        sub_agent = AgentClass(
            client=client,
            registry=filtered_registry,
            protocol=self._parent_agent.protocol,
            work_dir=self._parent_agent.work_dir,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=definition.system_prompt,
            instruction_warnings=self._parent_agent.instruction_warnings,
            hook_engine=self._parent_agent.hook_engine,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id

        if subagent_type is None:
            from rockcoder.context import clone_replacement_state
            sub_agent.replacement_state = clone_replacement_state(
                self._parent_agent.replacement_state
            )

        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=sub_agent.trace_id,
        )
        sub_agent.agent_id = trace_node.agent_id

        agent_name = name or subagent_type or f"agent-{trace_node.agent_id}"
        is_fork = subagent_type is None

        if is_background:
            if is_fork:
                sub_agent._fork_conversation = conversation
            task_id = self._task_manager.launch(
                agent=sub_agent,
                task="" if is_fork else prompt,
                name=agent_name,
                fork_conversation=conversation if is_fork else None,
            )
            output = (
                f"Sub-agent launched in background.\n"
                f"Task ID: {task_id}\n"
                f"Agent: {agent_name}\n"
                f"Type: {definition.agent_type}\n"
                f"The system will notify automatically when it completes.\n"
                f"Do NOT wait, sleep, or poll. Report the task ID to the user and move on."
            )
            return SubAgentLaunchResult(
                task_id=task_id,
                agent_name=agent_name,
                agent_type=definition.agent_type,
                output=output,
                is_background=True,
            )

        try:
            if is_fork:
                result_text = await sub_agent.run_to_completion("", conversation)
            else:
                result_text = await sub_agent.run_to_completion(prompt)
        except Exception as e:
            self._trace_manager.complete(trace_node.agent_id, "failed")
            raise RuntimeError(f"Sub-agent failed: {e}") from e

        self._trace_manager.update(
            trace_node.agent_id,
            input_tokens=sub_agent.total_input_tokens,
            output_tokens=sub_agent.total_output_tokens,
        )
        self._trace_manager.complete(trace_node.agent_id, "completed")

        output = result_text or "(sub-agent returned no output)"
        return SubAgentLaunchResult(
            task_id=None,
            agent_name=agent_name,
            agent_type=definition.agent_type,
            output=output,
            is_background=False,
        )

    async def _execute_as_teammate(self, p: AgentToolParams) -> ToolResult:
        if self._team_manager is None:
            return ToolResult(output="TeamManager not configured.", is_error=True)
        if self._worktree_manager is None:
            return ToolResult(output="WorktreeManager not configured for team spawn.", is_error=True)

        from rockcoder.agents.fork import ForkError, build_forked_messages
        from rockcoder.agents.parser import AgentDef
        from rockcoder.agents.tool_filter import build_teammate_tools
        from rockcoder.agent import Agent as AgentClass
        from rockcoder.conversation import ConversationManager
        from rockcoder.permissions import (
            DangerousCommandDetector,
            PathSandbox,
            PermissionChecker,
            PermissionMode,
            RuleEngine,
        )
        from rockcoder.teams.models import TeammateInfo
        from rockcoder.teams.registry import AgentNameRegistry

        team = self._team_manager.get_team(p.team_name)
        if team is None:
            return ToolResult(output=f"Team '{p.team_name}' not found. Create it first with TeamCreate.", is_error=True)

        base_name = p.name or p.subagent_type or "worker"
        existing_names = {m.name for m in team.members}
        teammate_name = base_name
        if teammate_name in existing_names:
            counter = 2
            while f"{base_name}-{counter}" in existing_names:
                counter += 1
            teammate_name = f"{base_name}-{counter}"

        # 1. 加载 agent 定义
        definition: AgentDef
        conversation: ConversationManager | None = None
        is_fork = False

        if p.subagent_type:
            defn = self._agent_loader.get(p.subagent_type)
            if defn is None:
                return ToolResult(
                    output=f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available: {', '.join(t for t, _ in self._agent_loader.list_agents())}",
                    is_error=True,
                )
            definition = defn
        else:
            if self._enable_fork:
                try:
                    parent_conv = getattr(self._parent_agent, '_current_conversation', None)
                    if parent_conv is None:
                        return ToolResult(output="Cannot fork: no active conversation.", is_error=True)
                    conversation = build_forked_messages(parent_conv, p.prompt)
                    is_fork = True
                except ForkError as e:
                    return ToolResult(output=str(e), is_error=True)

            definition = AgentDef(
                agent_type="teammate",
                when_to_use="Team member",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        # 2. 创建 worktree
        wt_name = f"team-{p.team_name}/{teammate_name}"
        try:
            wt = await self._worktree_manager.create(wt_name, "HEAD")
        except Exception as e:
            return ToolResult(output=f"Failed to create worktree for teammate: {e}", is_error=True)

        # 3. 选择 LLM
        client = self._select_llm(p, definition)

        # 4. 检测后端类型
        backend = self._team_manager.detect_backend()

        # 5. 构建队友的工具集
        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=self._parent_agent.trace_id or self._parent_agent.agent_id,
        )
        agent_id = trace_node.agent_id

        _has_full = getattr(self._parent_agent, '_full_registry', None) is not None
        full_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        _full_tools = [t.name for t in full_registry.list_tools()]
        log.info(
            "[teammate] has_full_registry=%s full_tools=%d names=%s backend=%s def_tools=%s def_disallowed=%s",
            _has_full, len(_full_tools), _full_tools,
            backend.value,
            getattr(definition, 'tools', []),
            getattr(definition, 'disallowed_tools', []),
        )
        teammate_registry = build_teammate_tools(
            parent_registry=full_registry,
            team_manager=self._team_manager,
            team_name=p.team_name,
            agent_id=agent_id,
            agent_name=teammate_name,
            backend_type=backend.value,
            definition=definition,
        )
        _tm_tools = [t.name for t in teammate_registry.list_tools()]
        log.info("[teammate] result_tools=%d names=%s", len(_tm_tools), _tm_tools)

        # 6. 创建子 agent 并附加队友专属指令
        instructions = (definition.system_prompt or "") + TEAMMATE_ADDENDUM

        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(wt.path),
            rule_engine=RuleEngine(),
            mode=PermissionMode.BYPASS,
        )

        sub_agent = AgentClass(
            client=client,
            registry=teammate_registry,
            protocol=self._parent_agent.protocol,
            work_dir=wt.path,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=instructions,
            instruction_warnings=self._parent_agent.instruction_warnings,
            hook_engine=self._parent_agent.hook_engine,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id
        sub_agent.agent_id = agent_id
        sub_agent.team_name = p.team_name
        sub_agent._team_manager = self._team_manager

        # 7. 注册名称和成员信息
        AgentNameRegistry.instance().register(teammate_name, agent_id)

        member = TeammateInfo(
            name=teammate_name,
            agent_id=agent_id,
            agent_type=definition.agent_type,
            model=p.model or definition.model,
            worktree_path=wt.path,
            backend_type=backend.value,
            is_active=True,
        )
        self._team_manager.register_member(p.team_name, member)

        # 获取team的mailbox用于长驻teammate通信
        mailbox = self._team_manager.get_mailbox(p.team_name)

        if mailbox is None:
            # 降级：mailbox不可用时使用传统单次执行模式
            log.warning("Mailbox not available for team %s, using single-shot mode", p.team_name)
            task_id = self._task_manager.launch(
                agent=sub_agent,
                task="" if is_fork else p.prompt,
                name=teammate_name,
                fork_conversation=conversation if is_fork else None,
            )
            backend_note = "single-shot (mailbox unavailable)"
        else:
            # 使用长驻teammate模式
            from rockcoder.teams.spawn_inprocess import spawn_inprocess_teammate

            handle = spawn_inprocess_teammate(
                agent=sub_agent,
                prompt="" if is_fork else p.prompt,
                name=teammate_name,
                conversation=conversation if is_fork else None,
                member=member,
                team_name=p.team_name,
                mailbox=mailbox,
            )

            task_id = self._task_manager.adopt_teammate_handle(
                handle=handle,
                agent=sub_agent,
                task_description=p.prompt,
                name=teammate_name,
            )
            backend_note = "long-running (idle after completion, responds to SendMessage)"

        return ToolResult(
            output=(
                f"Teammate '{teammate_name}' spawned in team '{p.team_name}'.\n"
                f"Agent ID: {agent_id}\n"
                f"Backend: {backend_note}\n"
                f"Worktree: {wt.path}\n"
                f"Task ID: {task_id}\n"
                "The system will notify when initial task completes."
            )
        )


    def _select_llm(
        self,
        params: AgentToolParams,
        definition: AgentDef,
    ) -> LLMClient:
        from rockcoder.agents.parser import AgentDef

        model_override = params.model or (
            definition.model if definition.model != "inherit" else None
        )

        if model_override and model_override != "inherit":
            client = self._create_client_for_model(model_override)
            if client is not None:
                return client

        return self._parent_agent.client


    async def _execute_with_worktree(self, p: AgentToolParams) -> ToolResult:
        if self._worktree_manager is None:
            return ToolResult(
                output="Worktree isolation is not available: WorktreeManager not configured.",
                is_error=True,
            )

        from rockcoder.agents.parser import AgentDef
        from rockcoder.agents.tool_filter import resolve_agent_tools
        from rockcoder.agent import Agent as AgentClass
        from rockcoder.conversation import ConversationManager
        from rockcoder.permissions import (
            DangerousCommandDetector,
            PathSandbox,
            PermissionChecker,
            PermissionMode,
            RuleEngine,
        )
        from rockcoder.worktree.integration import (
            build_worktree_notice,
            generate_worktree_name,
        )

        definition: AgentDef | None = None
        if p.subagent_type:
            definition = self._agent_loader.get(p.subagent_type)
            if definition is None:
                return ToolResult(
                    output=f"Unknown agent type: '{p.subagent_type}'. "
                    f"Available types: {', '.join(t for t, _ in self._agent_loader.list_agents())}",
                    is_error=True,
                )
        else:
            definition = AgentDef(
                agent_type="worktree-agent",
                when_to_use="Isolated worktree agent",
                system_prompt="",
                disallowed_tools=[],
                model="inherit",
                max_turns=self._parent_agent.max_iterations,
                permission_mode="bypassPermissions",
                source="builtin",
            )

        wt_name = generate_worktree_name()
        try:
            wt = await self._worktree_manager.create(wt_name, "HEAD")
        except Exception as e:
            return ToolResult(
                output=f"Failed to create worktree: {e}",
                is_error=True,
            )

        notice = build_worktree_notice(self._parent_agent.work_dir, wt.path)
        task = notice + "\n\n" + p.prompt

        client = self._select_llm(p, definition)

        _base_registry = getattr(self._parent_agent, '_full_registry', None) or self._parent_agent.registry
        filtered_registry = resolve_agent_tools(
            _base_registry, definition, False
        )

        pm_str = definition.permission_mode
        pm_enum = getattr(
            PermissionMode,
            PERMISSION_MODE_MAP.get(pm_str, "DEFAULT"),
            PermissionMode.DEFAULT,
        )
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(wt.path),
            rule_engine=RuleEngine(),
            mode=pm_enum,
        )

        sub_agent = AgentClass(
            client=client,
            registry=filtered_registry,
            protocol=self._parent_agent.protocol,
            work_dir=wt.path,
            max_iterations=definition.max_turns,
            permission_checker=checker,
            context_window=self._parent_agent.context_window,
            instructions_content=definition.system_prompt,
            instruction_warnings=self._parent_agent.instruction_warnings,
            hook_engine=self._parent_agent.hook_engine,
        )
        sub_agent.parent_id = self._parent_agent.agent_id
        sub_agent.trace_id = self._parent_agent.trace_id or self._parent_agent.agent_id

        trace_node = self._trace_manager.create(
            agent_type=definition.agent_type,
            parent_id=self._parent_agent.agent_id,
            trace_id=sub_agent.trace_id,
        )
        sub_agent.agent_id = trace_node.agent_id

        try:
            result_text = await sub_agent.run_to_completion(task)
        except Exception as e:
            self._trace_manager.complete(trace_node.agent_id, "failed")
            return ToolResult(
                output=f"Sub-agent in worktree failed: {e}",
                is_error=True,
            )

        self._trace_manager.update(
            trace_node.agent_id,
            input_tokens=sub_agent.total_input_tokens,
            output_tokens=sub_agent.total_output_tokens,
        )
        self._trace_manager.complete(trace_node.agent_id, "completed")

        cleanup = await self._worktree_manager.auto_cleanup(wt_name, wt.head_commit)
        if cleanup.kept:
            result_text = (result_text or "") + (
                f"\n[Worktree preserved at {cleanup.path}, branch {cleanup.branch}]"
            )

        return ToolResult(output=result_text or "(sub-agent returned no output)")


    def _create_client_for_model(self, model_alias: str) -> LLMClient | None:
        if self._provider_config is None:
            return None

        from rockcoder.client import create_client
        from rockcoder.config import ProviderConfig

        model_map = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-4-6-20250514",
            "opus": "claude-opus-4-6-20250514",
        }
        model_id = model_map.get(model_alias, model_alias)

        config = ProviderConfig(
            name=f"sub-{model_alias}",
            protocol=self._provider_config.protocol,
            base_url=self._provider_config.base_url,
            model=model_id,
            api_key=self._provider_config.api_key,
            context_window=self._provider_config.context_window,
        )
        try:
            return create_client(config)
        except Exception:
            return None
