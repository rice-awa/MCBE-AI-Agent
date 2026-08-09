#!/usr/bin/env node

/**
 * Synchronize the checked-in Addon projection from the SDK canonical resource.
 *
 * The SDK repository is intentionally not an Addon dependency.  The default
 * source path is the sibling checkout used by this workspace; CI can point at
 * an installed/wheel-extracted resource with MCBEWS_SDK_RESOURCE_DIR.
 */

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const addonRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const localProtocolRoot = join(addonRoot, "protocol");
const defaultSdkRoot = resolve(addonRoot, "..", "mcbe-ws-sdk", "src", "mcbe_ws_sdk", "profiles", "mcbews_v1");
const sdkRoot = process.env.MCBEWS_SDK_RESOURCE_DIR ? resolve(process.env.MCBEWS_SDK_RESOURCE_DIR) : defaultSdkRoot;

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const stableJson = (value) => `${JSON.stringify(value, null, 2)}\n`;

const toCamelKey = (key) => key.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
const toCamelProjection = (value) => {
  if (Array.isArray(value)) return value.map(toCamelProjection);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [toCamelKey(key), toCamelProjection(item)]));
  }
  return value;
};

function canonicalAssets() {
  const manifestPath = join(sdkRoot, "manifest.json");
  const vectorsPath = join(sdkRoot, "vectors.json");
  if (!existsSync(manifestPath) || !existsSync(vectorsPath)) return null;
  return { manifest: readJson(manifestPath), vectors: readJson(vectorsPath) };
}

function localAssets() {
  return {
    manifest: readJson(join(localProtocolRoot, "manifest.json")),
    vectors: readJson(join(localProtocolRoot, "vectors.json")),
  };
}

function renderGenerated(manifest, vectors) {
  const wire = manifest.wire;
  const versions = manifest.versions;
  const limits = manifest.limits;
  const textResponse = manifest.text_response;
  const names = {
    protocol_line: "MCBEWS_PROTOCOL_LINE",
    capability_request_script_event_id: "CAPABILITY_REQUEST_SCRIPT_EVENT_ID",
    capability_response_chat_prefix: "CAPABILITY_RESPONSE_CHAT_PREFIX",
    ui_chat_chunk_prefix: "UI_CHAT_CHUNK_PREFIX",
    session_request_chat_prefix: "SESSION_REQUEST_CHAT_PREFIX",
    session_request_script_event_id: "SESSION_REQUEST_SCRIPT_EVENT_ID",
    session_response_script_event_id: "SESSION_RESPONSE_SCRIPT_EVENT_ID",
    text_response_script_event_id: "TEXT_RESPONSE_SCRIPT_EVENT_ID",
    approval_allow_chat_prefix: "APPROVAL_ALLOW_CHAT_PREFIX",
    approval_deny_chat_prefix: "APPROVAL_DENY_CHAT_PREFIX",
    trusted_bridge_player_name: "TRUSTED_BRIDGE_PLAYER_NAME",
    capability_request_schema: "CAPABILITY_REQUEST_SCHEMA_VERSION",
    session_schema: "SESSION_SCHEMA_VERSION",
    text_response_framing: "TEXT_RESPONSE_FRAMING_VERSION",
    ddui_persistence: "DDUI_PERSISTENCE_VERSION",
    command_line_byte_budget: "COMMAND_LINE_BYTE_BUDGET",
    command_line_budget_source: "COMMAND_LINE_BUDGET_SOURCE",
    upstream_max_content_code_points: "UPSTREAM_MAX_CONTENT_CODE_POINTS",
    response_max_buffers: "RESPONSE_MAX_BUFFERS",
    response_max_chunks_per_message: "RESPONSE_MAX_CHUNKS_PER_MESSAGE",
    response_max_message_bytes: "RESPONSE_MAX_MESSAGE_BYTES",
    response_max_total_buffer_bytes: "RESPONSE_MAX_TOTAL_BUFFER_BYTES",
    response_buffer_ttl_ms: "RESPONSE_BUFFER_TTL_MS",
    session_response_max_command_bytes: "SESSION_RESPONSE_MAX_COMMAND_BYTES",
    textResponseAllowedRoles: "TEXT_RESPONSE_ALLOWED_ROLES",
    textResponseUsageField: "TEXT_RESPONSE_USAGE_FIELD",
    textResponseUsageCompletionOnly: "TEXT_RESPONSE_USAGE_COMPLETION_ONLY",
    textResponseConversationIdField: "TEXT_RESPONSE_CONVERSATION_ID_FIELD",
    textResponseTitleField: "TEXT_RESPONSE_TITLE_FIELD",
  };
  const line = (value) => JSON.stringify(value);
  const constLines = [
    `export const ${names.protocol_line} = ${line(manifest.protocol_line)} as const;`,
    ...Object.entries(wire).map(([key, value]) => `export const ${names[key]} = ${line(value)} as const;`),
    ...Object.entries(versions).map(([key, value]) => `export const ${names[key]} = ${line(value)} as const;`),
    ...Object.entries(limits).map(([key, value]) => `export const ${names[key]} = ${line(value)} as const;`),
    `export const ${names.textResponseAllowedRoles} = ${line(textResponse.allowed_roles)} as const;`,
    `export const ${names.textResponseUsageField} = ${line(textResponse.usage_field)} as const;`,
    `export const ${names.textResponseUsageCompletionOnly} = ${line(textResponse.usage_completion_only)} as const;`,
    `export const ${names.textResponseConversationIdField} = ${line(textResponse.conversation_id_field)} as const;`,
    `export const ${names.textResponseTitleField} = ${line(textResponse.title_field)} as const;`,
  ];
  const manifestProjection = `export const MCBEWS_V1_MANIFEST = ${JSON.stringify(toCamelProjection(manifest), null, 2)} as const;`;
  const vectorsProjection = `export const MCBEWS_V1_WIRE_VECTORS = ${JSON.stringify(toCamelProjection(vectors), null, 2)} as const;`;
  return [
    "/** Generated MCBEWS/1 projection; run pnpm protocol:sync to refresh. */",
    "",
    ...constLines,
    "",
    `export const MCBEWS_V1_ERROR_CODES = ${JSON.stringify(manifest.error_codes)} as const;`,
    "",
    manifestProjection,
    "",
    vectorsProjection,
    "",
  ].join("\n");
}

