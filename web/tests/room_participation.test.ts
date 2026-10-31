import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CloudService, handleRequest } from '../lib/cloud';

type Data=Record<string,any>;
const EPOCH=Date.parse('2030-06-01T00:00:00Z');
function fixture(t:any) {
  t.mock.timers.enable({apis:['Date'],now:EPOCH});
  const root=mkdtempSync(join(tmpdir(),'vp-participation-'));
  const options={path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'ak_fixture',publicUrl:'https://vp.example.test',fetch:async(_url:any,options:any)=>{
    const {account}=JSON.parse(options.body);
    return Response.json({code:200,data:{userId:account,userAccount:account,userName:account+' nickname',realName:account+' name',numberId:'2030-'+account,email:account+'@example.test'}});
  }};
  const service=new CloudService(options);
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,token?:string,body?:Data,status=200,instance=service)=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),path.split('/'),instance);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return result.data;
  };
  const login=async(account:string)=>api('POST','auth/login',undefined,{account,password:'fixture-password'});
  const room=async(owner:Data)=>api('POST','rooms',owner.token,{name:'Registration training',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(EPOCH+3600e3).toISOString(),freezeMinutes:0,scoringSystem:'ICPC'});
  const invite=async(roomId:string,owner:Data,target:Data)=>api('POST',`rooms/${roomId}/invitations`,owner.token,target);
  const accept=async(notice:Data,user:Data)=>api('POST',`notifications/${notice.id}/respond`,user.token,{accept:true});
  const team=async(captain:Data,members:Data[]=[])=>{
    const value=await api('POST','teams',captain.token,{nameZh:'Training team',nameEn:'Training'});
    for(const member of members)await accept(await api('POST',`teams/${value.id}/invitations`,captain.token,{userId:member.user.id}),member);
    return value;
  };
  const register=async(r:Data,owner:Data,user:Data,target:Data={userId:user.user.id})=>{
    await accept(await invite(r.id,owner,target),user);
    return (await api('POST',`client/rooms/${r.id}/authorize`,user.token,{})).participant;
  };
  return {service,options,api,login,room,invite,accept,team,register};
}
const snapshot=(room:Data,p:Data,localId='native-session'):Data=>({
  roomId:room.id,participantId:p.id,roomStartsAt:room.startsAt,localId,mode:'vp',status:'scheduled',startedAt:room.startsAt,
  contest:{platform:'qoj',contestId:'3617',url:room.contestUrl,name:'Native contest',problems:['A','B'],rules:{duration:7200,contest_format:'ICPC'}},
  rank:'8',solved:0,penalty:0,cells:{A:{tries:0,solved:false,practiceSolved:false}},submissions:[],revision:1
});

