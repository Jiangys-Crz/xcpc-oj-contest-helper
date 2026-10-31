import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CloudService, handleRequest } from '../lib/cloud';

type Data=Record<string,any>;
const EPOCH=Date.parse('2030-07-01T00:00:00Z');
function fixture(t:any) {
  t.mock.timers.enable({apis:['Date'],now:EPOCH});
  const root=mkdtempSync(join(tmpdir(),'vp-identity-'));
  const profiles:Record<string,Data>={
    owner:{userId:'owner',userAccount:'owner',userName:'Owner',realName:'房主'},
    captain:{userId:'captain',userAccount:'captain',userName:'Captain-nick',realName:'张-队长'},
    member:{userId:'member',userAccount:'member',userName:'Member-nick',realName:'李同学'},
    student:{userId:'student',userAccount:'student',userName:'昵称-with-hyphens',realName:'王-同学'}
  };
  const service=new CloudService({path:join(root,'vp.sqlite'),adminPassword:'Fixture-admin-only!',externalAppKey:'ak_fixture',publicUrl:'https://vp.example.test',fetch:async(_url:any,options:any)=>{
    const {account}=JSON.parse(options.body);
    return Response.json({code:200,data:{...profiles[account],email:'private@example.invalid',numberId:'private-number-id'}});
  }});
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,token?:string,body?:Data,status=200)=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})}),path.split('/'),service);
    const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));return result.data;
  };
  const login=async(account:string)=>api('POST','auth/login',undefined,{account,password:'fixture-password'});
  const room=async(owner:Data)=>api('POST','rooms',owner.token,{name:'Identity training',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(EPOCH+3600e3).toISOString(),freezeMinutes:0,scoringSystem:'ICPC'});
  const accept=async(notice:Data,user:Data)=>api('POST',`notifications/${notice.id}/respond`,user.token,{accept:true});
  const register=async(r:Data,owner:Data,user:Data,target:Data={userId:user.user.id})=>{
    await accept(await api('POST',`rooms/${r.id}/invitations`,owner.token,target),user);
    return (await api('POST',`client/rooms/${r.id}/authorize`,user.token,{})).participant;
  };
  return {service,api,login,room,accept,register,profiles};
}
const snapshot=(localId:string,mode='vp'):Data=>({
  localId,mode,status:'running',startedAt:new Date(EPOCH-600e3).toISOString(),rank:'57',rankSource:'original',
  contest:{platform:'qoj',contestId:'3617',url:'https://qoj.ac/contest/3617',name:'Native contest',problems:['A','B'],rules:{duration:7200,contest_format:'ICPC'}},
  solved:0,penalty:0,cells:{A:{tries:0,solved:false,practiceSolved:false}},submissions:[],revision:1
});
const identity=(value:Data)=>({participantTitle:value.participantTitle,participantSubtitle:value.participantSubtitle});

test('team identity uses its accepted name and roster across authorize, board, history and my submissions',async(t)=>{
  const {service,api,login,room,accept,register,profiles}=fixture(t);
  const owner=await login('owner'),captain=await login('captain'),member=await login('member');
  const team=await api('POST','teams',captain.token,{nameZh:'Code-Zero-Training',nameEn:'Code Zero'});
  await accept(await api('POST',`teams/${team.id}/invitations`,captain.token,{userId:member.user.id}),member);
  const r=await room(owner),p=await register(r,owner,captain,{teamId:team.id});
  const expected={participantTitle:'Code-Zero-Training',participantSubtitle:'张-队长、李同学'};
  assert.deepEqual(identity(p),expected);
  assert.equal(p.label,'Code-Zero-Training-张-队长、李同学');
  const pending=(await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0];
  assert.deepEqual(identity(pending),expected);assert.equal(pending.historyId,null);
  const data={...snapshot('team-native'),roomId:r.id,participantId:p.id,roomStartsAt:r.startsAt,startedAt:r.startsAt};
  const hid=(await api('POST','client/sync',captain.token,data)).id;
  for(const account of [captain,member]) {
    const history=await api('GET',`history/${hid}`,account.token);
    const mine=await api('GET',`rooms/${r.id}/mysubmit`,account.token);
    assert.deepEqual(identity(history),expected);assert.deepEqual(mine.history,history);
    assert.deepEqual(identity((await api('GET','history',account.token))[0]),expected);
  }
  await api('PATCH',`teams/${team.id}`,captain.token,{nameZh:'Renamed-team',removeUserId:member.user.id});
  profiles.captain={...profiles.captain,userName:'Changed nickname',realName:'Changed real name'};
  await login('captain');
  await api('POST','client/sync',captain.token,{...data,revision:2,participantTitle:'Spoofed team',participantSubtitle:'Spoofed member'});
  assert.deepEqual(identity((await api('GET',`rooms/${r.id}/leaderboard`,owner.token)).rows[0]),expected);
  assert.deepEqual(identity(await api('GET',`history/${hid}`,member.token)),expected);
  assert.deepEqual(identity((await api('POST',`client/rooms/${r.id}/authorize`,captain.token,{})).participant),expected);
  const stored=service.store.one('SELECT participant_title,participant_subtitle FROM histories WHERE id=?',hid)!;
  assert.equal(stored.participant_title,expected.participantTitle);assert.equal(stored.participant_subtitle,expected.participantSubtitle);
  assert(!JSON.stringify(expected).includes('private@'));assert(!JSON.stringify(expected).includes('private-number'));
});

