# MCBE Chat Agent 可验证方块操作工具规格

## Problem Statement

现在 MCBE Chat Agent 主要通过生成并执行 `setblock`、`fill` 等 Minecraft 命令改变方块。这条路径要求模型自行组装命令语法、计算坐标并推断现场状态，无法稳定获知被替换的方块，也难以在误放前阻止破坏。即使命令返回成功，模型仍然无法确认最终 permutation、检测并发修改，或者对失败的部分操作进行可靠回滚。

用户需要一组面向方块意图的专用工具，让 MCBE Chat Agent 能够使用结构化坐标和方块状态执行查询、单格放置、离散批量放置和区域填充，并在写入前检查、写入后验证，将被替换的方块和修复行为明确返回给模型。

## Solution

为 MCBE Chat Agent 公开两个专用工具：低风险的 `inspect_block` 用于查询单个或多个方块，高风险的 `edit_blocks` 通过 `place`、`batch` 和 `fill` 三种互斥模式执行方块写入。工具通过 Add-on bridge 调用 Minecraft Script API，在玩家审批前解析并锁定最终维度和绝对坐标，预检目标方块、permutation、区块加载状态与数据组件风险，执行后立即重读现场。

默认仅允许替换空气。玩家可明确允许任意覆写，或使用 `expected_previous` 进行条件写入。对箱子、告示牌等可携带额外数据的方块，第一版始终拒绝覆写。工具返回版本化 JSON 文本，包含稳定错误码、最终坐标、写入前后快照、自动修复、验证和回滚状态。

## User Stories