test('an individual cancels before start, retains data, then accepts a fresh invitation and resumes the same identity',async(t)=>{
  const {service,options,api,login,room,invite,accept,register}=fixture(t);
  const owner=await login('owner'),user=await login('student'),outsider=await login('outsider'),r=await room(owner),p=await register(r,owner,user);
  const data=snapshot(r,p),legacy={...data};delete legacy.participantId;delete legacy.roomStartsAt;
  const hid=(await api('POST','client/sync',user.token,legacy)).id;
  const print=await api('POST','client/prints',user.token,{roomId:r.id,participantId:p.id,filename:'a.cpp',language:'C++',code:'int main() {}'});
  const printId=print.id;
  await api('DELETE',`rooms/${r.id}/participation`,undefined,undefined,401);
  await api('DELETE',`rooms/${r.id}/participation`,user.token,{participantId:'stale-identity'},409);
  assert.equal((await api('GET',`rooms/${r.id}`,owner.token)).participants.length,1);
  const value=await api('DELETE',`rooms/${r.id}/participation`,user.token,{participantId:p.id});
  assert.deepEqual(value,{withdrawn:true,roomId:r.id,participantId:p.id});
  assert.equal((await api('GET','rooms',user.token)).length,0);
  assert.equal((await api('GET',`rooms/${r.id}`,owner.token)).participants.length,0);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows.length,0);
  await api('POST',`client/rooms/${r.id}/authorize`,user.token,{},403);
  await api('POST','client/sync',user.token,{...data,revision:2},403);
  await api('POST','client/prints',user.token,{roomId:r.id,filename:'b.cpp',code:'source'},403);
  await api('DELETE',`rooms/${r.id}/participation`,user.token,undefined,404);
  assert.equal((await api('GET','history',user.token))[0].id,hid);
  assert.equal((await api('GET',`history/${hid}`,user.token)).status,'scheduled');
  assert.equal((await api('GET',`prints/${printId}`,user.token)).code,'int main() {}');
  await api('GET',`prints/${printId}`,outsider.token,undefined,404);
  for(const account of [owner,user])assert((await api('GET','notifications',account.token)).some((n:Data)=>n.title==='VP 报名已取消'));
  assert.equal(service.store.one('SELECT COUNT(*) n FROM participant_members WHERE participant_id=?',p.id)!.n,0);
  assert.equal(service.store.one("SELECT COUNT(*) n FROM audit WHERE action='room.participant.withdrawn' AND entity_id=?",p.id)!.n,1);
  const reopened=new CloudService(options);
  try {
    assert.equal((await api('GET',`history/${hid}`,user.token,undefined,200,reopened)).id,hid);
    assert.equal((await api('GET',`rooms/${r.id}`,owner.token,undefined,200,reopened)).participants.length,0);
  }finally{reopened.close();}
  const notice=await invite(r.id,owner,{userId:user.user.id});await accept(notice,user);
  const resumed=await api('POST',`client/rooms/${r.id}/authorize`,user.token,{});
  assert.equal(resumed.participant.id,p.id);assert.equal(resumed.canSubmit,true);
  assert.equal((await api('GET',`rooms/${r.id}/prints`,user.token))[0].id,printId);
  assert.equal((await api('POST','client/sync',user.token,{...legacy,revision:2})).id,hid);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0].participantId,p.id);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM participants')!.n,1);
});

test('only a team captain can cancel the whole roster, while the room owner can remove a team or individual',async(t)=>{
  const {service,api,login,room,team,register}=fixture(t);
  const owner=await login('owner'),captain=await login('captain'),member=await login('member'),person=await login('individual'),stranger=await login('stranger');
  const r=await room(owner),another=await room(owner),squad=await team(captain,[member]);
  const p=await register(r,owner,captain,{teamId:squad.id}),solo=await register(r,owner,person);
  const data=snapshot(r,p),hid=(await api('POST','client/sync',captain.token,data)).id;
  const print=await api('POST','client/prints',member.token,{roomId:r.id,participantId:p.id,filename:'notes.cpp',code:'team-private'});
  await api('DELETE',`rooms/${r.id}/participation`,member.token,{participantId:p.id},403);
  await api('DELETE',`rooms/${r.id}/participants/${p.id}`,stranger.token,undefined,403);
  await api('DELETE',`rooms/${another.id}/participants/${p.id}`,owner.token,undefined,404);
  await api('DELETE',`rooms/${r.id}/participants/${p.id}`,owner.token);
  for(const account of [captain,member]) {
    assert.equal((await api('GET','rooms',account.token)).length,0);
    assert.equal((await api('GET','history',account.token))[0].id,hid);
    assert.equal((await api('GET',`history/${hid}`,account.token)).participantType,'team');
    assert.equal((await api('GET',`prints/${print.id}`,account.token)).code,'team-private');
    assert((await api('GET','notifications',account.token)).some((n:Data)=>n.title==='VP 参赛者已移除'));
  }
  await api('GET',`history/${hid}`,person.token,undefined,404);
  await api('GET',`prints/${print.id}`,owner.token,undefined,404);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows.length,1);
  await api('DELETE',`rooms/${r.id}/participants/${solo.id}`,owner.token);
  assert.equal((await api('GET',`rooms/${r.id}`,owner.token)).participants.length,0);
  const resumed=await register(r,owner,captain,{teamId:squad.id});assert.equal(resumed.id,p.id);
  await api('DELETE',`rooms/${r.id}/participation`,captain.token,{participantId:p.id});
  assert.equal(service.store.one('SELECT COUNT(*) n FROM team_members WHERE team_id=?',squad.id)!.n,2);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
});

