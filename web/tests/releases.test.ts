import { test } from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {handleRequest} from '../lib/cloud';

test('public release manifest is validated and does not require database or credentials',async()=>{
  const previous=process.env.VP_RELEASE_MANIFEST, root=mkdtempSync(join(tmpdir(),'vp-release-test-'));
  const revision='a'.repeat(40),sha256='b'.repeat(64),file=join(root,'latest.json');
  const valid={version:'0.9.0',revision,sha256,sourceUrl:`https://vp.cwnupaa.com/downloads/terminal/${revision}.tar.gz`,privateSecret:'must never leak'};
  process.env.VP_RELEASE_MANIFEST=file;
  const request=()=>handleRequest(new Request('https://vp.cwnupaa.com/api/client/releases/latest'),['client','releases','latest']);
  try {
    assert.equal((await request()).status,503);
    writeFileSync(file,JSON.stringify(valid));
    const response=await request();assert.equal(response.status,200);assert.equal(response.headers.get('Cache-Control'),'no-store');
    const {data}=await response.json();assert.deepEqual(data,{version:'0.9.0',revision,sha256,sourceUrl:valid.sourceUrl});
    for(const mutation of [{sourceUrl:'https://evil.example/terminal.tar.gz'},{sha256:'abc'},{revision:'not-a-revision'},{sourceUrl:valid.sourceUrl+'?token=secret'},{version:'bad'}]) {
      writeFileSync(file,JSON.stringify({...valid,...mutation}));const invalid=await request();assert.equal(invalid.status,503);assert(!JSON.stringify(await invalid.json()).includes(root));
    }
    writeFileSync(file,'invalid JSON');assert.equal((await request()).status,503);
  } finally { if(previous===undefined)delete process.env.VP_RELEASE_MANIFEST;else process.env.VP_RELEASE_MANIFEST=previous;rmSync(root,{recursive:true,force:true}); }
});
