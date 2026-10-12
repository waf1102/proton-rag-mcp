# AnythingLLM tool-call limit patch

The supported AnythingLLM 1.17.0 image removes tool definitions after its limit,
but still dispatches subsequent model-returned calls from a retained function map.
The original code also allows one extra call before removing definitions. A limit
of one executed four tools in an offline reproduction. This patch enforces the
exact per-turn boundary in streaming and non-streaming execution.

After the last permitted attempt, it retains the evidence and requests one final
synthesis with tools disabled, citation links and an explicit coverage caveat.
If the model still asks for a tool, returns nothing, or returns only reasoning,
the answer states that the evidence is inconclusive. Malformed unadvertised
calls cannot trigger the native non-streaming provider's JSON-repair retry loop.
Normal early answers, aborts, direct tool output and usage reporting remain intact.

The upstream default stays ten attempts. Unknown tool names consume attempts,
and the budget resets for each user question. `AGENT_MAX_TOOL_CALLS` or an explicit
`AIbitat` constructor setting can select another positive limit. An exhaustive
investigation needs a deliberately larger configured budget; this patch does not
infer that budget from the text of a user prompt. A lower limit is not proof of
an equally complete answer, and instructions do not guarantee correct inference.

## Build and verify

Prerequisites: Python 3, Podman and `patch`. From the repository root:

```sh
python3 integrations/anythingllm/build.py localhost/anythingllm-mail:tool-limit
```

The builder pins the base image by digest, verifies both original source hashes,
applies the patch without fuzzy matching, builds a derived image and runs its
offline regressions in a new container with networking disabled. An upstream
source mismatch fails before either file is patched. Dependency startup may log
a pricing-refresh network error in the offline container; no inference is used.
The tests use the real agent loop and native request formatter, with an offline
provider fixture and disabled telemetry. No live container or storage is changed
by building. The patch applies to upstream MIT-licensed AnythingLLM code.

## Deploy and roll back

Preserve the original image and Quadlet configuration first. Set the Quadlet's
`Image=` to the derived image's immutable ID, then reload user systemd and restart
only `anythingllm.service`. Keep existing storage, ports, mounts, credentials and
MCP configuration. Check the service and `/api/mcp-servers/list`, reload MCP
connections and run the regressions in the new live container. Restore the
previous `Image=` configuration and restart to roll back. A shared MCP launcher
update alone cannot fix this client-side loop.
