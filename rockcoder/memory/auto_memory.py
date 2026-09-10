 

"""自动记忆管理器（对齐 Go 版 memory.Manager + memdir + paths）。

使用独立 .md 文件 + frontmatter + MEMORY.md 索引的存储格式，
替代旧版集中式 memories.md。每条记忆存为一个文件，MEMORY.md
只保存索引指针。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from rockcoder.compat_paths import resolve_project_data_dir, resolve_user_data_dir
from rockcoder.conversation import ConversationManager, Message

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 记忆索引文件名
ENTRYPOINT_NAME = "MEMORY.md"

# 四种记忆类型（对齐 Go 版 MemoryType）
VALID_TYPES = {"user", "feedback", "project", "reference"}

# 记忆类型到存储目录的路由：user/feedback → 用户级，project/reference → 项目级
_USER_LEVEL_TYPES = {"user", "feedback"}
_PROJECT_LEVEL_TYPES = {"project", "reference"}

DEFAULT_TOPIC_FILE = "project-patterns.md"
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "user-preferences.md": ("偏好", "喜欢", "习惯", "协作", "风格", "测试"),
    "debugging.md": ("debug", "调试", "排查", "故障", "问题", "错误"),
    "workflows.md": ("workflow", "流程", "步骤", "命令", "发布", "构建"),
    "project-patterns.md": ("项目", "架构", "约定", "模式", "目录", "设计"),
}
SENSITIVE_PATTERNS = (
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
)
EXPLICIT_MEMORY_PATTERNS = (
    re.compile(r"^\s*请?记住这件事[：:，,\s]*(?P<fact>.+?)\s*$"),
    re.compile(r"^\s*请?记住[：:，,\s]*(?P<fact>.+?)\s*$"),
)
ONE_OFF_MEMORY_PATTERNS = (
    re.compile(r"今天|明天|后天|下午|晚上|早上|待会|稍后|临时|这次|本次"),
    re.compile(r"\b\d{1,2}[:：点]\d{0,2}\b"),
    re.compile(r"开会|会议|提醒|截止|deadline", re.IGNORECASE),
)

# MEMORY.md 截断限制
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000

# frontmatter 正则
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# 路径工具函数（对齐 Go 版 paths.go）
# ---------------------------------------------------------------------------

def get_auto_mem_path(project_root: str) -> str:
    """返回项目级记忆目录路径：<projectRoot>/.rockcoder/memory/。"""
    override = os.environ.get("ROCKCODER_REMOTE_MEMORY_DIR", "")
    if override:
        return override.rstrip(os.sep) + os.sep
    abs_root = os.path.abspath(project_root)
    data_dir = resolve_project_data_dir(abs_root).path
    return os.path.join(str(data_dir), "memory") + os.sep


def get_user_auto_mem_path() -> str:
    """返回用户级记忆目录路径：~/.rockcoder/memory/。"""
    try:
        home = Path.home()
    except RuntimeError:
        return ""
    data_dir = resolve_user_data_dir(home).path
    return os.path.join(str(data_dir), "memory") + os.sep


def is_auto_mem_path(absolute_path: str, project_root: str) -> bool:
    """检查路径是否在项目级或用户级记忆目录内。"""
    abs_p = os.path.normpath(absolute_path) + os.sep
    project_dir = get_auto_mem_path(project_root)
    if project_dir and abs_p.startswith(project_dir):
        return True
    user_dir = get_user_auto_mem_path()
    if user_dir and abs_p.startswith(user_dir):
        return True
    return False


def ensure_memory_dir_exists(memory_dir: str) -> None:
    """确保记忆目录存在，agent 可直接写入无需先 mkdir。"""
    if memory_dir:
        os.makedirs(memory_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Frontmatter 解析（对齐 Go 版 parseFrontmatter）
# ---------------------------------------------------------------------------

@dataclass
class MemoryFile:
    """一个记忆文件的元信息。"""
    path: str = ""
    name: str = ""
    description: str = ""
    type: str = ""
    topic: str = ""
    updated_at: str = ""
    source: str = ""


def parse_frontmatter(content: str) -> MemoryFile:
    """从 YAML-ish frontmatter 中提取 name/description/type。

    只读取三个已知字段，未知字段忽略。没有 frontmatter 的文件返回空字段。
    """
    mf = MemoryFile()
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return mf
    for line in m.group(1).split("\n"):
        colon = line.find(":")
        if colon < 0:
            continue
        key = line[:colon].strip()
        val = line[colon + 1:].strip()
        # 去除引号
        if len(val) >= 2 and (
            (val.startswith('"') and val.endswith('"'))
            or (val.startswith("'") and val.endswith("'"))
        ):
            val = val[1:-1]
        if key == "name":
            mf.name = val
        elif key == "description":
            mf.description = val
        elif key == "type" and val in VALID_TYPES:
            mf.type = val
        elif key == "topic":
            mf.topic = val
        elif key == "updated_at":
            mf.updated_at = val
        elif key == "source":
            mf.source = val
    return mf


# ---------------------------------------------------------------------------
# MEMORY.md 截断（对齐 Go 版 TruncateEntrypointContent）
# ---------------------------------------------------------------------------

def truncate_entrypoint_content(raw: str) -> str:
    """截断 MEMORY.md 内容，超过行数或字节限制时添加警告。"""
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count = len(lines)
    byte_count = len(trimmed.encode("utf-8"))

    over_lines = line_count > MAX_ENTRYPOINT_LINES
    over_bytes = byte_count > MAX_ENTRYPOINT_BYTES

    if not over_lines and not over_bytes:
        return trimmed

    result = trimmed
    if over_lines:
        result = "\n".join(lines[:MAX_ENTRYPOINT_LINES])

    result_bytes = result.encode("utf-8")
    if len(result_bytes) > MAX_ENTRYPOINT_BYTES:
        cut = result[:MAX_ENTRYPOINT_BYTES].rfind("\n")
        if cut > 0:
            result = result[:cut]
        else:
            result = result[:MAX_ENTRYPOINT_BYTES]

    # 构建警告信息
    if over_bytes and not over_lines:
        reason = f"{_format_size(byte_count)} (limit: {_format_size(MAX_ENTRYPOINT_BYTES)}) — index entries are too long"
    elif over_lines and not over_bytes:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {_format_size(byte_count)}"

    result += (
        f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. "
        "Only part of it was loaded. Keep index entries to one line "
        "under ~200 chars; move detail into topic files."
    )
    return result


def _format_size(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count}B"
    elif byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f}KB"
    else:
        return f"{byte_count / (1024 * 1024):.1f}MB"


# ---------------------------------------------------------------------------
# 构建记忆系统提示（对齐 Go 版 BuildMemoryPrompt）
# ---------------------------------------------------------------------------

def build_memory_prompt(user_mem_dir: str, project_mem_dir: str) -> str:
    """构建记忆系统提示，包含行为指令和 MEMORY.md 索引内容。

    对齐 Go 版 BuildMemoryPrompt：组合类型化记忆行为指令 + 两个 MEMORY.md
    的内容，生成完整的 '# auto memory' 系统提示段。
    """
    lines = _build_memory_lines(user_mem_dir, project_mem_dir)
    parts = [lines]

    if user_mem_dir:
        ep_path = os.path.join(user_mem_dir, ENTRYPOINT_NAME)
        parts.append("")
        parts.append(_build_entrypoint_section("User-level", ep_path))

    if project_mem_dir:
        ep_path = os.path.join(project_mem_dir, ENTRYPOINT_NAME)
        parts.append("")
        parts.append(_build_entrypoint_section("Project-level", ep_path))

    return "\n".join(parts)


def _build_entrypoint_section(scope_label: str, entrypoint_path: str) -> str:
    """读取一个 MEMORY.md 文件并格式化为系统提示段。"""
    header = f"## {scope_label} {ENTRYPOINT_NAME} (`{entrypoint_path}`)\n"
    try:
        data = Path(entrypoint_path).read_text(encoding="utf-8")
        if data.strip():
            return header + "\n" + truncate_entrypoint_content(data)
    except OSError:
        pass
    return header + f"\nThis {ENTRYPOINT_NAME} is currently empty. When you save new {scope_label.lower()}-level memories, add their pointers here."


def _build_memory_lines(user_mem_dir: str, project_mem_dir: str) -> str:
    """构建类型化记忆的行为指令文本（不含 MEMORY.md 内容）。"""
    dir_exists_guidance = (
        "This directory already exists — write to it directly with the Write tool "
        "(do not run mkdir or check for its existence)."
    )

    parts = ["# auto memory\n"]
    parts.append(
        "You have a persistent, file-based memory system organized into two locations by content type:\n"
    )

    if user_mem_dir:
        parts.append(
            f"- **User-level** (`{user_mem_dir}`) — memories with `type: user` or `type: feedback`. "
            f"These follow you across all projects, because they describe the human or how the human likes to work. "
            f"{dir_exists_guidance}"
        )
    if project_mem_dir:
        parts.append(
            f"- **Project-level** (`{project_mem_dir}`) — memories with `type: project` or `type: reference`. "
            f"These belong to the current repo, can be committed for team sharing or git-ignored for personal use. "
            f"{dir_exists_guidance}"
        )

    parts.append(
        "\nThe `type` field in each memory file's frontmatter determines which directory it belongs to "
        "— pick the type first, then write to the matching directory."
    )
    parts.append(
        "\nYou should build up this memory system over time so that future conversations can have "
        "a complete picture of who the user is, how they'd like to collaborate with you, what behaviors "
        "to avoid or repeat, and the context behind the work the user gives you."
    )

    # frontmatter 格式示例
    parts.append("\n## How to save memories\n")
    parts.append(
        "Saving a memory is a two-step process:\n\n"
        "**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) "
        "using this frontmatter format:\n\n"
        "```markdown\n"
        "---\n"
        "name: {{memory name}}\n"
        "description: {{one-line description}}\n"
        "type: {{user, feedback, project, reference}}\n"
        "---\n\n"
        "{{memory content}}\n"
        "```\n\n"
        f"**Step 2** — add a pointer to that file in the `{ENTRYPOINT_NAME}` index in the SAME directory "
        f"as the memory file. `{ENTRYPOINT_NAME}` is an index, not a memory — each entry should be one line, "
        "under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. "
        f"Never write memory content directly into `{ENTRYPOINT_NAME}`.\n\n"
        f"- Both `{ENTRYPOINT_NAME}` files are always loaded into your conversation context"
        f" — lines after {MAX_ENTRYPOINT_LINES} each will be truncated, so keep each index concise\n"
        "- Keep the name, description, and type fields in memory files up-to-date with the content\n"
        "- Organize memory semantically by topic, not chronologically\n"
        "- Update or remove memories that turn out to be wrong or outdated\n"
        "- Do not write duplicate memories. First check if there is an existing memory you can update "
        "before writing a new one."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# MemoryManager（对齐 Go 版 Manager）
# ---------------------------------------------------------------------------

class MemoryManager:
    """管理双层记忆：静态指令之外的长期动态记忆。"""

    def __init__(self, project_root: str) -> None:
        abs_root = os.path.abspath(project_root)
        self._project_root = abs_root
        self._user_mem_dir = get_user_auto_mem_path()
        self._mem_dir = get_auto_mem_path(abs_root)
        self._last_extraction_msg_count = 0
        self._warnings: list[str] = []

    @property
    def user_path(self) -> Path:
        if self._user_mem_dir:
            return Path(os.path.join(self._user_mem_dir, ENTRYPOINT_NAME))
        return resolve_user_data_dir(Path.home()).path / "memory" / ENTRYPOINT_NAME

    @property
    def project_path(self) -> Path:
        return Path(os.path.join(self._mem_dir, ENTRYPOINT_NAME))

    @property
    def user_mem_dir(self) -> Path:
        return Path(self._user_mem_dir.rstrip(os.sep)) if self._user_mem_dir else resolve_user_data_dir(Path.home()).path / "memory"

    @property
    def project_mem_dir(self) -> Path:
        return Path(self._mem_dir.rstrip(os.sep))

    def load(self) -> str:
        if self._user_mem_dir:
            ensure_memory_dir_exists(self._user_mem_dir)
            self._ensure_index(self.user_mem_dir)
        if self._mem_dir:
            ensure_memory_dir_exists(self._mem_dir)
            self._ensure_index(self.project_mem_dir)
        return build_memory_prompt(self._user_mem_dir, self._mem_dir)

    def load_all(self) -> list[MemoryFile]:
        self.clear_warnings()
        result = _load_dir(self._user_mem_dir, self._warnings)
        result.extend(_load_dir(self._mem_dir, self._warnings))
        return result

    def get_warnings(self) -> list[str]:
        return list(self._warnings)

    def clear_warnings(self) -> None:
        self._warnings.clear()

    def get_memories(self) -> list[str]:
        out: list[str] = []
        for f in self.load_all():
            type_tag = f.type if f.type else "?"
            topic = f.topic or Path(f.path).name
            desc = f.description if f.description else Path(f.path).name
            out.append(f"[{type_tag}] {topic} — {desc}")
        return out

    def get_display_text(self) -> str:
        memories = self.get_memories()
        if not memories:
            return "当前没有任何动态记忆。"
        parts = [
            "动态记忆目录：",
            f"  用户级: {self.user_mem_dir}",
            f"  项目级: {self.project_mem_dir}",
            "",
        ]
        parts.extend(f"  {line}" for line in memories)
        return "\n".join(parts)

    def search(self, query: str) -> list[MemoryFile]:
        q = query.strip().lower()
        if not q:
            return self.load_all()
        matches: list[MemoryFile] = []
        for mem in self.load_all():
            haystacks = [mem.name, mem.description, mem.type, mem.topic, Path(mem.path).name]
            try:
                haystacks.append(Path(mem.path).read_text(encoding="utf-8"))
            except OSError:
                pass
            if any(q in (h or "").lower() for h in haystacks):
                matches.append(mem)
        return matches

    def show_topic(self, topic: str) -> str:
        path = self._find_topic_file(topic)
        if path is None:
            raise FileNotFoundError(topic)
        return path.read_text(encoding="utf-8")

    def remember(
        self,
        fact: str,
        *,
        memory_type: str = "project",
        topic: str = "",
        scope: str = "project",
        source: str = "user-confirmed",
        confidence: str = "confirmed",
    ) -> tuple[str, str]:
        if memory_type not in VALID_TYPES:
            raise ValueError(f"invalid memory type: {memory_type}")
        cleaned = fact.strip()
        if not cleaned:
            raise ValueError("memory content is empty")
        if self._looks_sensitive(cleaned):
            raise ValueError("memory contains sensitive content")

        target_dir = self._dir_for_scope(memory_type if scope == "auto" else ("user" if scope == "user" else "project"))
        ensure_memory_dir_exists(str(target_dir))
        self._ensure_index(target_dir)

        topic_file = self._topic_filename(topic, cleaned)
        topic_path = target_dir / topic_file
        now = _utc_now()
        slug = _slugify(cleaned[:80]) or "memory"
        mem_source = source or "user-confirmed"

        existing = ""
        if topic_path.exists():
            existing = topic_path.read_text(encoding="utf-8")
            existing_facts = _extract_fact_lines(existing)
            normalized_existing = {
                self._normalize_fact_for_compare(item) for item in existing_facts
            }
            if self._normalize_fact_for_compare(cleaned) in normalized_existing:
                return ("duplicate", topic_file)

        frontmatter = {
            "name": topic_path.stem,
            "description": cleaned[:120],
            "type": memory_type,
            "topic": topic_path.stem,
            "updated_at": now,
            "source": mem_source,
        }
        entry = (
            f"\n## {cleaned[:80]}\n"
            f"- Fact: {cleaned}\n"
            f"- Scope: {scope}\n"
            f"- Source: {mem_source}\n"
            f"- Confidence: {confidence}\n"
            f"- Updated: {now}\n"
            f"- Id: {slug}\n"
        )
        body = _split_body(existing)
        if existing:
            content = _dump_frontmatter(frontmatter) + body.rstrip() + entry + "\n"
            action = "updated"
        else:
            header_body = (
                "# Dynamic Memory\n\n"
                f"Topic: {topic_path.stem}\n"
                f"Scope: {scope}\n"
            )
            content = _dump_frontmatter(frontmatter) + header_body + entry + "\n"
            action = "created"
        topic_path.write_text(content, encoding="utf-8")
        self._rebuild_index(target_dir)
        return (action, topic_file)

    def forget(self, topic: str) -> bool:
        path = self._find_topic_file(topic)
        if path is None:
            return False
        target_dir = path.parent
        path.unlink(missing_ok=True)
        self._rebuild_index(target_dir)
        return True

    def invalidate(self, topic: str) -> bool:
        path = self._find_topic_file(topic)
        if path is None:
            return False
        content = path.read_text(encoding="utf-8")
        if "Status: invalid" in content:
            return True
        path.write_text(content.rstrip() + "\n\nStatus: invalid\n", encoding="utf-8")
        self._rebuild_index(path.parent)
        return True

    def clear(self) -> None:
        _clear_dir(self._user_mem_dir)
        _clear_dir(self._mem_dir)
        self._ensure_index(self.user_mem_dir)
        self._ensure_index(self.project_mem_dir)

    async def extract(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str,
    ) -> None:
        new_messages = conversation.history[self._last_extraction_msg_count:]
        self._last_extraction_msg_count = len(conversation.history)

        for message in new_messages:
            if message.role != "user" or not message.content.strip() or message.tool_results:
                continue
            fact = self._extract_fact_from_message(message)
            if not fact:
                continue
            try:
                self.remember(
                    fact,
                    memory_type=self._infer_memory_type(fact),
                    topic=self._infer_topic(fact),
                    scope=self._infer_scope(fact),
                    source="auto-extracted",
                    confidence="explicit",
                )
            except ValueError:
                continue

    def _extract_fact_from_message(self, message: Message) -> str:
        text = message.content.strip()
        if not text or text.startswith("<system-reminder>"):
            return ""
        if "不要记住" in text:
            return ""
        for pattern in EXPLICIT_MEMORY_PATTERNS:
            match = pattern.match(text)
            if not match:
                continue
            fact = self._normalize_fact(match.group("fact"))
            if not fact or self._is_one_off_fact(fact) or self._looks_sensitive(fact):
                return ""
            return fact
        return ""

    def _normalize_fact(self, fact: str) -> str:
        cleaned = fact.strip().strip("。.!！?？；;")
        return cleaned.strip()

    def _normalize_fact_for_compare(self, fact: str) -> str:
        cleaned = self._normalize_fact(fact)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.casefold()

    def _is_one_off_fact(self, fact: str) -> bool:
        return any(pattern.search(fact) for pattern in ONE_OFF_MEMORY_PATTERNS)

    def _infer_memory_type(self, fact: str) -> str:
        if any(keyword in fact for keyword in ("我喜欢", "我不喜欢", "偏好", "习惯", "回答", "风格")):
            return "user"
        return "project"

    def _infer_scope(self, fact: str) -> str:
        memory_type = self._infer_memory_type(fact)
        if memory_type in _USER_LEVEL_TYPES:
            return "user"
        return "project"

    def _infer_topic(self, fact: str) -> str:
        if self._infer_memory_type(fact) == "user":
            return "user-preferences"
        return ""

    def _dir_for_scope(self, scope_or_type: str) -> Path:
        if scope_or_type in _USER_LEVEL_TYPES or scope_or_type == "user":
            return self.user_mem_dir
        return self.project_mem_dir

    def _topic_filename(self, topic: str, fact: str) -> str:
        if topic:
            base = _slugify(topic)
            if not base.endswith(".md"):
                return base + ".md"
            return base
        for filename, keywords in TOPIC_KEYWORDS.items():
            if any(keyword in fact for keyword in keywords):
                return filename
        return DEFAULT_TOPIC_FILE

    def _find_topic_file(self, topic: str) -> Path | None:
        normalized = topic.strip().lower()
        if not normalized:
            return None
        candidates = [normalized, _slugify(normalized), normalized.removesuffix(".md"), _slugify(normalized.removesuffix(".md"))]
        for directory in (self.project_mem_dir, self.user_mem_dir):
            for candidate in candidates:
                path = directory / (candidate if candidate.endswith(".md") else candidate + ".md")
                if path.exists():
                    return path
        return None

    def _ensure_index(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        index = directory / ENTRYPOINT_NAME
        if not index.exists():
            index.write_text("", encoding="utf-8")

    def _rebuild_index(self, directory: Path) -> None:
        files = _load_dir(str(directory))
        lines = [f"- [{m.name}]({Path(m.path).name}) — {m.description or m.topic or m.name}" for m in files]
        (directory / ENTRYPOINT_NAME).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _looks_sensitive(self, text: str) -> bool:
        lowered = text.lower()
        if "不要记住" in lowered:
            return True
        return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _load_dir(dir_path: str, warnings: list[str] | None = None) -> list[MemoryFile]:
    if not dir_path:
        return []
    d = Path(dir_path)
    if not d.is_dir():
        return []

    try:
        entries = sorted(d.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        if warnings is not None:
            warnings.append(f"Could not scan memory directory {d}: {exc}")
        return []

    result: list[MemoryFile] = []
    for entry in entries:
        if entry.is_dir() or entry.name == ENTRYPOINT_NAME or not entry.name.endswith(".md"):
            continue
        try:
            data = entry.read_text(encoding="utf-8")
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not read memory file {entry}: {exc}")
            continue
        mf = parse_frontmatter(data)
        mf.path = str(entry)
        if not mf.name:
            mf.name = entry.stem
        if not mf.topic:
            mf.topic = entry.stem
        result.append(mf)
    return result


def _clear_dir(dir_path: str) -> None:
    if not dir_path:
        return
    d = Path(dir_path)
    if not d.is_dir():
        return
    try:
        for entry in d.iterdir():
            if entry.is_dir() or not entry.name.endswith(".md"):
                continue
            try:
                entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", text.strip().lower())
    return cleaned.strip("-") or "memory"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump_frontmatter(frontmatter: dict[str, str]) -> str:
    return "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip() + "\n---\n\n"


def _extract_fact_lines(content: str) -> list[str]:
    facts: list[str] = []
    for line in content.splitlines():
        if line.startswith("- Fact: "):
            facts.append(line[len("- Fact: "):].strip())
    return facts


def _split_body(content: str) -> str:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return content
    return content[m.end():]
