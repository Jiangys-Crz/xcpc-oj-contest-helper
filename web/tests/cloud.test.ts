import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CloudService, handleRequest } from '../lib/cloud';

const PASSWORD='fixture-password', APP_KEY='ak_fixture_only_never_real';
type Data=Record<string,any>;
const profiles:Data={
  captain:{userId:1,userAccount:'captain',userName:'队长昵称',realName:'张队长',numberId:'20260001',email:'captain@example.test'},
  member:{userId:2,userAccount:'member',userName:'队员昵称',realName:'李队员',numberId:'20260002',email:'member@example.test'},
  other:{userId:3,userAccount:'other',userName:'其他用户',realName:'王同学',numberId:'20260003',email:'other@example.test'},
  fourth:{userId:4,userAccount:'fourth',userName:'第四用户',realName:'赵同学',numberId:'20260004',email:'fourth@example.test'},
  owner:{userId:5,userAccount:'owner',userName:'创建者',realName:'蒋同学',numberId:'20260005',email:'owner@example.test'},
  admin:{userId:6,userAccount:'admin',userName:'协会管理员',realName:'管理员姓名',numberId:'20260006',email:'admin@example.test'}
};
function fixture(t:any) {
  const root=mkdtempSync(join(tmpdir(),'vp-cloud-test-'));
  const service=new CloudService({path:join(root,'vp.sqlite'),adminPassword:'Admin-Only-Test-123!',externalAppKey:APP_KEY,publicUrl:'https://vp.example.test',fetch:async(url:any,options:any)=>{
    assert.equal(url,'https://www.cwnupaa.com/api/user/external/login');
    const data=JSON.parse(options.body); assert.equal(data.appKey,APP_KEY); assert.equal(data.loginType,'password');
    return Response.json(data.credential===PASSWORD && profiles[data.account]?{code:200,data:profiles[data.account]}:{code:401,msg:'账号或密码错误'});
  }});
  t.after(()=>{service.close();rmSync(root,{recursive:true,force:true});});
  const api=async(method:string,path:string,body?:Data,token?:string,status=200,extra:Record<string,string>={})=>{
    const response=await handleRequest(new Request('https://vp.example.test/api/'+path,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{}),...extra},...(body?{body:JSON.stringify(body)}:{})}),path.split('/').map(v=>v.split('?')[0]),service);
    const result=await response.json(); assert.equal(response.status,status,JSON.stringify(result)); return {...result,response};
  };
  const login=async(account:string)=> (await api('POST','auth/login',{account,password:PASSWORD})).data;
  const admin=async()=> (await api('POST','auth/admin-login',{account:'admin',password:'Admin-Only-Test-123!'})).data;
  const room=async(token:string)=> (await api('POST','rooms',{name:'多人训练赛',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(Date.now()+3600e3).toISOString()},token)).data;
  const invite=async(roomId:string,creator:string,target:Data)=> (await api('POST',`rooms/${roomId}/invitations`,target,creator)).data.id;
  return {service,root,api,login,admin,room,invite};
}
const snapshot=(localId:string,revision=1):Data=>({
  localId,contest:{platform:'qoj',contestId:'3617',url:'https://qoj.ac/contest/3617',name:'The Training Contest',problems:['A','B']},mode:'vp',startedAt:new Date(Date.now()-600e3).toISOString(),rank:'87',solved:1,penalty:20,
  cells:{A:{tries:1,pending:0,solved:true,practiceSolved:false,solveTime:20},B:{tries:1,pending:1,solved:false,practiceSolved:false}},
  submissions:[{id:'123',problem:'A',submittedAt:new Date(Date.now()-300e3).toISOString(),verdict:'CORRECT',practice:false,url:'https://qoj.ac/submission/123'},{id:'124',problem:'B',submittedAt:new Date(Date.now()-100e3).toISOString(),verdict:'PENDING',practice:false}],revision
});