1. As a Minecraft player, I want the Agent to inspect a block before changing it, so that it understands the current world state.
2. As a Minecraft player, I want the Agent to report the type and states of the block it replaced, so that I can verify what changed.
3. As a Minecraft player, I want block placement to default to empty spaces, so that existing builds are not destroyed accidentally.
4. As a Minecraft player, I want to explicitly authorize replacement of an existing block, so that intentional edits remain possible.
5. As a Minecraft player, I want conditional replacement based on the current block type and optional states, so that stale plans do not overwrite newer changes.
6. As a Minecraft player, I want the Agent to place blocks using absolute world coordinates, so that known locations can be edited precisely.
7. As a Minecraft player, I want the Agent to place blocks relative to my position and facing direction, so that nearby construction does not require manual coordinate arithmetic.
8. As a Minecraft player, I want relative coordinates to use forward, right, and up offsets, so that placement requests match natural spatial language.
9. As a Minecraft player, I want my relative target to be fixed before approval, so that moving or turning while approving does not change the authorized location.
10. As a Minecraft player in a multiplayer world, I want relative operations bound to the player who sent the current event, so that another player's position cannot leak into my operation.
11. As a Minecraft player, I want the Agent to place a block with explicit block states, so that stairs, slabs, doors, and other directional blocks are configured correctly.
12. As a Minecraft player, I want invalid states to be rejected rather than guessed, so that the resulting block is predictable.
13. As a Minecraft player, I want the Agent to place one shared block type at many discrete positions, so that repeated construction is efficient.
14. As a Minecraft player, I want a discrete batch to be prechecked as a whole, so that invalid input does not leave a partial structure.
15. As a Minecraft player, I want already-written ordinary blocks rolled back if a discrete batch fails during execution, so that the batch behaves atomically where restoration is reliable.
16. As a Minecraft player, I want the Agent to fill a rectangular region between two corners, so that walls, floors, and volumes can be constructed efficiently.
17. As a Minecraft player, I want region fill to affect only air by default, so that existing structures inside the volume are preserved.
18. As a Minecraft player, I want a region fill condition to work as an include filter, so that only matching blocks are replaced.
19. As a Minecraft player, I want fill results to summarize changed, skipped, and previous block types, so that large operations remain understandable without oversized responses.
20. As a Minecraft player, I want block deletion to be represented as an explicit placement of air with overwrite authorization, so that destructive intent is visible and reviewed.
21. As a Minecraft player, I want blocks with inventories, sign text, records, fluid data, or dynamic properties protected from generic replacement, so that data is not silently lost.
22. As a Minecraft player, I want all write operations to pass through the existing approval flow, so that world changes remain under my control.
23. As a Minecraft player, I want block inspection to run without approval, so that safe world queries remain efficient.
24. As a Minecraft player, I want approval text to show the final absolute target, dimension, block, states, count, and replacement policy, so that I approve the action that will actually execute.
25. As a Minecraft player, I want approval to expire with the existing approval timeout, so that old relative placement plans cannot be executed indefinitely.
26. As a Minecraft player, I want a changed world state detected immediately before writing, so that another player or system cannot invalidate the approved preconditions silently.
27. As a Minecraft player, I want a failed post-write verification to trigger rollback for ordinary blocks, so that detected placement inconsistencies are corrected automatically.
28. As a Minecraft player, I want uncertain fill failures reported as an unknown external state, so that the Agent does not falsely claim success or automatically repeat a potentially completed operation.
29. As a Minecraft player, I want unloaded chunks and out-of-world coordinates reported with stable error codes, so that the Agent can respond appropriately.
30. As a Minecraft player, I want harmless formatting mistakes repaired automatically, so that minor model output errors do not interrupt construction.
31. As a Minecraft player, I want an omitted `minecraft:` namespace added automatically, so that common vanilla block names remain convenient.
32. As a Minecraft player, I want a vanilla block ID with one unambiguous single-character typo corrected before approval, so that obvious low-level errors can be recovered safely.
33. As a Minecraft player, I want fuzzy correction limited to one unique vanilla candidate, so that custom blocks and ambiguous names are never guessed.
34. As a Minecraft player, I want every automatic repair visible in the result and final approval parameters, so that the tool never changes intent invisibly.
35. As a Minecraft player, I want an unavailable Add-on to produce a clear failure rather than a hidden command execution, so that verification guarantees are not silently discarded.
36. As a Minecraft player, I want the Agent to be told that the existing command tool is an optional fallback, so that unsupported operations remain possible through a separately approved path.
37. As a server administrator, I want limits for discrete operations and region volume, so that Agent construction cannot overload the game loop.
38. As a server administrator, I want configurable defaults bounded by hard maximums, so that limits can be tuned without allowing untested workloads.
39. As a server administrator, I want large scans distributed across ticks, so that block preflight does not cause noticeable world stalls.
40. As a server administrator, I want tool calls recorded through the existing tool audit, so that failures, repairs, approvals, and outcomes can feed the feedback loop.
41. As a server administrator, I want old Add-ons detected through a capability handshake, so that unsupported tools are hidden instead of repeatedly failing.
42. As a server administrator, I want capability results cached per connection and discarded on disconnect, so that compatibility checks are efficient and reconnects detect upgrades.
43. As a server administrator, I want Minecraft Script API dependencies pinned exactly, so that reinstalling dependencies does not silently change runtime API behavior.
44. As a developer, I want a stable versioned response schema and error vocabulary, so that model behavior and tests do not depend on exception wording.
45. As a developer, I want bridge responses with `ok: false` mapped to failed tool results, so that the runtime Harness and tool audit classify failures correctly.
46. As a developer, I want dedicated block tools preferred over handwritten block commands in the tool prompt, so that the safer path is used consistently.
47. As a developer, I want existing general command tools retained, so that advanced operations outside the dedicated tool contract are not blocked.
48. As a developer, I want real-world acceptance steps in addition to automated tests, so that Script API, chunk loading, and bridge behavior are verified in Minecraft when an environment is available.

## Implementation Decisions

