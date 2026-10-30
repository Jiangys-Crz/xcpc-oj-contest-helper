import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../lib/db';

test('legacy history migration backfills creation once without losing records or changing BT scheduling',t=>{
  const root=mkdtempSync(join(tmpdir(),'vp-creation-migration-')),path=join(root,'legacy.sqlite');
  t.after(()=>rmSync(root,{recursive:true,force:true}));
  const legacy=new DatabaseSync(path);
  legacy.exec(`CREATE TABLE histories(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, local_id TEXT NOT NULL,
    room_id TEXT, participant_id TEXT UNIQUE, participant_label TEXT NOT NULL, contest_name TEXT NOT NULL,
    contest_url TEXT NOT NULL, platform TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT, ended_at TEXT,
    rank TEXT NOT NULL, solved INTEGER NOT NULL, penalty REAL NOT NULL, problems_json TEXT NOT NULL,
    cells_json TEXT NOT NULL, submissions_json TEXT NOT NULL, revision REAL NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(owner_id,local_id));`);
  const cases=[
    {id:'native-vp',mode:'vp',start:'2020-01-01T12:00:00+08:00',updated:'2024-01-01T00:00:00.000Z',expected:'2020-01-01T04:00:00.000Z'},
    {id:'scheduled-vp',mode:'vp',start:'2025-01-01T00:00:00.000Z',updated:'2024-01-01T00:00:00.000Z',expected:'2024-01-01T00:00:00.000Z'},
    {id:'native-live',mode:'live',start:null,updated:'2023-01-01T00:00:00.000Z',expected:'2023-01-01T00:00:00.000Z'},
    {id:'native-bt',mode:'bt',start:'2018-01-01T00:00:00.000Z',updated:'2022-01-01T00:00:00.000Z',expected:'2022-01-01T00:00:00.000Z'}
  ];
  const records=JSON.stringify([{id:'77',problem:'A',verdict:'CORRECT',submittedAt:'2020-01-01T04:01:00.000Z',practice:false}]);
  for(const row of cases)legacy.prepare('INSERT INTO histories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').run(
    row.id,'owner',row.id,null,null,'Fixture user','Preserved contest','https://qoj.ac/contest/3617','qoj',row.mode,
    row.start,null,'7',1,20,'["A"]','{"A":{"tries":1,"solved":true}}',records,2,row.updated);
  legacy.close();
  let store=new Store(path);
  assert.equal(store.one('SELECT COUNT(*) n FROM histories')!.n,4);
  for(const row of cases) {
    const migrated=store.one('SELECT * FROM histories WHERE id=?',row.id)!;
    assert.equal(migrated.created_at,row.expected);assert.equal(migrated.started_at,row.start);
    assert.equal(migrated.owner_id,'owner');assert.equal(migrated.submissions_json,records);assert.equal(migrated.revision,2);
  }
  store.run('UPDATE histories SET updated_at=?','2029-01-01T00:00:00.000Z');
  store.close();store=new Store(path);
  try {
    for(const row of cases)assert.equal(store.one('SELECT created_at FROM histories WHERE id=?',row.id)!.created_at,row.expected);
    assert.deepEqual(store.all('SELECT id FROM histories ORDER BY created_at DESC,id DESC').map(row=>row.id),
                     ['scheduled-vp','native-live','native-bt','native-vp']);
    assert.equal(store.one('SELECT COUNT(*) n FROM histories')!.n,4);
  } finally { store.close(); }
});