test('cancellation and removal are locked exactly at the shared start time and cannot change reservations or membership',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),first=await login('first'),second=await login('second');
  const r=await room(owner),p=await register(r,owner,first),q=await register(r,owner,second);
  t.mock.timers.setTime(Date.parse(r.startsAt)-1);
  await api('DELETE',`rooms/${r.id}/participants/${q.id}`,owner.token);
  t.mock.timers.setTime(Date.parse(r.startsAt));
  const notices=service.store.one('SELECT COUNT(*) n FROM notifications')!.n;
  await api('DELETE',`rooms/${r.id}/participation`,first.token,{participantId:p.id},409);
  await api('DELETE',`rooms/${r.id}/participants/${p.id}`,owner.token,undefined,409);
  t.mock.timers.setTime(Date.parse(r.startsAt)+1);
  await api('DELETE',`rooms/${r.id}/participation`,first.token,undefined,409);
  await api('DELETE',`rooms/${r.id}/participants/${p.id}`,owner.token,undefined,409);
  assert.equal((await api('GET',`rooms/${r.id}`,owner.token)).participants.length,1);
  assert.equal((await api('POST',`client/rooms/${r.id}/authorize`,first.token,{})).participant.id,p.id);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM notifications')!.n,notices);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM participant_departures')!.n,1);
});

test('native reservations keep schedule locks after cancellation, while an unreserved reschedule creates a fresh identity and rejects stale clients',async(t)=>{
  const {service,api,login,room,register}=fixture(t),owner=await login('owner'),user=await login('student');
  const reserved=await room(owner),p=await register(reserved,owner,user);
  await api('POST','client/sync',user.token,snapshot(reserved,p));
  await api('DELETE',`rooms/${reserved.id}/participation`,user.token,{participantId:p.id});
  for(const patch of [{startsAt:new Date(EPOCH+7200e3).toISOString()},{contestUrl:'https://qoj.ac/contest/4606'},{freezeMinutes:20},{scoringSystem:'CCPC'}])await api('PATCH',`rooms/${reserved.id}`,owner.token,patch,409);
  await api('PATCH',`rooms/${reserved.id}`,owner.token,{name:'Renamed reservation'});
  const empty=await room(owner),old=await register(empty,owner,user);
  await api('DELETE',`rooms/${empty.id}/participation`,user.token,{participantId:old.id});
  const updated=await api('PATCH',`rooms/${empty.id}`,owner.token,{startsAt:new Date(EPOCH+7200e3).toISOString()});
  const active=await register(updated,owner,user);assert.notEqual(active.id,old.id);
  const stale=snapshot(empty,old,'new-local');
  await api('POST','client/sync',user.token,stale,409);
  await api('POST','client/sync',user.token,{...stale,participantId:active.id},409);
  await api('POST','client/prints',user.token,{roomId:empty.id,participantId:old.id,filename:'a.cpp',code:'stale'},409);
  await api('POST','client/prints',user.token,{roomId:empty.id,participantId:active.id,roomStartsAt:empty.startsAt,filename:'a.cpp',code:'stale'},409);
  await api('DELETE',`rooms/${empty.id}/participation`,user.token,{participantId:old.id},409);
  assert.equal((await api('GET',`rooms/${empty.id}`,owner.token)).participants[0].id,active.id);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM prints')!.n,0);
  const current=snapshot(updated,active,'new-local');
  await api('POST','client/sync',user.token,current);
  // A changed identity requires both guards, including when no prior snapshot
  // reached the server. An old queued upload cannot bind to the replacement.
  delete current.participantId;delete current.roomStartsAt;current.revision=2;
  await api('POST','client/sync',user.token,current,409);
});

