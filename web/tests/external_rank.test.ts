import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {CloudService,handleRequest} from '../lib/cloud';
import {Store} from '../lib/db';
import {publicScore} from '../lib/scoreboard';

type Data=Record<string,any>;
const EPOCH=Date.parse('2030-06-01T00:00:00Z');
const ICPC='https://board.xcpcio.com/icpc/51st/wuhan';
const CCPC='https://rank.iccpc.cn/r/e5697b3a-8326-4818-84ca-e2ce1688af23';
function fixture(t:any) {
  t.mock.timers.enable({apis:['Date'],now:EPOCH});
  const root=mkdtempSync(join(tmpdir(),'vp-external-rank-'));
  const options={path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'ak_fixture',publicUrl:'https://vp.example.test',fetch:async(url:any,options:any)=>{
    assert.equal(url,'https://www.cwnupaa.com/api/user/external/login');
    const {account}=JSON.parse(options.body);
    return Response.json({code:200,data:{userId:account,userAccount:account,userName:account,realName:account,numberId:'2030-'+account,email:account+'@example.test'}});
  }};
  const service=new CloudService(options);
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,token?:string,body?:Data,status=200)=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),path.split('/'),service);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return result.data;
  };
  const login=async(account:string)=>api('POST','auth/login',undefined,{account,password:'fixture-password'});
  const body=(extra:Data={})=>({name:'External board training',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(EPOCH+3600e3).toISOString(),freezeMinutes:0,scoringSystem:'ICPC',...extra});
  const register=async(room:Data,owner:Data)=>{
    const notice=await api('POST',`rooms/${room.id}/invitations`,owner.token,{userId:owner.user.id});
    await api('POST',`notifications/${notice.id}/respond`,owner.token,{accept:true});
    return api('POST',`client/rooms/${room.id}/authorize`,owner.token,{});
  };
  return {service,api,login,body,register};
}

test('room external board persists through listing, editing, authorization and ended restore',async t=>{
  const {service,api,login,body,register}=fixture(t),owner=await login('owner'),other=await login('other');
  const room=await api('POST','rooms',owner.token,body({externalRankUrl:ICPC+'/'}));
  assert.equal(room.externalRankUrl,ICPC);
  assert.equal((await api('GET','rooms',owner.token))[0].externalRankUrl,ICPC);
  await api('PATCH',`rooms/${room.id}`,other.token,{externalRankUrl:CCPC},403);
  assert.equal((await api('PATCH',`rooms/${room.id}`,owner.token,{name:'Renamed'})).externalRankUrl,ICPC);
  assert.equal((await api('PATCH',`rooms/${room.id}`,owner.token,{externalRankUrl:null})).externalRankUrl,null);
  const configured=await api('PATCH',`rooms/${room.id}`,owner.token,{externalRankUrl:CCPC,scoringSystem:'CCPC'});
  const authorization=await register(configured,owner);
  assert.equal(authorization.room.externalRankUrl,CCPC);
  const snapshot={roomId:room.id,participantId:authorization.participant.id,roomStartsAt:room.startsAt,localId:'rank-session',mode:'vp',status:'scheduled',startedAt:room.startsAt,
    contest:{platform:'qoj',contestId:'3617',url:room.contestUrl,name:'Original contest',problems:['A'],rules:{duration:7200,penalty_minutes:20,penalty_rounding:'total_seconds'}},
    rank:'—',solved:0,penalty:0,cells:{A:{tries:0,solved:false,practiceSolved:false}},submissions:[],revision:1};
  for(const roomExternalRankUrl of [null,ICPC])await api('POST','client/sync',owner.token,{...snapshot,roomExternalRankUrl},409);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,0);
  await api('POST','client/sync',owner.token,{...snapshot,roomExternalRankUrl:CCPC+'/'});
  for(const externalRankUrl of [null,'',ICPC])await api('PATCH',`rooms/${room.id}`,owner.token,{externalRankUrl},409);
  assert.equal((await api('PATCH',`rooms/${room.id}`,owner.token,{externalRankUrl:CCPC,name:'Same reserved board'})).externalRankUrl,CCPC);
  t.mock.timers.setTime(Date.parse(room.startsAt)+7200e3+1000);
  await api('POST','client/sync',owner.token,{...snapshot,status:'complete',revision:2});
  const restored=await api('POST',`client/rooms/${room.id}/restore`,owner.token,{});
  assert.equal(restored.externalRankUrl,CCPC);
  assert.equal(restored.contest.rules.penalty_rounding,'total_seconds');
  assert.equal(service.store.one('SELECT external_rank_url FROM room_rules WHERE room_id=?',room.id)!.external_rank_url,CCPC);
});