- The public Agent surface consists of `inspect_block` and `edit_blocks`; it does not expose separate tools for each write shape.
- `edit_blocks` uses three mutually exclusive modes: `place`, `batch`, and `fill`.
- `inspect_block` accepts one position or a list of up to the configured discrete-operation limit and returns compact construction snapshots: dimension, final coordinates, type ID, all states, waterlogged, air, and liquid flags.
- Coordinates use an explicit coordinate mode and structured position object. Absolute positions use `x`, `y`, and `z`; player-relative positions use `forward`, `right`, and `up`.
- Absolute operations require a valid dimension ID. Vanilla aliases are canonicalized, and arbitrary valid namespaced custom dimensions are allowed.
- Player-relative operations always use the current event's `player_name`, current dimension, foot block as origin, and horizontal facing snapped to the four cardinal directions. No target-player parameter or selector is exposed.
- Numeric coordinates and offsets are normalized with mathematical floor, including negative values. Any changed value is reported as a repair.
- Relative positions are resolved during read-only preflight before approval and cached with the normalized call for the existing 120-second approval lifetime. Approval recovery reuses the cached absolute coordinates.
- `place` accepts one position. `batch` accepts a position list and one shared target type, states, and precondition. `fill` accepts `from` and `to` corners that are normalized into minimum and maximum bounds.
- The default configured limits are 256 discrete positions and 4096 fill cells. Administrators may tune them up to hard maximums of 1024 and 16384.
- Add-on preflight processes at most 128 cells per tick. It validates all inputs and protected targets before producing an approval-ready normalized call.
- All writes share a target `type_id` and optional states. States are resolved strictly as a `BlockPermutation`; unknown names or invalid values are not guessed.
- Writes default to replacing air only. `replace_any=true` explicitly authorizes ordinary non-air replacement. A matching `expected_previous` also authorizes replacement and may contain a required type plus a partial state map.
- `expected_previous` and `replace_any` are mutually exclusive. In `place` and `batch`, a failed precondition rejects the full operation. In `fill`, the precondition acts as an include filter and unmatched positions are counted as skipped.
- `minecraft:air` is accepted as a deletion target only when non-air replacement has been explicitly authorized through `replace_any` or `expected_previous`.
- Generic writes reject blocks exposing known data-bearing components, including inventory, sign, record player, fluid container, and dynamic properties. This protection cannot be bypassed in version one.
- `place` and `batch` retain before permutations, recheck protected conditions immediately before writing, and verify the resulting permutation after writing. Concurrent changes reject the whole operation before mutation.
- A failed `place` or `batch` execution rolls back already-written ordinary blocks from retained permutations and reports both verification and rollback results.
- `fill` receives full preflight but does not promise full rollback after execution begins. An execution exception reports `STATE_UNKNOWN` and must not be automatically retried.
- Automatic repair runs at most once. It may trim whitespace, add the vanilla namespace, canonicalize dimension aliases, normalize field representation, and floor coordinates.
- Unknown block IDs may be corrected only within the vanilla namespace when exactly one registered block ID has edit distance one. Custom namespace IDs, state names, and state values are never fuzzily corrected.
- Block ID validation and candidate generation use the Add-on's runtime block registry, not a Python-maintained ID list.
- Repairs and relative-coordinate resolution happen before the runtime Harness policy decision. The canonical call is the source for approval summaries, argument hashes, tool audit parameters, and execution.
- The response remains compatible with the current string tool contract by returning versioned JSON text. It contains `schema_version`, `ok`, stable `code`, resolved targets, before/after data, change state, repairs, verification, rollback, and bounded batch summaries.
- Public stable error codes include `INVALID_ARGUMENT`, `INVALID_COORDINATE`, `BLOCK_UNKNOWN`, `STATE_INVALID`, `PROTECTED_BLOCK`, `PRECONDITION_FAILED`, `PRECONDITION_CHANGED`, `UNLOADED_CHUNK`, `OUT_OF_BOUNDS`, `LIMIT_EXCEEDED`, `ADDON_UNAVAILABLE`, `STATE_UNKNOWN`, and `INTERNAL_ERROR`.
- `inspect_block` is cataloged as a low-risk world query. `edit_blocks` is cataloged as a high-risk world mutation and uses the existing player approval, conversation auto-approval, idempotency, and tool audit mechanisms.
- Add-on responses with `ok: false` are converted to failed `ToolResult` values instead of successful JSON strings.
- A versioned capability handshake determines whether block tools are exposed. Results are cached by connection and cleared on disconnect. An old or missing Add-on leaves general command tools available but hides the dedicated block tools.
- If preflight cannot establish Add-on capability, no dedicated write approval is created. The tool explains that the model may separately call the existing command tool; it neither generates fallback commands nor executes them internally.
- Tool prompting requires dedicated block tools for operations they can express. General commands remain available for unsupported capabilities and keep their current command-specific approval behavior.
- The Add-on bridge router supports asynchronous capability handlers so tick-sliced preflight can complete without blocking the game loop.
- Minecraft module declarations are made consistent and exact: `@minecraft/server` 2.6.0, `@minecraft/server-ui` 2.0.0, and `@minecraft/server-gametest` 1.0.0-beta.1.26.20-preview.28 in package metadata, lock data, and behavior-pack manifest where applicable.
- The initial minimum engine version remains 1.21.80. It is raised only if the target stable engine explicitly rejects the pinned module combination, and that compatibility change is documented.
- Block operation limits and scan budget live under Add-on block-tool configuration because they control game-side workload; runtime Harness configuration continues to own risk, approval, and audit policy.