test('association login synchronizes all five fields, preserves administrator role and hashes tokens',async(t)=>{
  const {api,service,login,admin}=fixture(t); const user=await login('captain');
  assert.deepEqual(user.user,{id:user.user.id,useraccount:'captain',username:'队长昵称',realname:'张队长',numberid:'20260001',email:'captain@example.test',role:'user'});
  const session=service.store.one('SELECT * FROM sessions WHERE user_id=?',user.user.id)!;
  assert.notEqual(session.token_hash,user.token);assert.equal(session.token_hash.length,64);
  assert.equal((await api('GET','auth/me',undefined,user.token)).data.email,'captain@example.test');
  const localAdmin=await admin(), externalAdmin=await login('admin');
  assert.equal(externalAdmin.user.id,localAdmin.user.id);assert.equal(externalAdmin.user.role,'admin');assert.equal(externalAdmin.user.email,'admin@example.test');
  assert(!JSON.stringify(service.store.all('SELECT * FROM users')).includes('Admin-Only-Test-123!'));
  await api('POST','auth/login',{account:'captain',password:'incorrect'},undefined,401);
  await api('POST','auth/logout',{},user.token);await api('GET','auth/me',undefined,user.token,401);
});

test('API cookies, CSRF, settings masking, search privacy, body limits and rate limits are enforced',async(t)=>{
  const {api,service,login,admin}=fixture(t);const a=await admin(),u=await login('captain');
  const auth=await api('POST','auth/admin-login',{account:'admin',password:'Admin-Only-Test-123!'});
  const cookie=auth.response.headers.get('set-cookie')!;assert.match(cookie,/HttpOnly/);assert.match(cookie,/Secure/);assert.match(cookie,/SameSite=Lax/);
  const settings=(await api('GET','admin/settings',undefined,a.token)).data;
  assert(settings.externalAppKeyConfigured);assert(!JSON.stringify(settings).includes(APP_KEY));
  await api('PUT','admin/settings',{externalAppKey:'malicious'},u.token,403);
  await api('PUT','admin/settings',{externalBaseUrl:'http://127.0.0.1/api'},a.token,400);
  await api('POST','teams',{nameZh:'跨域',nameEn:'CSRF'},undefined,403,{Cookie:cookie.split(';')[0],Origin:'https://evil.example'});
  await api('POST','teams',{nameZh:'无来源',nameEn:'CSRF'},undefined,403,{Cookie:cookie.split(';')[0]});
  const results=(await api('GET','users?search=20260001',undefined,u.token)).data;assert.equal(results.length,1);assert.equal(results[0].email,'');
  assert.deepEqual((await api('GET','users?search=%',undefined,u.token)).data,[]);
  const tooLarge=new Request('https://vp.example.test/api/client/sync',{method:'POST',headers:{Authorization:'Bearer '+u.token},body:'x'.repeat(2*1024*1024+1)});
  assert.equal((await handleRequest(tooLarge,['client','sync'],service)).status,413);
  for(let i=0;i<12;i++)await api('POST','auth/admin-login',{account:'nonexistent',password:'wrong'},undefined,401);
  await api('POST','auth/admin-login',{account:'nonexistent',password:'wrong'},undefined,429);
});

