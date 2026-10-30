import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';

export type Row = Record<string, any>;
export class Store {
  readonly db: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, external_id TEXT UNIQUE, useraccount TEXT NOT NULL UNIQUE COLLATE NOCASE, username TEXT NOT NULL, realname TEXT NOT NULL DEFAULT '', numberid TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'user', password_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS teams(id TEXT PRIMARY KEY, name_zh TEXT NOT NULL, name_en TEXT NOT NULL, captain_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS team_members(team_id TEXT NOT NULL REFERENCES teams(id), user_id TEXT NOT NULL REFERENCES users(id), PRIMARY KEY(team_id,user_id));
CREATE TABLE IF NOT EXISTS rooms(id TEXT PRIMARY KEY, name TEXT NOT NULL, contest_url TEXT NOT NULL, starts_at TEXT NOT NULL, creator_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS room_rules(room_id TEXT PRIMARY KEY REFERENCES rooms(id), duration_seconds INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL REFERENCES users(id), sender_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL, team_id TEXT REFERENCES teams(id), room_id TEXT REFERENCES rooms(id), payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, responded_at TEXT);
CREATE INDEX IF NOT EXISTS notifications_recipient ON notifications(recipient_id,created_at);
CREATE TABLE IF NOT EXISTS participants(id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), type TEXT NOT NULL, user_id TEXT REFERENCES users(id), team_id TEXT REFERENCES teams(id), captain_id TEXT NOT NULL REFERENCES users(id), label TEXT NOT NULL, members_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS participant_members(room_id TEXT NOT NULL REFERENCES rooms(id), participant_id TEXT NOT NULL REFERENCES participants(id), user_id TEXT NOT NULL REFERENCES users(id), PRIMARY KEY(room_id,user_id));
CREATE TABLE IF NOT EXISTS histories(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), local_id TEXT NOT NULL, room_id TEXT REFERENCES rooms(id), participant_id TEXT UNIQUE REFERENCES participants(id), participant_label TEXT NOT NULL, contest_name TEXT NOT NULL, contest_url TEXT NOT NULL, platform TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT, ended_at TEXT, rank TEXT NOT NULL, solved INTEGER NOT NULL, penalty REAL NOT NULL, problems_json TEXT NOT NULL, cells_json TEXT NOT NULL, submissions_json TEXT NOT NULL, revision REAL NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner_id,local_id));
CREATE INDEX IF NOT EXISTS histories_owner ON histories(owner_id,updated_at);
CREATE TABLE IF NOT EXISTS prints(id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), participant_id TEXT NOT NULL REFERENCES participants(id), user_id TEXT NOT NULL REFERENCES users(id), filename TEXT NOT NULL, language TEXT NOT NULL, code TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS prints_participant ON prints(participant_id,created_at);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id), action TEXT NOT NULL, entity_id TEXT, created_at TEXT NOT NULL);
`);
    if (!(this.db.prepare('PRAGMA table_info(histories)').all() as Row[]).some(c => c.name === 'status')) this.db.exec("ALTER TABLE histories ADD COLUMN status TEXT NOT NULL DEFAULT 'running'");
    const historyColumns = new Set((this.db.prepare('PRAGMA table_info(histories)').all() as Row[]).map(c => c.name));
    for (const [column, definition] of Object.entries({rules_json:"TEXT NOT NULL DEFAULT '{}'",freeze_rank:'TEXT',freeze_rank_at:'TEXT',points:'REAL',freeze_points:'REAL',freeze_event_points:'REAL',freeze_cells_json:'TEXT',statistics_json:'TEXT',revealed_problems_json:'TEXT',statistics_at:'TEXT'})) {
      if (!historyColumns.has(column)) this.db.exec(`ALTER TABLE histories ADD COLUMN ${column} ${definition}`);
    }
    const ruleColumns = new Set((this.db.prepare('PRAGMA table_info(room_rules)').all() as Row[]).map(c => c.name));
    if (!ruleColumns.has('freeze_minutes')) this.db.exec('ALTER TABLE room_rules ADD COLUMN freeze_minutes INTEGER NOT NULL DEFAULT 0');
    if (!ruleColumns.has('scoring_system')) this.db.exec("ALTER TABLE room_rules ADD COLUMN scoring_system TEXT NOT NULL DEFAULT 'ICPC'");
    if (path !== ':memory:') chmodSync(path, 0o600);
  }
  one(sql: string, ...params: any[]): Row | undefined { return this.db.prepare(sql).get(...params) as Row | undefined; }
  all(sql: string, ...params: any[]): Row[] { return this.db.prepare(sql).all(...params) as Row[]; }
  run(sql: string, ...params: any[]) { return this.db.prepare(sql).run(...params); }
  transaction<T>(fn: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = fn(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  close() { this.db.close(); }
}
