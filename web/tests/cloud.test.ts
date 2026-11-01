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
  const room=async(token:string,freezeMinutes=0)=> (await api('POST','rooms',{scoringSystem:'ICPC',freezeMinutes,name:'多人训练赛',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(Date.now()+3600e3).toISOString()},token)).data;
  const invite=async(roomId:string,creator:string,target:Data)=> (await api('POST',`rooms/${roomId}/invitations`,target,creator)).data.id;
  return {service,root,api,login,admin,room,invite};
}
const snapshot=(localId:string,revision=1):Data=>({
  localId,contest:{platform:'qoj',contestId:'3617',url:'https://qoj.ac/contest/3617',name:'The Training Contest',problems:['A','B']},mode:'vp',startedAt:new Date(Date.now()-600e3).toISOString(),rankSource:'original',rank:'87',solved:1,penalty:20,
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

test('history uses immutable local creation order, including offline sync and legacy clients',async(t)=>{
  const epoch=Date.parse('2030-01-01T00:00:00Z');t.mock.timers.enable({apis:['Date'],now:epoch});
  const {api,login,service}=fixture(t),user=await login('captain');
  const recent=snapshot('recent');recent.createdAt=new Date(epoch-10000).toISOString();
  const recentId=(await api('POST','client/sync',recent,user.token)).data.id;
  t.mock.timers.setTime(epoch+60000);
  const offline=snapshot('offline');offline.createdAt='2029-12-01T12:00:00+08:00';
  const offlineId=(await api('POST','client/sync',offline,user.token)).data.id;
  assert.deepEqual((await api('GET','history',undefined,user.token)).data.map((h:Data)=>h.id),[recentId,offlineId]);
  const stored=(await api('GET',`history/${offlineId}`,undefined,user.token)).data;
  assert.equal(stored.createdAt,'2029-12-01T04:00:00.000Z');
  const update={...offline,revision:2,createdAt:new Date(epoch+60000).toISOString()};
  await api('POST','client/sync',update,user.token);
  assert.equal((await api('GET',`history/${offlineId}`,undefined,user.token)).data.createdAt,stored.createdAt);
  assert.deepEqual((await api('GET','history',undefined,user.token)).data.map((h:Data)=>h.id),[recentId,offlineId]);
  t.mock.timers.setTime(epoch+120000);
  const legacy=snapshot('legacy-bt');legacy.mode='bt';
  const legacyId=(await api('POST','client/sync',legacy,user.token)).data.id;
  const detail=(await api('GET',`history/${legacyId}`,undefined,user.token)).data;
  assert.equal(detail.createdAt,new Date(epoch+120000).toISOString());assert.equal(detail.startedAt,null);assert.equal(detail.endedAt,null);
  assert.deepEqual((await api('GET','history',undefined,user.token)).data.map((h:Data)=>h.id),[legacyId,recentId,offlineId]);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,3);
  const tied='2029-01-01T00:00:00.000Z';service.store.run('UPDATE histories SET created_at=?',tied);
  assert.deepEqual((await api('GET','history',undefined,user.token)).data.map((h:Data)=>h.id),[legacyId,recentId,offlineId].sort().reverse());
});

test('history creation timestamps reject malformed input and clamp future client clocks',async(t)=>{
  const epoch=Date.parse('2030-01-01T00:00:00Z');t.mock.timers.enable({apis:['Date'],now:epoch});
  const {api,login}=fixture(t),user=await login('captain');
  for(const value of [null,1700000000,'not-a-time','2029-01-01','2029-02-30T12:00:00Z','1969-12-31T23:59:59Z']) {
    await api('POST','client/sync',{...snapshot('invalid'),createdAt:value},user.token,400);
  }
  for(const [index,stamp] of ['2030-01-01T00:00:01Z','2099-01-01T00:00:00Z'].entries()) {
    const hid=(await api('POST','client/sync',{...snapshot('future-'+index),createdAt:stamp},user.token)).data.id;
    assert.equal((await api('GET',`history/${hid}`,undefined,user.token)).data.createdAt,new Date(epoch).toISOString());
  }
});

test('VP rooms use creation order independently from start changes and preserve team-visible history order',async(t)=>{
  const epoch=Date.parse('2030-01-01T00:00:00Z');t.mock.timers.enable({apis:['Date'],now:epoch});
  const {api,login,room,invite,service}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member');
  const team=(await api('POST','teams',{nameZh:'排序队',nameEn:'Ordering'},captain.token)).data;
  const teamNotice=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;
  await api('POST',`notifications/${teamNotice}/respond`,{accept:true},member.token);
  const older=await room(owner.token);await api('PATCH',`rooms/${older.id}`,{startsAt:new Date(epoch+7200e3).toISOString()},owner.token);
  t.mock.timers.setTime(epoch+1000);const newer=await room(owner.token);
  for(const r of [older,newer]) {
    const invitation=await invite(r.id,owner.token,{teamId:team.id});
    await api('POST',`notifications/${invitation}/respond`,{accept:true},captain.token);
  }
  for(const user of [owner,captain,member])assert.deepEqual((await api('GET','rooms',undefined,user.token)).data.map((r:Data)=>r.id),[newer.id,older.id]);
  await api('PATCH',`rooms/${older.id}`,{name:'Edited old room',startsAt:new Date(epoch+10800e3).toISOString()},owner.token);
  for(const user of [owner,captain,member])assert.deepEqual((await api('GET','rooms',undefined,user.token)).data.map((r:Data)=>r.id),[newer.id,older.id]);
  assert.equal((await api('GET',`rooms/${older.id}`,undefined,owner.token)).data.createdAt,older.createdAt);
  const recent=snapshot('new-team');recent.roomId=newer.id;recent.startedAt=newer.startsAt;recent.createdAt=new Date(epoch+1000).toISOString();
  const recentId=(await api('POST','client/sync',recent,captain.token)).data.id;
  t.mock.timers.setTime(epoch+2000);
  const old=snapshot('old-team');old.roomId=older.id;old.startedAt=new Date(epoch+10800e3).toISOString();old.createdAt=new Date(epoch).toISOString();
  const oldId=(await api('POST','client/sync',old,captain.token)).data.id;
  await api('POST','client/sync',{...old,revision:2,createdAt:new Date(epoch+2000).toISOString()},captain.token);
  for(const user of [captain,member]) {
    const histories=(await api('GET','history',undefined,user.token)).data;
    assert.deepEqual(histories.map((h:Data)=>h.id),[recentId,oldId]);assert(histories.every((h:Data)=>h.participantType==='team'));
  }
  assert.deepEqual((await api('GET','history',undefined,owner.token)).data,[]);
  service.store.run('UPDATE rooms SET created_at=?','2029-01-01T00:00:00.000Z');
  assert.deepEqual((await api('GET','rooms',undefined,owner.token)).data.map((r:Data)=>r.id),[newer.id,older.id].sort().reverse());
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
  await api('GET',`history/${hid}`,undefined,other.token,404);await api('GET',`history/${hid}`,undefined,owner.token,404);
  assert.equal(board.rows.find((r:Data)=>r.historyId===hid).canViewHistory,false);
  const ownBoard=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,captain.token)).data;
  assert.equal(ownBoard.rows.find((r:Data)=>r.historyId===hid).canViewHistory,true);
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

test('all three VP teammates share one history while later roster changes do not expand private access',async(t)=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-01-01T00:00:00Z')});
  const {api,login,room,invite,service}=fixture(t);
  const owner=await login('owner'),captain=await login('captain'),member=await login('member'),third=await login('other'),late=await login('fourth');
  const team=(await api('POST','teams',{nameZh:'完整历史队',nameEn:'Full History'},captain.token)).data;
  for(const person of [member,third]) {
    const nid=(await api('POST',`teams/${team.id}/invitations`,{userId:person.user.id},captain.token)).data.id;
    await api('POST',`notifications/${nid}/respond`,{accept:true},person.token);
  }
  const r=await room(owner.token),rn=await invite(r.id,owner.token,{teamId:team.id});
  await api('POST',`notifications/${rn}/respond`,{accept:true},captain.token);
  t.mock.timers.setTime(Date.parse(r.startsAt)+300e3);
  const data=snapshot('three-member-history');data.roomId=r.id;data.startedAt=r.startsAt;
  data.submissions[0].submittedAt=new Date(Date.parse(r.startsAt)+60e3).toISOString();
  data.submissions[1].submittedAt=new Date(Date.parse(r.startsAt)+80e3).toISOString();
  const hid=(await api('POST','client/sync',data,captain.token)).data.id;
  const registeredLabel=(await api('GET',`history/${hid}`,undefined,captain.token)).data.participantLabel;
  assert.ok(registeredLabel.startsWith('完整历史队-'));
  assert.deepEqual(registeredLabel.slice('完整历史队-'.length).split('、').sort(),['张队长','李队员','王同学'].sort());
  const assertShared=async(person:Data)=>{
    const histories=(await api('GET','history?search=Training&type=vp',undefined,person.token)).data;
    assert.equal(histories.length,1);assert.equal(histories[0].id,hid);
    assert.equal(histories[0].participantType,'team');assert.equal(histories[0].roomId,r.id);
    assert.equal(histories[0].participantLabel,registeredLabel);
    const detail=(await api('GET',`history/${hid}`,undefined,person.token)).data;
    assert.equal(detail.submissions.length,2);assert.equal(detail.submissions[0].verdict,'CORRECT');
    const own=(await api('GET',`rooms/${r.id}/mysubmit`,undefined,person.token)).data;
    assert.equal(own.history.id,hid);assert.equal(own.submissions.length,2);
  };
  for(const person of [captain,member,third])await assertShared(person);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
  await api('POST','client/sync',{...data,revision:2},member.token,403);
  for(const person of [owner,late]) {
    assert.deepEqual((await api('GET','history',undefined,person.token)).data,[]);
    await api('GET',`history/${hid}`,undefined,person.token,404);
  }
  await api('PATCH',`teams/${team.id}`,{nameZh:'后来的队名',removeUserId:third.user.id},captain.token);
  const lateInvite=(await api('POST',`teams/${team.id}/invitations`,{userId:late.user.id},captain.token)).data.id;
  await api('POST',`notifications/${lateInvite}/respond`,{accept:true},late.token);
  assert.deepEqual((await api('GET','history',undefined,late.token)).data,[]);
  await api('GET',`history/${hid}`,undefined,late.token,404);
  await api('DELETE',`teams/${team.id}`,undefined,captain.token);
  for(const person of [captain,member,third])await assertShared(person);
  await api('GET',`history/${hid}`,undefined,late.token,404);
  assert.equal(service.store.one('SELECT COUNT(*) n FROM histories')!.n,1);
});