test('team invitations allow reject/reinvite; acceptance is transactional with a three-member ceiling',async(t)=>{
  const {api,login}=fixture(t);const captain=await login('captain'),member=await login('member'),other=await login('other'),fourth=await login('fourth');
  const team=(await api('POST','teams',{nameZh:'代码零',nameEn:'Code Zero'},captain.token)).data;
  assert.equal(team.members.length,1);assert.equal(team.captainId,captain.user.id);
  const createInvite=async(user:Data)=>(await api('POST',`teams/${team.id}/invitations`,{userId:user.user.id},captain.token)).data.id;
  const first=await createInvite(member);
  await api('POST',`notifications/${first}/respond`,{accept:true},other.token,404);
  await api('POST',`notifications/${first}/respond`,{accept:false},member.token);
  const second=await createInvite(member),third=await createInvite(other),last=await createInvite(fourth);
  const concurrent=await Promise.all([api('POST',`notifications/${second}/respond`,{accept:true},member.token),api('POST',`notifications/${third}/respond`,{accept:true},other.token)]);
  assert.equal(concurrent.length,2);
  await api('POST',`notifications/${last}/respond`,{accept:true},fourth.token,409);
  assert.equal((await api('GET',`teams/${team.id}`,undefined,captain.token)).data.members.length,3);
  await api('PATCH',`teams/${team.id}`,{nameZh:'越权改名'},member.token,403);
  await api('PATCH',`teams/${team.id}`,{removeUserId:captain.user.id},captain.token,400);
  await api('PATCH',`teams/${team.id}`,{removeUserId:'missing-user'},captain.token,404);
  await api('PATCH',`teams/${team.id}`,{removeUserId:other.user.id},captain.token);
  await api('POST',`notifications/${last}/respond`,{accept:true},fourth.token);
  const notes=(await api('GET','notifications',undefined,captain.token)).data;assert(notes.some((n:Data)=>n.status==='info'&&n.title==='邀请已拒绝'));
  await api('DELETE',`teams/${team.id}`,undefined,captain.token);
  assert.deepEqual((await api('GET','teams',undefined,member.token)).data,[]);
});

test('mixed room participants require explicit invitations, prevent overlap and snapshot team membership',async(t)=>{
  const {api,login,room,invite}=fixture(t);const owner=await login('owner'),captain=await login('captain'),member=await login('member'),other=await login('other');
  const team=(await api('POST','teams',{nameZh:'代码零',nameEn:'Code Zero'},captain.token)).data;
  const tn=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;
  await api('POST',`notifications/${tn}/respond`,{accept:true},member.token);
  const r=await room(owner.token);assert.deepEqual(r.participants,[]);assert.equal(r.participation,null);
  await api('POST',`client/rooms/${r.id}/authorize`,{},owner.token,403);
  await api('GET',`rooms/${r.id}`,undefined,other.token,403);
  assert.deepEqual((await api('GET','rooms',undefined,member.token)).data,[]);
  const rn=await invite(r.id,owner.token,{teamId:team.id});await api('POST',`notifications/${rn}/respond`,{accept:true},member.token,404);
  await api('POST',`notifications/${rn}/respond`,{accept:false},captain.token);
  const again=await invite(r.id,owner.token,{teamId:team.id});await api('POST',`notifications/${again}/respond`,{accept:true},captain.token);
  const own=await invite(r.id,owner.token,{userId:owner.user.id});await api('POST',`notifications/${own}/respond`,{accept:true},owner.token);
  const auth=(await api('POST',`client/rooms/${r.id}/authorize`,{},captain.token)).data;
  assert.equal(auth.canSubmit,true);assert.equal(auth.participant.label,'代码零-张队长、李队员');
  assert.equal((await api('POST',`client/rooms/${r.id}/authorize`,{},member.token)).data.canSubmit,false);
  await api('POST',`rooms/${r.id}/invitations`,{userId:member.user.id},owner.token,409);
  await api('PATCH',`teams/${team.id}`,{nameZh:'新队名',removeUserId:member.user.id},captain.token);
  const snapshot=(await api('GET',`rooms/${r.id}`,undefined,member.token)).data.participation;
  assert.equal(snapshot.label,'代码零-张队长、李队员');assert.equal(snapshot.members.length,2);
  await api('POST','client/sync',{...snapshotFixture(),roomId:r.id},member.token,403);
  function snapshotFixture(){return {localId:'member',contest:{url:'https://qoj.ac/contest/3617',name:'Fixture',problems:[]},mode:'vp',rank:'—',solved:0,penalty:0,cells:{},submissions:[],revision:1};}
});

