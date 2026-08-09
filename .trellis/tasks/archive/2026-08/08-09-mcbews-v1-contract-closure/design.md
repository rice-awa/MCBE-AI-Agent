# MCBEWS/1 契约闭合与协议内核收敛 — Technical Design

## 1. Architecture boundaries

```text
SDK MCBEWS/1 protocol kernel (authority)
  ├─ manifest + wire vectors + semantic names/version axes/error codes
  ├─ Python typed codec/classifier/bounded session/delivery
  └─ generated/verified TypeScript protocol assets
           │
           ├──────── SDK reference Addon protocol adapters
           └──────── Product Addon bridge facade + UI/history/session sinks

Trusted ToolPlayer chat
  → SDK classifier/decoder (transport sender gate)
  → HostAddonIngressAdapter (connection + typed business identity)
  → ConversationOperations / approval pending store / MessageBroker
  → typed GatewayOutbound message
  → BrokerResponseBridge
  → SDK delivery
  → Product Addon bounded assembler
```

The SDK owns protocol and transport complexity. The Host owns authentication, conversation domain
operations and approval state. The product Addon owns Minecraft capability implementations and UI state.

## 2. Protocol kernel

### 2.1 Canonical assets

Add a package resource under the SDK MCBEWS/1 profile containing:

- compatibility line: `MCBEWS/1`;
- channel IDs/prefixes and semantic names;
- capability request schema `2`, session schema `1`, text framing schema `1`;
- empirical command budget and bounded assembler defaults;
- stable router/session error codes;
- wire vectors for capability, UI Chat, text response, session and approval.

Python profile/model/codec tests read the resource. A generator creates SDK Addon TypeScript constants
and fixtures. The root repository runs a sync/check script against the installed SDK resource to create
the product Addon generated asset. Hand-written facade constants may re-export semantic generated names;
deprecated aliases remain explicit and contain no values of their own.

### 2.2 Concrete v1 seam

`AddonBridgeSettings.profile` and all internal codec/delivery annotations use `McbewsV1Profile` (or a
single `McbewsV1Protocol` facade) rather than `AddonBridgeProfile`. The old Protocol remains only as a
deprecated public typing alias for one cycle, is not used to promise runtime substitutability, and its
documentation points to MCBEWS/1-only support.

Profile fields use semantic names such as `capability_request_script_event_id`,
`capability_response_chat_prefix`, `trusted_bridge_player_name`,
`capability_request_schema_version`. Old property names are read-only deprecated aliases.

## 3. Ingress and identity

### 3.1 SDK classification

The SDK classifier checks `sender == trusted_bridge_player_name` before recognizing any MCBEWS ToolPlayer
chat channel. It decodes:

- capability and UI Chat chunks into existing bounded per-connection sessions;
- session JSON into `SessionRequest`;
- approval JSON/legacy id into `ApprovalDecision`.

The Facade sends typed control messages to a new optional hook method. `NoOpHook` implements it, so Host
adoption is additive for consumers. Wrong-sender messages fall through only as ordinary player messages
and are never treated as control frames; diagnostic logging is bounded and body-free.

### 3.2 Host adapter

`HostAddonIngressAdapter` accepts `(ConnectionState, typed SDK message)` and produces Host-owned messages:

- Session: requires non-empty business player, validates action/version/parameters, then invokes
  `ConversationOperations` in a tracked hook task.
- Approval: new payload `{v, approval_id, player_name, cid}` is cross-checked against pending state.
  Legacy id-only payload resolves only when exactly one unexpired record for that connection/id exists;
  ambiguous or mismatched claims fail closed.
- UI Chat callback carries the decoded `UiChatMessage`, including `conversation_id`, instead of positional
  values that discard CID.

Transport `sender` is never passed as a business player fallback.

## 4. Host domain and outbound types

### 4.1 Conversation operations

Create a Gateway-domain `ConversationOperations` service with typed input and `ConversationOperationResult`:

```text
action, ok, code, message, data
```

It owns new/switch/clear/status/list/compress/save/restore/saved/delete behavior and queries the existing
Broker/ConversationManager/HostSessionStore. A chat renderer maps the result to protocol colors; a session
adapter maps the same result to a session DTO. No adapter parses rendered text.

### 4.2 Typed outbound messages

Add a discriminated union in `models/messages.py` (or a focused sibling module):

- `RunCommandOutbound`;
- `TextResponseOutbound` (`player_name`, `conversation_id`, role/kind, text, response id, title, usage);
- `SessionResponseOutbound` (typed session DTO);
- `GameMessageOutbound`.

