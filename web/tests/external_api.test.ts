import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {CloudService,handleRequest} from '../lib/cloud';

type Data=Record<string,any>;
const EPOCH=Date.parse('2030-07-01T12:00:00Z'),CONTEST='https://qoj.ac/contest/3617';
function fixture(t:any){
  t.mock.timers.enable({apis:['Date'],now:EPOCH});
  const root=mkdtempSync(join(tmpdir(),'vp-external-api-'));
  const profiles:Record<string,Data>={
    captain:{userId:101,userAccount:'captain',userName:'队长',realName:'张同学',email:'captain@example.test'},
    member:{userId:'202',userAccount:'member',userName:'队员',realName:'李同学',email:'member@example.test'},
    other:{userId:'303',userAccount:'other',userName:'旁观者',email:'other@example.test'}
  };
  const options={path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'app-fixture',publicUrl:'https://vp.example.test',fetch:async(_url:any,init:any)=>Response.json({code:200,data:profiles[JSON.parse(init.body).account]})};
  let service=new CloudService(options);
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,token?:string,body?:Data,status=200,headers:Record<string,string>={})=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,
      headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{}),...headers},
      ...(body?{body:JSON.stringify(body)}:{})}),path.split('?')[0].split('/'),service);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return {...result,response};
  };
  const login=async(account:string)=>(await api('POST','auth/login',undefined,{account,password:'fixture-password'})).data;
  const admin=async()=>(await api('POST','auth/admin-login',undefined,{account:'admin',password:'Fixture-admin-only!'})).data;
  const key=async(token:string)=>(await api('POST','admin/api-keys',token,{name:'协会官网训练统计'})).data;
  const query=(externalId:string,extra:Record<string,string>={})=>'external/vp-history?'+new URLSearchParams({externalId,contestUrl:CONTEST,...extra});
  return {api,login,admin,key,query,profiles,get service(){return service;},restart(){service.close();service=new CloudService(options);}};
}
function snapshot(localId:string,extra:Data={}):Data{
  return {localId,mode:'vp',status:'complete',startedAt:new Date(EPOCH-7200e3).toISOString(),endedAt:new Date(EPOCH-3600e3).toISOString(),
    contest:{url:CONTEST,name:'Original contest',platform:'qoj',contestId:'3617',problems:['A','B'],rules:{duration:3600,scoring:'ICPC'}},
    rank:'87',rankSource:'original',solved:1,penalty:20,cells:{A:{tries:1,solved:true,solveTime:20}},
    submissions:[{id:'s1',problem:'A',submittedAt:new Date(EPOCH-6000e3).toISOString(),verdict:'CORRECT',practice:false,url:'https://qoj.ac/submission/1'}],revision:1,...extra};
}

test('admin creates hashed persistent keys, lists metadata only, and revokes immediately',async t=>{
  const f=fixture(t),admin=await f.admin(),user=await f.login('captain');
  await f.api('GET','admin/api-keys',user.token,undefined,403);
  await f.api('POST','admin/api-keys',user.token,{name:'denied'},403);
  await f.api('POST','admin/api-keys',admin.token,{name:'   '},400);
  const key=await f.key(admin.token);assert.match(key.apiKey,/^vp_[A-Za-z0-9_-]{43}$/);
  const stored=f.service.store.one('SELECT * FROM api_keys WHERE id=?',key.id)!;
  assert.equal(stored.key_hash.length,64);assert.notEqual(stored.key_hash,key.apiKey);
  assert(!JSON.stringify(f.service.store.all('SELECT * FROM api_keys')).includes(key.apiKey));
  let list=(await f.api('GET','admin/api-keys',admin.token)).data;
  assert.equal(list[0].prefix,key.apiKey.slice(0,11));assert.equal(list[0].lastUsedAt,null);
  assert(!JSON.stringify(list).includes(key.apiKey));assert(!JSON.stringify(list).includes('key_hash'));
  const result=await f.api('GET',f.query('101'),key.apiKey);
  assert.equal(result.response.headers.get('Cache-Control'),'no-store');assert.equal(result.response.headers.get('Set-Cookie'),null);
  f.restart();await f.api('GET',f.query('101'),key.apiKey);
  list=(await f.api('GET','admin/api-keys',admin.token)).data;assert.equal(list[0].lastUsedAt,new Date(EPOCH).toISOString());
  await f.api('DELETE','admin/api-keys/'+key.id,user.token,undefined,403);
  await f.api('DELETE','admin/api-keys/'+key.id,admin.token);await f.api('DELETE','admin/api-keys/'+key.id,admin.token);
  f.restart();await f.api('GET',f.query('101'),key.apiKey,undefined,401);
  assert.deepEqual(f.service.store.all('SELECT action FROM audit WHERE entity_id=?',key.id).map(row=>row.action),['api-key.create','api-key.revoke']);
  assert((await f.api('GET','admin/api-keys',admin.token)).data[0].revokedAt);
});