test('virtual prints are visible only to the same participant, including against the room owner',async(t)=>{
  const {api,login,room,invite}=fixture(t);const owner=await login('owner'),captain=await login('captain'),member=await login('member'),other=await login('other');
  const team=(await api('POST','teams',{nameZh:'机房组',nameEn:'Lab'},captain.token)).data;
  const n=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;await api('POST',`notifications/${n}/respond`,{accept:true},member.token);
  const r=await room(owner.token),rn=await invite(r.id,owner.token,{teamId:team.id});await api('POST',`notifications/${rn}/respond`,{accept:true},captain.token);
  const job=(await api('POST','client/prints',{roomId:r.id,filename:'../A.cpp',code:'<script>not executed</script>\nint main(){}'},captain.token)).data;
  assert.match(job.url,new RegExp(`/vp/${r.id}/print/`));
  assert.equal((await api('GET',`prints/${job.id}`,undefined,member.token)).data.filename,'A.cpp');
  assert.match((await api('GET',`prints/${job.id}`,undefined,captain.token)).data.code,/<script>/);
  assert.deepEqual((await api('GET',`rooms/${r.id}/prints`,undefined,owner.token)).data,[]);
  await api('GET',`prints/${job.id}`,undefined,owner.token,404);await api('GET',`prints/${job.id}`,undefined,other.token,404);
  await api('POST','client/prints',{roomId:r.id,filename:'x.cpp',code:'stolen'},other.token,403);
  await api('POST','client/prints',{roomId:r.id,filename:'x.cpp',code:'x'.repeat(512*1024+1)},member.token,400);
});

test('snapshot sync is idempotent, preserves final results, excludes secrets, and gives BT no timer',async(t)=>{
  const {api,login,service,root}=fixture(t);const u=await login('captain'),other=await login('other');
  const first=snapshot('local-1');first.cookie='secret-cookie';first.contest.api_secret='secret-key';
  const h=(await api('POST','client/sync',first,u.token)).data;
  assert.equal((await api('POST','client/sync',first,u.token)).data.id,h.id);
  let detail=(await api('GET',`history/${h.id}`,undefined,u.token)).data;assert.equal(detail.rank,'87');assert.equal(detail.submissions.length,2);
  await api('GET',`history/${h.id}`,undefined,other.token,404);
  const final=snapshot('local-1',2);final.submissions[1].verdict='CORRECT';final.submissions[1].practice=true;final.cells.B={tries:1,pending:0,solved:false,practiceSolved:true};final.endedAt=new Date().toISOString();
  await api('POST','client/sync',final,u.token);
  const late=snapshot('local-1',3);late.rank='1';await api('POST','client/sync',late,u.token);
  detail=(await api('GET',`history/${h.id}`,undefined,u.token)).data;
  assert.equal(detail.submissions.find((s:Data)=>s.id==='124').verdict,'CORRECT');assert.equal(detail.cells.B.practiceSolved,true);assert.equal(detail.rank,'87');
  const stale=snapshot('local-1',1);assert.equal((await api('POST','client/sync',stale,u.token)).data.stale,true);
  const bt=snapshot('local-bt');bt.mode='bt';bt.endedAt=new Date().toISOString();
  const btid=(await api('POST','client/sync',bt,u.token)).data.id;
  const btd=(await api('GET',`history/${btid}`,undefined,u.token)).data;assert.equal(btd.startedAt,null);assert.equal(btd.endedAt,null);
  assert.equal((await api('GET','history?search=Training&type=bt',undefined,u.token)).data.length,1);
  assert(!JSON.stringify(service.store.all('SELECT * FROM histories')).includes('secret-cookie'));
  assert(!JSON.stringify(service.store.all('SELECT * FROM histories')).includes('secret-key'));
  const raw=readFileSync(join(root,'vp.sqlite-wal')).toString();assert(!raw.includes(PASSWORD));
});

