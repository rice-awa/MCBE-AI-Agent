# Review evidence and current documentation

## Local baselines

- Root: `dev@4927482`, clean before task creation, one commit ahead of `origin/dev` containing the review.
- SDK: separate ignored repo, `feature/session-text-resp-fields@8a98f4c`, clean.
- Source review: `docs/review/addon-bridge-architecture-review-20260809.md`.

## Confirmed local defects

- SDK decodes UI Chat `cid` in `profiles/mcbews_v1/codec.py`, but
  `addon/service.py` callback, `gateway/server_facade.py` and `gateway/hook.py` still use three positional
  values and drop it.
- `services/gateway/broker_bridge.py::_ai_sync` does not pass `conversation_id` to delivery even though
  `services/agent/worker.py` produces it.
- `services/gateway/hook.py` parses session/approval by prefix before a trusted sender check; session uses
  `setdefault`, while approval forwards the ToolPlayer sender as business player.
- Product `chunking.ts` slices UTF-16 code units and does not use its declared byte budget.
- Product `responseSync.ts` has unbounded maps, id/cid-only keys and insufficient metadata/duplicate checks.
- Product `sessionClient.ts` parses each `mcbews:session_resp` event as complete JSON, while generic SDK
  `send_scriptevent` may split it.
- Host session handler reuses rendered chat text, infers error from `§c`, and hard-codes list message count.
- Product router casts JSON without schema/version/handler-failure protection; SDK reference Addon already
  contains a stronger decoder/queue pattern.
- Root requirement is `mcbe-ws-sdk>=0.1.0`, while current Host uses unreleased post-tag fields. SDK source
  and Addon metadata still report `0.1.0`.
- SDK is ignored by the root Git repo; changes and commits must be handled independently.

## Context7 documentation check (2026-08-09)

Selected source: `/microsoftdocs/minecraft-creator` (official Microsoft Minecraft Creator docs; high source
reputation). Queries were scoped to ScriptEvent source, chat sender and command length.

Findings:

- `ScriptEventCommandMessageAfterEvent` exposes read-only `id`, `message`, `sourceType`, optional
  `sourceBlock`, `sourceEntity` and `initiator`.
- `ScriptEventSource` values are Block, Entity, NPCDialogue and Server. A receiver cannot safely assume
  `/wsserver`-originated protocol events are always `Server`; source policy must be explicit and tested.
- `ChatSendBeforeEvent.sender` is a read-only `Player`; the chat sender is suitable as transport identity.
- The callback runs with restricted-execution privilege and can cancel the impending chat broadcast.
- Official docs did not return a documented Player/Dimension `runCommand` command byte ceiling. The project
  461-byte threshold remains an empirical compatibility limit from stress testing, not an official promise.

Primary documentation pages returned by Context7:

- Microsoft Creator docs: `ScriptEventCommandMessageAfterEvent`
- Microsoft Creator docs: `ScriptEventSource`
- Microsoft Creator docs: `ChatSendBeforeEvent` / `ChatSendBeforeEventSignal`

## Design consequences

- Trust the exact ToolPlayer name at the chat transport gate, then separately validate business owner data.
- Do not reject valid ScriptEvents solely because `sourceType != Server`; log bounded diagnostics and rely on
  channel/schema/correlation validation.
- Probe the complete UTF-8 command string against the configured empirical budget.
- Keep real-world MCBE sender/source/byte-budget smoke tests in the release checklist even when unit vectors pass.