test('a previously synced standalone VP upgrades to its matching room without duplicating or losing data',async(t)=>{
  const {api,login,room,invite,service}=fixture(t),owner=await login('owner'),captain=await login('captain'),member=await login('member');
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
  assert.equal((await api('GET',`history/${hid}`,undefined,member.token)).data.submissions.length,2);
  assert.equal(JSON.parse(service.store.one('SELECT submissions_json FROM histories WHERE id=?',hid)!.submissions_json).length,3); // Future submissions are retained but not exposed early.
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

test('last-hour freezing sanitizes opponent results and rank while own team submissions stay live, then releases at the scheduled end',async(t)=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-01-01T00:00:00Z')});
  const {api,login,room,invite,service,root}=fixture(t);
  const owner=await login('owner'),captain=await login('captain'),member=await login('member'),opponent=await login('other'),outsider=await login('fourth');
  const team=(await api('POST','teams',{nameZh:'封榜测试队',nameEn:'Freeze Test'},captain.token)).data;
  const teamInvite=(await api('POST',`teams/${team.id}/invitations`,{userId:member.user.id},captain.token)).data.id;
  await api('POST',`notifications/${teamInvite}/respond`,{accept:true},member.token);
  const r=await room(owner.token,60),rn=await invite(r.id,owner.token,{teamId:team.id}),on=await invite(r.id,owner.token,{userId:opponent.user.id});
  await api('POST',`notifications/${rn}/respond`,{accept:true},captain.token);await api('POST',`notifications/${on}/respond`,{accept:true},opponent.token);
  const start=Date.parse(r.startsAt), stamp=(seconds:number)=>new Date(start+seconds*1000).toISOString();
  const data=snapshot('freeze-team');
  data.roomId=r.id;data.startedAt=r.startsAt;data.contest.problems=['A','B','C','D'];data.contest.rules={duration:18000,penalty_minutes:20,compile_penalty:true};
  data.submissions=[];data.cells={};data.solved=0;data.penalty=0;data.rank='87';
  const hid=(await api('POST','client/sync',data,captain.token)).data.id;
  const timing=(await api('GET',`rooms/${r.id}`,undefined,owner.token)).data;
  assert.equal(timing.durationSeconds,18000);assert.equal(timing.durationSource,'platform');assert.equal(timing.freezeAt,stamp(14400));assert.equal(timing.endsAt,stamp(18000));assert.equal(timing.frozen,false);
  const event=(id:string,problem:string,seconds:number,verdict:string,practice=false)=>({id,problem,submittedAt:stamp(seconds),verdict,practice,url:'https://qoj.ac/submission/'+id});
  t.mock.timers.setTime(start+14399e3);
  data.revision=2;data.rank='60';data.solved=1;data.penalty=80;
  data.submissions=[event('101','A',600,'WRONG-ANSWER'),event('102','A',1200,'COMPILER-ERROR'),event('103','A',2400,'CORRECT'),event('104','B',14300,'PENDING')];
  data.cells={A:{tries:3,pending:0,solved:true,practiceSolved:false,solveTime:40},B:{tries:0,pending:1,solved:false,practiceSolved:false}};
  await api('POST','client/sync',data,captain.token);
  assert.equal((await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data.frozen,false);
  for(const viewer of [owner,opponent])await api('GET',`history/${hid}`,undefined,viewer.token,404);
  for(const viewer of [captain,member])assert.equal((await api('GET',`history/${hid}`,undefined,viewer.token)).data.id,hid);
  t.mock.timers.setTime(start+14400e3);
  let board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  assert.equal(board.frozen,true);assert.equal(board.rows[0].rank,'60');
  t.mock.timers.setTime(start+15000e3);
  data.revision=3;data.rank='7';data.solved=4;data.penalty=888;data.status='complete';data.endedAt=stamp(14500); // A local manual end must not release the room.
  data.submissions=[...data.submissions.slice(0,3),event('104','B',14300,'CORRECT'),event('105','C',14500,'WRONG-ANSWER'),event('106','C',14600,'CORRECT'),event('107','D',17000,'CORRECT')];
  data.cells={A:{tries:3,pending:0,solved:true,practiceSolved:false},B:{tries:1,pending:0,solved:true,practiceSolved:false},C:{tries:2,pending:0,solved:true,practiceSolved:false},D:{tries:1,pending:0,solved:true,practiceSolved:false}};
  await api('POST','client/sync',data,captain.token);
  for(const viewer of [owner,opponent]) {
    board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,viewer.token)).data;
    const row=board.rows.find((x:Data)=>x.historyId===hid);
    assert.equal(row.frozen,true);assert.equal(row.rank,'60');assert.equal(row.solved,2);assert.equal(row.penalty,318);
    assert.deepEqual(row.cells.C,{tries:0,pending:2,solved:false,practiceSolved:false});assert.equal(row.cells.D.pending,0);
    assert.equal(row.canViewHistory,false);await api('GET',`history/${hid}`,undefined,viewer.token,404);
  }
  for(const viewer of [captain,member]) {
    const publicBoard=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,viewer.token)).data;
    const publicRow=publicBoard.rows.find((x:Data)=>x.historyId===hid);
    assert.equal(publicRow.frozen,true);assert.equal(publicRow.solved,2);assert.equal(publicRow.rank,'60');assert.equal(publicRow.canViewHistory,true);
    const own=(await api('GET',`rooms/${r.id}/mysubmit`,undefined,viewer.token)).data;
    assert.equal(own.participation.type,'team');assert.equal(own.history.frozen,false);assert.equal(own.history.solved,3);assert.equal(own.submissions.length,6);
    assert.equal(own.submissions.find((s:Data)=>s.id==='106').verdict,'CORRECT');assert.equal(own.submissions.find((s:Data)=>s.id==='106').url,'https://qoj.ac/submission/106');
    assert.equal((await api('GET','history',undefined,viewer.token)).data[0].frozen,false);
  }
  await api('GET',`rooms/${r.id}/mysubmit`,undefined,owner.token,403);await api('GET',`rooms/${r.id}/mysubmit`,undefined,outsider.token,403);
  const empty=(await api('GET',`rooms/${r.id}/mysubmit`,undefined,opponent.token)).data;assert.equal(empty.history,null);assert.deepEqual(empty.submissions,[]);
  const reopened=new CloudService({path:join(root,'vp.sqlite'),publicUrl:'https://vp.example.test'});
  try {
    const response=await handleRequest(new Request(`https://vp.example.test/api/rooms/${r.id}/leaderboard`,{headers:{Authorization:'Bearer '+owner.token}}),['rooms',r.id,'leaderboard'],reopened);
    assert.equal((await response.json()).data.rows.find((x:Data)=>x.historyId===hid).rank,'60');
  }finally{reopened.close();}
  t.mock.timers.setTime(start+18000e3);
  board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  const released=board.rows.find((x:Data)=>x.historyId===hid);
  assert.equal(board.frozen,false);assert.equal(released.frozen,false);assert.equal(released.rank,'7');assert.equal(released.solved,4);assert.equal(released.canViewHistory,true);
  const detail=(await api('GET',`history/${hid}`,undefined,opponent.token)).data;
  assert.equal(detail.submissions.find((s:Data)=>s.id==='106').verdict,'CORRECT');assert.equal(detail.submissions.find((s:Data)=>s.id==='106').url,'https://qoj.ac/submission/106');
  assert.equal(service.store.one('SELECT freeze_rank FROM histories WHERE id=?',hid)!.freeze_rank,'60');
});