test('room sync only accepts captain and the configured contest; leaderboard shows original rank',async(t)=>{
  const {api,login,room,invite}=fixture(t);const owner=await login('owner'),captain=await login('captain'),other=await login('other');
  const r=await room(owner.token),n=await invite(r.id,owner.token,{userId:captain.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},captain.token);
  const otherN=await invite(r.id,owner.token,{userId:other.user.id});await api('POST',`notifications/${otherN}/respond`,{accept:true},other.token);
  const bad=snapshot('bad');bad.roomId=r.id;bad.contest.url='https://qoj.ac/contest/9999';await api('POST','client/sync',bad,captain.token,409);
  const data=snapshot('captain-room');data.roomId=r.id;const hid=(await api('POST','client/sync',data,captain.token)).data.id;
  const second=snapshot('other-room');second.roomId=r.id;second.rank='991';second.solved=2;second.penalty=100;await api('POST','client/sync',second,other.token);
  const board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  assert.deepEqual(board.rows.map((r:Data)=>r.rank),['991','87']);assert.deepEqual(board.problems,['A','B']);
  assert.equal((await api('GET',`history/${hid}`,undefined,other.token)).data.id,hid);
  assert.deepEqual((await api('GET','history',undefined,owner.token)).data,[]);
  await api('PATCH',`rooms/${r.id}`,{contestUrl:'https://qoj.ac/contest/4000'},owner.token,409);
  await api('PATCH',`rooms/${r.id}`,{startsAt:new Date(Date.now()+7200e3).toISOString()},owner.token,409);
  await api('PATCH',`rooms/${r.id}`,{name:'更名训练'},owner.token);
});

test('new snapshots accept calibrated native start time, reject unsafe problem labels, and track failed reservations',async(t)=>{
  const {api,login}=fixture(t),u=await login('captain');
  const first=snapshot('clock-test'), hid=(await api('POST','client/sync',first,u.token)).data.id;
  const calibrated=snapshot('clock-test',2);calibrated.startedAt=new Date(Date.parse(first.startedAt)+16000).toISOString();
  await api('POST','client/sync',calibrated,u.token);assert.equal((await api('GET',`history/${hid}`,undefined,u.token)).data.startedAt,calibrated.startedAt);
  for(const label of ['__proto__','constructor','prototype','too/unsafe']) {
    const unsafe=snapshot('unsafe-'+label);unsafe.contest.problems=[label];unsafe.submissions=[];unsafe.cells={};await api('POST','client/sync',unsafe,u.token,400);
  }
  const failed=snapshot('failed-reservation');failed.startedAt=new Date(Date.now()+3600e3).toISOString();failed.endedAt=new Date().toISOString();failed.status='failed';
  const fid=(await api('POST','client/sync',failed,u.token)).data.id;
  const detail=(await api('GET',`history/${fid}`,undefined,u.token)).data;assert.equal(detail.status,'failed');assert.equal(detail.startedAt,null);
});

test('team history is shared with every accepted snapshot member and explicitly identifies participation type',async(t)=>{
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member'),other=await login('other');
  const team=(await api('POST','teams',{nameZh:'团队历史',nameEn:'Shared History'},captain.token)).data;
  const inviteId=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;
  await api('POST',`notifications/${inviteId}/respond`,{accept:true},member.token);
  const r=await room(owner.token),roomInvite=await invite(r.id,owner.token,{teamId:team.id});await api('POST',`notifications/${roomInvite}/respond`,{accept:true},captain.token);
  const payload=snapshot('shared-team-history');payload.roomId=r.id;
  const hid=(await api('POST','client/sync',payload,captain.token)).data.id;
  for(const person of [captain,member]) {
    const list=(await api('GET','history',undefined,person.token)).data;
    assert.equal(list.find((h:Data)=>h.id===hid).participantType,'team');
    assert.equal((await api('GET',`history/${hid}`,undefined,person.token)).data.participantType,'team');
  }
  assert.deepEqual((await api('GET','history',undefined,other.token)).data,[]);
  await api('GET',`history/${hid}`,undefined,other.token,404);
  await api('PATCH',`teams/${team.id}`,{removeUserId:member.user.id},captain.token);
  assert.equal((await api('GET','history',undefined,member.token)).data[0].id,hid);
  const personal=(await api('POST','client/sync',snapshot('personal-history'),captain.token)).data.id;
  assert.equal((await api('GET',`history/${personal}`,undefined,captain.token)).data.participantType,'individual');
  await api('GET',`history/${personal}`,undefined,member.token,404);
});