test('only supported public pages and ICPC/CCPC formats accept an external board',async t=>{
  const {api,login,body}=fixture(t),owner=await login('owner');
  const invalid=[false,123,{},'http://board.xcpcio.com/icpc/51st/wuhan','https://board.xcpcio.com.evil.test/icpc/51st/wuhan','https://user:pass@board.xcpcio.com/icpc/51st/wuhan',ICPC+'?token=secret',ICPC+'#rank','https://board.xcpcio.com/data/icpc/51st/wuhan/config.json','https://rank.iccpc.cn/r/not-a-uuid','https://rank.iccpc.cn/upload/outrank_attach/123/rank.json','https://board.xcpcio.com/icpc/%2e%2e/51st/wuhan'];
  for(const externalRankUrl of invalid)await api('POST','rooms',owner.token,body({externalRankUrl}),400);
  for(const scoringSystem of ['OI','IOI'])await api('POST','rooms',owner.token,body({externalRankUrl:ICPC,scoringSystem}),400);
  const plain=await api('POST','rooms',owner.token,body());assert.equal(plain.externalRankUrl,null);
  const configured=await api('POST','rooms',owner.token,body({externalRankUrl:ICPC}));
  await api('PATCH',`rooms/${configured.id}`,owner.token,{scoringSystem:'OI'},400);
  assert.equal((await api('PATCH',`rooms/${configured.id}`,owner.token,{scoringSystem:'OI',externalRankUrl:''})).externalRankUrl,null);
});

test('changing an unreserved external board prevents reusing a withdrawn native identity',async t=>{
  const {api,login,body,register}=fixture(t),owner=await login('owner');
  const room=await api('POST','rooms',owner.token,body({externalRankUrl:ICPC}));
  const original=await register(room,owner);
  await api('DELETE',`rooms/${room.id}/participation`,owner.token,{participantId:original.participant.id});
  await api('PATCH',`rooms/${room.id}`,owner.token,{externalRankUrl:CCPC});
  const changed=await register(room,owner);
  assert.notEqual(changed.participant.id,original.participant.id);
});

test('legacy room rules migrate once to an empty external board without changing scheduling',t=>{
  const root=mkdtempSync(join(tmpdir(),'vp-rank-migration-')),path=join(root,'legacy.sqlite');
  t.after(()=>rmSync(root,{recursive:true,force:true}));
  const legacy=new DatabaseSync(path);
  legacy.exec("CREATE TABLE room_rules(room_id TEXT PRIMARY KEY,duration_seconds INTEGER NOT NULL,freeze_minutes INTEGER NOT NULL DEFAULT 0,scoring_system TEXT NOT NULL DEFAULT 'ICPC');INSERT INTO room_rules VALUES('legacy',18000,60,'CCPC');");
  legacy.close();
  for(let repeat=0;repeat<2;repeat++) {const store=new Store(path);try {assert.deepEqual({...store.one('SELECT * FROM room_rules WHERE room_id=?','legacy')},{room_id:'legacy',duration_seconds:18000,freeze_minutes:60,scoring_system:'CCPC',external_rank_url:null});} finally {store.close();}}
});

test('public and frozen CCPC penalties floor each acceptance to seconds before flooring the team total',()=>{
  const start=EPOCH,room={starts_at:new Date(start).toISOString(),duration_seconds:7200,freeze_minutes:60};
  const event=(id:string,problem:string,seconds:number,verdict='CORRECT')=>({id,problem,submittedAt:new Date(start+seconds*1000).toISOString(),verdict,practice:false});
  const history={mode:'vp',started_at:room.starts_at,problems_json:'["A","B","C","D"]',rules_json:JSON.stringify({penalty_rounding:'total_seconds',penalty_minutes:20}),submissions_json:JSON.stringify([
    event('1','A',5,'WRONG-ANSWER'),event('2','A',89.999),event('3','B',90.999),event('4','C',30.5),event('5','D',4000),event('6','B',7300)
  ])};
  const early=publicScore(history,room,start+100000,false);assert.equal(early.penalty,23);
  const frozen=publicScore(history,room,start+5000e3,true);assert.equal(frozen.penalty,23);assert.equal(frozen.cells.D.solved,false);assert.equal(frozen.cells.D.pending,1);
  assert.equal(frozen.cells.A.solveTime,1);assert.equal(frozen.cells.B.solveTime,1);assert.equal(frozen.cells.C.solveTime,0);
  const final=publicScore(history,room,start+8000e3,false);assert.equal(final.penalty,90);
  history.rules_json='{}';assert.equal(publicScore(history,room,start+5000e3,true).penalty,22);
});
