// Run in the old pinned image, with a frozen read-only /app/server/storage mount.
const crypto = require('node:crypto');
const fs = require('node:fs');
const { once } = require('node:events');
const lance = require('/app/server/node_modules/@lancedb/lancedb');
async function emit(value) {
  const line = JSON.stringify(value) + '\n';
  if (!process.stdout.write(line)) await once(process.stdout, 'drain');
  return line;
}
(async () => {
  const workspace = process.env.RAG_EXPORT_WORKSPACE || 'proton-mail';
  if (!/^[a-zA-Z0-9_-]+$/.test(workspace)) throw new Error('Invalid workspace');
  const profile = JSON.parse(fs.readFileSync('/export/profile.json', 'utf8'));
  const db = await lance.connect('/app/server/storage/lancedb');
  const table = await db.openTable(workspace);
  const count = await table.countRows();
  const versions = await table.listVersions();
  const version = Math.max(...versions.map(v => Number(v.version)));
  await table.checkout(version);
  await emit({type: 'header', format: 1, profile, version, workspace});
  const hash = crypto.createHash('sha256');
  let emitted = 0;
  for (let offset = 0; offset < count; offset += 128) {
    const rows = await table.query().limit(Math.min(128, count-offset)).offset(offset).toArray();
    for (const row of rows) {
      const vector = Array.from(row.vector);
      if (vector.length !== profile.dimension || !vector.every(Number.isFinite)) throw new Error('Invalid vector');
      const line = await emit({type:'chunk', id:row.id, docSource:row.docSource,
                              chunkSource:row.chunkSource, text:row.text, vector});
      hash.update(line);
      emitted++;
    }
  }
  if (emitted !== count) throw new Error('Incomplete export');
  await emit({type:'footer', rows:emitted, sha256:hash.digest('hex')});
})().catch(() => {process.stderr.write('Frozen index export failed\n'); process.exitCode=1;});