test('a previously synced standalone VP upgrades to its matching room without duplicating or losing data',async(t)=>{
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member');
  const team=(await api('POST','teams',{nameZh:'原记录关联',nameEn:'Upgrade'},captain.token)).data;
  const tn=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;await api('POST',`notifications/${tn}/respond`,{accept:true},member.token);
  const r=await room(owner.token),rn=await invite(r.id,owner.token,{teamId:team.id});await api('POST',`notifications/${rn}/respond`,{accept:true},captain.token);
  const payload=snapshot('pre-synced-standalone',10);payload.startedAt=r.startsAt;
  const hid=(await api('POST','client/sync',payload,captain.token)).data.id;
  assert.equal((await api('GET',`history/${hid}`,undefined,captain.token)).data.participantType,'individual');
  const stale={...payload,roomId:r.id,revision:9,submissions:[],cells:{}};
  const upgraded=(await api('POST','client/sync',stale,captain.token)).data;
  assert.equal(upgraded.id,hid);assert.equal(upgraded.upgraded,true);
  const detail=(await api('GET',`history/${hid}`,undefined,member.token)).data;
  assert.equal(detail.participantType,'team');assert.equal(detail.roomId,r.id);assert.equal(detail.startedAt,r.startsAt);assert.equal(detail.submissions.length,2);
  assert.equal((await api('GET','history',undefined,captain.token)).data.length,1);
  assert.equal((await api('GET','history',undefined,member.token)).data.length,1);
  const next:Data={...payload,roomId:r.id,revision:11};next.submissions=[...payload.submissions,{id:'125',problem:'A',submittedAt:payload.startedAt,verdict:'WRONG-ANSWER',practice:true}];
  assert.equal((await api('POST','client/sync',next,captain.token)).data.id,hid);
  assert.equal((await api('GET',`history/${hid}`,undefined,member.token)).data.submissions.length,3);
  const r2=await room(owner.token),rn2=await invite(r2.id,owner.token,{teamId:team.id});await api('POST',`notifications/${rn2}/respond`,{accept:true},captain.token);
  await api('POST','client/sync',{...payload,roomId:r2.id,revision:12},captain.token,409);
  await api('POST','client/sync',{...payload,revision:12},captain.token,409);
});

test('standalone-to-room association rejects mismatched contest, start time and a different owner',async(t)=>{
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain'),other=await login('other');
  const r=await room(owner.token),n=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
  const mismatched=snapshot('wrong-start');await api('POST','client/sync',mismatched,user.token);
  await api('POST','client/sync',{...mismatched,roomId:r.id,revision:2},user.token,409);
  const foreign=snapshot('same-local-id');foreign.startedAt=r.startsAt;
  const otherId=(await api('POST','client/sync',foreign,other.token)).data.id;
  const own=(await api('POST','client/sync',{...foreign,roomId:r.id},user.token)).data.id;
  assert.notEqual(own,otherId);
  assert.equal((await api('GET',`history/${otherId}`,undefined,other.token)).data.participantType,'individual');
  await api('GET',`history/${otherId}`,undefined,user.token,404);
  const third=await room(owner.token),thirdInvite=await invite(third.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${thirdInvite}/respond`,{accept:true},user.token);
  const calibrated=snapshot('higher-revision-upgrade');calibrated.startedAt=new Date(Date.parse(third.startsAt)+16000).toISOString();
  const calibratedId=(await api('POST','client/sync',calibrated,user.token)).data.id;
  await api('POST','client/sync',{...calibrated,roomId:third.id,revision:2,startedAt:third.startsAt,submissions:[]},user.token);
  const result=(await api('GET',`history/${calibratedId}`,undefined,user.token)).data;
  assert.equal(result.startedAt,calibrated.startedAt);assert.equal(result.submissions.length,2);assert.equal(result.roomId,third.id);
  const different=snapshot('wrong-contest');different.contest.url='https://qoj.ac/contest/9999';different.startedAt=third.startsAt;
  await api('POST','client/sync',different,user.token);
  await api('POST','client/sync',{...different,contest:calibrated.contest,roomId:r.id,revision:2},user.token,409);
});
