import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CloudService, handleRequest } from '../lib/cloud';
import { frozenRank, historyRank } from '../lib/scoreboard';

type Data=Record<string,any>;
function fixture(t:any) {
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-07-01T00:00:00Z')});
  const root=mkdtempSync(join(tmpdir(),'vp-original-rank-'));
  const service=new CloudService({path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'ak_fixture',publicUrl:'https://vp.example.test',fetch:async(_url:any,options:any)=>{
    const {account}=JSON.parse(options.body);
    return Response.json({code:200,data:{userId:account,userAccount:account,userName:account,realName:account}});
  }});
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,token?:string,body?:Data,status=200)=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),path.split('/'),service);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return result.data;
  };
  const login=async(account:string)=>api('POST','auth/login',undefined,{account,password:'fixture-password'});
  const room=async(owner:Data,freezeMinutes=0)=>api('POST','rooms',owner.token,{name:'Original contest ranking',contestUrl:'https://codeforces.com/gym/106554',startsAt:new Date(Date.now()+3600e3).toISOString(),freezeMinutes,scoringSystem:'ICPC'});
  const register=async(r:Data,owner:Data,user:Data)=>{
    const notice=await api('POST',`rooms/${r.id}/invitations`,owner.token,{userId:user.user.id});
    await api('POST',`notifications/${notice.id}/respond`,user.token,{accept:true});
    return (await api('POST',`client/rooms/${r.id}/authorize`,user.token,{})).participant;
  };
  return {service,api,login,room,register};
}
function snapshot(room:Data,p:Data,rank:string,revision=1):Data {return {
  roomId:room.id,participantId:p.id,roomStartsAt:room.startsAt,localId:'native-'+p.id,mode:'vp',status:'running',startedAt:room.startsAt,
  contest:{platform:'gym',contestId:'106554',url:room.contestUrl,name:'Original official contest',problems:['A','B'],rules:{duration:7200,contest_format:'ICPC'}},
  rank,rankSource:'original',solved:1,penalty:51,cells:{A:{tries:1,solved:true,practiceSolved:false},B:{tries:0}},submissions:[],revision
};}

test('room rows retain original official ranks instead of receiving a relative room rank',async(t)=>{
  const {api,login,room,register}=fixture(t),owner=await login('owner'),first=await login('first'),second=await login('second');
  const r=await room(owner),p=await register(r,owner,first),q=await register(r,owner,second);
  const one=snapshot(r,p,'57'),two={...snapshot(r,q,'=103'),solved:2,penalty:80};
  await api('POST','client/sync',first.token,one);await api('POST','client/sync',second.token,two);
  const board=await api('GET',`rooms/${r.id}/leaderboard`,owner.token);
  assert.deepEqual(board.rows.map((row:Data)=>row.rank),['103','57']);
  assert.equal(board.rows[0].rankSource,'original');assert.equal(board.rows[1].rankSource,'original');
  const history=(await api('GET','history',first.token))[0];assert.equal(history.rank,'57');assert.equal(history.rankSource,'original');
  assert.equal((await api('GET',`history/${history.id}`,first.token)).rank,'57');
  assert.equal((await api('GET',`rooms/${r.id}/mysubmit`,first.token)).history.rank,'57');
});

test('legacy, unavailable or native virtual ranks cannot masquerade as official room ranks',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const r=await room(owner,30),p=await register(r,owner,user),data=snapshot(r,p,'1');delete data.rankSource;
  const hid=(await api('POST','client/sync',user.token,data)).id;
  service.store.run("UPDATE histories SET rank='1',freeze_rank='1',freeze_rank_at=?,freeze_rank_source='legacy' WHERE id=?",new Date(Date.parse(r.startsAt)+1000).toISOString(),hid);
  assert.equal((await api('GET',`history/${hid}`,user.token)).rank,'—');
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].rank,'—');
  await api('POST','client/sync',user.token,{...data,revision:2,rankSource:'native'});
  assert.equal((await api('GET',`rooms/${r.id}/mysubmit`,user.token)).history.rank,'—');
  await api('POST','client/sync',user.token,{...data,revision:3,rankSource:'unavailable'});
  t.mock.timers.setTime(Date.parse(r.startsAt)+6000e3);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].rank,'?');
  await api('POST','client/sync',user.token,{...data,revision:4,rank:'86',rankSource:'original'});
  assert.equal((await api('GET',`history/${hid}`,user.token)).rank,'86');
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].rank,'?');
  assert.equal(service.store.one('SELECT freeze_rank FROM histories WHERE id=?',hid)!.freeze_rank,null);
  t.mock.timers.setTime(Date.parse(r.startsAt)+7200e3);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].rank,'86');
});

