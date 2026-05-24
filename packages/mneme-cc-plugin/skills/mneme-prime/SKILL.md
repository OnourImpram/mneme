---
name: mneme-prime
description: Use when the user starts a new task that may have prior vault context worth surfacing. Invokes mneme_prime to build a token-budgeted preamble of recent sessions and topic matches.
---

# mneme-prime

You are guiding the user into a new task. Before answering, retrieve
relevant prior context from their vault using the `mneme_prime` MCP
tool.

## When to invoke

- The user starts the conversation with a task description that
  sounds like it continues prior work.
- The user explicitly says "remember what we did about X" or
  similar.
- The user types `/mneme:prime` directly.

## How to invoke

Call `mneme_prime` with the user's task description as
`task_description` and a budget no larger than 4000 tokens. Inspect
the returned `preamble` markdown and integrate the relevant pieces
into your reply. Cite paths from the `sources` array so the user
can navigate.

## What not to do

- Do not call `mneme_prime` on every message. The hook system
  already injects session-start context once per session.
- Do not include the full preamble verbatim in your reply. Summarize
  and cite. The preamble is for you, not the user.
- Do not call this with a `budget_tokens` higher than the user's
  remaining context window minus a safe reserve. Smaller is better.
