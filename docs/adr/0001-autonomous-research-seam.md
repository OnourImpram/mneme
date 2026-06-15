# ADR 0001. Autonomous research seam

Status: Proposed
Date: 2026-06-14
Deciders: maintainer
Supersedes: none

## Context

A recurring request for vault-native memory tools is autonomous research. The
user names a topic, the agent reads sources, extracts what matters, files it
into the vault as durable, cross-referenced memory, then loops, detecting what
is still missing and filling the gaps. The most visible implementation of this
idea in the Claude-Code-plus-markdown niche is claude-obsidian (MIT, 6.8k
stars), whose `autoresearch` command runs a multi-round, gap-filling loop and
writes a self-organizing wiki.

mneme today is a substrate, not a researcher. It ships the connectors that pull
external sources, the redactor that scrubs them, the claim extractor that turns
prose into temporal claims, the graph that cross-references them, the policy and
approval layers that gate durable writes, and the audit chain that journals every
change. What it does not ship is the orchestration that drives those parts in a
loop toward a research goal.

The tempting move is to copy claude-obsidian's design. That would be a mistake.
claude-obsidian's loop is LLM-driven on the hot path and trusting by
construction. Ingested source text flows into the wiki with no redaction-before-store
and no firewall on tainted content. That model is reasonable for a single-user
personal knowledge base. It is the opposite of mneme's identity, which is
local-first, zero-LLM on the Stop and critical path, redaction-before-store on
every write, and a capability firewall over anything an untrusted source can
influence.

The question this ADR answers is whether mneme can offer autonomous research
without becoming the trusting, LLM-on-the-hot-path tool it was built to be an
alternative to.

## Decision

We will add an opt-in autonomous research seam that is a composition of existing
mneme primitives, not a new parallel subsystem. The only net-new code is a round
controller and a gap detector. Every other step in the loop is an existing,
already-tested module. The seam is default off, lives off the Stop and critical
path, and routes all model use through the existing cost-capped compression gate.

The loop, per round, maps to existing modules as follows.

| Step | Reused primitive |
|---|---|
| Seed query and gap re-detection | NEW: `mneme_core/research/` round controller and gap detector |
| Ingest candidate sources | `connectors.py`, `connectors_net.py` |
| Redact before any persistence | `privacy.py` |
| Mark ingested content tainted, restrict to non-mutating capabilities | `taint.py`, `capability.py`, `injection.py`, `security.py` |
| Extract claims, rule-based by default | `temporal/` |
| Optional model-assisted extraction, cost-capped, off the critical path | `compression/` gate and config |
| Cross-reference and link new claims | `kg/`, `mneme-graph` |
| Land output as gated, journalled writes | `policy.py`, `approval.py`, `memory_apply.py` |
| Tamper-evident journal and one-command rollback | `audit.py`, `audit_chain.py` |
| Survive long research sessions across compaction | `cce/` checkpoints |
| Operator entry point | `cli.py` subcommand, optional Claude Code skill |

A round is therefore. Detect the largest knowledge gap against the seed goal.
Ask the connectors for candidate sources for that gap. Redact each candidate.
Mark it tainted and hand it only read capabilities. Extract claims, rule-based
first, model-assisted only if the operator enabled the compression gate and the
cost cap has room. Cross-reference the surviving claims. Emit them as proposals.
Apply only the operator-policy-allowed low-risk classes autonomously, route the
rest to the approval queue, and journal everything into the audit chain. Re-detect
the gap. Stop when the gap closes, the round budget is spent, or two consecutive
rounds add nothing new.

## Invariants

The ADR locks four invariants. Any implementation that breaks one is rejected at
review regardless of how useful the feature is.

1. Zero-LLM-Stop preserved. The loop never runs on the Stop hook or any latency
   path. All model use goes through the existing `compression/` gate, which is
   opt-in, cost-capped, and already off the critical path. With the gate off, the
   loop still runs using rule-based extraction only.

2. Redaction-before-store. Every ingested span passes `privacy.redact` before it
   touches disk or the index. A surviving private span aborts the write, the same
   rule the `sync` path already enforces. There is no path from a raw source to a
   stored artifact that skips the redactor.

3. Tainted-source firewall. Ingested content is marked tainted on entry and is
   granted only non-mutating capabilities until it clears the approval gate. A
   source cannot, by its content, cause a durable write, a deletion, an external
   call, or a capability escalation.

4. Durable writes are never autonomous. Research output lands as proposals.
   Durable memory categories require a human through `approval.py`. Only the
   low-risk edit classes the operator has allowed in `policy.json` apply
   autonomously, and every applied change is journalled into the HMAC audit chain
   for one-command rollback.

## Consequences

Positive. mneme gains the one capability its closest competitor leads on, without
giving up the cost, privacy, security, and multi-client properties that
differentiate it. Because the loop is a composition, the new surface area is
small and most of it is already covered by existing tests. The same loop works
across Claude Code, Codex, and Antigravity because it lives in `mneme-core`, not
in a single client plugin. Research output is auditable and reversible, which no
trusting-by-construction competitor can claim.

Negative and risks. Model-assisted extraction, when enabled, costs tokens and
time, so the cost cap and the default-off posture matter. Gap detection quality
bounds the loop's usefulness and is the hardest part of the net-new code. A
poorly tuned gap detector either loops forever or stops early. The connectors
define the reachable source universe, so research is only as good as the
connectors configured. None of these risks threaten the four invariants. They are
quality and cost concerns, handled by the budget, the default-off gate, and the
two-rounds-without-progress stop condition.

Rejected alternative. Port claude-obsidian's loop directly. Rejected because it
violates invariants 2 and 3 by construction. Its value is the loop shape and the
gap-filling idea, both of which we adopt. Its implementation is incompatible with
mneme's threat model and is not reused.

## Implementation pointer

Phased implementation, module layout, the gap-detector interface, config gating,
the `mneme-core research` subcommand, and the test plan are specified separately
and are not part of this decision. This ADR fixes the architecture and the four
invariants. Implementation is a separate, operator-gated effort.
