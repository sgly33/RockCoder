# Memory System

## Static Instruction Layer

- Static project instructions load at startup through `rockcoder.memory.instructions.load_instructions`.
- The loader prefers `RockCoder.md`, supports compatible legacy instruction files, and expands supported `@include` directives.
- Missing instruction files are non-fatal.
- Unreadable instruction files surface warnings that are injected into the long-term reminder context.
- Instruction files are treated as user-owned and are blocked from implicit edits by `WriteFile` and `EditFile`.

## Dynamic Memory Layer

- Dynamic memory is stored in `.rockcoder/memory/` for project-level records and the user data directory for user-level records.
- `MEMORY.md` is the index; topic files hold detailed records with frontmatter and append-only fact entries.
- Automatic extraction is conservative and explicit-only. Inputs such as `记住：...` or `记住这件事：...` are persisted after one-off-task and sensitive-content filtering.
- Duplicate memory entries are suppressed using normalized fact comparison rather than raw substring matching.
- Unreadable dynamic memory files are skipped, warnings are retained by `MemoryManager`, and `/memory` can surface them to the user.

## Recall Path

- Startup loads the memory index prompt through `MemoryManager.load()` and injects it into the conversation as long-term context.
- For a user query, `rockcoder.app` can prefetch relevant detailed memories with `find_relevant_memories()`.
- Selected memory files are rendered by `render_reminder()` and injected into the active conversation as a system reminder when the prefetch task completes.
- Both the streaming agent loop and `run_to_completion()` consume ready prefetched recall through the same helper path.

## User Controls

The `/memory` command supports:

- `list`
- `show <topic>`
- `search <query>`
- `remember <fact>`
- `forget <topic>`
- `invalidate <topic>`
- `clear`
- `edit`