All internal producers construct these models. `BrokerResponseBridge` pattern-matches model types and keeps
its current deep responsibility: response queue draining, delivery selection, flow control, error logging
and lifecycle. Transitional dict input is permitted only if required by an external public surface, with a
single deprecated validation adapter and no internal producers.

### 4.3 Approval delivery lifecycle

Approval remains an allowed MCBEWS/1 text-response role to preserve wire compatibility. The inner DTO gains
`player_name` and `cid`. Auxiliary send tasks are registered per connection in `BrokerResponseBridge`, log
failures, and are cancelled/awaited during `stop`; alternatively the dispatcher may await them where doing
so does not block a critical path. Unknown text roles are rejected before history mutation.

## 5. Framing and reassembly

### 5.1 Upstream chat chunker

Port the SDK algorithm into generated/shared protocol code:

1. Iterate `Array.from(payload)` (code points), never UTF-16 offsets.
2. Probe the complete `tell @s ${prefix}|${id}|${i}/${n}|${content}` UTF-8 bytes.
3. Enforce both content code-point and command byte limits.
4. Recompute until `total` digit width converges.
5. Emit one `1/1` frame for empty payload; fail if wrapper leaves no room for one code point.

### 5.2 Text response assembler

Use a pure `BoundedTextResponseAssembler` shared by the product UI facade and SDK reference Addon. Buffer
identity is `(player_name, response_id)` and stream identity is `(player_name, conversation_id, response_id)`.
Each buffer records total/player/role/cid/title, byte count, chunks and last update. On push it:

- prunes TTL-expired buffers;
- validates integer range and maximum chunks;
- enforces max buffers, per-message bytes and total bytes;
- rejects metadata changes and conflicting duplicates;
- completes only when indices are exactly `1..n`;
- returns a typed `TextResponseMessage` with optional usage.

`usage` is accepted only on the final frame and emitted only there by Python. UI/history code receives a
complete typed message; streaming preview consumes a safe contiguous prefix projection without owning raw
buffer rules. UI close finalizes only streams for that player and removes their state.

### 5.3 Session atomic delivery

Add an SDK single-event encoder/delivery that probes the complete command without generic chunking.

- If the requested session JSON fits, send exactly one `mcbews:session_resp` event.
- If it does not fit, replace it with a compact correlated response:
  `{v:1,request_id,action,ok:false,error:{code:"SESSION_RESPONSE_TOO_LARGE",message:"..."}}`.
- If even the compact error cannot fit because correlation metadata is pathological, reject locally with a
  typed protocol error and a bounded log; never emit fragments.

The Addon session client validates the full response object/version/request id and treats parse/send errors
as immediate structured failures rather than waiting only for timeout.

## 6. Product Addon router and registry

Create a pure request decoder with a discriminated parse result and stable codes:
`MALFORMED_JSON`, `INVALID_REQUEST`, `UNSUPPORTED_VERSION`, `UNSUPPORTED_CAPABILITY`,
`CAPABILITY_FAILED`, `RESPONSE_SEND_FAILED`, `BRIDGE_NOT_READY_QUEUE_FULL`.

The capability registry entry contains both handler and advertised metadata. `get_capabilities` projects
from the registry, including `multiblock_placement="command_fallback"`. Event subscription snapshots input,
uses a bounded pre-ready queue, serializes handler work deliberately, catches handler/send failures and does
not log full player payloads at normal levels.

## 7. Compatibility and migration

- Wire values stay unchanged. Optional fields are additive.
- UI Chat without cid remains readable and normalizes to `default`; all new product producers include cid.
- Approval id-only decisions remain readable through pending-record lookup, but new Addon sends typed JSON.
- `u` moving from every frame to completion-only is compatible because it is optional and UI consumes it at
  completion.
- `AddonProtocolConfig` continues accepting old config input for one cycle but exposes it as ignored
  diagnostics/deprecation; runtime values always come from SDK.
- `models/addon_bridge.py` re-exports SDK models so external imports survive without duplicated fields.

Rollback is by repository: SDK 0.2.0 must be released first; Host/Add-on migration targets that version.
Because Host pinning depends on the release, Host must not merge before the SDK artifact exists.

## 8. Operational and security notes

- No logs include full capability/session/approval payloads at INFO.
- Random IDs are correlation, not authentication; authentication is the trusted ToolPlayer sender gate plus
  pending-record owner validation.
- 461 bytes is an empirical configurable ceiling. Tests assert configured budgets and vector behavior.
- Actual PyPI/tag/GitHub publication and real-world MCBE smoke tests are operator actions, documented as
  release gates rather than performed automatically.
