# `mem/` — in-repo project memory

The project-local stand-in for Claude Code's built-in memory.

**Why it exists.** MCP Agent Hub is worked on from **two separate PCs**, and we deliberately keep **no per-PC Claude memories** — nothing under `~/.claude` (its `MEMORY.md` / `memory/` files don't travel between machines). Anything worth preserving across sessions must live **in the repo**, so it moves with `git`.

**What goes where.**
- `tasks.md` — what's still pending (the source of truth for open work).
- `sessions.md` — append-only history of what's been done.
- `mem/` *(this folder)* — everything else worth keeping that doesn't fit the two above: external references, environment/setup recipes, half-formed decisions, scratch context for the next session.

**How to use it.** Add tracked markdown files here (e.g. `mem/<topic>.md`), one topic per file with a clear name, and commit them in the **same change** as the work they describe. Never write project memory to `~/.claude`.