test('a verified original rank survives the freeze while unavailable native fallback ranks are ignored',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const r=await room(owner,30),p=await register(r,owner,user),data=snapshot(r,p,'72');
  t.mock.timers.setTime(Date.parse(r.startsAt)+100e3);
  const hid=(await api('POST','client/sync',user.token,data)).id;
  assert.equal(service.store.one('SELECT freeze_rank_source FROM histories WHERE id=?',hid)!.freeze_rank_source,'original');
  t.mock.timers.setTime(Date.parse(r.startsAt)+6000e3);
  await api('POST','client/sync',user.token,{...data,rank:'1',rankSource:'unavailable',revision:2});
  const frozen=(await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0];
  assert.equal(frozen.rank,'72');assert.equal(frozen.rankSource,'original');
  assert.equal((await api('GET',`history/${hid}`,user.token)).rank,'—');
  t.mock.timers.setTime(Date.parse(r.startsAt)+7200e3);
  await api('POST','client/sync',user.token,{...data,rank:'60',revision:3,status:'complete',endedAt:new Date().toISOString()});
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].rank,'60');
});

test('verified final ranks replace a legacy final rank and stay fixed for practice, while independent live history is preserved',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const r=await room(owner),p=await register(r,owner,user),data=snapshot(r,p,'1');delete data.rankSource;
  t.mock.timers.setTime(Date.parse(r.startsAt)+7200e3);
  data.status='complete';data.endedAt=new Date().toISOString();
  const hid=(await api('POST','client/sync',user.token,data)).id;
  service.store.run("UPDATE histories SET rank='1',rank_source='legacy' WHERE id=?",hid);
  await api('POST','client/sync',user.token,{...data,rank:'87',rankSource:'original',revision:2});
  assert.equal((await api('GET',`history/${hid}`,user.token)).rank,'87');
  await api('POST','client/sync',user.token,{...data,mode:'bt',rank:'1',rankSource:'unavailable',revision:3});
  const history=await api('GET',`history/${hid}`,user.token);assert.equal(history.rank,'87');assert.equal(history.rankSource,'original');
  const live:Data={...data,localId:'independent-live',mode:'live',rank:'12'};delete live.roomId;delete live.participantId;delete live.roomStartsAt;
  const liveId=(await api('POST','client/sync',user.token,live)).id;
  const native=await api('GET',`history/${liveId}`,user.token);assert.equal(native.rank,'12');assert.equal(native.rankSource,'native');
  for(const rankSource of [null,0,'room','virtual','Original'])await api('POST','client/sync',user.token,{...live,rankSource,revision:2},400);
});