test('an unsynced legacy snapshot or print cannot cross into a new roster or rescheduled identity',async(t)=>{
  const {service,api,login,room,team,register,accept}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member'),newMember=await login('new-member');
  const r=await room(owner),squad=await team(captain,[member]),old=await register(r,owner,captain,{teamId:squad.id});
  const queued=snapshot(r,old,'offline-private-session');delete queued.participantId;delete queued.roomStartsAt;
  queued.submissions=[{id:'offline-private',problem:'A',submittedAt:new Date(EPOCH-1000).toISOString(),verdict:'WRONG-ANSWER',practice:false}];
  const queuedPrint={roomId:r.id,filename:'private.cpp',code:'old team private source'};
  await api('DELETE',`rooms/${r.id}/participation`,captain.token);
  await accept(await api('POST',`teams/${squad.id}/invitations`,captain.token,{userId:newMember.user.id}),newMember);
  const updated=await api('PATCH',`rooms/${r.id}`,owner.token,{startsAt:new Date(EPOCH+7200e3).toISOString()});
  const replacement=await register(updated,owner,captain,{teamId:squad.id});assert.notEqual(replacement.id,old.id);
  for(const guards of [{},{participantId:replacement.id},{roomStartsAt:updated.startsAt}]) {
    await api('POST','client/sync',captain.token,{...queued,...guards},409);
    for(const operator of [captain,member])await api('POST','client/prints',operator.token,{...queuedPrint,...guards},409);
  }
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,0);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM prints')!.n,0);
  assert.deepEqual(await api('GET','history',newMember.token),[]);
  assert.deepEqual((await api('GET',`rooms/${r.id}/mysubmit`,newMember.token)).submissions,[]);
  await api('POST','client/sync',captain.token,snapshot(updated,replacement,'fresh-new-session'));
  await api('POST','client/prints',member.token,{...queuedPrint,participantId:replacement.id,roomStartsAt:updated.startsAt,code:'new team source'});
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM prints')!.n,1);
  assert.deepEqual((await api('GET',`rooms/${r.id}/mysubmit`,newMember.token)).submissions,[]);
  // With a formerly ordinary member now operating individually, the archived
  // roster still triggers the guard even though the old captain differs.
  await api('DELETE',`rooms/${r.id}/participation`,captain.token);
  const solo=await register(updated,owner,member);
  const legacySolo=snapshot(updated,solo,'member-old-queue');delete legacySolo.participantId;delete legacySolo.roomStartsAt;
  await api('POST','client/sync',member.token,legacySolo,409);
  await api('POST','client/prints',member.token,queuedPrint,409);
});

test('switching participation type or roster never grants a new member access to the previous team history',async(t)=>{
  const {api,login,room,team,register,accept}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member'),newMember=await login('new-member');
  const r=await room(owner),squad=await team(captain,[member]),old=await register(r,owner,captain,{teamId:squad.id});
  const hid=(await api('POST','client/sync',captain.token,snapshot(r,old))).id;
  const print=await api('POST','client/prints',member.token,{roomId:r.id,filename:'old.cpp',code:'old-roster-private'});
  await api('DELETE',`rooms/${r.id}/participation`,captain.token,{participantId:old.id});
  await accept(await api('POST',`teams/${squad.id}/invitations`,captain.token,{userId:newMember.user.id}),newMember);
  const next=await register(r,owner,captain,{teamId:squad.id});assert.notEqual(next.id,old.id);
  assert.equal(next.members.length,3);
  assert.equal((await api('GET','history',newMember.token)).length,0);
  await api('GET',`history/${hid}`,newMember.token,undefined,404);
  await api('GET',`prints/${print.id}`,newMember.token,undefined,404);
  assert.equal((await api('GET','history',member.token))[0].id,hid);
  await api('POST','client/sync',captain.token,{...snapshot(r,old),revision:2},409);
  await api('DELETE',`rooms/${r.id}/participation`,captain.token,{participantId:next.id});
  const individual=await register(r,owner,captain);assert.notEqual(individual.id,old.id);assert.equal(individual.type,'individual');
  await api('POST','client/sync',captain.token,{...snapshot(r,individual),revision:3},409);
});

test('withdrawal cancels overlapping outstanding room invitations so old notices cannot enroll a removed roster',async(t)=>{
  const {api,login,room,team,register,invite,accept}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member'),other=await login('other');
  const r=await room(owner),first=await team(captain,[member]),second=await team(other);
  await register(r,owner,captain,{teamId:first.id});
  const pending=await invite(r.id,owner,{teamId:second.id});
  await accept(await api('POST',`teams/${second.id}/invitations`,other.token,{userId:member.user.id}),member);
  await api('DELETE',`rooms/${r.id}/participation`,captain.token);
  const notice=(await api('GET','notifications',other.token)).find((n:Data)=>n.id===pending.id);
  assert.equal(notice.status,'cancelled');assert.equal(notice.canRespond,false);
  await api('POST',`notifications/${pending.id}/respond`,other.token,{accept:true},409);
  await accept(await invite(r.id,owner,{teamId:second.id}),other);
  assert.equal((await api('GET',`rooms/${r.id}`,owner.token)).participants.length,1);
});
