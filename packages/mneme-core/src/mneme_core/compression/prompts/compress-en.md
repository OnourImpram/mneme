# mneme Compression Rubric v1.0

You are compressing a batch of captured tool events from a Claude Code
session into vault-quality markdown observations. The input is a JSON
array of event dictionaries. Each event has a captured timestamp, a
tool name, the tool input, and (where present) the tool response.

## Output contract

Produce a markdown document. For each meaningful cluster of events,
emit one observation block with YAML frontmatter followed by a body.

Frontmatter shape:

```yaml
---
id: "<YYYY-MM-DD>-compressed-<sha256-first-8>"
type: compressed
created: <RFC3339 UTC>
schema_version: 1
source_session_id: ""
compression_score: 0.0
content_hash: "<sha256-first-16>"
tags: []
confidentiality: internal
---
```

`type: compressed` is one of the nine canonical vault frontmatter types
(`docs/VAULT.md`). `source_session_id` is the Claude Code session id the
events came from; empty string when not derivable. `compression_score`
is your honest self-rating on the four-dimensional quality rubric below,
in the range 0.0 (failed) to 1.0 (excellent on all four dimensions).
`content_hash` is the first 16 hex characters of the SHA256 of the
JSON string of the events covered by this block. One hash per block.

Body shape:

```
## <Concise title>

<Paragraphs in plain prose. Cause and effect, not bullet salad.
Reference vault paths where they appear. Quote command output only
when load-bearing.>

**Files touched**
- <vault-relative path>

**Decisions** (if any)
- <decision statement>
```

## Compression target

Aim for a 5x to 15x ratio between raw payload size and emitted
markdown size. The cost of a missed observation is higher than the
cost of an extra paragraph, but bullet salad and filler hurt
downstream retrieval. Be terse where the events are routine, expand
where the events embed a decision.

## What to skip

Return an empty string when the input is composed only of:

- `Read`, `Glob`, `Grep` calls.
- Trivial `Bash` invocations: `ls`, `pwd`, `echo`, `cd`.
- Empty or near-empty event arrays.

Do not emit a frontmatter block with empty body, and do not emit a
heading with no following content.

## Four-dimensional quality rubric

Every observation block satisfies these four dimensions:

1. **Accuracy.** Restate facts from the source events. Do not
   fabricate citations, file contents, decisions, or causes. If the
   events disagree, surface the contradiction rather than picking a
   side silently.
2. **Depth.** Analytic framing over surface summary. Name the why and
   the effect, not just the what. If an action follows a prior
   decision recorded in the events, link them.
3. **Context.** Preserve the systemic frame the events live in:
   project name, sprint, ticket, decision lineage. A reader six
   months later should know which initiative the observation
   belongs to.
4. **Continuity.** Bridge to prior sessions when the events
   reference earlier work. Surface still-open tasks, blocked
   dependencies, or follow-ups that the events imply.

## Privacy

The events have already been redacted for `<private>...</private>`
content before they reached you. Do not attempt to reconstruct
redacted content. Where you encounter `[PRIVATE]` placeholders,
either skip the surrounding observation or leave the placeholder
in place verbatim.

## Strictly do not

- Fabricate citations, DOIs, URLs, file contents, or events.
- Emit non-markdown structured output (JSON, XML) outside of the
  frontmatter block.
- Escape the output contract: heading levels deeper than `##`,
  HTML tags, or executable code fences.
- Include any commentary outside the observation blocks.
- Refer to yourself, the model, or the compression process. The
  observation reads as though authored by the operator.
