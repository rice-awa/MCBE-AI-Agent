# MCBEWS/1 契约闭合与协议内核收敛 — Implementation Plan

## Repository strategy

- Root topic branch: `fix/mcbews-v1-contract-closure`, base local `dev@4927482`, PR target `dev`.
- SDK topic branch: `fix/mcbews-v1-contract-closure`, base current
  `feature/session-text-resp-fields@8a98f4c`; final SDK PR target must be confirmed against the SDK's active
  integration branch before push.
- The SDK is a separate ignored repository. Never stage SDK files in the root commit and never reset either
  repository's unrelated changes.

## Ordered implementation

### Phase 1 — SDK protocol authority

- [ ] Create canonical MCBEWS/1 manifest and executable wire vectors under the installed Python package.
- [ ] Add semantic profile names/version axes/error codes and deprecated aliases; make internal settings,
  codec, service, session and delivery concrete to MCBEWS/1.
- [ ] Add typed session request/response, approval decision and text response models/decoders.
- [ ] Extend the trusted ToolPlayer classifier and Facade/Hook typed control callback; propagate UI Chat cid.
- [ ] Make text response usage completion-frame-only and add atomic session response delivery with correlated
  oversize error.
- [ ] Generate/verify SDK reference Addon constants, byte-aware chunker, bounded assembler and router assets
  against manifest/vectors.
- [ ] Expand parity/conformance checks from strings to fields, versions, optional metadata and behavior vectors.
- [ ] Bump SDK Python and Addon metadata to `0.2.0`; update release notes/docs.

SDK gate:

```bash
cd mcbe-ws-sdk
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
python tools/check_protocol_names.py
cd addon
npm test
npm run lint
npm run typecheck
npm run build:production
cd ..
python -m build --sdist --wheel
python -m twine check dist/*
python tools/check_dist.py dist
```

Build a clean temporary venv, install `dist/*.whl`, and run public/codec/Host-facing contract probes without
adding the SDK source tree to `PYTHONPATH`.

### Phase 2 — Host ingress, domain results and typed outbound

- [ ] Add Host addon ingress adapter and wire it to the SDK typed callback; remove prefix/JSON/identity
  parsing from independent `HostConnectionHook.on_player_message` branches.
- [ ] Pass `UiChatMessage.conversation_id` into `handle_ui_chat`/`handle_chat` and `ChatRequest`; include cid
  in the user echo.
- [ ] Add typed gateway outbound models and migrate every run-command/text/session/game producer.
- [ ] Update `BrokerResponseBridge` typed dispatch, CID/title/usage forwarding, approval task tracking and
  atomic session delivery.
- [ ] Add pending-approval owner lookup for legacy id-only decisions and strict claim cross-checking for new
  payloads.
- [ ] Extract `ConversationOperations`; adapt chat renderer and typed session response; fix real message counts.
- [ ] Convert `models/addon_bridge.py` to deprecated SDK re-exports and make `AddonProtocolConfig` explicitly
  ignored/deprecated rather than runtime-configurable.

Host focused gate:

```bash
pytest -q tests/test_gateway_hook_auth_chat.py tests/test_gateway_broker_bridge.py \
  tests/test_tool_approval_command.py tests/test_queue_context.py tests/test_sdk_dependency.py
```

### Phase 3 — Product Addon protocol adapters

- [ ] Generate/sync semantic constants and vectors from the installed SDK resource; migrate internal imports
  away from deprecated aliases.
- [ ] Replace UTF-16 length slicing with the byte-aware convergent chunker and route bridge/UI Chat through it.
- [ ] Split raw text framing/assembly from `responseSync` UI facade; implement all bounds, consistency and
  player+cid+response stream keys.
- [ ] Extend approval DTO/decision transport with player+cid and centralize ToolPlayer sending outside panels.
- [ ] Strengthen session client request/response validation, immediate send failure and oversize handling.
- [ ] Replace router casts with validated decoder/stable errors/bounded startup queue; co-locate capability
  metadata with handlers.
- [ ] Ensure UI close, persistence and streaming completion clean only the owning player/response state.

Addon gate:

```bash
cd MCBE-AI-Agent-addon
pnpm test
pnpm lint
pnpm build
```

### Phase 4 — Packaging, CI and documentation

- [ ] Pin Host to `mcbe-ws-sdk>=0.2.0,<0.3.0` and update dependency contract tests.
- [ ] Add SDK wheel-installed contract job/check and root workflow step that cannot pass through an editable
  nested checkout.
- [ ] Update main protocol doc, Addon README, CLAUDE/config docs and Trellis addon/backend specs.
- [ ] Document release order: merge/release SDK `v0.2.0`, verify PyPI artifact, then merge Host/Add-on.
- [ ] Add real-MCBE smoke checklist for sender source, ToolPlayer identity, Unicode, long session and approval.

### Phase 5 — Full integration review

- [ ] Run generated-asset check and search all legacy aliases/magic dict producers.
- [ ] Trace UI Chat and text response data flow end to end for two players/two conversations.
- [ ] Trace session and approval sender/business identity independently.
- [ ] Run full SDK, root Python and product Addon gates; fix all regressions.
- [ ] Use `trellis-update-spec` to preserve final executable contracts, then use `trellis-check` for the final
  full-scope quality pass.

## Mandatory regression matrix

| Channel | Cases |
|---|---|
| capability | malformed/shape/version/unknown/handler throw/send failure/pre-ready overflow/out-of-order/duplicate/TTL |
| UI Chat | CJK/emoji/CID round trip/two players×two conversations/conflicting duplicate/buffer limits |
| text response | cid/title/final-only usage/empty/metadata conflict/concurrent response/UI close/unknown role |
| session | sender gate/schema/action/real counts/oversize single error/correlation/timeout/send failure |
| approval | original player+cid/spoof/legacy lookup/batch/expiry/panel close/disconnect task cleanup |
| packaging | built wheel import/API/version/Host contract tests/no editable source |

## Risky files and rollback points

- `mcbe-ws-sdk/src/mcbe_ws_sdk/gateway/server_facade.py` and hook protocol: land with NoOp compatibility and
  unit tests before Host migration.
- `services/gateway/command_handlers.py`: extract operations in behavior-preserving steps; keep chat renderer
  tests green before deleting legacy `_handle_conversation` internals.
- `MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts`: first land pure assembler tests, then switch UI facade;
  do not combine raw state rewrite with panel redesign.
- Dependency pin: do not merge until the `0.2.0` artifact is actually available; locally verify with wheel.

## Pre-start review gate

- [x] Goal, scope, compatibility line and acceptance criteria are explicit.
- [x] Repository evidence and current library docs are captured in `research/review-evidence.md`.
- [x] No unresolved user-owned product/UX/risk decision remains.
- [x] `prd.md`, `design.md`, `implement.md`, `implement.jsonl` and `check.jsonl` exist.
- [ ] User explicitly approves this latest planning summary in a subsequent message.
- [ ] Run `task.py start` only after that approval; do not edit product code before it.
