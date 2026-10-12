// Run in the supported AnythingLLM image; only inference and telemetry are offline.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const Module = require('node:module');
const original = '/app/server/utils/agents/aibitat/index.js';
require('/app/server/models/telemetry.js').Telemetry.sendTelemetry = () => {};
let AIbitat;
if (process.env.CANDIDATE_INDEX) {
  const loaded = new Module(original, module);
  loaded.filename = original;
  loaded.paths = Module._nodeModulePaths('/app/server/utils/agents/aibitat');
  loaded._compile(fs.readFileSync(process.env.CANDIDATE_INDEX, 'utf8'), original);
  AIbitat = loaded.exports;
} else {
  AIbitat = require(original);
}

function fixture(mode, replies, {limit = 1, direct = false, abort = false} = {}) {
  let executions = 0;
  const requests = [], events = [];
  const agent = new AIbitat({provider:'openrouter', model:'fixture', maxToolCalls:limit,
    handlerProps:{log:()=>{}}});
  agent.introspect = () => {};
  agent.socket = {send:(type, data) => events.push({type, data})};
  agent.functions.set('fixture', {name:'fixture', description:'Offline evidence fixture',
    parameters:{type:'object', properties:{}}, handler:async()=> {
      executions++;
      if (direct) agent.skipHandleExecution = true;
      if (abort) agent._aborted = true;
      return 'Evidence [source](proton-mail://fixture/INBOX/1#abc)';
    }});
  agent.providerInstance = {model:'fixture', providerSlug:'fixture',
    resetCumulativeUsage:()=>{}, getCumulativeUsage:()=>({}),
    [mode]:async(messages, functions)=> {
      requests.push({messages, functions});
      const reply = replies[Math.min(requests.length - 1, replies.length - 1)];
      return typeof reply === 'function' ? reply(messages, functions) : reply;
    }};
  const run = () => agent[mode === 'stream' ? 'handleAsyncExecution' : 'handleExecution'](
    [{role:'user', content:'Find evidence'}], Array.from(agent.functions.values()), '@agent');
  return {agent, run, requests, events, executed:()=>executions};
}

for (const content of [null, undefined]) {
  test(`native complete: reasoning with ${content} content gets an inconclusive final answer`, async()=> {
    const client = {chat:{completions:{create:async()=> ({choices:[{message:{content,
      reasoning:'Need more evidence.'}}]})}}};
    const f = fixture('complete', [call, (messages, functions)=>helpers.tooledComplete(
      client, 'fixture', messages, functions)]);
    const answer = await f.run();
    assert.match(answer, /inconclusive|insufficient|could not/i);
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 2);
  });
}

test('stream: fallback answer, usage, citations and chat ID share one UUID', async()=> {
  const f = fixture('stream', [call, call]);
  f.agent.addCitation({id:'fixture', title:'Evidence', text:'Supported fact'});
  f.agent.registerChatId(42);
  await f.run();
  const response = f.events.find(e=>e.data.type === 'fullTextResponse');
  const usage = f.events.find(e=>e.data.type === 'usageMetrics');
  const citation = f.events.find(e=>e.data.type === 'citations');
  const chat = f.events.find(e=>e.data.type === 'chatId');
  assert.ok(response.data.uuid);
  assert.equal(response.data.uuid, usage.data.uuid);
  assert.equal(citation.data.uuid, response.data.uuid);
  assert.equal(chat.data.uuid, response.data.uuid);
  assert.equal(chat.data.chatId, 42);
});
const call = {functionCall:{name:'fixture', arguments:{}}, textResponse:''};
const final = {functionCall:null, textResponse:'Supported finding [source](proton-mail://fixture/INBOX/1#abc). Coverage is limited.'};

const helperPath = '/app/server/utils/agents/aibitat/providers/helpers/tooled.js';
let helpers;
if (process.env.CANDIDATE_TOOLED) {
  const loaded = new Module(helperPath, module);
  loaded.filename = helperPath;
  loaded.paths = Module._nodeModulePaths('/app/server/utils/agents/aibitat/providers/helpers');
  loaded._compile(fs.readFileSync(process.env.CANDIDATE_TOOLED, 'utf8'), helperPath);
  helpers = loaded.exports;
} else helpers = require(helperPath);
for (const stream of [true, false]) {
  test(`native ${stream ? 'stream' : 'complete'}: tool-free request explicitly prohibits tools`, async()=> {
    let request;
    const client = {chat:{completions:{create:async value=> {
      request = value;
      const completion = {choices:[{message:{content:'Done'}, delta:{content:'Done'}}]};
      return stream ? (async function*(){yield completion;})() : completion;
    }}}};
    const run = stream ? helpers.tooledStream : helpers.tooledComplete;
    await run(client, 'fixture', [{role:'user', content:'Summarize evidence'}], []);
    assert.equal(request.tool_choice, 'none');
    assert.equal(request.tools, undefined);
  });
}

