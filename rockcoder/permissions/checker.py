 
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rockcoder.compat_paths import resolve_project_data_dir
from rockcoder.permissions.dangerous import DangerousCommandDetector, is_safe_command
from rockcoder.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from rockcoder.permissions.rules import RuleEngine, extract_content
from rockcoder.permissions.sandbox import PathSandbox
from rockcoder.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"})


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
        sandbox_enabled: bool = False,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""
        # OS 级沙箱是否启用（开启后命令类工具可自动放行，因为内核会兜底）
        self.sandbox_enabled = sandbox_enabled
        # Layer 4b: 会话级 allow-always 集合（内存中，不持久化）
        # 存放格式为 "ToolName:pattern"，用户选择 "don't ask again" 时记录
        self._session_allowed: set[str] = set()


    def add_session_allow(self, tool_name: str, content: str) -> None:
        """将工具+内容模式加入会话级放行集合（Layer 4b）。

        比持久化规则引擎优先级更高，但不写入磁盘——会话结束即消失。
        """
        key = f"{tool_name}:{content}"
        self._session_allowed.add(key)

    def _check_session_allowed(self, tool_name: str, content: str) -> bool:
        """检查是否匹配会话级放行记录。"""
        if not self._session_allowed:
            return False
        key = f"{tool_name}:{content}"
        if key in self._session_allowed:
            return True
        # 前缀匹配：已记录的 pattern 可能带通配尾缀
        for allowed in self._session_allowed:
            if allowed.endswith("*") and key.startswith(allowed[:-1]):
                return True
        return False

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """为 HITL 确认生成人类可读的操作描述（对齐 Go 版 ExtractContent + formatToolArgs）。"""
        content = extract_content(tool_name, arguments)
        if content:
            return content
        # 无法从标准字段提取时，拼接参数摘要
        parts = []
        for k, v in arguments.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts) if parts else tool_name


    def _allow_plan_mode(self, tool: Tool, content: str) -> Decision | None:
        if self.mode != PermissionMode.PLAN:
            return None
        if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
            return Decision(effect="allow", reason="Plan mode: allowed tool")
        if tool.name in ("WriteFile", "EditFile") and content:
            if self._is_plan_file(content):
                return Decision(effect="allow", reason="Plan mode: plan file write")
        return None

    def _deny_dangerous_command(self, tool: Tool, content: str) -> Decision | None:
        if tool.category != "command":
            return None
        hit, reason = self.detector.detect(content)
        if hit:
            return Decision(effect="deny", reason=f"危险命令拦截: {reason}")
        return None

    def _allow_safe_command(self, tool: Tool, content: str) -> Decision | None:
        if tool.category == "command" and is_safe_command(content):
            return Decision(effect="allow", reason="Safe read-only command")
        return None

    def _allow_session_rule(self, tool: Tool, content: str) -> Decision | None:
        if self._check_session_allowed(tool.name, content):
            return Decision(effect="allow", reason="会话级放行（session allow-always）")
        return None

    def _check_path_sandbox(self, tool: Tool, content: str) -> Decision | None:
        if tool.category not in ("read", "write") or not content:
            return None
        ok, reason = self.sandbox.check(content)
        if not ok and self.mode != PermissionMode.BYPASS:
            return Decision(effect="ask", reason=f"路径沙箱拦截: {reason}")
        return None

    def _check_rules(self, tool: Tool, content: str) -> Decision | None:
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "allow":
            return Decision(effect="allow", reason="权限规则放行")
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝")
        return None

    def _apply_mode_fallback(self, tool: Tool) -> Decision | None:
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 放行")
        if effect == "deny":
            return Decision(effect="deny", reason=f"权限模式 {self.mode.value} 拒绝")
        return None

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)

        # 0. Plan 模式例外：计划文件和少数工具允许直接通过
        decision = self._allow_plan_mode(tool, content)
        if decision is not None:
            return decision

        # 1. 硬拒绝：危险命令先拦
        decision = self._deny_dangerous_command(tool, content)
        if decision is not None:
            return decision

        # 2. 自动放行：安全只读命令不需要确认
        decision = self._allow_safe_command(tool, content)
        if decision is not None:
            return decision

        # 3. 会话级放行：本轮已确认过的同类操作直接放行
        decision = self._allow_session_rule(tool, content)
        if decision is not None:
            return decision

        # 4. OS 沙箱：文件类路径越界直接进入确认
        decision = self._check_path_sandbox(tool, content)
        if decision is not None:
            return decision

        # 5. 长期规则：本地 allow / deny 规则
        decision = self._check_rules(tool, content)
        if decision is not None:
            return decision

        # 6. 模式兜底：default / acceptEdits / plan / bypassPermissions
        decision = self._apply_mode_fallback(tool)
        if decision is not None:
            return decision

        # 7. 最终兜底：人工确认
        return Decision(effect="ask", reason="需要用户确认")


    def _is_plan_file(self, target_path: str) -> bool:
        if not target_path:
            return False

        def _has_data_plans_segment(path_str: str) -> bool:
            try:
                path = Path(path_str)
                parts = path.parts
                for data_dir_name in (".rockcoder",):
                    for index, part in enumerate(parts[:-1]):
                        if part == data_dir_name and index + 1 < len(parts) and parts[index + 1] == "plans":
                            return True
            except Exception:
                return False
            return False

        if not self.plan_file_path:
            return _has_data_plans_segment(target_path)
        try:
            abs_target = os.path.abspath(target_path)
            abs_plan = os.path.abspath(self.plan_file_path)
            if abs_target == abs_plan:
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        if _has_data_plans_segment(target_path):
            return True
        try:
            project_data_dir = resolve_project_data_dir(Path.cwd()).path
            abs_target = os.path.abspath(target_path)
            return os.path.commonpath([abs_target, str(project_data_dir / "plans")]) == str(project_data_dir / "plans")
        except Exception:
            return False
