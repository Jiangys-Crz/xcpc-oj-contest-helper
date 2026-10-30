import { DatabaseSync, backup } from 'node:sqlite';
import { mkdir, readdir, unlink, chmod } from 'node:fs/promises';
import path from 'node:path';
const root = process.env.VP_DATA_DIR || '/home/paa/xcpc-vp-system/shared/data';
const directory = path.join(root,'backups');
await mkdir(directory,{recursive:true,mode:0o700});
const db = new DatabaseSync(path.join(root,'vp.sqlite'));
try {
  const target=path.join(directory,`vp-${new Date().toISOString().replaceAll(':','-')}.sqlite`);
  await backup(db,target);
  await chmod(target,0o600);
  const files=(await readdir(directory)).filter(f=>/^vp-.*\.sqlite$/.test(f)).sort();
  for (const file of files.slice(0,Math.max(0,files.length-14))) await unlink(path.join(directory,file));
} finally { db.close(); }
