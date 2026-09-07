/*
 * Apigee JavaScript callout: turn one MCP request into a quota and analytics key.
 *
 * The problem this exists for. Every MCP call is `POST /mcp` with a JSON-RPC body, so an
 * API gateway sees one path, one operation and one quota bucket for however many tools
 * the server offers — thirteen, in FinChat's case. Every dimension API management is
 * built on (per-operation quota, per-operation latency, "which endpoint is the expensive
 * one") collapses to a single row the moment the binding changes from REST to MCP.
 *
 * Nothing solves this out of the box, because the operation identity has moved from the
 * request line into the body. So read the body:
 *
 *   {"jsonrpc":"2.0","id":1,"method":"tools/call",
 *    "params":{"name":"get_account_balance","arguments":{...}}}
 *
 * and set `mcp.operation` to `tools/call:get_account_balance`. A Quota policy keyed on
 * that variable gives per-tool limits; an analytics collector on it gives per-tool
 * reporting. The gateway gets its granularity back without the client changing anything.
 *
 * Deliberate choices, each one a failure mode this would otherwise have:
 *
 *  - **Never throw.** A callout that fails on an unexpected body takes the whole proxy
 *    down for every caller. Anything unparseable resolves to `mcp.operation = "unknown"`
 *    and is allowed through to the target, which is the component that actually owns
 *    deciding whether a request is valid. A gateway is not a second JSON-RPC server.
 *  - **Tool names are constrained before use.** The name comes from a caller and is
 *    about to become a quota identifier and an analytics dimension. Left raw, a caller
 *    could mint unbounded distinct keys — cardinality explosion in analytics, and a
 *    trivial way to route around a per-tool limit by varying the name.
 *  - **Batches are handled.** JSON-RPC permits an array, and MCP's 2025-06-18 revision
 *    removed batching — but "the spec says clients will not" is not a thing to build a
 *    policy on. A batch resolves to `batch:<n>` rather than being misread as one call.
 *
 * Status: the logic is unit-tested (extract-mcp-tool.test.js, `node --test`). It has NOT
 * been run inside an Apigee proxy, because standing up an Apigee org is a real cost and
 * this repository's premise is near-zero. Treat the XML as reviewed-but-unexercised.
 */

/* eslint-disable no-var */

var MAX_TOOL_NAME = 64;
// MCP tool names are identifiers, and this is also what Vertex accepts in a
// functionDeclaration. Anything outside it is not a tool this server offers.
var TOOL_NAME_OK = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

/**
 * Resolve one MCP request body to a stable operation key.
 * @param {string} body raw request payload
 * @returns {{operation: string, method: string, tool: string|null, reason: string}}
 */
function mcpOperation(body) {
  var parsed;
  try {
    parsed = JSON.parse(body);
  } catch (e) {
    return result("unknown", "", null, "unparseable-body");
  }

  if (parsed === null || typeof parsed !== "object") {
    return result("unknown", "", null, "not-an-object");
  }

  if (Object.prototype.toString.call(parsed) === "[object Array]") {
    // Sized, not enumerated: a batch that names one tool would otherwise be charged as
    // that tool while carrying twenty others.
    return result("batch:" + parsed.length, "batch", null, "jsonrpc-batch");
  }

  var method = typeof parsed.method === "string" ? parsed.method : "";
  if (!method) {
    // A response, or something that is not a request. Nothing to charge.
    return result("unknown", "", null, "no-method");
  }

  if (method !== "tools/call") {
    // `initialize`, `tools/list`, `resources/read`, notifications. Worth counting
    // separately — a client looping on tools/list is a real and invisible cost — but
    // they carry no tool identity.
    return result(method, method, null, "non-tool-method");
  }

  var params = parsed.params;
  if (!params || typeof params !== "object" || typeof params.name !== "string") {
    return result("tools/call:unknown", method, null, "missing-tool-name");
  }

  var name = params.name;
  if (name.length > MAX_TOOL_NAME || !TOOL_NAME_OK.test(name)) {
    // Rejected as a KEY, not as a request. The target decides whether the tool exists;
    // this only refuses to mint an unbounded analytics dimension from caller input.
    return result("tools/call:invalid", method, null, "tool-name-rejected");
  }

  return result("tools/call:" + name, method, name, "ok");
}

function result(operation, method, tool, reason) {
  return { operation: operation, method: method, tool: tool, reason: reason };
}

// --- Apigee entry point ------------------------------------------------------
// `context` exists only inside a JavaScript policy; guarding on it lets the same file be
// loaded by `node --test`, which is the only place this logic is currently proven.
if (typeof context !== "undefined" && context !== null) {
  var payload = context.getVariable("request.content") || "";
  var resolved = mcpOperation(payload);
  context.setVariable("mcp.operation", resolved.operation);
  context.setVariable("mcp.method", resolved.method);
  context.setVariable("mcp.tool", resolved.tool === null ? "" : resolved.tool);
  context.setVariable("mcp.resolution", resolved.reason);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { mcpOperation: mcpOperation, MAX_TOOL_NAME: MAX_TOOL_NAME };
}