test('API keys and login sessions are separate credentials and external API is read only',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token);await f.login('captain');
  await f.api('GET',f.query('101'),undefined,undefined,401);await f.api('GET',f.query('101'),admin.token,undefined,401);
  await f.api('GET',f.query('101'),undefined,undefined,401,{Cookie:'xcpc_session='+admin.token});
  await f.api('GET',f.query('101'),'vp_'+'a'.repeat(43),undefined,401);
  await f.api('GET',f.query('101'),undefined,undefined,200,{'X-API-Key':key.apiKey});
  await f.api('GET',f.query('101'),key.apiKey,undefined,401,{'X-API-Key':'vp_'+'a'.repeat(43)});
  for(const path of ['auth/me','history','admin/settings','admin/api-keys'])await f.api('GET',path,key.apiKey,undefined,401);
  await f.api('POST','client/sync',key.apiKey,snapshot('denied'),401);
  await f.api('POST',f.query('101'),key.apiKey,{},405);
  await f.api('GET','external/users?email=captain@example.test',key.apiKey,undefined,404);
});

test('association ID remains bound across account changes and is exposed only on own profile',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token),first=await f.login('captain');
  assert.equal(first.user.externalId,'101');assert.equal(f.service.store.one('SELECT external_id FROM users WHERE id=?',first.user.id)!.external_id,'101');
  f.profiles.captain={...f.profiles.captain,userAccount:'renamed',email:'new@example.test'};
  const second=await f.login('captain');assert.equal(second.user.id,first.user.id);assert.equal(second.user.externalId,'101');f.restart();
  const result=(await f.api('GET',f.query('101'),key.apiKey)).data;
  assert.equal(result.user.useraccount,'renamed');assert.equal(result.user.externalId,'101');
  assert.equal((await f.api('GET','auth/me',first.token)).data.externalId,'101');
  const publicUser=(await f.api('GET','users?search=renamed',admin.token)).data[0];assert.equal(publicUser.email,'');assert.equal(publicUser.externalId,undefined);
  await f.api('GET',f.query(first.user.id),key.apiKey,undefined,404);await f.api('GET',f.query('404'),key.apiKey,undefined,404);
});

test('lookup requires association ID and original contest URL with validated pagination',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token);await f.login('captain');
  const valid=(await f.api('GET',f.query('101',{contestUrl:'https://www.qoj.ac/contest/3617/'}),key.apiKey)).data;
  assert.equal(valid.contestUrl,CONTEST);assert.deepEqual(valid.records,[]);assert.equal(valid.total,0);
  const invalid=[
    'external/vp-history?'+new URLSearchParams({contestUrl:CONTEST}),
    'external/vp-history?'+new URLSearchParams({email:'captain@example.test',contestUrl:CONTEST}),
    'external/vp-history?externalId=101',f.query('101',{email:'captain@example.test'}),
    f.query('101')+'&externalId=202',f.query('101')+'&contestUrl='+encodeURIComponent(CONTEST),
    ...['http://qoj.ac/contest/3617','https://qoj.ac/contest/3617?x=1','https://vp.cwnupaa.com/vp/room','https://evil.test/contest/3617'].map(contestUrl=>f.query('101',{contestUrl})),
    ...['0','101','-1','1.5','abc',''].map(limit=>f.query('101',{limit})),...['-1','1.5','1000001',''].map(offset=>f.query('101',{offset}))
  ];
  for(const path of invalid)await f.api('GET',path,key.apiKey,undefined,400);
});