## Testing Decisions

- The primary test seam is the highest public Agent contract: a model-facing call to `inspect_block` or `edit_blocks` produces the expected versioned JSON, approval behavior, and world-facing bridge request.
- The second integration seam is the Add-on bridge capability boundary: a request with a controlled world facade produces the expected snapshot or world mutation result. Internal coordinate and permutation helpers are tested directly only when their behavior cannot be observed clearly through this seam.
- Good tests assert externally visible state, stable codes, requests, approvals, and rollback outcomes. They do not assert internal helper call order or incidental exception messages.
- Python tests cover public schemas, capability-based tool exposure, canonical preflight caching, approval recovery, audit parameters, bridge failure mapping, stable response JSON, and idempotent execution.
- Runtime Harness tests follow the existing real model-to-tool-to-model and deferred approval tests: inspection auto-executes, every edit mode defers, approved calls execute once, and conversation auto-approval remains effective.
- Add-on tests use the existing mocked `@minecraft/server` pattern and cover absolute and relative coordinates, cardinal snapping, negative flooring, dimension aliases, ID repair, strict states, component protection, preconditions, tick-sliced scans, verification, and rollback.
- Batch tests cover full preflight failure with no changes, concurrent state changes before commit, mid-execution failure with rollback, operation limits, and bounded summaries.
- Fill tests cover air-only filtering, expected-previous filtering, protected components, unloaded chunks, out-of-bounds coordinates, configured and hard limits, and uncertain execution state without automatic retry.
- Compatibility tests cover capability handshake success, old Add-on responses, connection-scoped cache reuse, disconnect cleanup, and hidden tools when capability is absent.
- Dependency acceptance requires Add-on unit tests, production build, relevant Python tests, and the full Python test suite.
- A real Minecraft acceptance checklist covers absolute and relative placement, directional states, conditional replacement, explicit air deletion, discrete rollback, fill summaries, protected containers, chunk errors, approval display, and old Add-on compatibility.
- If no real Bedrock world is available during implementation, the delivery explicitly identifies game-only scenarios as unverified instead of claiming full runtime validation.

## Out of Scope

- Generic block entity or raw NBT editing.
- Editing container contents, sign text, record players, fluid containers, or dynamic properties.
- Overriding data-component protection through an `allow_data_loss` option.
- Raycast or player-look target anchors.
- Targeting another player or a selector as a relative-coordinate anchor.
- Mixed block types or mixed coordinate modes within one batch call.
- Multi-step edit plans that mix place, batch, and fill in one approval.
- Arbitrary fuzzy correction of block IDs, custom namespace IDs, states, or state values.
- Guaranteed atomic rollback for region fill.
- Automatically loading chunks, creating ticking areas, or teleporting entities to load a target.
- Internally executing or generating Minecraft fallback commands when the Add-on is unavailable.
- Policy-level prohibition of `setblock`, `fill`, or `clone` through the existing command tools.
- Migrating to preview-only `@minecraft/server` 2.8.0 or maintaining stable and beta build tracks.
- DDUI migration or unrelated Add-on UI changes.

## Further Notes

- The current dependency state is inconsistent: package metadata permits `@minecraft/server` from 2.0.0, the lock resolves 2.6.0, and the behavior-pack manifest declares 2.0.0. This specification intentionally removes that drift.
- The feature advances the runtime Harness goal of tool intelligence: the MCBE Chat Agent receives clearer intent-oriented tools, stable failures, bounded repairs, approval-ready parameters, and verifiable outcomes.
- Returning only the replaced block type would improve observability but would not prevent a bad write. The write-before-check and conditional replacement rules are essential to the safety objective.
- The Script API region-fill return value identifies affected positions but does not provide previous block types. Previous-type statistics therefore come from bounded preflight rather than the fill return value itself.
- The exact `min_engine_version` compatibility of the pinned stable modules must be confirmed by build and target-engine loading. Any required increase is a compatibility correction, not an API-track change.