function sync() {
  const assets = canonicalAssets();
  if (!assets) {
    throw new Error(`SDK canonical resources not found under ${sdkRoot}`);
  }
  writeFileSync(join(localProtocolRoot, "manifest.json"), stableJson(assets.manifest));
  writeFileSync(join(localProtocolRoot, "vectors.json"), stableJson(assets.vectors));
  const generatedPath = join(addonRoot, "scripts", "bridge", "protocol.generated.ts");
  writeFileSync(generatedPath, renderGenerated(assets.manifest, assets.vectors));
  execFileSync("npx", ["prettier", "--write", generatedPath], { cwd: addonRoot, stdio: "ignore" });
}

function check() {
  const expected = canonicalAssets();
  if (!expected) {
    if (process.env.MCBEWS_PROTOCOL_STRICT === "1") {
      throw new Error(`SDK canonical resources not found under ${sdkRoot}`);
    }
    console.warn(`SDK canonical resources not found under ${sdkRoot}; checking local projection only`);
  }
  const actual = localAssets();
  if (expected && JSON.stringify(expected) !== JSON.stringify(actual)) {
    throw new Error("Addon protocol assets drift from SDK canonical manifest/vectors; run pnpm protocol:sync");
  }
  const generated = readFileSync(join(addonRoot, "scripts", "bridge", "protocol.generated.ts"), "utf8");
  for (const value of [actual.manifest.protocol_line, ...Object.values(actual.manifest.wire)]) {
    if (!generated.includes(JSON.stringify(value))) {
      throw new Error(`Generated semantic projection is missing ${String(value)}`);
    }
  }
  console.log(
    expected
      ? "MCBEWS/1 protocol assets are in sync with SDK canonical resources"
      : "MCBEWS/1 local protocol assets are valid"
  );
}

const command = process.argv[2] ?? "check";
if (command === "sync") sync();
else if (command === "check") check();
else throw new Error(`Unknown protocol asset command: ${command}`);
