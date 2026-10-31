import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CloudService, handleRequest } from '../lib/cloud';

type Data=Record<string,any>;
const CLOCK=Date.parse('2030-07-01T00:00:00Z'),URL='https://qoj.ac/contest/3617';
function fixture(t:any) {
  t.mock.timers.enable({apis:['Date'],now:CLOCK});
  const root=mkdtempSync(join(tmpdir(),'vp-restore-'));
  const service=new CloudService({path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'ak_fixture',fetch:async(_url:any,options:any)=>{
    const {account}=JSON.parse(options.body);
    return Response.json({code:200,data:{userId:account,userAccount:account,userName:account+' nick',realName:account+' name',email:'private@example.invalid',numberId:'private-number'}});
  }});
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,user?:Data,body?:Data,status=200)=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(user?{Authorization:'Bearer '+user.token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),path.split('?')[0].split('/'),service);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return result.data;
  };
  const login=(account:string)=>api('POST','auth/login',undefined,{account,password:'fixture-password'});
  const accept=(notice:Data,user:Data)=>api('POST',`notifications/${notice.id}/respond`,user,{accept:true});
  const setup=async()=>{
    const owner=await login('owner'),captain=await login('captain'),member=await login('member'),other=await login('other');
    const team=await api('POST','teams',captain,{nameZh:'Code-Zero',nameEn:'Code Zero'});
    await accept(await api('POST',`teams/${team.id}/invitations`,captain,{userId:member.user.id}),member);
    const room=await api('POST','rooms',owner,{name:'Restore room',contestUrl:URL,startsAt:new Date(CLOCK+3600e3).toISOString(),freezeMinutes:60,scoringSystem:'ICPC'});
    await accept(await api('POST',`rooms/${room.id}/invitations`,owner,{teamId:team.id}),captain);
    await accept(await api('POST',`rooms/${room.id}/invitations`,owner,{userId:other.user.id}),other);
    const participant=(await api('POST',`client/rooms/${room.id}/authorize`,captain,{})).participant;
    const payload={localId:'vp-qoj-original',roomId:room.id,participantId:participant.id,roomStartsAt:room.startsAt,mode:'vp',status:'complete',startedAt:room.startsAt,
      contest:{url:URL,name:'Native contest',problems:['A','B'],rules:{duration:7200,scoring:'ICPC',penalty_minutes:20,compile_penalty:false}},
      rank:'57',rankSource:'original',solved:1,penalty:20,cells:{A:{tries:2,solved:true},B:{tries:1,practiceSolved:true}},
      submissions:[{id:'101',problem:'A',submittedAt:new Date(CLOCK+3610e3).toISOString(),verdict:'CORRECT',practice:false},{id:'102',problem:'B',submittedAt:new Date(CLOCK+10810e3).toISOString(),verdict:'CORRECT',practice:true}],revision:1};
    const hid=(await api('POST','client/sync',captain,payload)).id;
    return {owner,captain,member,other,team,room,participant,payload,hid};
  };
  return {api,setup,service,login};
}

test('ended room restores only own team records and leaves live captain permissions unchanged',async(t)=>{
  const {api,setup}=fixture(t),{owner,captain,member,other,room,payload,hid}=await setup();
  const active=await api('POST',`client/rooms/${room.id}/authorize`,member,{});
  assert.equal(active.canSubmit,false);assert.equal(active.canRestore,false);
  await api('POST',`client/rooms/${room.id}/restore`,member,{},409);
  t.mock.timers.setTime(CLOCK+10820e3);
  const ended=await api('POST',`client/rooms/${room.id}/authorize`,member,{});
  assert.equal(ended.canSubmit,false);assert.equal(ended.canRestore,true);
  const restored=await api('POST',`client/rooms/${room.id}/restore`,member,{});
  assert.equal(restored.historyId,hid);assert.equal(restored.isOwner,false);assert.equal(restored.contest.url,URL);
  assert.equal(restored.contest.rules.duration,7200);assert.equal(restored.submissions.length,2);
  assert.equal(restored.submissions[0].practice,false);assert.equal(restored.submissions[1].practice,true);
  assert.equal(restored.participantTitle,'Code-Zero');assert.equal(restored.participantSubtitle,'captain name、member name');
  assert.equal((await api('POST',`client/rooms/${room.id}/restore`,captain,{})).isOwner,true);
  await api('POST',`client/rooms/${room.id}/restore`,owner,{},403);
  await api('POST',`client/rooms/${room.id}/restore`,other,{historyId:hid},404);
  await api('POST','client/sync',member,{...payload,revision:2},403);
  assert.equal((await api('GET',`history/${hid}`,captain)).participantLabel,'Code-Zero-captain name、member name');
});