test('late first sync has no invented frozen rank, legacy duration defaults safely, and real platform duration overrides the default',async(t)=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-02-01T00:00:00Z')});
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const r=await room(owner.token,60),n=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
  assert.equal(r.durationSeconds,18000);assert.equal(r.durationSource,'default');
  const start=Date.parse(r.startsAt),data=snapshot('late-first');data.roomId=r.id;data.startedAt=r.startsAt;data.submissions=[];data.cells={};data.contest.rules={duration:7200,penalty_minutes:10};
  t.mock.timers.setTime(start+3600e3);
  const hid=(await api('POST','client/sync',data,user.token)).data.id;
  let board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  assert.equal(board.durationSeconds,7200);assert.equal(board.durationSource,'platform');assert.equal(board.frozen,true);assert.equal(board.rows[0].rank,'?');
  await api('GET',`history/${hid}`,undefined,owner.token,404);
  for(const duration of [0,-1,1.5,604801,'7200'])await api('POST','client/sync',{...data,revision:2,contest:{...data.contest,rules:{duration}}},user.token,400);
  await api('POST','client/sync',{...data,revision:2,contest:{...data.contest,rules:{duration:18000}}},user.token,409);
  await api('POST','client/sync',{...data,revision:2,contest:{...data.contest,rules:{duration:7200,compile_penalty:'true'}}},user.token,400);
  t.mock.timers.setTime(start+7200e3);
  board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;assert.equal(board.frozen,false);assert.equal(board.rows[0].rank,'87');
});