test('individual room identity shows nickname and real name on separate fields without splitting hyphens',async(t)=>{
  const {api,login,room,register,profiles}=fixture(t),owner=await login('owner'),student=await login('student');
  const r=await room(owner),p=await register(r,owner,student);
  const expected={participantTitle:'昵称-with-hyphens',participantSubtitle:'王-同学'};
  assert.deepEqual(identity(p),expected);
  assert.deepEqual(identity((await api('GET',`rooms/${r.id}`,student.token)).participation),expected);
  profiles.student={...profiles.student,userName:'Later nickname',realName:'Later real name'};
  await login('student');
  const data={...snapshot('personal-native'),roomId:r.id,participantId:p.id,roomStartsAt:r.startsAt,startedAt:r.startsAt};
  const hid=(await api('POST','client/sync',student.token,data)).id;
  assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),expected);
  assert.deepEqual(identity((await api('GET',`rooms/${r.id}/mysubmit`,student.token)).history),expected);
});

test('standalone VP, live and practice save association identity once and keep it after profile changes',async(t)=>{
  const {api,login,profiles,service}=fixture(t),student=await login('student');
  const expected={participantTitle:'昵称-with-hyphens',participantSubtitle:'王-同学'},histories:Data[]=[];
  for(const mode of ['vp','live','bt']) {
    const data=snapshot('standalone-'+mode,mode),hid=(await api('POST','client/sync',student.token,data)).id;
    histories.push({data,hid});assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),expected);
  }
  profiles.student={...profiles.student,userName:'Changed nickname',realName:'Changed name'};
  await login('student');
  for(const {data,hid} of histories) {
    await api('POST','client/sync',student.token,{...data,revision:2,participantTitle:'Untrusted native nickname',participantSubtitle:'Untrusted name'});
    const history=await api('GET',`history/${hid}`,student.token);
    assert.deepEqual(identity(history),expected);assert.equal(history.participantLabel,'昵称-with-hyphens-王-同学');
    assert.equal(service.store.one('SELECT participant_title FROM histories WHERE id=?',hid)!.participant_title,expected.participantTitle);
  }
});

test('legacy labels only separate an exact real-name suffix and never guess at the first hyphen',async(t)=>{
  const {api,login,service,profiles}=fixture(t),student=await login('student');
  const data=snapshot('legacy-native'),hid=(await api('POST','client/sync',student.token,data)).id;
  service.store.run("UPDATE histories SET participant_label=?,participant_title='',participant_subtitle='' WHERE id=?",'Legacy-hyphen-nickname-王-同学',hid);
  assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),{participantTitle:'Legacy-hyphen-nickname',participantSubtitle:'王-同学'});
  profiles.student={...profiles.student,realName:'A different name'};await login('student');
  const combined='Legacy-hyphen-nickname-王-同学';
  assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),{participantTitle:combined,participantSubtitle:''});
  await api('POST','client/sync',student.token,{...data,revision:2});
  assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),{participantTitle:combined,participantSubtitle:''});
  assert.equal(service.store.one('SELECT participant_label FROM histories WHERE id=?',hid)!.participant_label,combined);
});

test('a missing real name stays empty instead of duplicating the nickname',async(t)=>{
  const {api,login,profiles}=fixture(t);profiles.student={...profiles.student,realName:''};
  const student=await login('student'),hid=(await api('POST','client/sync',student.token,snapshot('no-real-name'))).id;
  assert.deepEqual(identity(await api('GET',`history/${hid}`,student.token)),{participantTitle:'昵称-with-hyphens',participantSubtitle:''});
});