test('native URL restore is private and its outbound snapshot excludes arbitrary stored data',async(t)=>{
  const {api,setup,service,login}=fixture(t),{member,captain,other,hid}=await setup();
  t.mock.timers.setTime(CLOCK+10820e3);
  const row=service.store.one('SELECT * FROM histories WHERE id=?',hid)!;
  const rules=JSON.parse(row.rules_json),records=JSON.parse(row.submissions_json);
  rules.cookie='never-return-cookie';rules.source='never-return-source';records[0].source='never-return-code';records[0].password='never-return-password';
  service.store.run('UPDATE histories SET rules_json=?,submissions_json=? WHERE id=?',JSON.stringify(rules),JSON.stringify(records),hid);
  const result=await api('POST','client/restore',member,{contestUrl:URL});
  assert.equal(result.snapshot.historyId,hid);assert.equal(result.snapshot.isOwner,false);
  const encoded=JSON.stringify(result);for(const secret of ['never-return','private@example','private-number','password','cookie','source','members'])assert(!encoded.includes(secret),secret);
  assert.equal((await api('POST','client/restore',captain,{contestUrl:URL})).snapshot.isOwner,true);
  assert.equal((await api('POST','client/restore',other,{contestUrl:URL,historyId:hid})).snapshot,null);
  const stranger=await login('stranger');assert.equal((await api('POST','client/restore',stranger,{contestUrl:URL})).snapshot,null);
});

test('a guessed default duration and an early complete flag never unlock teammate restore',async(t)=>{
  const {api,setup,service}=fixture(t),{member,room,hid}=await setup();
  t.mock.timers.setTime(CLOCK+10799e3);
  await api('POST',`client/rooms/${room.id}/restore`,member,{},409);
  service.store.run('UPDATE room_rules SET duration_seconds=0 WHERE room_id=?',room.id);
  t.mock.timers.setTime(CLOCK+7*3600e3);
  assert.equal((await api('POST',`client/rooms/${room.id}/authorize`,member,{})).canRestore,false);
  await api('POST',`client/rooms/${room.id}/restore`,member,{},409);
  assert.equal((await api('POST','client/restore',member,{contestUrl:URL})).snapshot,null);
  assert(service.store.one('SELECT id FROM histories WHERE id=?',hid));
});

test('restore includes the caller own later personal practice without reading another team member practice',async(t)=>{
  const {api,setup}=fixture(t),{member,captain,room,payload}=await setup();
  t.mock.timers.setTime(CLOCK+11000e3);
  const personal=(localId:string,sid:string)=>({...payload,localId,mode:'bt',roomId:undefined,participantId:undefined,roomStartsAt:undefined,startedAt:undefined,submissions:[{id:sid,problem:'B',submittedAt:new Date(CLOCK+10900e3).toISOString(),verdict:'CORRECT',practice:true}]});
  const own=await api('POST','client/sync',member,personal('bt-qoj-3617-own','501'));
  await api('POST','client/sync',captain,personal('bt-qoj-3617-captain','999'));
  for(const restored of [await api('POST',`client/rooms/${room.id}/restore`,member,{}),(await api('POST','client/restore',member,{contestUrl:URL})).snapshot]) {
    assert.equal(restored.personalPractice.historyId,own.id);assert.equal(restored.personalPractice.localId,'bt-qoj-3617-own');
    assert.deepEqual(restored.personalPractice.submissions.map((s:Data)=>s.id),['501']);
    assert(!JSON.stringify(restored).includes('"999"'));
  }
});

test('history projects current room title without changing legacy native identity or stored snapshots',async(t)=>{
  const {api,setup,service}=fixture(t),{owner,captain,member,room,payload,hid}=await setup();
  const stored=service.store.one('SELECT contest_name,contest_url,problems_json,rules_json,submissions_json FROM histories WHERE id=?',hid)!;
  assert.equal(stored.contest_name,'Native contest');
  for(const viewer of [captain,member]) {
    assert.equal((await api('GET',`history/${hid}`,viewer)).contestName,'Restore room');
    assert.equal((await api('GET',`rooms/${room.id}/mysubmit`,viewer)).history.contestName,'Restore room');
    assert.equal((await api('GET','history?search=restore',viewer))[0].id,hid);
    assert.equal((await api('GET','history?search=Native%20contest',viewer))[0].id,hid);
  }
  await api('PATCH',`rooms/${room.id}`,owner,{name:'CCPC Friday / Association Training'});
  assert.equal((await api('GET',`history/${hid}`,member)).contestName,'CCPC Friday / Association Training');
  assert.equal((await api('GET','history?search=association',member))[0].id,hid);
  await api('POST','client/sync',captain,{...payload,revision:2});
  assert.deepEqual(service.store.one('SELECT contest_name,contest_url,problems_json,rules_json,submissions_json FROM histories WHERE id=?',hid),stored);
  t.mock.timers.setTime(CLOCK+10820e3);
  const restored=await api('POST',`client/rooms/${room.id}/restore`,member,{});
  assert.equal(restored.roomName,'CCPC Friday / Association Training');assert.equal(restored.contest.name,'Native contest');
  assert.equal(restored.contest.url,URL);assert.equal(restored.participantId,payload.participantId);
  const legacy=service.store.one('SELECT * FROM histories WHERE id=?',hid)!;
  assert.equal((service as any).historyView({...legacy,room_id:'missing-legacy-room'},false,captain.user).contestName,'Native contest');
  const individual={...payload,localId:'standalone-native',roomId:undefined,participantId:undefined,roomStartsAt:undefined};
  const nativeId=(await api('POST','client/sync',captain,individual)).id;
  assert.equal((await api('GET',`history/${nativeId}`,captain)).contestName,'Native contest');
});
