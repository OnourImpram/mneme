# Agent rules — mneme MCP tools

These rules apply to any AI agent that has access to the mneme MCP server.
Copy or reference this file from your client's agent-rules configuration.

## Core discipline

1. **Recall before acting.** Before starting any task that may continue prior
   work, call `mneme_prime` with a short description of the task. Integrate
   the returned preamble into your reasoning. Do not recite it verbatim to
   the user.

2. **Search before guessing.** When the user asks a question that sounds like
   recall — "did we decide X?", "what was the conclusion on Y?", "show me
   everything on Z" — call `mneme_search` first. If search returns no hits,
   say so. Do not fabricate note paths, titles, or prior decisions.

3. **Write durable notes.** After any decision, design choice, or outcome
   worth remembering, call `mneme_write` to persist it. Use a descriptive
   `section` header. Keep notes atomic: one topic per call.

4. **Cite sources.** When you surface vault content, include the `path` from
   the search hit or recall result so the user can navigate to the original.

## Tool quick reference

| Tool | When to call |
|---|---|
| `mneme_prime` | Session start or before continuing prior work. |
| `mneme_search` | User asks a recall question. |
| `mneme_recall` | Pull the full body of a specific note by path or session id. |
| `mneme_write` | Persist a decision, outcome, or note. |
| `mneme_summarize` | Condense a topic across multiple sessions. |
| `mneme_timeline` | Retrieve temporally ordered events for a subject. |

## What not to do

- Do not call `mneme_prime` on every message. Call it once at the start of a
  task, not on every user turn.
- Do not call `mneme_search` for questions whose answer is general knowledge.
- Do not search for the same query twice without refining the terms.
- Do not present raw snippets verbatim unless the user asks for them.
  Summarize and cite.
