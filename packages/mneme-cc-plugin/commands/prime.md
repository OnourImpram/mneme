# /mneme:prime

Inject preflight context for the current task into the conversation.

## Usage

```
/mneme:prime <task description>
```

The task description is free text. The longer and more specific, the
better mneme can match relevant vault content.

## What it does

Calls the `mneme_prime` MCP tool with the supplied task description.
The tool returns a markdown bundle that contains:

- The most recent session-typed vault documents.
- Vault documents whose content matches tokens in the task description.

Both selections are formatted with vault paths so you can navigate
to source content directly. The bundle is truncated to fit within the
configured token budget (default 4000 tokens).

## When to use

- Starting a new session that picks up a long-running thread.
- Switching topics mid-session and wanting Claude oriented before
  the first prompt.
- Before a code review session where prior decisions matter.

## Related

- `/mneme:recall` to fetch a specific session by id or date.
- `/mneme:migrate` to bring data over from another memory plugin.
