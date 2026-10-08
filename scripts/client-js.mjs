import assert from 'node:assert/strict';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
const client = new Client({name: 'proton-js-interop', version: '1.0.0'});
const live = process.env.RAG_INTEROP_LIVE === '1';
const transport = new StdioClientTransport({
  command: '.venv/bin/python',
  args: live ? ['-m', 'proton_rag.mcp_server'] : ['tests/fixture_server.py'],
  env: Object.fromEntries(Object.entries(process.env).filter(([k]) =>
    ['PATH', 'RAG_STATE_DIR', 'ANYTHING_URL', 'ANYTHING_API_KEY'].includes(k))),
  stderr: 'pipe',
});
try {
  await client.connect(transport);
  const {tools} = await client.listTools();
  assert.deepEqual(tools.map(t => t.name), ['search_mail']);
  assert.equal(tools[0].annotations.readOnlyHint, true);
  const result = await client.callTool({name: 'search_mail', arguments: {query: 'When does cobalt arrive?'}});
  assert.equal(result.isError, false);
  assert.match(JSON.stringify(result), /Tuesday/);
  assert.match(JSON.stringify(result), /imap:\/\/Folders\/test\//);
  const bad = await client.callTool({name: 'search_mail', arguments: {query: '', limit: 99}});
  assert.equal(bad.isError, true);
  console.log(JSON.stringify({client: 'TypeScript SDK', transport: 'stdio', live, passed: true}));
} finally {
  await client.close();
}