test('returns all ended VP attempts with details and stable pagination, excluding live, BT and active contests',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token),user=await f.login('captain'),other=await f.login('other'),ids:string[]=[];
  for(let i=0;i<3;i++)ids.push((await f.api('POST','client/sync',user.token,snapshot('attempt-'+i,{createdAt:new Date(EPOCH-10000+i).toISOString()}))).data.id);
  await f.api('POST','client/sync',user.token,snapshot('live',{mode:'live'}));await f.api('POST','client/sync',user.token,snapshot('bt',{mode:'bt'}));
  await f.api('POST','client/sync',user.token,snapshot('other-contest',{contest:{...snapshot('').contest,url:'https://qoj.ac/contest/9999'}}));
  await f.api('POST','client/sync',other.token,snapshot('other-user'));
  for(const status of ['scheduled','running','complete','failed','cancelled'])await f.api('POST','client/sync',user.token,snapshot('active-'+status,{status,startedAt:new Date(EPOCH-600e3).toISOString(),endedAt:new Date(EPOCH-1).toISOString()}));
  const first=(await f.api('GET',f.query('101',{limit:'2'}),key.apiKey)).data;
  assert.equal(first.total,3);assert.equal(first.limit,2);assert.equal(first.offset,0);assert.deepEqual(first.records.map((r:Data)=>r.id),[ids[2],ids[1]]);
  const record=first.records[0];assert.equal(record.rank,'87');assert.equal(record.rankSource,'original');assert.equal(record.solved,1);assert.equal(record.penalty,20);assert.equal(record.cells.A.solved,true);
  assert.equal(record.submissions[0].id,'s1');assert.equal(record.participantType,'individual');
  for(const secret of ['password_hash','key_hash','app-fixture','fixture-password','localId'])assert(!JSON.stringify(first).includes(secret),secret);
  const second=(await f.api('GET',f.query('101',{limit:'2',offset:'2'}),key.apiKey)).data;assert.deepEqual(second.records.map((r:Data)=>r.id),[ids[0]]);
  assert.deepEqual((await f.api('GET',f.query('101',{offset:'99'}),key.apiKey)).data.records,[]);
});

test('legacy ended timestamps and elapsed or manually archived contests can be queried',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token),user=await f.login('captain');
  for(const [localId,extra] of [
    ['legacy',{contest:{...snapshot('').contest,rules:undefined}}],['elapsed',{status:'running',endedAt:undefined}],
    ['archived',{status:'archived',startedAt:new Date(EPOCH-600e3).toISOString(),endedAt:new Date(EPOCH-1).toISOString()}]
  ] as [string,Data][])await f.api('POST','client/sync',user.token,snapshot(localId,extra));
  assert.equal((await f.api('GET',f.query('101'),key.apiKey)).data.total,3);
});

test('team roster snapshots retain member history after team and room membership are removed',async t=>{
  const f=fixture(t),admin=await f.admin(),key=await f.key(admin.token),captain=await f.login('captain'),member=await f.login('member');await f.login('other');
  const team=(await f.api('POST','teams',captain.token,{nameZh:'训练队',nameEn:'Team'})).data;
  let invitation=(await f.api('POST',`teams/${team.id}/invitations`,captain.token,{userId:member.user.id})).data;
  await f.api('POST',`notifications/${invitation.id}/respond`,member.token,{accept:true});
  const room=(await f.api('POST','rooms',admin.token,{name:'Team VP',contestUrl:CONTEST,startsAt:new Date(EPOCH+3600e3).toISOString(),freezeMinutes:10,scoringSystem:'ICPC'})).data;
  invitation=(await f.api('POST',`rooms/${room.id}/invitations`,admin.token,{teamId:team.id})).data;await f.api('POST',`notifications/${invitation.id}/respond`,captain.token,{accept:true});
  const participant=(await f.api('POST',`client/rooms/${room.id}/authorize`,captain.token,{})).data.participant;
  const hid=(await f.api('POST','client/sync',captain.token,snapshot('team',{roomId:room.id,participantId:participant.id,roomStartsAt:room.startsAt,startedAt:room.startsAt,endedAt:undefined,submissions:[]}))).data.id;
  assert.equal((await f.api('GET',f.query('202'),key.apiKey)).data.total,0);
  await f.api('PATCH',`teams/${team.id}`,captain.token,{removeUserId:member.user.id});
  await f.api('DELETE',`rooms/${room.id}/participants/${participant.id}`,admin.token);
  t.mock.timers.setTime(EPOCH+6900e3);assert.equal((await f.api('GET',f.query('202'),key.apiKey)).data.total,0);
  t.mock.timers.setTime(EPOCH+7200e3);
  for(const externalId of ['101','202']){
    const result=(await f.api('GET',f.query(externalId),key.apiKey)).data;assert.equal(result.total,1);assert.equal(result.records[0].id,hid);
    assert.equal(result.records[0].participantType,'team');assert.equal(result.records[0].participantId,participant.id);
  }
  assert.equal((await f.api('GET',f.query('303'),key.apiKey)).data.total,0);
});

test('valid API keys are rate limited independently and recover after one minute',async t=>{
  const f=fixture(t),admin=await f.admin(),first=await f.key(admin.token),second=await f.key(admin.token);await f.login('captain');
  for(let i=0;i<120;i++)await f.api('GET',f.query('101'),first.apiKey);
  await f.api('GET',f.query('101'),first.apiKey,undefined,429);await f.api('GET',f.query('101'),second.apiKey);
  t.mock.timers.setTime(EPOCH+60000);await f.api('GET',f.query('101'),first.apiKey);
});