test('additive freeze metadata preserves the previous release room insert for safe code rollback',async(t)=>{
  const {service,api,login}=fixture(t),owner=await login('owner');
  service.store.run('INSERT INTO rooms VALUES(?,?,?,?,?,?)','legacy-compatible-room','Legacy room','https://qoj.ac/contest/3617',new Date(Date.now()+3600e3).toISOString(),owner.user.id,new Date().toISOString());
  const room=(await api('GET','rooms/legacy-compatible-room',undefined,owner.token)).data;
  assert.equal(room.durationSource,'default');assert.equal(room.durationSeconds,18000);assert.equal(room.freezeMinutes,0);assert.equal(room.scoringSystem,'ICPC');
});

test('room freeze minutes are explicit, validated, propagated to clients, and locked after a reservation',async(t)=>{
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const body={scoringSystem:'ICPC',name:'Freeze settings',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(Date.now()+3600e3).toISOString()};
  for(const freezeMinutes of [undefined,null,-1,0.5,'30',10081])await api('POST','rooms',{...body,...(freezeMinutes===undefined?{}:{freezeMinutes})},owner.token,400);
  const r=await room(owner.token);assert.equal(r.freezeMinutes,0);assert.equal(r.freezeAt,null);assert.equal(r.frozen,false);
  let updated=(await api('PATCH',`rooms/${r.id}`,{freezeMinutes:15},owner.token)).data;assert.equal(updated.freezeMinutes,15);
  const invitation=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${invitation}/respond`,{accept:true},user.token);
  const authorized=(await api('POST',`client/rooms/${r.id}/authorize`,{},user.token)).data;assert.equal(authorized.room.freezeMinutes,15);
  const data=snapshot('freeze-settings');data.roomId=r.id;data.startedAt=r.startsAt;data.contest.rules={duration:7200,freeze_minutes:60};data.submissions=[];
  const hid=(await api('POST','client/sync',data,user.token)).data.id;
  assert.equal((await api('GET',`history/${hid}`,undefined,user.token)).data.rules.freeze_minutes,15);
  await api('PATCH',`rooms/${r.id}`,{freezeMinutes:0},owner.token,409);
  updated=(await api('PATCH',`rooms/${r.id}`,{name:'Name only',freezeMinutes:15},owner.token)).data;assert.equal(updated.name,'Name only');
  assert.equal(updated.freezeMinutes,15);assert.equal(updated.durationSeconds,7200);
});

test('native graded scores survive sync, freeze safely including native CF adjustments, and never leak opponents details',async(t)=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-03-01T00:00:00Z')});
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const r=await room(owner.token,30),n=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
  const start=Date.parse(r.startsAt),stamp=(seconds:number)=>new Date(start+seconds*1000).toISOString();
  t.mock.timers.setTime(start+5000e3);
  const data=snapshot('native-points');data.roomId=r.id;data.startedAt=r.startsAt;data.contest.rules={duration:7200,scoring:'CF',score_policy:'best',problem_max_scores:{A:100,B:100}};
  data.points=-60;data.solved=0;data.penalty=0;data.cells={A:{tries:1,points:40,maxPoints:100},B:{tries:0,points:0,maxPoints:100}};
  data.submissions=[{id:'1',problem:'A',submittedAt:stamp(100),verdict:'PARTIAL',points:40,practice:false}];
  const hid=(await api('POST','client/sync',data,user.token)).data.id;
  t.mock.timers.setTime(start+6000e3);data.revision=2;data.points=200;data.solved=2;data.rank='2';data.cells={A:{tries:1,solved:true,points:100,maxPoints:100},B:{tries:1,solved:true,points:100,maxPoints:100}};
  data.submissions=[{...data.submissions[0],verdict:'CORRECT',points:100},{id:'2',problem:'B',submittedAt:stamp(5500),verdict:'CORRECT',points:100,practice:false,url:'https://qoj.ac/submission/2'}];
  await api('POST','client/sync',data,user.token);
  const board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data,row=board.rows[0];
  assert.equal(row.points,0);assert.equal(row.rank,'87');assert.equal(row.cells.A.points,100);assert.equal(row.cells.B.points,0);assert.equal(row.cells.B.pending,1);assert.equal(row.cells.B.solved,false);
  await api('GET',`history/${hid}`,undefined,owner.token,404);
  const own=(await api('GET',`rooms/${r.id}/mysubmit`,undefined,user.token)).data;assert.equal(own.history.points,200);assert.equal(own.submissions[1].points,100);
  t.mock.timers.setTime(start+7200e3);const ended=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data.rows[0];assert.equal(ended.points,200);assert.equal(ended.rank,'2');
  for(const scoring of ['unsupported',null])await api('POST','client/sync',{...data,revision:3,contest:{...data.contest,rules:{scoring}}},user.token,400);
});

test('standalone practice never acquires scores and practice continuation preserves the original VP score',async(t)=>{
  const {api,login}=fixture(t),user=await login('captain');
  const data=snapshot('points-history');data.contest.rules={duration:7200,scoring:'IOI',score_policy:'best',problem_max_scores:{A:100}};data.points=100;data.penalty=0;data.endedAt=new Date().toISOString();data.cells.A.points=100;data.cells.A.maxPoints=100;
  const hid=(await api('POST','client/sync',data,user.token)).data.id;
  const bt={...data,mode:'bt',revision:2,rank:'1',points:999,penalty:999,solved:99,cells:{A:{tries:2,practiceSolved:true},B:{tries:3,practiceSolved:true}}};
  await api('POST','client/sync',bt,user.token);const resumed=(await api('GET',`history/${hid}`,undefined,user.token)).data;
  assert.equal(resumed.mode,'vp');assert.equal(resumed.points,100);assert.equal(resumed.solved,1);assert.equal(resumed.penalty,0);assert.equal(resumed.rank,'87');assert.equal(resumed.cells.A.points,100);assert.equal(resumed.cells.A.solved,true);assert.equal(resumed.submissions.find((s:Data)=>s.id==='123').practice,false);
  const standalone={...bt,localId:'standalone-bt',revision:1};const bhid=(await api('POST','client/sync',standalone,user.token)).data.id;
  const history=(await api('GET',`history/${bhid}`,undefined,user.token)).data;assert.equal(history.mode,'bt');assert.equal(history.points,null);assert.equal(history.penalty,0);assert.equal(history.rank,'—');assert.equal(history.cells.A.points,undefined);assert.equal(history.cells.A.solved,false);assert.equal(history.submissions.every((s:Data)=>s.practice),true);
});

test('room ICPC or CCPC format is mandatory, authorized consistently and cannot change after reservation',async(t)=>{
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const body={name:'Contest format',contestUrl:'https://qoj.ac/contest/3617',startsAt:new Date(Date.now()+3600e3).toISOString(),freezeMinutes:0};
  for(const scoringSystem of [undefined,null,'CF','ccpc'])await api('POST','rooms',{...body,...(scoringSystem===undefined?{}:{scoringSystem})},owner.token,400);
  const r=await room(owner.token),updated=(await api('PATCH',`rooms/${r.id}`,{scoringSystem:'CCPC'},owner.token)).data;assert.equal(updated.scoringSystem,'CCPC');
  const n=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
  assert.equal((await api('POST',`client/rooms/${r.id}/authorize`,{},user.token)).data.room.scoringSystem,'CCPC');
  const data=snapshot('ccpc-format');data.roomId=r.id;data.startedAt=r.startsAt;data.contest.rules={duration:18000,contest_format:'ICPC',problem_reveal_count:null};
  const hid=(await api('POST','client/sync',data,user.token)).data.id,view=(await api('GET',`history/${hid}`,undefined,user.token)).data;
  assert.equal(view.rules.contest_format,'CCPC');assert.equal(view.rules.problem_reveal_count,50);
  await api('PATCH',`rooms/${r.id}`,{scoringSystem:'ICPC'},owner.token,409);
});

test('CCPC public statistics preserve native reveal decisions, anonymize labels in API and reveal all at the configured freeze',async(t)=>{
  t.mock.timers.enable({apis:['Date'],now:Date.parse('2030-04-01T00:00:00Z')});
  const {api,login,room,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const r=await room(owner.token,30);await api('PATCH',`rooms/${r.id}`,{scoringSystem:'CCPC'},owner.token);
  const n=await invite(r.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
  const start=Date.parse(r.startsAt),stamp=(seconds:number)=>new Date(start+seconds*1000).toISOString();t.mock.timers.setTime(start+5000e3);
  const data=snapshot('ccpc-statistics');data.roomId=r.id;data.startedAt=r.startsAt;data.contest.problems=['A','B','C'];data.contest.rules={duration:7200};data.submissions=[];data.cells={};
  data.statisticsSource='original';
  data.publicStatistics={submitted:{A:80,B:2,C:20},attempted:{A:60,B:2,C:15},accepted:{A:49,B:0,C:10}};
  // The native timeline counts starred teams toward the 50-team reveal threshold,
  // while the published accepted count here contains only official teams.
  data.revealedProblems=['A'];data.statisticsAt=stamp(4999);
  await api('POST','client/sync',data,user.token);
  let board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  assert.deepEqual(board.publicProblemStatistics,[{label:'A',submitted:80,attempted:60,accepted:49},{label:null,submitted:20,attempted:15,accepted:10},{label:null,submitted:2,attempted:2,accepted:0}]);
  assert.equal(board.publicStatistics,undefined);assert.equal(board.revealedProblems,undefined);
  t.mock.timers.setTime(start+6000e3);data.revision=2;data.statisticsAt=stamp(5900);data.publicStatistics.accepted.C=90;data.revealedProblems=['A','B','C'];
  await api('POST','client/sync',data,user.token);
  board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;
  assert.equal(board.frozen,true);assert.deepEqual(board.publicProblemStatistics.map((s:Data)=>s.label),['A','B','C']);assert.equal(board.publicProblemStatistics[2].accepted,10);assert.equal(board.statisticsAt,stamp(4999));
  t.mock.timers.setTime(start+7200e3);data.revision=3;await api('POST','client/sync',data,user.token);
  board=(await api('GET',`rooms/${r.id}/leaderboard`,undefined,owner.token)).data;assert.equal(board.publicProblemStatistics[2].accepted,90);
});

test('room formats follow native platform VP support and graded QOJ snapshots match the selected format',async(t)=>{
  const {api,login,invite}=fixture(t),owner=await login('owner'),user=await login('captain');
  const body={name:'Native formats',contestUrl:'https://codeforces.com/contest/2272',startsAt:new Date(Date.now()+3600e3).toISOString(),freezeMinutes:0};
  for(const scoringSystem of ['OI','IOI'])await api('POST','rooms',{...body,scoringSystem},owner.token,400);
  for(const scoringSystem of ['OI','IOI']) {
    const room=(await api('POST','rooms',{...body,contestUrl:'https://qoj.ac/contest/3617',scoringSystem},owner.token)).data;
    assert.equal(room.scoringSystem,scoringSystem);
    await api('PATCH',`rooms/${room.id}`,{contestUrl:'https://codeforces.com/gym/123456'},owner.token,400);
    const n=await invite(room.id,owner.token,{userId:user.user.id});await api('POST',`notifications/${n}/respond`,{accept:true},user.token);
    const auth=(await api('POST',`client/rooms/${room.id}/authorize`,{},user.token)).data;assert.equal(auth.room.scoringSystem,scoringSystem);
    const pendingBoard=(await api('GET',`rooms/${room.id}/leaderboard`,undefined,user.token)).data;assert.equal(pendingBoard.rows[0].scoring,scoringSystem);assert.equal(pendingBoard.rows[0].points,null);
    const data=snapshot('graded-room-'+scoringSystem);data.roomId=room.id;data.startedAt=room.startsAt;data.contest.rules={duration:7200,scoring:scoringSystem,score_policy:scoringSystem==='OI'?'latest':'best'};data.points=75;data.cells.A={tries:1,points:75,maxPoints:100,solved:false};data.submissions=[];
    const hid=(await api('POST','client/sync',data,user.token)).data.id;const result=(await api('GET',`history/${hid}`,undefined,user.token)).data;assert.equal(result.points,75);assert.equal(result.rules.contest_format,scoringSystem);
    await api('POST','client/sync',{...data,revision:2,contest:{...data.contest,rules:{scoring:'ICPC'}}},user.token,409);
  }
  const fractional=snapshot('native-fractional-freeze');fractional.contest.rules={duration:7200,freeze_minutes:15.5};
  const hid=(await api('POST','client/sync',fractional,user.token)).data.id;assert.equal((await api('GET',`history/${hid}`,undefined,user.token)).data.rules.freeze_minutes,15.5);
});

test('submission language categories survive ingestion and are identical in history and room My submissions',async(t)=>{
  const {api,login,room,invite,service}=fixture(t),user=await login('captain');
  const r=await room(user.token),notice=await invite(r.id,user.token,{userId:user.user.id});
  await api('POST',`notifications/${notice}/respond`,{accept:true},user.token);
  const data=snapshot('language-room');data.roomId=r.id;
  const labels=['GNU C11','GNU G++20 13.2','PyPy 3','Java 21','Rust 1.75',undefined];
  data.submissions=labels.map((language,index)=>({id:'language-'+index,problem:'A',submittedAt:new Date(Date.now()-60000-index).toISOString(),verdict:'CORRECT',practice:false,language,url:'https://qoj.ac/submission/'+index}));
  const hid=(await api('POST','client/sync',data,user.token)).data.id;
  const history=(await api('GET',`history/${hid}`,undefined,user.token)).data;
  const own=(await api('GET',`rooms/${r.id}/mysubmit`,undefined,user.token)).data;
  const languages=(records:Data[])=>labels.map((_,index)=>records.find(s=>s.id==='language-'+index)!.language);
  assert.deepEqual(languages(history.submissions),['c','cpp','py','java','other','other']);
  assert.deepEqual(own.history.submissions,history.submissions);assert.deepEqual(own.submissions,history.submissions);
  const stored=JSON.parse(service.store.one('SELECT submissions_json FROM histories WHERE id=?',hid)!.submissions_json);
  assert.equal(stored.find((s:Data)=>s.id==='language-1').language,'cpp');
  // Pre-upgrade records can still contain a raw name: both DTOs normalize it.
  stored.find((s:Data)=>s.id==='language-1').language='GNU G++17';
  service.store.run('UPDATE histories SET submissions_json=? WHERE id=?',JSON.stringify(stored),hid);
  assert.equal((await api('GET',`history/${hid}`,undefined,user.token)).data.submissions.find((s:Data)=>s.id==='language-1').language,'cpp');
  assert.equal((await api('GET',`rooms/${r.id}/mysubmit`,undefined,user.token)).data.submissions.find((s:Data)=>s.id==='language-1').language,'cpp');
});

test('language enrichment preserves final verdicts and known categories through older-client refreshes and restore',async(t)=>{
  const {api,login}=fixture(t),user=await login('captain');
  const first=snapshot('language-merging');first.mode='bt';
  first.submissions[1].language='Python 3';
  const hid=(await api('POST','client/sync',first,user.token)).data.id;
  const update=snapshot('language-merging',2);update.mode='bt';update.submissions[0].verdict='PENDING';update.submissions[0].language='GNU C17';update.submissions[1].verdict='CORRECT';
  await api('POST','client/sync',update,user.token);
  const late=snapshot('language-merging',3);late.mode='bt';late.submissions[1].language='Rust 1.75';
  await api('POST','client/sync',late,user.token);
  const records=(await api('GET',`history/${hid}`,undefined,user.token)).data.submissions;
  assert.equal(records.find((s:Data)=>s.id==='123').verdict,'CORRECT');assert.equal(records.find((s:Data)=>s.id==='123').language,'c');
  assert.equal(records.find((s:Data)=>s.id==='124').verdict,'CORRECT');assert.equal(records.find((s:Data)=>s.id==='124').language,'py');
  const restored=(await api('POST','client/restore',{contestUrl:first.contest.url},user.token)).data.snapshot;
  assert.deepEqual(restored.submissions.map((s:Data)=>s.language),['c','py']);
});
