import { createHash, randomBytes, randomUUID, scryptSync, timingSafeEqual } from 'node:crypto';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { Store, type Row } from './db';
import { roomTiming, publicScore, frozenRank } from './scoreboard';

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
const fail = (status: number, message: string): never => { throw new ApiError(status, message); };
const now = () => new Date().toISOString();
const id = () => randomUUID();
const hash = (token: string) => createHash('sha256').update(token).digest('hex');
function text(value: unknown, field: string, max = 120, optional = false): string {
  if (optional && (value === undefined || value === null)) return '';
  if (typeof value !== 'string' || !value.trim() || value.trim().length > max) return fail(400, `${field}不能为空且不能超过${max}个字符`);
  return value.trim();
}
function iso(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length > 80 || !Number.isFinite(Date.parse(value))) return fail(400, `${field}必须是有效时间`);
  return new Date(value).toISOString();
}
function number(value: unknown, field: string, max = 1e12): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > max) return fail(400, `${field}无效`);
  return value;
}
export function contestUrl(raw: unknown, roomsOnly = false): string {
  let url: URL;
  try { url = new URL(text(raw, '比赛链接', 500)); } catch { return fail(400, '比赛链接无效'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash) return fail(400, '请使用 HTTPS 原站比赛链接');
  const host = url.hostname.toLowerCase().replace(/^www\./, '');
  const patterns: Record<string,string> = {
    'codeforces.com':'^/(contest|gym)/[0-9]+/?$', 'qoj.ac':'^/contest/[0-9]+/?$',
    'contest.ucup.ac':'^/contest/[0-9]+/?$',
    ...(!roomsOnly ? {'oj.cwnupaa.com':'^/(?:d/[A-Za-z0-9_.-]+/)?contest/[a-fA-F0-9]{24}/?$', 'ac.nowcoder.com':'^/acm/contest/[0-9]+/?$'} : {})
  };
  if (!patterns[host] || !new RegExp(patterns[host]).test(url.pathname)) return fail(400, roomsOnly ? 'VP 房间仅支持 Codeforces、Gym、QOJ 比赛链接' : '不支持此比赛链接');
  return `https://${host}${url.pathname.replace(/\/$/,'')}`;
}
function passwordHash(password: string) { const salt = randomBytes(16).toString('hex'); return `${salt}:${scryptSync(password,salt,64).toString('hex')}`; }
function passwordMatches(password: string, stored?: string) {
  if (!stored || password.length > 1024) return false;
  const [salt, value] = stored.split(':');
  if (!salt || !value || value.length !== 128) return false;
  return timingSafeEqual(scryptSync(password,salt,64), Buffer.from(value,'hex'));
}

function latestRelease() {
  try {
    if(!process.env.VP_RELEASE_MANIFEST)return fail(503,'终端更新尚未发布');
    const value=JSON.parse(readFileSync(process.env.VP_RELEASE_MANIFEST,'utf8'));
    if(typeof value.version!=='string' || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(value.version)
      || typeof value.revision!=='string' || !/^[0-9a-f]{40}(?:[0-9a-f]{24})?$/.test(value.revision)
      || typeof value.sha256!=='string' || !/^[0-9a-f]{64}$/.test(value.sha256)
      || value.sourceUrl!==`https://vp.cwnupaa.com/downloads/terminal/${value.revision}.tar.gz`)return fail(503,'终端更新清单暂不可用');
    return {version:value.version,revision:value.revision,sha256:value.sha256,sourceUrl:value.sourceUrl};
  } catch(error) {if(error instanceof ApiError)throw error;return fail(503,'终端更新清单暂不可用');}
}

export type User = {id:string;useraccount:string;username:string;realname:string;numberid:string;email:string;role:string};
function profile(row: Row, full = false): User {
  return {id:String(row.id),useraccount:row.useraccount,username:row.username,realname:row.realname,
    numberid:row.numberid,email:full?row.email:'',role:row.role};
}
const labelUser = (u: User) => `${u.username || u.useraccount}-${u.realname || u.username || u.useraccount}`;
const pendingVerdicts = new Set(['PENDING','TESTING','RUNNING','QUEUED','JUDGING','COMPILING','WAITING','WAITING-FOR-JUDGING','WAITING-FOR-TESTING']);
export type CloudOptions = { path?:string; adminAccount?:string;adminPassword?:string; externalAppKey?:string;externalBaseUrl?:string;publicUrl?:string; fetch?:typeof fetch };

export class CloudService {
  readonly store: Store;
  readonly publicUrl: string;
  readonly externalFetch: typeof fetch;
  private attempts = new Map<string,{at:number,count:number}>();
  constructor(options: CloudOptions = {}) {
    this.store = new Store(options.path || resolve(process.env.VP_DATA_DIR || './data','vp.sqlite'));
    this.publicUrl = options.publicUrl || process.env.VP_PUBLIC_URL || 'http://127.0.0.1:3100';
    this.externalFetch = options.fetch || fetch;
    const adminAccount = options.adminAccount || process.env.VP_ADMIN_ACCOUNT || 'admin';
    const adminPassword = options.adminPassword || process.env.VP_ADMIN_PASSWORD;
    if (!this.store.one("SELECT id FROM users WHERE role='admin'")) {
      if (!adminPassword || adminPassword.length < 8) { this.store.close(); throw new Error('首次启动必须配置 VP_ADMIN_PASSWORD（至少8字符）'); }
      this.store.run('INSERT INTO users(id,useraccount,username,role,password_hash,created_at) VALUES(?,?,?,?,?,?)',id(),adminAccount,'管理员','admin',passwordHash(adminPassword),now());
    }
    const initial = {externalBaseUrl:options.externalBaseUrl || process.env.VP_EXTERNAL_BASE_URL || 'https://www.cwnupaa.com/api', externalAppKey:options.externalAppKey || process.env.VP_EXTERNAL_APP_KEY || ''};
    for (const [key,value] of Object.entries(initial)) this.store.run('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',key,value);
  }
  close() { this.store.close(); }
  private user(uid: string): User { const user=this.store.one('SELECT * FROM users WHERE id=?',uid); if(!user)return fail(404,'用户不存在'); return profile(user,true); }
  private setting(key:string):string { return this.store.one('SELECT value FROM settings WHERE key=?',key)?.value || ''; }
  private audit(user:User,action:string,entity:string) { this.store.run('INSERT INTO audit VALUES(?,?,?,?,?)',id(),user.id,action,entity,now()); }
  private auth(request: Request): {user:User;token:string} {
    const authorization=request.headers.get('authorization');
    const bearer=authorization?.match(/^Bearer ([A-Za-z0-9_-]{40,100})$/)?.[1];
    if(authorization && !bearer)return fail(401,'登录令牌无效');
    const token = bearer || request.headers.get('cookie')?.match(/(?:^|;\s*)xcpc_session=([A-Za-z0-9_-]{40,100})(?:;|$)/)?.[1];
    if (!token) return fail(401,'请先登录');
    const session = this.store.one('SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?',hash(token),now());
    if (!session) return fail(401,'登录已过期，请重新登录');
    return {user:this.user(session.user_id),token};
  }
  private session(user:User) {
    const token=randomBytes(32).toString('base64url'), expiresAt=new Date(Date.now()+30*864e5).toISOString();
    this.store.run('DELETE FROM sessions WHERE expires_at<?',now());
    this.store.run('INSERT INTO sessions VALUES(?,?,?,?)',hash(token),user.id,expiresAt,now());
    return {user,token,expiresAt};
  }
  private limited(request: Request, account:string) {
    const key=hash(`${request.headers.get('x-forwarded-for')?.split(',')[0] || 'local'}:${account.toLowerCase()}`), current=Date.now();
    let entry=this.attempts.get(key);
    if (!entry || current-entry.at>60000) { entry={at:current,count:0}; this.attempts.set(key,entry); }
    if (++entry.count>12) fail(429,'登录尝试过于频繁，请一分钟后再试');
    if(this.attempts.size>10000) for(const [key,value] of this.attempts) if(current-value.at>60000)this.attempts.delete(key);
  }
  private async login(request:Request, body:Row, admin=false) {
    const account=text(body.account,'账号',254);
    if(typeof body.password!=='string' || !body.password || body.password.length>1024)return fail(400,'请输入有效密码');
    const password=body.password;
    this.limited(request,account);
    if(admin) {
      const row=this.store.one("SELECT * FROM users WHERE useraccount=? AND role='admin'",account);
      if(!row || !passwordMatches(password,row.password_hash)) return fail(401,'管理员账号或密码不正确');
      return this.session(profile(row,true));
    }
    const appKey=this.setting('externalAppKey');
    if(!appKey) return fail(503,'管理员尚未配置协会登录 appKey');
    let response:Response;
    try { response=await this.externalFetch(this.setting('externalBaseUrl').replace(/\/$/,'')+'/user/external/login',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({appKey,loginType:'password',account,credential:password}),signal:AbortSignal.timeout(15000),redirect:'error'
    }); } catch { return fail(502,'协会登录服务暂时不可用，请稍后重试'); }
    let external:Row;
    try { external=await response.json(); } catch { return fail(502,'协会登录服务返回了无效响应'); }
    if(!response.ok || external.code!==200 || !external.data) {
      const msg=typeof external.msg==='string' ? external.msg.replaceAll(appKey,'[redacted]').replaceAll(password,'[redacted]').slice(0,200) : '协会账号或密码验证失败';
      return fail(401,msg);
    }
    const d=external.data;
    if (!['string','number'].includes(typeof d.userId) || !String(d.userId) || String(d.userId).length>64) return fail(502,'协会登录服务返回的用户标识无效');
    const externalId=String(d.userId), useraccount=text(d.userAccount,'协会账号',100);
    const fields=[text(d.userName || useraccount,'昵称',100), String(d.realName || '').slice(0,100),String(d.numberId || '').slice(0,64),String(d.email || '').slice(0,254)];
    return this.store.transaction(()=>{
      let row=this.store.one('SELECT * FROM users WHERE external_id=?',externalId);
      const sameAccount=this.store.one('SELECT * FROM users WHERE useraccount=?',useraccount);
      if(sameAccount && sameAccount.external_id && sameAccount.external_id!==externalId) return fail(409,'协会账号标识发生冲突，请联系管理员');
      if(!row && sameAccount) row=sameAccount;
      const uid=row?.id || id();
      if(row) this.store.run('UPDATE users SET external_id=?,useraccount=?,username=?,realname=?,numberid=?,email=? WHERE id=?',externalId,useraccount,...fields,uid);
      else this.store.run('INSERT INTO users(id,external_id,useraccount,username,realname,numberid,email,created_at) VALUES(?,?,?,?,?,?,?,?)',uid,externalId,useraccount,...fields,now());
      return this.session(this.user(uid));
    });
  }
  private notify(sender:User,recipient:string,kind:string,title:string,message:string,extra:{teamId?:string;roomId?:string;payload?:Row}={}) {
    const nid=id(); this.store.run('INSERT INTO notifications(id,recipient_id,sender_id,kind,title,message,status,team_id,room_id,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',nid,recipient,sender.id,kind,title,message,kind==='info'?'info':'pending',extra.teamId||null,extra.roomId||null,JSON.stringify(extra.payload||{}),now()); return nid;
  }
  private team(teamId:string):Row { const row=this.store.one('SELECT * FROM teams WHERE id=? AND deleted_at IS NULL',teamId); if(!row)return fail(404,'队伍不存在'); return row; }
  private teamView(team:Row) { return {id:team.id,nameZh:team.name_zh,nameEn:team.name_en,captainId:team.captain_id,createdAt:team.created_at,members:this.store.all('SELECT u.* FROM users u JOIN team_members m ON m.user_id=u.id WHERE m.team_id=? ORDER BY CASE WHEN u.id=? THEN 0 ELSE 1 END,u.created_at',team.id,team.captain_id).map(u=>profile(u))}; }
  private captain(teamId:string,user:User) {const team=this.team(teamId);if(team.captain_id!==user.id)return fail(403,'只有队长可以管理队伍');return team;}
  private participant(roomId:string,uid:string):Row|undefined { return this.store.one('SELECT p.* FROM participants p JOIN participant_members m ON p.id=m.participant_id WHERE m.room_id=? AND m.user_id=?',roomId,uid); }
  private participantView(row:Row) { return {id:row.id,type:row.type,userId:row.user_id,teamId:row.team_id,captainId:row.captain_id,label:row.label,members:JSON.parse(row.members_json)}; }
  private room(roomId:string):Row { const row=this.store.one('SELECT r.*,rules.duration_seconds FROM rooms r LEFT JOIN room_rules rules ON rules.room_id=r.id WHERE r.id=?',roomId);if(!row)return fail(404,'VP 房间不存在');return row; }
  private visibleRoom(roomId:string,user:User):Row {const room=this.room(roomId);if(room.creator_id!==user.id && !this.participant(roomId,user.id))return fail(403,'你尚未接受此 VP 房间的邀请');return room;}
  private manager(roomId:string,user:User):Row {const room=this.room(roomId);if(room.creator_id!==user.id)return fail(403,'只有创建者可以管理 VP 房间');return room;}
  private roomView(room:Row,user:User) {const p=this.participant(room.id,user.id),duration=this.store.one('SELECT duration_seconds FROM room_rules WHERE room_id=?',room.id)?.duration_seconds;return {id:room.id,name:room.name,contestUrl:room.contest_url,startsAt:room.starts_at,...roomTiming({...room,duration_seconds:duration}),createdBy:room.creator_id,createdAt:room.created_at,canManage:room.creator_id===user.id,participants:this.store.all('SELECT * FROM participants WHERE room_id=? ORDER BY created_at',room.id).map(p=>this.participantView(p)),participation:p?this.participantView(p):null,serverNow:now()};}
  private notificationView(row:Row) {return {id:row.id,kind:row.kind,title:row.title,message:row.message,status:row.status,createdAt:row.created_at,respondedAt:row.responded_at,teamId:row.team_id,roomId:row.room_id,canRespond:row.status==='pending'};}
  private inviteTeam(teamId:string,user:User,body:Row) {
    const team=this.captain(teamId,user), uid=text(body.userId,'用户ID',80);this.user(uid);
    if(this.store.one('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',teamId,uid))return fail(409,'该用户已在队伍中');
    if(this.store.one('SELECT COUNT(*) n FROM team_members WHERE team_id=?',teamId)!.n>=3)return fail(409,'队伍最多只能有3人');
    if(this.store.one("SELECT 1 FROM notifications WHERE kind='team_invite' AND team_id=? AND recipient_id=? AND status='pending'",teamId,uid))return fail(409,'已有待处理的队伍邀请');
    const nid=this.notify(user,uid,'team_invite','队伍邀请',`${user.username} 邀请你加入 ${team.name_zh}`,{teamId});
    this.audit(user,'team.invite',teamId);return {id:nid};
  }
  private inviteRoom(roomId:string,user:User,body:Row) {
    const room=this.manager(roomId,user);
    if(Date.parse(room.starts_at)<=Date.now()) return fail(409,'VP 已开始，不能新增邀请');
    if(Boolean(body.userId)===Boolean(body.teamId))return fail(400,'请选择一个个人或队伍进行邀请');
    let uid:string, teamId:string|undefined;
    if(body.teamId) { teamId=text(body.teamId,'队伍ID',80); uid=this.team(teamId).captain_id; }
    else {uid=text(body.userId,'用户ID',80);this.user(uid);}
    if(this.store.one("SELECT 1 FROM notifications WHERE kind='room_invite' AND room_id=? AND status='pending' AND json_extract(payload,'$.targetKey')=?",roomId,teamId?`team:${teamId}`:`user:${uid}`)) return fail(409,'已有待处理的 VP 邀请');
    const targetMembers=teamId?this.teamView(this.team(teamId)).members:[this.user(uid)];
    for(const member of targetMembers) if(this.participant(roomId,member.id)) return fail(409,`${member.username} 已参与此 VP，不能重复报名`);
    const nid=this.notify(user,uid,'room_invite','VP 参赛邀请',`${user.username} 邀请${teamId?'你的队伍':'你'}参加 ${room.name}`,{roomId,teamId,payload:{targetKey:teamId?`team:${teamId}`:`user:${uid}`,type:teamId?'team':'individual'}});
    this.audit(user,'room.invite',roomId);return {id:nid};
  }
  private respond(nid:string,user:User,body:Row) {
    if(typeof body.accept!=='boolean')return fail(400,'请选择接受或拒绝');
    return this.store.transaction(()=>{
      const n=this.store.one('SELECT * FROM notifications WHERE id=? AND recipient_id=?',nid,user.id);
      if(!n)return fail(404,'通知不存在');if(n.status!=='pending')return fail(409,'此邀请已经处理');
      if(body.accept) {
        if(n.kind==='team_invite') {
          const team=this.team(n.team_id);
          if(team.captain_id!==n.sender_id)return fail(409,'队伍邀请已失效');
          if(this.store.one('SELECT COUNT(*) n FROM team_members WHERE team_id=?',team.id)!.n>=3)return fail(409,'队伍已满，最多3人');
          if(this.store.one('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',team.id,user.id))return fail(409,'你已在此队伍中');
          this.store.run('INSERT INTO team_members VALUES(?,?)',team.id,user.id);
        } else if(n.kind==='room_invite') {
          const room=this.room(n.room_id);if(Date.parse(room.starts_at)<=Date.now())return fail(409,'VP 已开始，此邀请已过期');
          let members:User[], captainId:string, label:string;
          const type=JSON.parse(n.payload).type;
          if(type==='team') {
            const team=this.team(n.team_id);if(team.captain_id!==user.id)return fail(403,'只有队长可以接受队伍参赛邀请');
            const view=this.teamView(team);members=view.members;captainId=team.captain_id;
            if(members.length<1 || members.length>3)return fail(409,'队伍成员必须为1–3人');
            label=`${team.name_zh}-${members.map(u=>u.realname||u.username||u.useraccount).join('、')}`;
          } else {members=[profile(user)];captainId=user.id;label=labelUser(user);}
          for(const member of members)if(this.participant(room.id,member.id))return fail(409,`${member.username} 已参与此 VP，不能重复报名`);
          const pid=id();this.store.run('INSERT INTO participants VALUES(?,?,?,?,?,?,?,?,?)',pid,room.id,type,type==='team'?null:user.id,type==='team'?n.team_id:null,captainId,label,JSON.stringify(members),now());
          for(const member of members)this.store.run('INSERT INTO participant_members VALUES(?,?,?)',room.id,pid,member.id);
        } else return fail(400,'此通知不需要处理');
      }
      const status=body.accept?'accepted':'rejected';
      this.store.run('UPDATE notifications SET status=?,responded_at=? WHERE id=?',status,now(),nid);
      this.notify(user,n.sender_id,'info',body.accept?'邀请已接受':'邀请已拒绝',`${user.username} ${body.accept?'接受':'拒绝'}了${n.kind==='team_invite'?'队伍':'VP'}邀请`,{teamId:n.team_id||undefined,roomId:n.room_id||undefined});
      this.audit(user,'invitation.'+status,nid);return this.notificationView(this.store.one('SELECT * FROM notifications WHERE id=?',nid)!);
    });
  }
  private historyVisible(row:Row,user:User,details=false) {
    if(row.owner_id===user.id)return true;
    if(row.participant_id && this.store.one('SELECT 1 FROM participant_members WHERE participant_id=? AND user_id=?',row.participant_id,user.id))return true;
    if(!details || !row.room_id)return false;
    const room=this.room(row.room_id);
    if(Date.now()<Date.parse(roomTiming(room).endsAt))return false;
    return Boolean(room.creator_id===user.id || this.participant(row.room_id,user.id));
  }
  private historyView(row:Row, details=false, viewer?:User, publicBoard=false) {
    const participantType=row.participant_id?this.store.one('SELECT type FROM participants WHERE id=?',row.participant_id)?.type||'individual':'individual';
    const room=row.room_id?this.room(row.room_id):undefined,timing=room?roomTiming(room):undefined;
    const frozen=Boolean(timing?.frozen && (publicBoard || (viewer && !this.historyVisible(row,viewer))));
    const at=Date.now(), submissions:Row[]=JSON.parse(row.submissions_json);
    const hidePractice=Boolean(publicBoard && timing && at<Date.parse(timing.endsAt));
    const filtered=room && (frozen || submissions.some(s=>Date.parse(s.submittedAt)>at || (hidePractice && s.practice)))?publicScore(row,room,at,frozen,hidePractice):undefined;
    const value={id:row.id,participantType,contestName:row.contest_name,contestUrl:row.contest_url,platform:row.platform,mode:row.mode,rank:frozen?frozenRank(row,room!):row.rank,solved:filtered?.solved??row.solved,penalty:filtered?.penalty??row.penalty,problems:JSON.parse(row.problems_json),cells:filtered?.cells??JSON.parse(row.cells_json),startedAt:row.mode==='bt'?null:row.started_at,endedAt:row.mode==='bt'?null:row.ended_at,participantLabel:row.participant_label,roomId:row.room_id,status:row.status,updatedAt:row.updated_at,rules:JSON.parse(row.rules_json||'{}'),...(timing?{...timing,freezeActive:timing.frozen,frozen}: {frozen:false})};
    return details?{...value,submissions:filtered?.submissions??submissions.filter(s=>Date.parse(s.submittedAt)<=at)}:value;
  }
  private sanitizeSnapshot(body:Row) {
    const localId=text(body.localId,'本地记录标识',200), c=body.contest;
    if(!c || typeof c!=='object')return fail(400,'缺少比赛资料');
    const url=contestUrl(c.url), mode=body.mode;
    if(!['vp','live','bt'].includes(mode))return fail(400,'参赛类型无效');
    const platform=url.includes('codeforces.com/gym/')?'gym':url.includes('codeforces.com/')?'cf':url.includes('nowcoder.com/')?'nowcoder':url.includes('cwnupaa.com/')?'cwnuoj':'qoj';
    const name=text(c.name,'比赛名称',300);
    const rules:Row={};
    if(c.rules!==undefined) {
      if(!c.rules || typeof c.rules!=='object' || Array.isArray(c.rules))return fail(400,'比赛规则无效');
      if(c.rules.duration!==undefined) {rules.duration=number(c.rules.duration,'比赛时长',7*86400);if(!Number.isInteger(rules.duration)||rules.duration<1)return fail(400,'比赛时长必须为正整数秒');}
      if(c.rules.penalty_minutes!==undefined)rules.penalty_minutes=number(c.rules.penalty_minutes,'错误提交罚时',1000);
      if(c.rules.compile_penalty!==undefined && typeof c.rules.compile_penalty!=='boolean')return fail(400,'编译错误罚时配置无效');
      rules.compile_penalty=c.rules.compile_penalty===true;
    }
    if(!Array.isArray(c.problems) || c.problems.length>100 || c.problems.some((v:unknown)=>typeof v!=='string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$/.test(v) || ['constructor','prototype','__proto__'].includes(v)) || new Set(c.problems).size!==c.problems.length)return fail(400,'题目列表无效');
    const problems=c.problems as string[], cells:Row=Object.create(null);
    for(const key of problems) {
      const cell=body.cells?.[key]||{};
      cells[key]={tries:number(cell.tries??0,'尝试次数',1e6),pending:number(cell.pending??0,'待评测次数',1e6),solved:cell.solved===true,practiceSolved:cell.practiceSolved===true};
      if(cell.solveTime!==undefined)cells[key].solveTime=number(cell.solveTime,'通过时间');
    }
    if(!Array.isArray(body.submissions) || body.submissions.length>20000)return fail(400,'提交记录列表无效');
    const submissions=body.submissions.map((s:Row)=>{
      if(!s || !problems.includes(s.problem))return fail(400,'提交记录题号不属于此比赛');
      const value:Row={id:text(String(s.id??''),'提交ID',120),problem:s.problem,submittedAt:iso(s.submittedAt,'提交时间'),verdict:text(s.verdict,'判题状态',100),practice:s.practice===true};
      if(s.url) {let link:URL;try{link=new URL(s.url);}catch{return fail(400,'提交记录链接无效');} if(link.protocol==='https:' && !link.username && !link.password && link.hostname.replace(/^www\./,'')===new URL(url).hostname)value.url=link.href;}
      return value;
    });
    const status=['scheduled','running','complete','archived','cancelled','failed'].includes(body.status)?body.status:mode==='bt'?'practice':body.endedAt?'complete':'running';
    let startedAt=mode==='bt'?null:body.startedAt?iso(body.startedAt,'开始时间'):null;
    const endedAt=mode==='bt'?null:body.endedAt?iso(body.endedAt,'结束时间'):null;
    if(status==='failed')startedAt=null;
    if(startedAt && endedAt && endedAt<startedAt) {if(['failed','cancelled'].includes(status))startedAt=null;else return fail(400,'结束时间不能早于开始时间');}
    return {localId,url,mode,status,platform,name,problems,rules,cells,submissions,startedAt,endedAt,rank:text(String(body.rank??'—'),'原站排名',100),solved:number(body.solved,'解决题数',100),penalty:number(body.penalty,'罚时'),revision:number(body.revision,'同步版本',Number.MAX_SAFE_INTEGER)};
  }
  private sync(user:User,body:Row) {
    const data=this.sanitizeSnapshot(body);
    return this.store.transaction(()=>{
      let participant:Row|undefined, room:Row|undefined;
      if(body.roomId) {
        room=this.room(text(body.roomId,'房间ID',80)); participant=this.participant(room.id,user.id);
        if(!participant || participant.captain_id!==user.id)return fail(403,'只有已报名的个人或队长可以同步此 VP');
        if(room.contest_url!==data.url)return fail(409,'同步比赛链接与 VP 房间不一致');
      }
      const localHistory=this.store.one('SELECT * FROM histories WHERE owner_id=? AND local_id=?',user.id,data.localId);
      const roomHistory=participant?this.store.one('SELECT * FROM histories WHERE participant_id=?',participant.id):undefined;
      if(localHistory?.room_id && localHistory.room_id!==room?.id) {
        if(!room && localHistory.revision>=data.revision)return {id:localHistory.id,stale:true};
        return fail(409,'此本地记录已经绑定其他 VP 上下文，不能重新关联');
      }
      if(participant && localHistory?.participant_id && localHistory.participant_id!==participant.id)return fail(409,'此本地记录已经绑定其他参赛身份');
      if(roomHistory && localHistory && roomHistory.id!==localHistory.id)return fail(409,'此 VP 已绑定另一条本地记录，请恢复原会话');
      const old=roomHistory||localHistory;
      if(old && old.owner_id!==user.id)return fail(403,'不能修改其他用户的比赛记录');
      if(old && old.contest_url!==data.url)return fail(409,'本地记录标识已用于另一场比赛');
      const upgrading=Boolean(room && participant && old && !old.room_id && !old.participant_id);
      if(upgrading && (old!.mode!=='vp' || !old!.started_at || Math.abs(Date.parse(old!.started_at)-Date.parse(room!.starts_at))>60000))return fail(409,'本地 VP 开始时间与房间不匹配，不能关联');
      if(old && old.revision>=data.revision) {
        if(upgrading) {
          this.store.run('UPDATE histories SET room_id=?,participant_id=?,participant_label=?,updated_at=? WHERE id=?',room!.id,participant!.id,participant!.label,now(),old.id);
          this.audit(user,'history.bind-room',old.id);
          return {id:old.id,upgraded:true};
        }
        return {id:old.id,stale:true};
      }
      if(room && data.rules.duration!==undefined) {
        if(room.duration_seconds && room.duration_seconds!==data.rules.duration)return fail(409,'比赛时长与房间已同步的原站时长不一致');
        if(!room.duration_seconds) {this.store.run('INSERT INTO room_rules(room_id,duration_seconds) VALUES(?,?)',room.id,data.rules.duration);room.duration_seconds=data.rules.duration;}
      }
      const records=new Map<string,Row>();
      if(old)for(const s of JSON.parse(old.submissions_json))records.set(s.id,s);
      for(const s of data.submissions) {
        const prior=records.get(s.id);
        if(prior && !pendingVerdicts.has(prior.verdict) && pendingVerdicts.has(s.verdict))continue;
        records.set(s.id,s);
      }
      if(records.size>20000)return fail(400,'单场比赛提交记录超出上限');
      const submissions=[...records.values()].sort((a,b)=>a.submittedAt.localeCompare(b.submittedAt));
      const accepted=new Set(['AC','ACCEPTED','CORRECT','OK','PRETESTS-PASSED']);
      for(const event of submissions)if(accepted.has(event.verdict)&&data.cells[event.problem]) {
        data.cells[event.problem][event.practice?'practiceSolved':'solved']=true;
      }
      const mode=old && old.mode!=='bt'?old.mode:room?'vp':data.mode;
      const startedAt=mode==='bt' || data.status==='failed' || (data.status==='cancelled' && !data.startedAt)?null:(upgrading?old!.started_at:data.startedAt||old?.started_at||room?.starts_at||null);
      const endedAt=mode==='bt'?null:(data.endedAt||old?.ended_at||null);
      const hid=old?.id||id();
      const values=[user.id,old?.local_id||data.localId,room?.id||null,participant?.id||null,participant?.label||labelUser(user),data.name,data.url,data.platform,mode,startedAt,endedAt,old?.ended_at && old.rank!=='—'?old.rank:data.rank,data.solved,data.penalty,JSON.stringify(data.problems),JSON.stringify(data.cells),JSON.stringify(submissions),data.revision,now()];
      if(old)this.store.run('UPDATE histories SET owner_id=?,local_id=?,room_id=?,participant_id=?,participant_label=?,contest_name=?,contest_url=?,platform=?,mode=?,started_at=?,ended_at=?,rank=?,solved=?,penalty=?,problems_json=?,cells_json=?,submissions_json=?,revision=?,updated_at=? WHERE id=?',...values,hid);
      else this.store.run('INSERT INTO histories(id,owner_id,local_id,room_id,participant_id,participant_label,contest_name,contest_url,platform,mode,started_at,ended_at,rank,solved,penalty,problems_json,cells_json,submissions_json,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',hid,...values);
      this.store.run('UPDATE histories SET status=? WHERE id=?',data.status,hid);
      const rules={...JSON.parse(old?.rules_json||'{}'),...data.rules};
      this.store.run('UPDATE histories SET rules_json=? WHERE id=?',JSON.stringify(rules),hid);
      // Only server receipt time establishes that an original rank predates freezing.
      if(room && Date.now()<Date.parse(roomTiming(room).freezeAt))this.store.run('UPDATE histories SET freeze_rank=?,freeze_rank_at=? WHERE id=?',data.rank,now(),hid);
      return {id:hid};
    });
  }
  private printView(row:Row,detail=false) {const value={id:row.id,roomId:row.room_id,participantLabel:row.label,filename:row.filename,language:row.language,createdAt:row.created_at};return detail?{...value,code:row.code}:value;}
  async dispatch(request:Request, segments:string[], body:Row={}):Promise<{data:unknown;session?:{token:string;expiresAt:string};clearCookie?:boolean}> {
    const method=request.method, path=segments.join('/');
    if(path==='health' && method==='GET')return {data:{status:'ok'}};
    if(path==='client/releases/latest' && method==='GET')return {data:latestRelease()};
    if((path==='auth/login'||path==='auth/admin-login') && method==='POST') {const data=await this.login(request,body,path.endsWith('admin-login'));return {data,session:data};}
    const {user,token}=this.auth(request);
    if(path==='auth/me' && method==='GET')return {data:user};
    if(path==='auth/logout' && method==='POST') {this.store.run('DELETE FROM sessions WHERE token_hash=?',hash(token));return {data:{ok:true},clearCookie:true};}
    if(path==='admin/settings') {
      if(user.role!=='admin')return fail(403,'只有超级管理员可以管理设置');
      if(method==='PUT') {
        if(body.externalBaseUrl!==undefined) {
          const base=text(body.externalBaseUrl,'协会接口地址',300);let target:URL;try{target=new URL(base);}catch{return fail(400,'协会接口地址无效');}
          if(target.protocol!=='https:' || !['www.cwnupaa.com','cwnupaa.com'].includes(target.hostname) || target.port || target.username || target.password || target.search || target.hash || target.pathname.replace(/\/$/,'')!=='/api')return fail(400,'协会接口必须使用 https://www.cwnupaa.com/api');
          this.store.run('UPDATE settings SET value=? WHERE key=?',base.replace(/\/$/,''),'externalBaseUrl');
        }
        if(body.externalAppKey) {const key=text(body.externalAppKey,'appKey',512);if(/\s/.test(key))return fail(400,'appKey不能包含空白');this.store.run('UPDATE settings SET value=? WHERE key=?',key,'externalAppKey');}
        this.audit(user,'settings.update','external-login');
      } else if(method!=='GET')return fail(405,'不支持此操作');
      const key=this.setting('externalAppKey');return {data:{externalBaseUrl:this.setting('externalBaseUrl'),externalAppKey:key?'••••••••'+key.slice(-4):'',externalAppKeyConfigured:Boolean(key)}};
    }
    if(path==='users' && method==='GET') {
      const search=new URL(request.url).searchParams.get('search')?.trim()||'';
      if(search.length<2)return {data:[]};if(search.length>100)return fail(400,'搜索条件过长');
      const escaped=search.replace(/[\\%_]/g,'\\$&'),like='%'+escaped+'%';
      return {data:this.store.all("SELECT * FROM users WHERE realname LIKE ? ESCAPE '\\' OR numberid LIKE ? ESCAPE '\\' OR useraccount LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\' LIMIT 30",like,like,like,like).map(u=>profile(u))};
    }
    if(path==='teams') {
      if(method==='GET')return {data:this.store.all('SELECT t.* FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=? AND t.deleted_at IS NULL ORDER BY t.created_at DESC',user.id).map(t=>this.teamView(t))};
      if(method==='POST') {const tid=id(),nameZh=text(body.nameZh,'中文队名',100),nameEn=text(body.nameEn,'英文队名',100);this.store.transaction(()=>{this.store.run('INSERT INTO teams(id,name_zh,name_en,captain_id,created_at) VALUES(?,?,?,?,?)',tid,nameZh,nameEn,user.id,now());this.store.run('INSERT INTO team_members VALUES(?,?)',tid,user.id);this.audit(user,'team.create',tid);});return {data:this.teamView(this.team(tid))};}
    }
    if(segments[0]==='teams' && segments.length>=2) {
      const tid=segments[1];
      if(segments.length===3 && segments[2]==='invitations' && method==='POST')return {data:this.inviteTeam(tid,user,body)};
      if(segments.length===2) {
        if(method==='GET') {const t=this.team(tid);if(!this.store.one('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',tid,user.id))return fail(403,'你不在此队伍中');return {data:this.teamView(t)};}
        if(method==='PATCH') {this.captain(tid,user);this.store.transaction(()=>{
          if(body.removeUserId!==undefined) {const uid=text(body.removeUserId,'成员ID',80);if(uid===user.id)return fail(400,'队长不能移除自己，请删除队伍');if(!this.store.one('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',tid,uid))return fail(404,'队伍成员不存在');this.store.run('DELETE FROM team_members WHERE team_id=? AND user_id=?',tid,uid);this.notify(user,uid,'info','队伍成员已更新','队长已将你移出队伍',{teamId:tid});}
          if(body.nameZh!==undefined)this.store.run('UPDATE teams SET name_zh=? WHERE id=?',text(body.nameZh,'中文队名',100),tid);
          if(body.nameEn!==undefined)this.store.run('UPDATE teams SET name_en=? WHERE id=?',text(body.nameEn,'英文队名',100),tid);
          this.audit(user,'team.update',tid);
        });return {data:this.teamView(this.team(tid))};}
        if(method==='DELETE') {const team=this.captain(tid,user);this.store.transaction(()=>{for(const member of this.teamView(team).members)if(member.id!==user.id)this.notify(user,member.id,'info','队伍已解散',`${team.name_zh} 已被队长解散`,{teamId:tid});this.store.run('UPDATE teams SET deleted_at=? WHERE id=?',now(),tid);this.store.run("UPDATE notifications SET status='cancelled',responded_at=? WHERE team_id=? AND status='pending'",now(),tid);this.audit(user,'team.delete',tid);});return {data:{ok:true}};}
      }
    }
    if(path==='notifications' && method==='GET')return {data:this.store.all('SELECT * FROM notifications WHERE recipient_id=? ORDER BY created_at DESC LIMIT 300',user.id).map(n=>this.notificationView(n))};
    if(segments[0]==='notifications' && segments.length===3 && segments[2]==='respond' && method==='POST')return {data:this.respond(segments[1],user,body)};
    if(path==='rooms') {
      if(method==='GET')return {data:this.store.all('SELECT DISTINCT r.* FROM rooms r LEFT JOIN participant_members m ON m.room_id=r.id WHERE r.creator_id=? OR m.user_id=? ORDER BY r.starts_at DESC',user.id,user.id).map(r=>this.roomView(r,user))};
      if(method==='POST') {const name=text(body.name,'VP名称',200),url=contestUrl(body.contestUrl,true),startsAt=iso(body.startsAt,'开始时间');if(Date.parse(startsAt)<=Date.now())return fail(400,'开始时间必须晚于当前时间');const rid=id();this.store.run('INSERT INTO rooms(id,name,contest_url,starts_at,creator_id,created_at) VALUES(?,?,?,?,?,?)',rid,name,url,startsAt,user.id,now());this.audit(user,'room.create',rid);return {data:this.roomView(this.room(rid),user)};}
    }
    if(segments[0]==='rooms' && segments.length>=2) {
      const rid=segments[1];
      if(segments.length===2) {
        if(method==='GET')return {data:this.roomView(this.visibleRoom(rid,user),user)};
        if(method==='PATCH') {
          const room=this.manager(rid,user);
          if(Date.parse(room.starts_at)<=Date.now())return fail(409,'VP 已开始，不能修改比赛设置');
          const url=body.contestUrl===undefined?room.contest_url:contestUrl(body.contestUrl,true),startsAt=body.startsAt===undefined?room.starts_at:iso(body.startsAt,'开始时间');
          if((url!==room.contest_url || startsAt!==room.starts_at) && this.store.one('SELECT 1 FROM histories WHERE room_id=?',rid))return fail(409,'已有客户端预约此 VP，不能更改比赛或开始时间');
          if(Date.parse(startsAt)<=Date.now())return fail(400,'开始时间必须晚于当前时间');
          this.store.run('UPDATE rooms SET name=?,contest_url=?,starts_at=? WHERE id=?',body.name===undefined?room.name:text(body.name,'VP名称',200),url,startsAt,rid);this.audit(user,'room.update',rid);return {data:this.roomView(this.room(rid),user)};
        }
      }
      if(segments.length===3 && segments[2]==='invitations' && method==='POST')return {data:this.inviteRoom(rid,user,body)};
      if(segments.length===3 && segments[2]==='mysubmit' && method==='GET') {
        const room=this.visibleRoom(rid,user),part=this.participant(rid,user.id);
        if(!part)return fail(403,'请先接受 VP 参赛邀请后查看我的提交');
        const row=this.store.one('SELECT * FROM histories WHERE participant_id=?',part.id);
        const history=row?this.historyView(row,true,user):null;
        return {data:{room:this.roomView(room,user),participation:this.participantView(part),history,submissions:history && 'submissions' in history?history.submissions:[],serverNow:now()}};
      }
      if(segments.length===3 && segments[2]==='leaderboard' && method==='GET') {
        const room=this.visibleRoom(rid,user), histories=this.store.all('SELECT h.*,p.id participant_key,p.label participant_name FROM participants p LEFT JOIN histories h ON h.participant_id=p.id WHERE p.room_id=? ORDER BY p.created_at,p.id',rid);
        const problems=[...new Set<string>(histories.flatMap(r=>r.problems_json?JSON.parse(r.problems_json):[]))];
        const rows=histories.map(r=>{
          const view=r.id?this.historyView(r,false,user,true):undefined;
          return {participantId:r.participant_key,label:r.participant_name,rank:view?.rank||'—',solved:view?.solved??0,penalty:view?.penalty??0,cells:view?.cells||{},historyId:r.id,lastSyncedAt:r.updated_at,status:r.status||'scheduled',frozen:view?.frozen||false,canViewHistory:Boolean(r.id && this.historyVisible(r,user,true))};
        }).sort((a,b)=>b.solved-a.solved||a.penalty-b.penalty);
        return {data:{room:this.roomView(room,user),...roomTiming(room),rows,problems,serverNow:now()}};
      }
      if(segments.length===3 && segments[2]==='prints' && method==='GET') {
        this.visibleRoom(rid,user);const part=this.participant(rid,user.id);return {data:part?this.store.all('SELECT j.*,p.label FROM prints j JOIN participants p ON p.id=j.participant_id WHERE j.participant_id=? ORDER BY j.created_at DESC LIMIT 1000',part.id).map(j=>this.printView(j)):[]};
      }
    }
    if(segments[0]==='prints' && segments.length===2 && method==='GET') {
      const job=this.store.one('SELECT j.*,p.label FROM prints j JOIN participants p ON p.id=j.participant_id JOIN participant_members m ON m.participant_id=p.id WHERE j.id=? AND m.user_id=?',segments[1],user.id);if(!job)return fail(404,'打印记录不存在或无权查看');return {data:this.printView(job,true)};
    }
    if(path==='history' && method==='GET') {
      const query=new URL(request.url).searchParams,search=(query.get('search')||'').slice(0,200).toLowerCase(),type=query.get('type');
      return {data:this.store.all('SELECT DISTINCT h.* FROM histories h LEFT JOIN participant_members m ON m.participant_id=h.participant_id WHERE h.owner_id=? OR m.user_id=? ORDER BY h.updated_at DESC',user.id,user.id).filter(h=>(!search||h.contest_name.toLowerCase().includes(search))&&(!type||type===h.mode)).map(h=>this.historyView(h,false,user))};
    }
    if(segments[0]==='history' && segments.length===2 && method==='GET') {const h=this.store.one('SELECT * FROM histories WHERE id=?',segments[1]);if(!h||!this.historyVisible(h,user,true))return fail(404,'比赛记录不存在或无权查看');return {data:this.historyView(h,true,user)};}
    if(segments[0]==='client') {
      if(segments.length===4 && segments[1]==='rooms' && segments[3]==='authorize' && method==='POST') {const room=this.visibleRoom(segments[2],user),p=this.participant(room.id,user.id);if(!p)return fail(403,'请先接受 VP 参赛邀请');return {data:{room:{id:room.id,name:room.name,contestUrl:room.contest_url,startsAt:room.starts_at},participant:this.participantView(p),canSubmit:p.captain_id===user.id,serverNow:now()}};}
      if(path==='client/sync' && method==='POST')return {data:this.sync(user,body)};
      if(path==='client/prints' && method==='POST') {
        const rid=text(body.roomId,'VP房间ID',80),p=this.participant(rid,user.id);if(!p)return fail(403,'你未报名此 VP，不能打印');
        if(typeof body.code!=='string'||!body.code.length||Buffer.byteLength(body.code,'utf8')>512*1024)return fail(400,'打印代码必须为非空文本且不超过512KiB');
        const raw=text(body.filename,'文件名',255),filename=raw.replaceAll('\\','/').split('/').pop()!;
        if(!filename || filename==='.' || filename==='..' || /[\x00-\x1f\x7f]/.test(filename))return fail(400,'文件名无效');
        const pid=id();this.store.run('INSERT INTO prints VALUES(?,?,?,?,?,?,?,?)',pid,rid,p.id,user.id,filename,typeof body.language==='string'?body.language.slice(0,50):'',body.code,now());
        return {data:{id:pid,url:`${this.publicUrl}/vp/${rid}/print/${pid}`}};
      }
    }
    return fail(404,'接口不存在');
  }
}

let singleton:CloudService|undefined;
export function cloud() { return singleton ||= new CloudService(); }
export async function readBody(request:Request):Promise<Row> {
  if(!['POST','PUT','PATCH','DELETE'].includes(request.method))return {};
  let total=0;const chunks:Uint8Array[]=[];const reader=request.body?.getReader();
  if(reader)while(true) {const part=await reader.read();if(part.done)break;total+=part.value.byteLength;if(total>2*1024*1024){await reader.cancel();return fail(413,'请求内容超过2MiB限制');}chunks.push(part.value);}
  if(!total)return {};
  let body:unknown;try{body=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return fail(400,'JSON格式无效');}
  if(!body || typeof body!=='object' || Array.isArray(body))return fail(400,'请求必须为JSON对象');return body as Row;
}
export async function handleRequest(request:Request,segments:string[], service?:CloudService):Promise<Response> {
  const headers=new Headers({'Cache-Control':'no-store','Content-Type':'application/json; charset=utf-8','X-Content-Type-Options':'nosniff'});
  try {
    if(request.method==='GET' && segments.join('/')==='client/releases/latest')return new Response(JSON.stringify({data:latestRelease()}),{headers});
    const current=service||cloud();
    if(!['GET','HEAD','OPTIONS'].includes(request.method) && !request.headers.has('authorization')) {
      const origin=request.headers.get('origin');
      if(origin && ![new URL(request.url).origin,new URL(current.publicUrl).origin].includes(origin))return fail(403,'请求来源不匹配');
      if(request.headers.has('cookie') && !origin)return fail(403,'请求缺少来源信息');
    }
    const result=await current.dispatch(request,segments,await readBody(request));
    const secure=new URL(current.publicUrl).protocol==='https:'?'; Secure':'';
    if(result.session)headers.set('Set-Cookie',`xcpc_session=${result.session.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000${secure}`);
    if(result.clearCookie)headers.set('Set-Cookie',`xcpc_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secure}`);
    return new Response(JSON.stringify({data:result.data}),{headers});
  } catch(error) {
    if(error instanceof ApiError)return new Response(JSON.stringify({error:error.message}),{status:error.status,headers});
    if(error instanceof Error && /UNIQUE constraint/.test(error.message))return new Response(JSON.stringify({error:'数据已存在或请求发生冲突，请刷新后重试'}),{status:409,headers});
    console.error('XCPC API internal failure:',error instanceof Error?error.name:'unknown');
    return new Response(JSON.stringify({error:'服务器暂时无法处理请求，请稍后重试'}),{status:500,headers});
  }
}
