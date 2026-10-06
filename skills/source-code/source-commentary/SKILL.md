---
name: source-commentary
description: Add or improve explanatory source-code comments/docstrings for readability, especially when the user asks to annotate code in the Waku/Niko style. Do not use for prose documentation that does not touch source files.
metadata:
  short-description: Add high-signal source comments
---

# Source Commentary

Use this skill when the user wants source files commented, annotated, or made easier to read without changing behavior.

## Style

Prefer the Waku/Niko style of comments:

- Start important modules with a short docstring explaining what the file owns, what it deliberately does not own, and the flow or invariant a reader should keep in mind.
- Add class/function docstrings when they clarify a contract, boundary, side effect, concurrency rule, fallback, or data shape.
- Add inline comments only before non-obvious blocks: guardrails, error handling, thread/lock behavior, routing decisions, persistence boundaries, API quirks, and compatibility paths.
- Explain "why this exists" more than "what this line does".
- Keep comments short and close to the code they explain.
- Match the user's requested language. For Vietnamese repositories, use Vietnamese with accents when possible.

## Boundaries

- Preserve runtime behavior. Do not refactor, rename, reformat broadly, or change strings/config/schema unless the user explicitly asks.
- Do not comment every function mechanically. Skip obvious getters, trivial wrappers, and self-explanatory code.
- Do not introduce speculative architecture claims. Ground comments in the code and project docs.
- Do not reveal secrets, local tokens, raw private trace data, or `.env` values in comments.
- Keep existing project terminology consistent instead of inventing new abstractions.

## Workflow

1. Read the user's requested style reference if provided, then inspect the target files before editing.
2. Identify the reader's likely confusion points: module responsibility, ownership boundaries, lifecycle, fallback behavior, state mutation, external APIs, and persistence.
3. Add module docstrings first, then targeted docstrings/comments around the high-value blocks.
4. Keep edits comment-only unless a syntax issue is discovered and must be fixed for the comments to parse.
5. Validate with a syntax check or the repo's existing tests when practical, for example `python -m compileall` or the existing test command.

## Project Boundaries

Before adding comments, use the repository's own source of truth for module
ownership: `AGENTS.md`, architecture docs, README files, package layout, and
nearby tests. Preserve those boundaries in the comments.

If the repo distinguishes gateways, runtimes, memory, observability, business
graphs, or adapters, describe each component in those local terms. Do not copy
project-specific boundaries from another codebase into the current one.
