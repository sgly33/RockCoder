 

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from rockcoder.compat_paths import resolve_project_data_dir, resolve_user_data_dir

MAX_INCLUDE_DEPTH = 5
_INSTRUCTION_FILENAMES = {
    "RockCoder.md",
    "RockCoder.local.md",
}


@dataclass
class InstructionLoadResult:
    content: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# @include 指令格式（对齐 Go 版）
# ---------------------------------------------------------------------------
# 支持以下格式：
#   @./relative/path  @../relative/path  @~/home/path  @/absolute/path
# 其他 @-token（如 @username）被忽略，不视为 include 指令。
# 旧的 "@include path" 格式仍保留兼容。


def _parse_include(trimmed: str) -> str:
    """解析一行文本，提取 @include 路径。

    对齐 Go 版 parseInclude：支持 @./path @../path @~/path @/path 语法，
    以及旧格式 "@include path"。返回空字符串表示该行不是 include 指令。
    """
    # 旧格式兼容：@include <path>
    if trimmed.startswith("@include "):
        return trimmed[len("@include "):].strip()

    # 新格式：@./path, @../path, @~/path, @/abs/path
    if not trimmed.startswith("@") or trimmed.startswith("@@"):
        return ""
    rest = trimmed[1:]  # 去掉 @
    if not rest:
        return ""
    # 包含空白字符则不是 include 指令（如 @username 等普通文本）
    if " " in rest or "\t" in rest:
        return ""
    if rest.startswith("./") or rest.startswith("../") or rest.startswith("~/") or rest.startswith("/"):
        return rest
    return ""


def _resolve_include(path: str, base_dir: Path) -> Path:
    """将 include 路径解析为绝对路径。

    对齐 Go 版 resolveInclude：~/ 展开为 home，相对路径基于 base_dir 解析。
    """
    if path.startswith("~/"):
        return Path.home() / path[2:]
    if os.path.isabs(path):
        return Path(path)
    return base_dir / path


def process_includes(
    content: str,
    base_dir: Path,
    project_root: Path,
    depth: int = 0,
    seen: set[str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """展开 @include 指令，对齐 Go 版 expandIncludes。

    - 循环检测：通过 seen 集合记录已包含文件的绝对路径，防止 A→B→A 无限递归
    - 代码块跳过：``` 围栏代码块内的 @include 不展开
    - 深度限制：最多递归 MAX_INCLUDE_DEPTH 层
    """
    if depth > MAX_INCLUDE_DEPTH:
        return content

    if seen is None:
        seen = set()

    lines = content.split("\n")
    result: list[str] = []
    in_code = False  # 追踪是否处于 ``` 围栏代码块内

    for line in lines:
        stripped = line.strip()

        # 检测围栏代码块边界
        if stripped.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue

        # 代码块内不展开 include 指令
        if not in_code:
            include_path = _parse_include(stripped)
            if include_path:
                resolved = _resolve_include(include_path, base_dir)
                try:
                    abs_str = str(resolved.resolve())
                except OSError:
                    result.append(line)
                    continue

                # 循环检测：已包含过的文件跳过
                if abs_str in seen:
                    result.append(line)
                    continue

                if not resolved.exists() or not resolved.is_file():
                    if include_path.startswith("../") or include_path.startswith("/") or include_path.startswith("~/"):
                        result.append(f"<!-- skipped: file not found: {include_path} -->")
                    continue

                try:
                    included = resolved.read_text(encoding="utf-8")
                except OSError as exc:
                    if warnings is not None:
                        warnings.append(f"Could not read included instruction file {abs_str}: {exc}")
                    continue

                seen.add(abs_str)
                result.append(f"<!-- included from {include_path} -->")
                result.append(
                    process_includes(
                        included, resolved.parent, project_root, depth + 1, seen, warnings
                    )
                )
                continue

        result.append(line)

    return "\n".join(result)


def is_instruction_file(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.name in _INSTRUCTION_FILENAMES


def guard_instruction_write(
    path: str | Path,
    *,
    explicit_user_request: bool = False,
) -> None:
    if is_instruction_file(path) and not explicit_user_request:
        raise PermissionError(
            "instruction file is user-owned and requires an explicit user request to modify"
        )


def _find_git_root(start: Path) -> Path | None:
    """从 start 向上查找 .git 目录，返回 git 仓库根目录。"""
    cur = start.resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def _project_instruction_dirs(work_dir: Path) -> list[Path]:
    """返回从 git root 到 work_dir 的所有目录（对齐 Go 版 projectInstructionDirs）。

    如果 work_dir 不在 git 仓库内，只返回 [work_dir]。
    """
    abs_dir = work_dir.resolve()
    root = _find_git_root(abs_dir)
    if root is None:
        return [abs_dir]

    dirs: list[Path] = []
    cur = abs_dir
    while True:
        dirs.insert(0, cur)
        if cur == root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return dirs


def load_instructions(project_root: str) -> InstructionLoadResult:
    """发现并拼接项目和用户指令文件。

    发现顺序（低优先级在前，高优先级在后）：
    1. 用户全局 RockCoder.md
    2. 项目目录链上的 RockCoder.md
    3. 本地覆盖文件

    旧版项目指令文件由显式迁移流程转换为 RockCoder.md，正常加载路径不再读取旧品牌文件。
    """
    root = Path(project_root).resolve()
    home = Path.home()
    seen: set[str] = set()  # 用于文件去重
    sources: list[tuple[str, str]] = []  # (label, content)
    warnings: list[str] = []

    def _add(path: Path) -> None:
        """尝试加载一个指令文件，处理 include 展开。"""
        try:
            abs_path = path.resolve()
            abs_str = str(abs_path)
        except OSError:
            return
        if abs_str in seen:
            return
        if not abs_path.exists() or not abs_path.is_file():
            return
        try:
            data = abs_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Could not read {abs_str}: {exc}")
            return
        seen.add(abs_str)
        # 每个文件独立的 include seen 集合，但共享全局文件去重
        include_seen: set[str] = {abs_str}
        content = process_includes(data, abs_path.parent, root, 0, include_seen, warnings)

        # 生成标签：尽量用相对路径
        try:
            label = str(abs_path.relative_to(root))
        except ValueError:
            label = abs_str
        sources.append((label, content.rstrip("\n")))

    user_data_dir = resolve_user_data_dir(home).path
    project_data_dir = resolve_project_data_dir(root).path

    # 1. 用户全局 RockCoder.md
    _add(user_data_dir / "RockCoder.md")

    # 2. 项目目录链上的 RockCoder.md
    for d in _project_instruction_dirs(root):
        _add(d / "RockCoder.md")

    # 3. 当前目录本地覆盖
    _add(root / "RockCoder.local.md")

    if not sources:
        return InstructionLoadResult(warnings=warnings)

    parts = [f"Contents of {label}:\n\n{content}" for label, content in sources]
    return InstructionLoadResult(
        content="\n\n---\n\n".join(parts),
        warnings=warnings,
    )

