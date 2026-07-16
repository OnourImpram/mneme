---
name: mneme-search
description: Use when the user asks a factual question whose answer might live in the vault. Invokes mneme_search. Production `mneme_search` is FTS5 BM25. The experimental feature-hashed lexical-vector backend is not wired into MCP. KG enrichment is gated to summarize or timeline when full-profile graph state is active.
---

# mneme-search

When the user asks a question that sounds like recall from prior work
or notes, search the vault first instead of guessing.

## When to invoke

- "Did we decide X?"
- "What was the conclusion about Y?"
- "Show me everything I have on Z."
- The user invokes this skill directly.

## How to invoke

Call the `mneme_search` MCP tool with:

- `query`: the user's question in natural language.
- `top_k`: 5 by default. Raise to 10 only when the user asks for a
  broad sweep.
- Optional `filters.date_from` / `filters.date_to` when the user
  scopes their question to a specific time window.

Inspect the returned `hits`. Each has `path`, `title`, `snippet`, and
a relevance `score`. Read the snippets first. If they answer the
question, cite the path and reply. If they only partially answer,
follow up with `mneme_recall` on the most promising paths to pull the
full body.

## What not to do

- Do not invoke this for the user's first hello or for questions whose
  answer is general knowledge.
- Do not search for the same query twice in a row. If the first pass
  returned nothing useful, refine the query terms or change the filter
  window.
- Do not present raw snippets in your reply unless the user explicitly
  asks for them. Summarize and link.
