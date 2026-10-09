// Run offline, in the exact AnythingLLM image that owns this volume.
const lancedb = require('@lancedb/lancedb');
(async () => {
  const workspace = process.env.RAG_MAINTENANCE_WORKSPACE;
  const hours = Number(process.env.RAG_MAINTENANCE_RETAIN_HOURS);
  if (!/^[a-zA-Z0-9_-]+$/.test(workspace || '') || !Number.isFinite(hours) || hours < 1)
    throw new Error('Invalid maintenance configuration');
  const db = await lancedb.connect('/app/server/storage/lancedb');
  if (!(await db.tableNames()).includes(workspace)) {
    console.log(JSON.stringify({event: 'maintenance_no_table'}));
    return;
  }
  const table = await db.openTable(workspace);
  const before = await table.countRows();
  const versionsBefore = (await table.listVersions()).length;
  await table.optimize({cleanupOlderThan: new Date(Date.now() - hours * 3600000),
                        deleteUnverified: false});
  const after = await table.countRows();
  if (before !== after) throw new Error('Row count changed during maintenance');
  console.log(JSON.stringify({event: 'maintenance_ok', rows_before: before, rows_after: after,
    versions_before: versionsBefore, versions_after: (await table.listVersions()).length}));
})().catch(() => {
  console.error(JSON.stringify({event: 'maintenance_failed'}));
  process.exitCode = 1;
});