test('native complete: malformed unadvertised calls cannot cause a final-only retry loop', async()=> {
  const client = {chat:{completions:{create:async()=> ({choices:[{message:{content:'',
    tool_calls:[{id:'fixture', function:{name:'fixture', arguments:'not valid JSON at all'}}]}}]})}}};
  const result = await helpers.tooledComplete(client, 'fixture', [], []);
  assert.equal(result.retryWithError, undefined);
  assert.ok(result.functionCall);
});

for (const mode of ['stream', 'complete']) {
  test(`${mode}: never dispatches a tool after the exact limit`, async()=> {
    const f = fixture(mode, [call, call, call, final]);
    const answer = await f.run();
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 2);
    assert.deepEqual(f.requests.map(r=>r.functions.length), [1, 0]);
    assert.match(answer, /inconclusive|insufficient|could not/i);
    assert.match(answer, /limit|budget/i);
  });
  test(`${mode}: final synthesis retains evidence and asks for citations and limits`, async()=> {
    const f = fixture(mode, [call, final]);
    assert.equal(await f.run(), final.textResponse);
    assert.equal(f.executed(), 1);
    assert.deepEqual(f.requests.map(r=>r.functions.length), [1, 0]);
    const last = f.requests[1].messages;
    assert.ok(last.some(m=>m.role === 'function' && m.content.includes('proton-mail://')));
    assert.match(last.at(-1).content, /citat/i);
    assert.match(last.at(-1).content, /inconclusive|unknown/i);
  });
  test(`${mode}: unknown tools consume the bounded attempts`, async()=> {
    const f = fixture(mode, [{...call, functionCall:{name:'unknown', arguments:{}}}, call, final]);
    assert.match(await f.run(), /inconclusive|insufficient|could not/i);
    assert.equal(f.executed(), 0);
    assert.equal(f.requests.length, 2);
  });
  test(`${mode}: budget resets for the next user question`, async()=> {
    const f = fixture(mode, [call, final, call, final]);
    await f.run(); await f.run();
    assert.equal(f.executed(), 2);
    assert.deepEqual(f.requests.map(r=>r.functions.length), [1, 0, 1, 0]);
  });
  test(`${mode}: explicit larger budget remains available`, async()=> {
    const f = fixture(mode, [call, call, final], {limit:3});
    assert.equal(await f.run(), final.textResponse);
    assert.equal(f.executed(), 2);
  });
  test(`${mode}: normal early answers do not use tools`, async()=> {
    const f = fixture(mode, [final]);
    assert.equal(await f.run(), final.textResponse);
    assert.equal(f.executed(), 0);
  });
  test(`${mode}: direct output at the limit is returned without another inference`, async()=> {
    const f = fixture(mode, [call, final], {direct:true});
    assert.match(await f.run(), /Evidence \[source\]/);
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 1);
  });
  test(`${mode}: abort after a handler prevents further inference`, async()=> {
    const f = fixture(mode, [call, final], {abort:true});
    assert.equal(await f.run(), null);
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 1);
  });
  test(`${mode}: empty final-only response is explicitly inconclusive`, async()=> {
    const f = fixture(mode, [call, {functionCall:null, textResponse:''}]);
    assert.match(await f.run(), /inconclusive|insufficient|could not/i);
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 2);
    if (mode === 'stream') assert.ok(f.events.some(e=>e.data.type === 'fullTextResponse' && e.data.content));
  });
  test(`${mode}: reasoning without an answer is explicitly inconclusive`, async()=> {
    const f = fixture(mode, [call, {functionCall:null, textResponse:'<think>Need more evidence.</think>'}]);
    assert.match(await f.run(), /inconclusive|insufficient|could not/i);
    assert.equal(f.executed(), 1);
    assert.equal(f.requests.length, 2);
  });
}