test('frozen rank provenance is required only for VP histories, and no empty room invents rank one',()=>{
  const start=Date.parse('2030-07-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200,freeze_minutes:30};
  const row={mode:'vp',rank:'1',rank_source:'native',freeze_rank:'1',freeze_rank_at:new Date(start+1000).toISOString()};
  assert.equal(historyRank(row,room),'—');assert.equal(frozenRank(row,room),'?');
  assert.equal(historyRank({...row,rank:'83',rank_source:'original'},room),'83');
  assert.equal(frozenRank({...row,freeze_rank:'83',freeze_rank_source:'original'},room),'83');
  assert.equal(historyRank({mode:'live',rank:'9',rank_source:'legacy'}),'9');
  assert.equal(historyRank({mode:'bt',rank:'1',rank_source:'original'},room),'—');
  assert.equal(historyRank({},room),'—');
});

test('ongoing standalone VP and live ranks keep updating until their actual final snapshot',async t=>{
  const {api,login}=fixture(t),user=await login('student');
  for(const mode of ['vp','live']){
    const source=mode==='vp'?'original':'native',start=Date.now()-600e3,end=start+7200e3;
    const data={localId:'realtime-'+mode,mode,status:'running',startedAt:new Date(start).toISOString(),endedAt:new Date(end).toISOString(),
      contest:{url:'https://codeforces.com/gym/106554',name:'Current results',problems:['A'],rules:{duration:7200}},
      rank:'51',rankSource:source,solved:1,penalty:51,cells:{A:{tries:1,solved:true}},submissions:[],revision:1};
    const saved=await api('POST','client/sync',user.token,data);
    await api('POST','client/sync',user.token,{...data,rank:'40',revision:2});
    assert.equal((await api('GET',`history/${saved.id}`,user.token)).rank,'40');
    t.mock.timers.setTime(end+1000);
    await api('POST','client/sync',user.token,{...data,rank:'32',revision:3});
    assert.equal((await api('GET',`history/${saved.id}`,user.token)).rank,'32','Judging may still be running after the scheduled end');
    await api('POST','client/sync',user.token,{...data,rank:'21',status:'complete',revision:4});
    assert.equal((await api('GET',`history/${saved.id}`,user.token)).rank,'21');
    await api('POST','client/sync',user.token,{...data,mode:'bt',rank:'1',rankSource:'unavailable',revision:5});
    assert.equal((await api('GET',`history/${saved.id}`,user.token)).rank,'21');
  }
});

test('room public statistics never fall back to legacy or native own-submission counts',async(t)=>{
  const {api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const r=await room(owner),p=await register(r,owner,user),data=snapshot(r,p,'85');
  data.publicStatistics={submitted:{A:1,B:0},attempted:{A:1,B:0},accepted:{A:1,B:0}};
  data.revealedProblems=['A','B'];data.statisticsAt=new Date().toISOString();
  data.submissions=[{id:'1',problem:'A',submittedAt:new Date().toISOString(),verdict:'CORRECT',practice:false}];
  const hid=(await api('POST','client/sync',user.token,data)).id;
  for(const view of [await api('GET',`history/${hid}`,user.token),await api('GET',`rooms/${r.id}/leaderboard`,owner.token)]) {
    assert.equal(view.statisticsSource,'unavailable');assert.equal(view.statisticsAt,null);
    assert.deepEqual(view.publicProblemStatistics,[{label:'A',submitted:null,attempted:null,accepted:null},{label:'B',submitted:null,attempted:null,accepted:null}]);
  }
  await api('POST','client/sync',user.token,{...data,statisticsSource:'native',revision:2});
  const board=await api('GET',`rooms/${r.id}/leaderboard`,owner.token);
  assert.equal(board.publicProblemStatistics[0].submitted,null);assert.equal(board.publicProblemStatistics[0].accepted,null);
});

test('original public statistics are forwarded exactly once without adding local or room participants',async(t)=>{
  const {api,login,room,register}=fixture(t),owner=await login('owner'),first=await login('first'),second=await login('second');
  const r=await room(owner),p=await register(r,owner,first),q=await register(r,owner,second);
  const stats={submitted:{A:120,B:20},attempted:{A:90,B:17},accepted:{A:51,B:13}};
  const expected=[{label:'A',submitted:120,attempted:90,accepted:51},{label:'B',submitted:20,attempted:17,accepted:13}];
  const data={...snapshot(r,p,'85'),publicStatistics:stats,statisticsSource:'original',revealedProblems:['A','B'],statisticsAt:new Date().toISOString()};
  const hid=(await api('POST','client/sync',first.token,data)).id;
  await api('POST','client/sync',second.token,{...data,...snapshot(r,q,'56')});
  const board=await api('GET',`rooms/${r.id}/leaderboard`,owner.token);
  assert.deepEqual(board.publicProblemStatistics,expected);assert.equal(board.statisticsSource,'original');
  const detail=await api('GET',`history/${hid}`,first.token);
  assert.deepEqual(detail.publicProblemStatistics,expected);assert.equal(detail.statisticsSource,'original');
});

test('unavailable statistics clear old counts and reveal metadata independently from a verified original rank',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const r=await room(owner,30),p=await register(r,owner,user),data=snapshot(r,p,'57');
  data.publicStatistics={submitted:{A:120,B:20},attempted:{A:90,B:17},accepted:{A:51,B:13}};
  data.statisticsSource='original';data.revealedProblems=['A'];data.statisticsAt=new Date().toISOString();
  const hid=(await api('POST','client/sync',user.token,data)).id;
  t.mock.timers.setTime(Date.parse(r.startsAt)+6000e3);
  const unavailable:Data={...data,statisticsSource:'unavailable',revision:2};
  delete unavailable.publicStatistics;delete unavailable.revealedProblems;delete unavailable.statisticsAt;
  await api('POST','client/sync',user.token,unavailable);
  const row=service.store.one('SELECT statistics_json,statistics_source,statistics_at,revealed_problems_json,rank_source FROM histories WHERE id=?',hid)!;
  assert.equal(row.statistics_json,null);assert.equal(row.statistics_at,null);assert.equal(row.revealed_problems_json,null);assert.equal(row.statistics_source,'unavailable');assert.equal(row.rank_source,'original');
  const board=await api('GET',`rooms/${r.id}/leaderboard`,owner.token);
  assert.equal(board.statisticsAt,null);assert.equal(board.publicProblemStatistics[0].submitted,null);assert.equal(board.rows[0].rank,'57');
  const detail=await api('GET',`history/${hid}`,user.token);
  assert.equal(detail.publicProblemStatistics[0].submitted,null);assert.equal(detail.publicProblemStatistics[0].attempted,null);assert.equal(detail.publicProblemStatistics[0].accepted,null);
});

test('independent live native statistics keep compatibility and malformed statistics source is rejected',async(t)=>{
  const {api,login}=fixture(t),user=await login('student');
  const data:Data={localId:'live-stats',mode:'live',contest:{url:'https://codeforces.com/contest/2277',name:'Live contest',problems:['A'],rules:{duration:7200}},rank:'12',solved:1,penalty:10,cells:{A:{tries:1,solved:true}},submissions:[],revision:1,
    publicStatistics:{submitted:{A:125},attempted:{A:82},accepted:{A:61}},revealedProblems:['A'],statisticsAt:new Date().toISOString()};
  const hid=(await api('POST','client/sync',user.token,data)).id;
  const detail=await api('GET',`history/${hid}`,user.token);
  assert.deepEqual(detail.publicProblemStatistics,[{label:'A',submitted:125,attempted:82,accepted:61}]);assert.equal(detail.statisticsSource,'native');assert.equal(detail.rank,'12');
  for(const statisticsSource of [null,0,'self','room','Original'])await api('POST','client/sync',user.token,{...data,statisticsSource,revision:2},400);
});

test('standalone VP also hides legacy rank and own-only statistics, and accepts a verified final repair',async(t)=>{
  const {service,api,login}=fixture(t),user=await login('student');
  const data:Data={localId:'standalone-vp',mode:'vp',contest:{url:'https://codeforces.com/gym/106554',name:'Formal comparison',problems:['A'],rules:{duration:7200}},startedAt:new Date(Date.now()-7200e3).toISOString(),endedAt:new Date().toISOString(),rank:'1',status:'complete',solved:1,penalty:51,cells:{A:{tries:1,solved:true}},submissions:[],revision:1,
    publicStatistics:{submitted:{A:1},attempted:{A:1},accepted:{A:1}},revealedProblems:['A'],statisticsAt:new Date().toISOString()};
  const hid=(await api('POST','client/sync',user.token,data)).id;
  service.store.run("UPDATE histories SET rank='1',rank_source='legacy' WHERE id=?",hid);
  const legacy=await api('GET',`history/${hid}`,user.token);
  assert.equal(legacy.rank,'—');assert.equal(legacy.publicProblemStatistics[0].submitted,null);assert.equal(legacy.publicProblemStatistics[0].accepted,null);
  assert.equal((await api('GET','history',user.token))[0].rank,'—');
  await api('POST','client/sync',user.token,{...data,rank:'85',rankSource:'original',statisticsSource:'original',publicStatistics:{submitted:{A:450},attempted:{A:307},accepted:{A:182}},revision:2});
  const corrected=await api('GET',`history/${hid}`,user.token);
  assert.equal(corrected.rank,'85');assert.equal(corrected.rankSource,'original');assert.deepEqual(corrected.publicProblemStatistics,[{label:'A',submitted:450,attempted:307,accepted:182}]);
  await api('POST','client/sync',user.token,{...data,mode:'bt',rank:'1',rankSource:'unavailable',statisticsSource:'unavailable',revision:3});
  assert.equal((await api('GET',`history/${hid}`,user.token)).rank,'85');
});

test('all live platforms show native global counts and display unknown counts instead of personal submissions when unavailable',async(t)=>{
  const {api,login}=fixture(t),user=await login('student');
  for(const [platform,url] of [['cf','https://codeforces.com/contest/2277'],['cwnuoj','https://oj.cwnupaa.com/contest/6adb59680b835c39ff13486c'],['nowcoder','https://ac.nowcoder.com/acm/contest/142115']]) {
    const data:Data={localId:'native-global-'+platform,mode:'live',contest:{platform,url,name:'Live global statistics',problems:['A'],rules:{duration:7200}},startedAt:new Date(Date.now()-600e3).toISOString(),rank:'12',solved:1,penalty:10,cells:{A:{tries:1,solved:true}},submissions:[{id:'1',problem:'A',submittedAt:new Date(Date.now()-300e3).toISOString(),verdict:'CORRECT',practice:false}],revision:1,statisticsSource:'unavailable'};
    const hid=(await api('POST','client/sync',user.token,data)).id;
    const absent=await api('GET',`history/${hid}`,user.token);
    assert.deepEqual(absent.publicProblemStatistics,[{label:'A',submitted:null,attempted:null,accepted:null}]);assert.equal(absent.submissions.length,1);assert.equal(absent.statisticsSource,'unavailable');
    await api('POST','client/sync',user.token,{...data,statisticsSource:'native',publicStatistics:{submitted:{A:450},attempted:{A:307},accepted:{A:182}},revealedProblems:['A'],statisticsAt:new Date().toISOString(),revision:2});
    const global=await api('GET',`history/${hid}`,user.token);
    assert.deepEqual(global.publicProblemStatistics,[{label:'A',submitted:450,attempted:307,accepted:182}]);assert.equal(global.statisticsSource,'native');assert.equal(global.rank,'12');
    await api('POST','client/sync',user.token,{...data,revision:3});
    assert.equal((await api('GET',`history/${hid}`,user.token)).publicProblemStatistics[0].submitted,null);
  }
});
