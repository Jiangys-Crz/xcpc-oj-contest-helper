import { createHash, randomBytes, randomUUID, scryptSync, timingSafeEqual } from 'node:crypto';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { Store, type Row } from './db';
import { roomTiming, publicScore, historyRank, pointScoring } from './scoreboard';
import { submissionLanguage } from './submission-language';

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
function freezeMinutes(value: unknown): number {
  const minutes=number(value,'封榜分钟数',7*24*60);
  if(!Number.isInteger(minutes))return fail(400,'封榜分钟数必须为非负整数，0 表示不封榜');
  return minutes;
}
function score(value:unknown):number {
  if(typeof value!=='number' || !Number.isFinite(value) || Math.abs(value)>1e12)return fail(400,'比赛得分无效');
  return value;
}
function scoringSystem(value:unknown,url?:string):string {
  if(!['ICPC','CCPC','OI','IOI'].includes(value as string))return fail(400,'请选择有效的 VP 赛制');
  if(url && new URL(url).hostname==='codeforces.com' && !['ICPC','CCPC'].includes(value as string))return fail(400,'Codeforces / Gym 原站 VP 仅支持 ICPC 或 CCPC 展示赛制');
  return value as string;
}
function externalRankUrl(raw:unknown,system:string):string|null {
  if(raw===undefined || raw===null || raw==='')return null;
  const value=text(raw,'外榜链接',500);
  if(!['ICPC','CCPC'].includes(system))return fail(400,'外榜仅支持 ICPC / CCPC 赛制');
  let url:URL;
  try {url=new URL(value);} catch {return fail(400,'外榜链接无效');}
  if(url.protocol!=='https:' || url.username || url.password || url.port || url.search || url.hash || /[\\\s%]/.test(value))return fail(400,'请使用 HTTPS 外榜页面链接');
  const board=url.hostname==='board.xcpcio.com' && /^\/icpc\/[A-Za-z0-9][A-Za-z0-9_-]*(?:\/[A-Za-z0-9][A-Za-z0-9_-]*)+\/?$/.test(url.pathname);
  const ccpc=url.hostname==='rank.iccpc.cn' && /^\/r\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/?$/i.test(url.pathname);
  if(!board && !ccpc)return fail(400,'外榜支持 board.xcpcio.com/icpc/… 或 rank.iccpc.cn/r/… 页面链接');
  return `https://${url.hostname}${(ccpc?url.pathname.toLowerCase():url.pathname).replace(/\/$/,'')}`;
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

export type User = {id:string;externalId?:string|null;useraccount:string;username:string;realname:string;numberid:string;email:string;role:string};
function profile(row: Row, full = false): User {
  return {id:String(row.id),useraccount:row.useraccount,username:row.username,realname:row.realname,
    numberid:row.numberid,email:full?row.email:'',role:row.role,...(full?{externalId:row.external_id??null}:{})};
}
const labelUser = (u: User) => `${u.username || u.useraccount}-${u.realname || u.username || u.useraccount}`;
type ParticipantIdentity = {participantTitle:string;participantSubtitle:string};
const userIdentity = (user:Pick<User,'username'|'realname'|'useraccount'>):ParticipantIdentity => ({participantTitle:user.username||user.useraccount,participantSubtitle:user.realname||''});
function legacyIdentity(label:string,user:Pick<User,'username'|'realname'|'useraccount'>):ParticipantIdentity {
  const suffix='-'+(user.realname||user.username||user.useraccount);
  // Nicknames and team names may contain hyphens. A legacy label can only be
  // separated when its entire saved real-name suffix is still identifiable.
  return label.endsWith(suffix) && label.length>suffix.length
    ? {participantTitle:label.slice(0,-suffix.length),participantSubtitle:user.realname||''}
    : {participantTitle:label,participantSubtitle:''};
}
const pendingVerdicts = new Set(['PENDING','TESTING','RUNNING','QUEUED','JUDGING','COMPILING','WAITING','WAITING-FOR-JUDGING','WAITING-FOR-TESTING']);
export type CloudOptions = { path?:string; adminAccount?:string;adminPassword?:string; externalAppKey?:string;externalBaseUrl?:string;publicUrl?:string; fetch?:typeof fetch };

export class CloudService {
  readonly store: Store;
  readonly publicUrl: string;
  readonly externalFetch: typeof fetch;
  private attempts = new Map<string,{at:number,count:number}>();
  private apiAttempts = new Map<string,{at:number,count:number}>();
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
  private apiKeyView(row:Row) {
    return {id:row.id,name:row.name,prefix:row.prefix,createdAt:row.created_at,lastUsedAt:row.last_used_at,revokedAt:row.revoked_at};
  }
  private manageApiKeys(method:string,segments:string[],body:Row,user:User) {
    if(user.role!=='admin')return fail(403,'只有超级管理员可以管理 API Key');
    if(segments.length===2) {
      if(method==='GET')return this.store.all('SELECT * FROM api_keys ORDER BY created_at DESC,id DESC').map(row=>this.apiKeyView(row));
      if(method==='POST') {
        const name=text(body.name,'密钥名称',100);
        return this.store.transaction(()=>{
          if(this.store.one('SELECT COUNT(*) count FROM api_keys WHERE revoked_at IS NULL')!.count>=100)return fail(409,'最多保留 100 个有效 API Key，请先撤销不再使用的密钥');
          const keyId=id(),apiKey='vp_'+randomBytes(32).toString('base64url');
          this.store.run('INSERT INTO api_keys(id,name,key_hash,prefix,created_by,created_at) VALUES(?,?,?,?,?,?)',keyId,name,hash(apiKey),apiKey.slice(0,11),user.id,now());
          this.audit(user,'api-key.create',keyId);
          return {...this.apiKeyView(this.store.one('SELECT * FROM api_keys WHERE id=?',keyId)!),apiKey};
        });
      }
      return fail(405,'不支持此操作');
    }
    if(segments.length===3 && method==='DELETE') {
      const keyId=segments[2];
      return this.store.transaction(()=>{
        const row=this.store.one('SELECT * FROM api_keys WHERE id=?',keyId);
        if(!row)return fail(404,'API Key 不存在');
        if(!row.revoked_at) {
          this.store.run('UPDATE api_keys SET revoked_at=? WHERE id=?',now(),keyId);
          this.audit(user,'api-key.revoke',keyId);
        }
        return {ok:true};
      });
    }
    return fail(405,'不支持此操作');
  }
  private authenticateApiKey(request:Request) {
    const authorization=request.headers.get('authorization'),headerKey=request.headers.get('x-api-key');
    const bearer=authorization?.match(/^Bearer (vp_[A-Za-z0-9_-]{43})$/)?.[1];
    if((authorization && !bearer) || (bearer && headerKey && bearer!==headerKey))return fail(401,'API Key 无效');
    const key=headerKey||bearer;
    if(!key || !/^vp_[A-Za-z0-9_-]{43}$/.test(key))return fail(401,'请提供有效的 API Key');
    const row=this.store.one('SELECT id FROM api_keys WHERE key_hash=? AND revoked_at IS NULL',hash(key));
    if(!row)return fail(401,'API Key 无效或已撤销');
    const current=Date.now();let entry=this.apiAttempts.get(row.id);
    if(!entry || current-entry.at>=60000) {entry={at:current,count:0};this.apiAttempts.set(row.id,entry);}
    if(++entry.count>120)return fail(429,'API 请求过于频繁，请一分钟后重试');
    if(this.apiAttempts.size>1000)for(const [key,value] of this.apiAttempts)if(current-value.at>=60000)this.apiAttempts.delete(key);
    this.store.run('UPDATE api_keys SET last_used_at=? WHERE id=?',now(),row.id);
  }
  private externalUser(params:URLSearchParams) {
    const row=this.store.one('SELECT * FROM users WHERE external_id=?',text(params.get('externalId'),'协会官网 ID',64));
    if(!row)return fail(404,'未找到已绑定的协会用户，请先在 VP 系统使用协会账号登录');
    return row;
  }
  private externalUserView(row:Row) {
    return {id:String(row.id),externalId:String(row.external_id),useraccount:row.useraccount,username:row.username,realname:row.realname};
  }
  private externalHistoryEnded(row:Row) {
    if(['failed','cancelled','scheduled'].includes(row.status))return false;
    if(row.room_id || JSON.parse(row.rules_json||'{}').duration>0)return this.restoreEnded(row);
    // Legacy standalone clients may have sent only the actual end timestamp.
    return ['complete','archived'].includes(row.status) && Date.parse(row.ended_at)<=Date.now();
  }
  private externalApi(request:Request,segments:string[]) {
    this.authenticateApiKey(request);
    if(request.method!=='GET')return fail(405,'外部 API 仅支持 GET 查询');
    const path=segments.join('/'),params=new URL(request.url).searchParams;
    if(path==='external/vp-history') {
      for(const name of new Set(params.keys())) {
        if(!['externalId','contestUrl','limit','offset'].includes(name))return fail(400,`不支持查询参数：${name}`);
        if(params.getAll(name).length>1)return fail(400,'查询参数不能重复');
      }
      const url=contestUrl(params.get('contestUrl'));
      const integer=(name:string,fallback:number,min:number,max:number)=>{
        const raw=params.get(name);if(raw===null)return fallback;
        if(!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw)<min || Number(raw)>max)return fail(400,`${name} 必须是 ${min}–${max} 的整数`);
        return Number(raw);
      };
      const limit=integer('limit',20,1,100),offset=integer('offset',0,0,1000000),user=this.externalUser(params);
      const records=this.store.all(`SELECT h.id,h.room_id,h.mode,h.status,h.started_at,h.ended_at,h.rules_json FROM histories h WHERE h.mode='vp' AND h.contest_url=? AND
        (h.owner_id=? OR EXISTS(SELECT 1 FROM participants p,json_each(p.members_json) m
          WHERE p.id=h.participant_id AND json_extract(m.value,'$.id')=?))
        ORDER BY h.created_at DESC,h.id DESC`,url,user.id,user.id).filter(row=>this.externalHistoryEnded(row));
      return {user:this.externalUserView(user),contestUrl:url,total:records.length,limit,offset,
        records:records.slice(offset,offset+limit).map(record=>{
          const row=this.store.one('SELECT * FROM histories WHERE id=?',record.id)!;
          return {...this.historyView(row,true),participantId:row.participant_id};
        }),serverNow:now()};
    }
    return fail(404,'接口不存在');
  }
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
  private participant(roomId:string,uid:string):Row|undefined { return this.store.one('SELECT p.* FROM participants p JOIN participant_members m ON p.id=m.participant_id WHERE m.room_id=? AND m.user_id=? AND NOT EXISTS(SELECT 1 FROM participant_departures d WHERE d.participant_id=p.id)',roomId,uid); }
  private roomRegistrationConfig(room:Row) {return JSON.stringify({contestUrl:room.contest_url,startsAt:room.starts_at,freezeMinutes:room.freeze_minutes??0,scoringSystem:room.scoring_system||'ICPC',...(room.external_rank_url?{externalRankUrl:room.external_rank_url}:{})});}
  private snapshotMember(participantId:string,userId:string) {return Boolean(this.store.one("SELECT 1 FROM participants p,json_each(p.members_json) m WHERE p.id=? AND json_extract(m.value,'$.id')=?",participantId,userId));}
  private checkParticipantContext(body:Row,participant:Row,room:Row,operatorId?:string) {
    // A legacy first-sync snapshot has no server history to bind it to its old
    // identity. After changing a roster or schedule, require explicit context
    // so old queued submissions/prints cannot become a new team's private data.
    if(operatorId && (body.participantId===undefined || body.roomStartsAt===undefined) && this.store.one("SELECT 1 FROM participant_departures d JOIN participants p ON p.id=d.participant_id WHERE p.room_id=? AND p.id<>? AND (p.captain_id=? OR EXISTS(SELECT 1 FROM json_each(p.members_json) m WHERE json_extract(m.value,'$.id')=?))",room.id,participant.id,operatorId,operatorId))return fail(409,'参赛身份或比赛设置已改变，请先运行 xcpc update，再重新运行房间 VP 命令');
    if(body.participantId!==undefined && text(body.participantId,'参赛身份',80)!==participant.id)return fail(409,'参赛身份已改变，请重新运行房间 VP 命令');
    if(body.roomStartsAt!==undefined && iso(body.roomStartsAt,'房间开始时间')!==room.starts_at)return fail(409,'VP 房间开始时间已改变，请重新运行房间 VP 命令');
    if(body.roomExternalRankUrl!==undefined && externalRankUrl(body.roomExternalRankUrl,room.scoring_system||'ICPC')!==(room.external_rank_url??null))return fail(409,'VP 房间外榜已改变，请重新运行房间 VP 命令');
  }
  private participantIdentity(row:Row):ParticipantIdentity {
    const members:User[]=JSON.parse(row.members_json);
    if(row.type!=='team')return members[0]?userIdentity(members[0]):{participantTitle:row.label,participantSubtitle:''};
    const names=members.map(member=>member.realname||member.username||member.useraccount).join('、');
    const suffix='-'+names;
    return {participantTitle:names && row.label.endsWith(suffix) && row.label.length>suffix.length?row.label.slice(0,-suffix.length):row.label,participantSubtitle:names};
  }
  private historyIdentity(row:Row):ParticipantIdentity {
    const participant=row.participant_id?this.store.one('SELECT * FROM participants WHERE id=?',row.participant_id):undefined;
    if(participant)return this.participantIdentity(participant);
    if(row.participant_title)return {participantTitle:row.participant_title,participantSubtitle:row.participant_subtitle||''};
    return legacyIdentity(row.participant_label,this.user(row.owner_id));
  }
  private participantView(row:Row) { return {id:row.id,type:row.type,userId:row.user_id,teamId:row.team_id,captainId:row.captain_id,label:row.label,...this.participantIdentity(row),members:JSON.parse(row.members_json)}; }
  private room(roomId:string):Row { const row=this.store.one('SELECT r.*,rules.duration_seconds,rules.freeze_minutes,rules.scoring_system,rules.external_rank_url FROM rooms r LEFT JOIN room_rules rules ON rules.room_id=r.id WHERE r.id=?',roomId);if(!row)return fail(404,'VP 房间不存在');return row; }
  private visibleRoom(roomId:string,user:User):Row {const room=this.room(roomId);if(room.creator_id!==user.id && !this.participant(roomId,user.id))return fail(403,'你尚未接受此 VP 房间的邀请');return room;}
  private manager(roomId:string,user:User):Row {const room=this.room(roomId);if(room.creator_id!==user.id)return fail(403,'只有创建者可以管理 VP 房间');return room;}
  private roomView(room:Row,user:User) {const p=this.participant(room.id,user.id),rules=this.store.one('SELECT duration_seconds,freeze_minutes,scoring_system,external_rank_url FROM room_rules WHERE room_id=?',room.id);return {id:room.id,name:room.name,contestUrl:room.contest_url,startsAt:room.starts_at,...roomTiming({...room,...rules}),scoringSystem:rules?.scoring_system||'ICPC',externalRankUrl:rules?.external_rank_url??null,createdBy:room.creator_id,createdAt:room.created_at,canManage:room.creator_id===user.id,participants:this.store.all('SELECT p.* FROM participants p WHERE room_id=? AND NOT EXISTS(SELECT 1 FROM participant_departures d WHERE d.participant_id=p.id) ORDER BY created_at',room.id).map(p=>this.participantView(p)),participation:p?this.participantView(p):null,serverNow:now()};}
  private withdrawRoom(roomId:string,user:User,body:Row,participantId?:string) {
    return this.store.transaction(()=>{
      const room=participantId?this.manager(roomId,user):this.room(roomId);
      if(Date.parse(room.starts_at)<=Date.now())return fail(409,'VP 已开始，不能取消报名或移除参赛者');
      const participant=participantId?this.store.one('SELECT p.* FROM participants p WHERE p.id=? AND p.room_id=? AND NOT EXISTS(SELECT 1 FROM participant_departures d WHERE d.participant_id=p.id)',participantId,roomId):this.participant(roomId,user.id);
      if(!participant)return fail(404,'参赛报名不存在或已经取消');
      if(!participantId && participant.captain_id!==user.id)return fail(403,'只有队长可以取消整支队伍的报名');
      this.checkParticipantContext(body,participant,room);
      const members:User[]=JSON.parse(participant.members_json),memberIds=new Set(members.map(member=>member.id));
      const stamp=now(),reason=participantId?'removed':'withdrawn';
      // Active membership is released; the original immutable roster, history
      // and print rows remain available to their original owners and members.
      this.store.run('INSERT INTO participant_departures VALUES(?,?,?,?,?,?)',participant.id,roomId,user.id,reason,stamp,this.roomRegistrationConfig(room));
      this.store.run('DELETE FROM participant_members WHERE participant_id=?',participant.id);
      for(const invitation of this.store.all("SELECT * FROM notifications WHERE room_id=? AND kind='room_invite' AND status='pending'",roomId)) {
        const payload=JSON.parse(invitation.payload);
        const targets=payload.type==='team'?this.store.all('SELECT user_id FROM team_members WHERE team_id=?',invitation.team_id).map(row=>row.user_id):[invitation.recipient_id];
        if(!targets.some(uid=>memberIds.has(uid)))continue;
        this.store.run("UPDATE notifications SET status='cancelled',responded_at=? WHERE id=?",stamp,invitation.id);
        if(!memberIds.has(invitation.recipient_id) && invitation.recipient_id!==room.creator_id)this.notify(user,invitation.recipient_id,'info','VP 邀请已撤销',`${room.name} 的参赛人员已调整，请等待新的邀请`,{roomId});
      }
      for(const recipient of new Set([...memberIds,room.creator_id]))this.notify(user,recipient,'info',participantId?'VP 参赛者已移除':'VP 报名已取消',`${participant.label} ${participantId?'已被房主移出':'已取消报名'} ${room.name}。重新参赛需要房主再次邀请。`,{roomId,teamId:participant.team_id||undefined});
      this.audit(user,'room.participant.'+reason,participant.id);
      return {withdrawn:true,roomId,participantId:participant.id};
    });
  }
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
          const roster=members.map(member=>member.id).sort().join('\n');
          const previous=this.store.all('SELECT p.*,d.config_json FROM participants p JOIN participant_departures d ON d.participant_id=p.id WHERE p.room_id=? AND p.type=? AND p.captain_id=? ORDER BY d.departed_at DESC,p.id',room.id,type,captainId).find(p=>p.team_id===(type==='team'?n.team_id:null) && p.user_id===(type==='individual'?user.id:null) && p.config_json===this.roomRegistrationConfig(room) && JSON.parse(p.members_json).map((member:User)=>member.id).sort().join('\n')===roster);
          const pid=previous?.id||id();
          if(previous)this.store.run('DELETE FROM participant_departures WHERE participant_id=?',pid);
          else this.store.run('INSERT INTO participants VALUES(?,?,?,?,?,?,?,?,?)',pid,room.id,type,type==='team'?null:user.id,type==='team'?n.team_id:null,captainId,label,JSON.stringify(members),now());
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
    if(row.participant_id && this.snapshotMember(row.participant_id,user.id))return true;
    if(!details || !row.room_id)return false;
    const room=this.room(row.room_id);
    if(Date.now()<Date.parse(roomTiming(room).endsAt))return false;
    return Boolean(room.creator_id===user.id || this.participant(row.room_id,user.id));
  }
  private statisticsView(room:Row,histories:Row[],problems:string[],at=Date.now()) {
    const timing=roomTiming(room,at),cutoff=timing.frozen && timing.freezeAt?Date.parse(timing.freezeAt):at;
    const reference=histories.filter(h=>h.statistics_json && h.statistics_at && Date.parse(h.statistics_at)<=cutoff && (h.mode!=='vp' || h.statistics_source==='original')).sort((a,b)=>b.statistics_at.localeCompare(a.statistics_at))[0];
    const stats=reference?JSON.parse(reference.statistics_json):null;
    const allRevealed=room.scoring_system!=='CCPC' || timing.frozen || at>=Date.parse(timing.endsAt);
    const revealed=new Set<string>(allRevealed?problems:reference?JSON.parse(reference.revealed_problems_json||'[]'):[]);
    // Unrevealed CCPC identities are omitted from JSON, not merely hidden by CSS.
    const publicProblemStatistics=problems.map(problem=>({label:revealed.has(problem)?problem:null,submitted:stats?.submitted?.[problem]??null,attempted:stats?.attempted?.[problem]??null,accepted:stats?.accepted?.[problem]??null})).sort((a,b)=>a.label && b.label?a.label.localeCompare(b.label,'en',{numeric:true}):a.label?-1:b.label?1:(b.accepted??-1)-(a.accepted??-1)||(b.attempted??-1)-(a.attempted??-1)||(b.submitted??-1)-(a.submitted??-1));
    return {publicProblemStatistics,statisticsAt:reference?.statistics_at??null,statisticsSource:reference?.statistics_source||'unavailable'};
  }
  private historyPrize(row:Row) {
    if(row.mode==='bt' || !row.external_rank_url)return {prize:null,prizeSource:'unavailable'};
    const ended=['complete','archived'].includes(row.status) && this.restoreEnded(row);
    if(row.mode==='bt' || !ended || !row.external_rank_url)return {prize:null,prizeSource:'unavailable'};
    const valid=row.rank_source==='original' && row.prize_source==='original' && ['Gold','Silver','Bronze','None'].includes(row.prize);
    return {prize:valid?row.prize:'Unknown',prizeSource:valid?'original':'unavailable'};
  }
  private historyView(row:Row, details=false, viewer?:User, publicBoard=false) {
    const participantType=row.participant_id?this.store.one('SELECT type FROM participants WHERE id=?',row.participant_id)?.type||'individual':'individual';
    const room=row.room_id?this.store.one('SELECT r.*,rules.duration_seconds,rules.freeze_minutes,rules.scoring_system,rules.external_rank_url FROM rooms r LEFT JOIN room_rules rules ON rules.room_id=r.id WHERE r.id=?',row.room_id):undefined,timing=room?roomTiming(room):undefined;
    const frozen=Boolean(timing?.frozen && (publicBoard || (viewer && !this.historyVisible(row,viewer))));
    const at=Date.now(), submissions:Row[]=JSON.parse(row.submissions_json);
    const hidePractice=Boolean(publicBoard && timing && at<Date.parse(timing.endsAt));
    const filtered=room && (frozen || submissions.some(s=>Date.parse(s.submittedAt)>at || (hidePractice && s.practice)))?publicScore(row,room,at,frozen,hidePractice):undefined;
    const rules=JSON.parse(row.rules_json||'{}');
    if(frozen && rules.score_policy!=='subtasks' && filtered?.points!==undefined && row.freeze_points!==null && row.freeze_points!==undefined && row.freeze_event_points!==null && row.freeze_event_points!==undefined)filtered.points+=row.freeze_points-row.freeze_event_points;
    else if(frozen && filtered && (filtered.points===undefined || row.freeze_event_points===null) && row.freeze_points!==null && row.freeze_points!==undefined)filtered.points=row.freeze_points;
    const value={id:row.id,...this.historyPrize(row),participantType,...this.historyIdentity(row),contestName:room?.name||row.contest_name,contestUrl:row.contest_url,externalRankUrl:row.external_rank_url??room?.external_rank_url??null,platform:row.platform,mode:row.mode,rank:historyRank(row,room,frozen),rankSource:row.mode==='bt'?'unavailable':frozen?(row.freeze_rank_source==='original'?'original':'unavailable'):row.rank_source||'legacy',solved:filtered?.solved??row.solved,penalty:row.mode==='bt'?0:filtered?.penalty??row.penalty,points:row.mode==='bt'?null:filtered?filtered.points??null:row.points??null,scoring:rules.scoring||'ICPC',problems:JSON.parse(row.problems_json),cells:filtered?.cells??JSON.parse(row.cells_json),startedAt:row.mode==='bt'?null:row.started_at,endedAt:row.mode==='bt'?null:row.ended_at,participantLabel:row.participant_label,roomId:row.room_id,status:row.status,createdAt:row.created_at,updatedAt:row.updated_at,rules,...(timing?{...timing,freezeActive:timing.frozen,frozen}: {frozen:false})};
    const statistics=row.mode!=='bt' || row.statistics_json?this.statisticsView(room||{starts_at:row.started_at||now(),duration_seconds:rules.duration,freeze_minutes:rules.freeze_minutes,scoring_system:rules.contest_format},[row],JSON.parse(row.problems_json),at):{};
    return details?{...value,...statistics,submissions:(filtered?.submissions??submissions.filter(s=>Date.parse(s.submittedAt)<=at)).map(s=>({...s,language:submissionLanguage(s.language)}))}:value;
  }
  private restoreEnded(row:Row):boolean {
    if(['failed','cancelled','scheduled'].includes(row.status))return false;
    if(row.mode==='bt')return true;
    if(row.room_id) {
      const room=this.room(row.room_id);
      // Never authorize a teammate from a guessed/default duration or a
      // client's early "complete" flag while the shared room is still live.
      return room.duration_seconds>0 && Date.now()>=Date.parse(roomTiming(room).endsAt);
    }
    const rules=JSON.parse(row.rules_json||'{}'),start=Date.parse(row.started_at);
    const natural=Number.isFinite(start)&&rules.duration>0?start+rules.duration*1000:Infinity;
    const manual=row.status==='archived'&&row.ended_at?Date.parse(row.ended_at):Infinity;
    return Date.now()>=Math.min(natural,manual);
  }
  private restoreView(row:Row,user:User) {
    if(row.owner_id!==user.id && (!row.participant_id || !this.snapshotMember(row.participant_id,user.id)))return fail(404,'比赛记录不存在或无权恢复');
    if(!this.restoreEnded(row))return fail(409,'本场比赛尚未结束，不能恢复为补题');
    const saved=JSON.parse(row.rules_json||'{}'),rules:Row={};
    for(const key of ['duration','penalty_minutes','penalty_rounding','compile_penalty','freeze_minutes','scoring','score_policy','problem_max_scores','contest_format','problem_reveal_count'])if(saved[key]!==undefined)rules[key]=saved[key];
    const room=row.room_id?this.room(row.room_id):undefined;
    const recordView=(history:Row):Row[]=>JSON.parse(history.submissions_json).filter((s:Row)=>Date.parse(s.submittedAt)<=Date.now()).map((s:Row)=>({id:s.id,problem:s.problem,submittedAt:s.submittedAt,verdict:s.verdict,practice:s.practice===true,language:submissionLanguage(s.language),...(s.points===undefined?{}:{points:s.points})}));
    // Explicit outbound allowlist: no judge credentials, local paths, source
    // files, association passwords, or other teams' records can be restored.
    const submissions=recordView(row),personal=this.store.one("SELECT * FROM histories WHERE owner_id=? AND contest_url=? AND mode='bt' AND room_id IS NULL AND id<>? ORDER BY created_at DESC,id DESC LIMIT 1",user.id,row.contest_url,row.id);
    return {historyId:row.id,...this.historyPrize(row),localId:row.local_id,isOwner:row.owner_id===user.id,roomId:row.room_id,roomName:room?.name??null,externalRankUrl:row.external_rank_url??room?.external_rank_url??null,participantId:row.participant_id,
      participantType:row.participant_id?this.store.one('SELECT type FROM participants WHERE id=?',row.participant_id)?.type||'individual':'individual',
      ...this.historyIdentity(row),mode:row.mode,startedAt:row.started_at,endedAt:room?roomTiming(room).endsAt:row.ended_at,
      rank:historyRank(row,room,false),revision:row.revision,updatedAt:row.updated_at,
      contest:{platform:row.platform,contestId:new URL(row.contest_url).pathname.split('/').pop(),url:row.contest_url,name:row.contest_name,problems:JSON.parse(row.problems_json),rules},submissions,
      personalPractice:personal?{historyId:personal.id,localId:personal.local_id,revision:personal.revision,submissions:recordView(personal)}:null,serverNow:now()};
  }
  private sanitizeSnapshot(body:Row) {
    const localId=text(body.localId,'本地记录标识',200), c=body.contest;
    if(!c || typeof c!=='object')return fail(400,'缺少比赛资料');
    const url=contestUrl(c.url), mode=body.mode;
    if(!['vp','live','bt'].includes(mode))return fail(400,'参赛类型无效');
    if(body.rankSource!==undefined && !['original','native','unavailable'].includes(body.rankSource))return fail(400,'排名来源无效');
    const rankSource=mode==='bt'?'unavailable':body.rankSource??(mode==='live'?'native':'legacy');
    if(body.statisticsSource!==undefined && !['original','native','unavailable'].includes(body.statisticsSource))return fail(400,'原站统计来源无效');
    const statisticsSource=body.statisticsSource??(mode==='live'?'native':'legacy');
    const platform=url.includes('codeforces.com/gym/')?'gym':url.includes('codeforces.com/')?'cf':url.includes('nowcoder.com/')?'nowcoder':url.includes('cwnupaa.com/')?'cwnuoj':'qoj';
    const name=text(c.name,'比赛名称',300);
    const rules:Row={};
    if(c.rules!==undefined) {
      if(!c.rules || typeof c.rules!=='object' || Array.isArray(c.rules))return fail(400,'比赛规则无效');
      if(c.rules.duration!==undefined) {rules.duration=number(c.rules.duration,'比赛时长',7*86400);if(!Number.isInteger(rules.duration)||rules.duration<1)return fail(400,'比赛时长必须为正整数秒');}
      if(c.rules.penalty_minutes!==undefined)rules.penalty_minutes=number(c.rules.penalty_minutes,'错误提交罚时',1000);
      if(c.rules.penalty_rounding!==undefined) {if(!['per_problem','total_seconds'].includes(c.rules.penalty_rounding))return fail(400,'罚时取整方式无效');rules.penalty_rounding=c.rules.penalty_rounding;}
      if(c.rules.compile_penalty!==undefined && typeof c.rules.compile_penalty!=='boolean')return fail(400,'编译错误罚时配置无效');
      rules.compile_penalty=c.rules.compile_penalty===true;
      if(c.rules.freeze_minutes!==undefined)rules.freeze_minutes=number(c.rules.freeze_minutes,'封榜分钟数',7*24*60);
      if(c.rules.scoring!==undefined) {if(!['ICPC','CF','OI','IOI','WEEKLY'].includes(c.rules.scoring))return fail(400,'计分赛制无效');rules.scoring=c.rules.scoring;}
      if(c.rules.score_policy!==undefined) {if(!['best','latest','subtasks','native'].includes(c.rules.score_policy))return fail(400,'分数统计方式无效');rules.score_policy=c.rules.score_policy==='native'?'best':c.rules.score_policy;}
      if(c.rules.contest_format!==undefined)rules.contest_format=scoringSystem(c.rules.contest_format);
      if(c.rules.problem_reveal_count!==undefined) {if(c.rules.problem_reveal_count!==null && c.rules.problem_reveal_count!==50)return fail(400,'CCPC 公开题号阈值无效');rules.problem_reveal_count=c.rules.problem_reveal_count;}
    }
    if(!Array.isArray(c.problems) || c.problems.length>100 || c.problems.some((v:unknown)=>typeof v!=='string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$/.test(v) || ['constructor','prototype','__proto__'].includes(v)) || new Set(c.problems).size!==c.problems.length)return fail(400,'题目列表无效');
    const problems=c.problems as string[], cells:Row=Object.create(null);
    let publicStatistics:Row|null=null, revealedProblems:string[]|null=null,statisticsAt:string|null=null;
    if(body.publicStatistics!==undefined) {
      if(!body.publicStatistics || typeof body.publicStatistics!=='object' || Array.isArray(body.publicStatistics))return fail(400,'原站公开统计无效');
      publicStatistics=Object.create(null);
      for(const kind of ['submitted','attempted','accepted']) {
        const values=body.publicStatistics[kind];if(!values || typeof values!=='object' || Array.isArray(values))return fail(400,'原站公开统计无效');
        publicStatistics![kind]=Object.create(null);
        for(const [problem,count] of Object.entries(values)) {if(!problems.includes(problem))return fail(400,'统计题号不属于此比赛');const value=number(count,'原站统计',1e9);if(!Number.isInteger(value))return fail(400,'原站统计必须为整数');publicStatistics![kind][problem]=value;}
      }
      if(!Array.isArray(body.revealedProblems) || body.revealedProblems.some((p:unknown)=>!problems.includes(p as string)) || new Set(body.revealedProblems).size!==body.revealedProblems.length)return fail(400,'公开题号列表无效');
      revealedProblems=body.revealedProblems;statisticsAt=iso(body.statisticsAt,'原站统计时刻');
    }
    if(c.rules?.problem_max_scores!==undefined) {
      if(!c.rules.problem_max_scores || typeof c.rules.problem_max_scores!=='object' || Array.isArray(c.rules.problem_max_scores))return fail(400,'题目满分配置无效');
      rules.problem_max_scores=Object.create(null);
      for(const [problem,score] of Object.entries(c.rules.problem_max_scores)) {if(!problems.includes(problem))return fail(400,'满分配置题号不属于此比赛');rules.problem_max_scores[problem]=number(score,'题目满分');}
    }
    for(const key of problems) {
      const cell=body.cells?.[key]||{};
      cells[key]={tries:number(cell.tries??0,'尝试次数',1e6),pending:number(cell.pending??0,'待评测次数',1e6),solved:mode!=='bt'&&cell.solved===true,practiceSolved:cell.practiceSolved===true || mode==='bt'&&cell.solved===true};
      if(mode!=='bt'&&cell.solveTime!==undefined)cells[key].solveTime=number(cell.solveTime,'通过时间');
      if(mode!=='bt') {
        if(cell.points!==undefined)cells[key].points=number(cell.points,'题目得分');
        if(cell.maxPoints!==undefined)cells[key].maxPoints=number(cell.maxPoints,'题目满分');
      }
    }
    if(!Array.isArray(body.submissions) || body.submissions.length>20000)return fail(400,'提交记录列表无效');
    const submissions=body.submissions.map((s:Row)=>{
      if(!s || !problems.includes(s.problem))return fail(400,'提交记录题号不属于此比赛');
      const value:Row={id:text(String(s.id??''),'提交ID',120),problem:s.problem,submittedAt:iso(s.submittedAt,'提交时间'),verdict:text(s.verdict,'判题状态',100),practice:mode==='bt'||s.practice===true};
      if(s.language!==undefined)value.language=submissionLanguage(s.language);
      if(s.points!==undefined)value.points=number(s.points,'提交得分');
      if(s.url) {let link:URL;try{link=new URL(s.url);}catch{return fail(400,'提交记录链接无效');} if(link.protocol==='https:' && !link.username && !link.password && link.hostname.replace(/^www\./,'')===new URL(url).hostname)value.url=link.href;}
      return value;
    });
    const status=['scheduled','running','complete','archived','cancelled','failed'].includes(body.status)?body.status:mode==='bt'?'practice':body.endedAt?'complete':'running';
    let createdAt:string|null=null;
    if(body.createdAt!==undefined) {
      if(typeof body.createdAt!=='string' || !/^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(body.createdAt))return fail(400,'创建时间必须是有效 ISO 时间');
      const stamp=Date.parse(iso(body.createdAt,'创建时间'));
      if(new Date(body.createdAt.slice(0,10)+'T00:00:00Z').toISOString().slice(0,10)!==body.createdAt.slice(0,10))return fail(400,'创建时间无效');
      if(stamp<0)return fail(400,'创建时间无效');
      // Native clocks may lead this server slightly. A future client clock
      // must neither reject otherwise valid results nor pin history on top.
      createdAt=new Date(Math.min(stamp,Date.now())).toISOString();
    }
    let startedAt=mode==='bt'?null:body.startedAt?iso(body.startedAt,'开始时间'):null;
    const endedAt=mode==='bt'?null:body.endedAt?iso(body.endedAt,'结束时间'):null;
    if(status==='failed')startedAt=null;
    if(startedAt && endedAt && endedAt<startedAt) {if(['failed','cancelled'].includes(status))startedAt=null;else return fail(400,'结束时间不能早于开始时间');}
    const externalSource=body.externalRankUrl===undefined?undefined:externalRankUrl(body.externalRankUrl,rules.contest_format||rules.scoring||'ICPC');
    if(body.prize!==undefined && body.prize!==null && !['Gold','Silver','Bronze','None','Unknown'].includes(body.prize))return fail(400,'奖项无效');
    if(body.prizeSource!==undefined && !['original','unavailable'].includes(body.prizeSource))return fail(400,'奖项来源无效');
    return {localId,url,mode,status,platform,name,problems,rules,externalRankUrl:externalSource,prize:body.prize,prizeSource:body.prizeSource??'unavailable',cells,submissions,publicStatistics,revealedProblems,statisticsAt,statisticsSource,startedAt,endedAt,createdAt,rankSource,rank:mode==='bt'?'—':text(String(body.rank??'—'),'原站排名',100),solved:number(body.solved,'解决题数',100),penalty:mode==='bt'?0:number(body.penalty,'罚时'),points:mode==='bt'||body.points===undefined?null:score(body.points),revision:number(body.revision,'同步版本',Number.MAX_SAFE_INTEGER)};
  }
  private sync(user:User,body:Row) {
    const data=this.sanitizeSnapshot(body);
    return this.store.transaction(()=>{
      let participant:Row|undefined, room:Row|undefined;
      if(body.roomId) {
        room=this.room(text(body.roomId,'房间ID',80)); participant=this.participant(room.id,user.id);
        if(!participant || participant.captain_id!==user.id)return fail(403,'只有已报名的个人或队长可以同步此 VP');
        this.checkParticipantContext(body,participant,room,user.id);
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
          const identity=this.participantIdentity(participant!);
          this.store.run('UPDATE histories SET room_id=?,participant_id=?,participant_label=?,participant_title=?,participant_subtitle=?,updated_at=? WHERE id=?',room!.id,participant!.id,participant!.label,identity.participantTitle,identity.participantSubtitle,now(),old.id);
          this.audit(user,'history.bind-room',old.id);
          return {id:old.id,upgraded:true};
        }
        return {id:old.id,stale:true};
      }
      if(room && data.rules.duration!==undefined) {
        if(room.duration_seconds && room.duration_seconds!==data.rules.duration)return fail(409,'比赛时长与房间已同步的原站时长不一致');
        if(!room.duration_seconds) {this.store.run('INSERT INTO room_rules(room_id,duration_seconds,freeze_minutes) VALUES(?,?,?) ON CONFLICT(room_id) DO UPDATE SET duration_seconds=excluded.duration_seconds',room.id,data.rules.duration,room.freeze_minutes??0);room.duration_seconds=data.rules.duration;}
      }
      if(room) {
        data.rules.freeze_minutes=room.freeze_minutes??0;data.rules.contest_format=room.scoring_system||'ICPC';data.rules.problem_reveal_count=room.scoring_system==='CCPC'?50:null;
        if(['OI','IOI'].includes(room.scoring_system)) {
          if(data.rules.scoring && data.rules.scoring!==room.scoring_system)return fail(409,'原站计分赛制与 VP 房间设置不一致');
          data.rules.scoring=room.scoring_system;
          data.rules.score_policy ||= room.scoring_system==='OI'?'latest':'best';
        }
      }
      const records=new Map<string,Row>();
      if(old)for(const s of JSON.parse(old.submissions_json))records.set(s.id,s);
      for(const s of data.submissions) {
        const prior=records.get(s.id);
        // Older clients and judge refreshes may omit language metadata. Keep
        // an existing known category without changing final-verdict precedence.
        const language=submissionLanguage(s.language),previousLanguage=submissionLanguage(prior?.language);
        const languageInfo=language!=='other'?{language}:previousLanguage!=='other'?{language:previousLanguage}:{};
        if(prior && !pendingVerdicts.has(prior.verdict) && pendingVerdicts.has(s.verdict)) {
          records.set(s.id,{...prior,...languageInfo});continue;
        }
        records.set(s.id,{...s,...languageInfo,...(data.mode==='bt' && prior && !prior.practice && s.practice?{practice:false}:{})});
      }
      if(records.size>20000)return fail(400,'单场比赛提交记录超出上限');
      const submissions=[...records.values()].sort((a,b)=>a.submittedAt.localeCompare(b.submittedAt));
      const accepted=new Set(['AC','ACCEPTED','CORRECT','OK','PRETESTS-PASSED']);
      const effectiveRules={...JSON.parse(old?.rules_json||'{}'),...data.rules};
      for(const event of submissions)if(!pointScoring(effectiveRules) && accepted.has(event.verdict)&&data.cells[event.problem]) {
        data.cells[event.problem][event.practice?'practiceSolved':'solved']=true;
      }
      const mode=old && old.mode!=='bt'?old.mode:room?'vp':data.mode;
      const startedAt=mode==='bt' || data.status==='failed' || (data.status==='cancelled' && !data.startedAt)?null:(upgrading?old!.started_at:data.startedAt||old?.started_at||room?.starts_at||null);
      const endedAt=mode==='bt'?null:(data.endedAt||old?.ended_at||null);
      const hid=old?.id||id();
      const practiceContinuation=Boolean(old && old.mode!=='bt' && data.mode==='bt');
      if(practiceContinuation)for(const [problem,prior] of Object.entries(JSON.parse(old!.cells_json)) as [string,Row][]) {
        if(data.cells[problem])for(const key of ['solved','solveTime','points','maxPoints'])if(prior[key]!==undefined)data.cells[problem][key]=prior[key];
      }
      const externalSource=data.externalRankUrl===undefined?(old?.external_rank_url??room?.external_rank_url??null):data.externalRankUrl;
      if(room?.external_rank_url && externalSource!==room.external_rank_url)return fail(409,'同步外榜与房间指定的外榜不一致');
      const sameRankBoard=data.externalRankUrl===undefined || old?.external_rank_url===externalSource;
      // A scheduled future end is not a final result. Require a snapshot
      // received at/after its end; rooms also require the shared contest end.
      const finalStatus=Boolean(old && (['complete','archived'].includes(old.status) || !JSON.parse(old.rules_json||'{}').duration));
      const endedRank=Boolean(finalStatus && old?.ended_at && Date.parse(old.ended_at)<=Date.now() && Date.parse(old.updated_at)>=Date.parse(old.ended_at) && (!old.room_id || this.restoreEnded(old)));
      const preserveRank=Boolean(old && (practiceContinuation || endedRank && sameRankBoard && old.rank!=='—') && (mode!=='vp' || old.rank_source==='original'));
      const rankSource=preserveRank?old!.rank_source:data.rankSource;
      const rank=mode==='vp' && rankSource!=='original'?'—':preserveRank?old!.rank:data.rank;
      const identity=participant?this.participantIdentity(participant):old?this.historyIdentity(old):userIdentity(user);
      const values=[user.id,old?.local_id||data.localId,room?.id||null,participant?.id||null,participant?.label||old?.participant_label||labelUser(user),data.name,data.url,data.platform,mode,startedAt,endedAt,rank,practiceContinuation?old!.solved:data.solved,practiceContinuation?old!.penalty:data.penalty,JSON.stringify(data.problems),JSON.stringify(data.cells),JSON.stringify(submissions),data.revision,now()];
      if(old)this.store.run('UPDATE histories SET owner_id=?,local_id=?,room_id=?,participant_id=?,participant_label=?,contest_name=?,contest_url=?,platform=?,mode=?,started_at=?,ended_at=?,rank=?,solved=?,penalty=?,problems_json=?,cells_json=?,submissions_json=?,revision=?,updated_at=? WHERE id=?',...values,hid);
      else this.store.run('INSERT INTO histories(id,owner_id,local_id,room_id,participant_id,participant_label,contest_name,contest_url,platform,mode,started_at,ended_at,rank,solved,penalty,problems_json,cells_json,submissions_json,revision,updated_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',hid,...values,data.createdAt||now());
      this.store.run('UPDATE histories SET status=?,rank_source=?,participant_title=?,participant_subtitle=?,external_rank_url=? WHERE id=?',preserveRank && ['complete','archived'].includes(old?.status) && data.status==='running'?old!.status:data.status,rankSource,identity.participantTitle,identity.participantSubtitle,externalSource,hid);
      const rules=effectiveRules;
      this.store.run('UPDATE histories SET rules_json=?,points=? WHERE id=?',JSON.stringify(rules),practiceContinuation?old!.points:data.points??old?.points??null,hid);
      const latest=this.store.one('SELECT * FROM histories WHERE id=?',hid)!;
      const ended=['complete','archived'].includes(latest.status) && this.restoreEnded(latest);
      const keepPrize=Boolean(old && sameRankBoard && (data.prize===undefined || preserveRank && old.prize_source==='original'));
      const validPrize=ended && externalSource && rankSource==='original' && data.prizeSource==='original'
        && ['Gold','Silver','Bronze','None'].includes(data.prize) && String(data.rank).replace(/^=/,'')===String(rank).replace(/^=/,'');
      const prize=keepPrize?old!.prize:validPrize?data.prize:null;
      const prizeSource=keepPrize?old!.prize_source:validPrize?'original':'unavailable';
      this.store.run('UPDATE histories SET prize=?,prize_source=? WHERE id=?',prize,prizeSource,hid);
      const statisticsTiming=room?roomTiming(room):null;
      const statisticsCutoff=statisticsTiming?.frozen && statisticsTiming.freezeAt?Date.parse(statisticsTiming.freezeAt):Infinity;
      if(data.statisticsSource==='unavailable')this.store.run('UPDATE histories SET statistics_json=NULL,revealed_problems_json=NULL,statistics_at=NULL,statistics_source=? WHERE id=?','unavailable',hid);
      else if(data.publicStatistics && Date.parse(data.statisticsAt!)<=statisticsCutoff)this.store.run('UPDATE histories SET statistics_json=?,revealed_problems_json=?,statistics_at=?,statistics_source=? WHERE id=?',JSON.stringify(data.publicStatistics),JSON.stringify(data.revealedProblems),data.statisticsAt,data.statisticsSource,hid);
      // Only server receipt time establishes that an original rank predates freezing.
      const freezeAt=room?roomTiming(room).freezeAt:null;
      if(room && old?.freeze_rank_source!=='original')this.store.run('UPDATE histories SET freeze_rank=NULL,freeze_rank_at=NULL,freeze_rank_source=? WHERE id=?','unavailable',hid);
      if(room && (!freezeAt || Date.now()<Date.parse(freezeAt))) {
        if(rankSource==='original' && /^=?[1-9][0-9]*$/.test(rank))this.store.run('UPDATE histories SET freeze_rank=?,freeze_rank_at=?,freeze_rank_source=? WHERE id=?',rank,now(),'original',hid);
        if(pointScoring(rules) && data.points!==null) {
          const projection=publicScore(this.store.one('SELECT * FROM histories WHERE id=?',hid)!,room,Date.now(),false,true);
          this.store.run('UPDATE histories SET freeze_points=?,freeze_event_points=?,freeze_cells_json=? WHERE id=?',data.points,projection.points??null,JSON.stringify(data.cells),hid);
        }
      }
      return {id:hid};
    });
  }
  private printView(row:Row,detail=false) {const value={id:row.id,roomId:row.room_id,participantLabel:row.label,filename:row.filename,language:row.language,createdAt:row.created_at};return detail?{...value,code:row.code}:value;}
  async dispatch(request:Request, segments:string[], body:Row={}):Promise<{data:unknown;session?:{token:string;expiresAt:string};clearCookie?:boolean}> {
    const method=request.method, path=segments.join('/');
    if(path==='health' && method==='GET')return {data:{status:'ok'}};
    if(path==='client/releases/latest' && method==='GET')return {data:latestRelease()};
    if((path==='auth/login'||path==='auth/admin-login') && method==='POST') {const data=await this.login(request,body,path.endsWith('admin-login'));return {data,session:data};}
    if(segments[0]==='external')return {data:this.externalApi(request,segments)};
    const {user,token}=this.auth(request);
    if(path==='auth/me' && method==='GET')return {data:user};
    if(path==='auth/logout' && method==='POST') {this.store.run('DELETE FROM sessions WHERE token_hash=?',hash(token));return {data:{ok:true},clearCookie:true};}
    if(segments[0]==='admin' && segments[1]==='api-keys')return {data:this.manageApiKeys(method,segments,body,user)};
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
      if(method==='GET')return {data:this.store.all('SELECT DISTINCT r.* FROM rooms r LEFT JOIN participant_members m ON m.room_id=r.id WHERE r.creator_id=? OR m.user_id=? ORDER BY r.created_at DESC,r.id DESC',user.id,user.id).map(r=>this.roomView(r,user))};
      if(method==='POST') {const name=text(body.name,'VP名称',200),url=contestUrl(body.contestUrl,true),startsAt=iso(body.startsAt,'开始时间'),freeze=freezeMinutes(body.freezeMinutes),system=scoringSystem(body.scoringSystem,url),rankUrl=externalRankUrl(body.externalRankUrl,system);if(Date.parse(startsAt)<=Date.now())return fail(400,'开始时间必须晚于当前时间');const rid=id();this.store.transaction(()=>{this.store.run('INSERT INTO rooms(id,name,contest_url,starts_at,creator_id,created_at) VALUES(?,?,?,?,?,?)',rid,name,url,startsAt,user.id,now());this.store.run('INSERT INTO room_rules(room_id,duration_seconds,freeze_minutes,scoring_system,external_rank_url) VALUES(?,?,?,?,?)',rid,0,freeze,system,rankUrl);this.audit(user,'room.create',rid);});return {data:this.roomView(this.room(rid),user)};}
    }
    if(segments[0]==='rooms' && segments.length>=2) {
      const rid=segments[1];
      if(segments.length===2) {
        if(method==='GET')return {data:this.roomView(this.visibleRoom(rid,user),user)};
        if(method==='PATCH') {
          const room=this.manager(rid,user);
          if(Date.parse(room.starts_at)<=Date.now())return fail(409,'VP 已开始，不能修改比赛设置');
          const url=body.contestUrl===undefined?room.contest_url:contestUrl(body.contestUrl,true),startsAt=body.startsAt===undefined?room.starts_at:iso(body.startsAt,'开始时间');
          const freeze=body.freezeMinutes===undefined?(room.freeze_minutes??0):freezeMinutes(body.freezeMinutes),system=body.scoringSystem===undefined?(room.scoring_system||'ICPC'):scoringSystem(body.scoringSystem,url);
          scoringSystem(system,url);
          const rankUrl=externalRankUrl(body.externalRankUrl===undefined?room.external_rank_url:body.externalRankUrl,system);
          if((url!==room.contest_url || startsAt!==room.starts_at || freeze!==(room.freeze_minutes??0) || system!==(room.scoring_system||'ICPC') || rankUrl!==(room.external_rank_url??null)) && this.store.one('SELECT 1 FROM histories WHERE room_id=?',rid))return fail(409,'已有客户端预约此 VP，不能更改比赛、开始时间、赛制、封榜或外榜设置');
          if(Date.parse(startsAt)<=Date.now())return fail(400,'开始时间必须晚于当前时间');
          this.store.transaction(()=>{this.store.run('UPDATE rooms SET name=?,contest_url=?,starts_at=? WHERE id=?',body.name===undefined?room.name:text(body.name,'VP名称',200),url,startsAt,rid);this.store.run('INSERT INTO room_rules(room_id,duration_seconds,freeze_minutes,scoring_system,external_rank_url) VALUES(?,?,?,?,?) ON CONFLICT(room_id) DO UPDATE SET duration_seconds=excluded.duration_seconds,freeze_minutes=excluded.freeze_minutes,scoring_system=excluded.scoring_system,external_rank_url=excluded.external_rank_url',rid,url===room.contest_url?(room.duration_seconds??0):0,freeze,system,rankUrl);this.audit(user,'room.update',rid);});return {data:this.roomView(this.room(rid),user)};
        }
      }
      if(segments.length===3 && segments[2]==='invitations' && method==='POST')return {data:this.inviteRoom(rid,user,body)};
      if(segments.length===3 && segments[2]==='participation' && method==='DELETE')return {data:this.withdrawRoom(rid,user,body)};
      if(segments.length===4 && segments[2]==='participants' && method==='DELETE')return {data:this.withdrawRoom(rid,user,body,text(segments[3],'参赛身份',80))};
      if(segments.length===3 && segments[2]==='mysubmit' && method==='GET') {
        const room=this.visibleRoom(rid,user),part=this.participant(rid,user.id);
        if(!part)return fail(403,'请先接受 VP 参赛邀请后查看我的提交');
        const row=this.store.one('SELECT * FROM histories WHERE participant_id=?',part.id);
        const history=row?this.historyView(row,true,user):null;
        return {data:{room:this.roomView(room,user),participation:this.participantView(part),history,submissions:history && 'submissions' in history?history.submissions:[],serverNow:now()}};
      }
      if(segments.length===3 && segments[2]==='leaderboard' && method==='GET') {
        const room=this.visibleRoom(rid,user), histories=this.store.all('SELECT h.*,p.id participant_key,p.label participant_name,p.type participant_type,p.members_json participant_members_json FROM participants p LEFT JOIN histories h ON h.participant_id=p.id WHERE p.room_id=? AND NOT EXISTS(SELECT 1 FROM participant_departures d WHERE d.participant_id=p.id) ORDER BY p.created_at,p.id',rid);
        const problems=[...new Set<string>(histories.flatMap(r=>r.problems_json?JSON.parse(r.problems_json):[]))];
        const timing=roomTiming(room),statistics=this.statisticsView(room,histories,problems);
        const rows=histories.map(r=>{
          const view=r.id?this.historyView(r,false,user,true):undefined;
          const identity=this.participantIdentity({label:r.participant_name,type:r.participant_type,members_json:r.participant_members_json});
          return {participantId:r.participant_key,label:r.participant_name,...identity,rank:view?.rank||'—',rankSource:view?.rankSource||'unavailable',prize:view?.prize??null,prizeSource:view?.prizeSource||'unavailable',solved:view?.solved??0,penalty:view?.penalty??0,points:view?.points??null,scoring:view?.scoring||(['OI','IOI'].includes(room.scoring_system)?room.scoring_system:'ICPC'),cells:view?.cells||{},historyId:r.id,lastSyncedAt:r.updated_at,status:r.status||'scheduled',frozen:view?.frozen||false,canViewHistory:Boolean(r.id && this.historyVisible(r,user,true))};
        }).sort((a,b)=>a.points!==null||b.points!==null?(b.points??0)-(a.points??0)||a.penalty-b.penalty:b.solved-a.solved||a.penalty-b.penalty);
        return {data:{room:this.roomView(room,user),...timing,rows,problems,...statistics,serverNow:now()}};
      }
      if(segments.length===3 && segments[2]==='prints' && method==='GET') {
        this.visibleRoom(rid,user);const part=this.participant(rid,user.id);return {data:part?this.store.all('SELECT j.*,p.label FROM prints j JOIN participants p ON p.id=j.participant_id WHERE j.participant_id=? ORDER BY j.created_at DESC LIMIT 1000',part.id).map(j=>this.printView(j)):[]};
      }
    }
    if(segments[0]==='prints' && segments.length===2 && method==='GET') {
      const job=this.store.one("SELECT j.*,p.label FROM prints j JOIN participants p ON p.id=j.participant_id WHERE j.id=? AND EXISTS(SELECT 1 FROM json_each(p.members_json) m WHERE json_extract(m.value,'$.id')=?)",segments[1],user.id);if(!job)return fail(404,'打印记录不存在或无权查看');return {data:this.printView(job,true)};
    }
    if(path==='history' && method==='GET') {
      const query=new URL(request.url).searchParams,search=(query.get('search')||'').slice(0,200).toLowerCase(),type=query.get('type');
      return {data:this.store.all("SELECT h.* FROM histories h WHERE h.owner_id=? OR EXISTS(SELECT 1 FROM participants p,json_each(p.members_json) m WHERE p.id=h.participant_id AND json_extract(m.value,'$.id')=?) ORDER BY h.created_at DESC,h.id DESC",user.id,user.id).filter(h=>!type||type===h.mode).map(h=>({nativeName:h.contest_name,view:this.historyView(h,false,user)})).filter(({nativeName,view})=>!search||view.contestName.toLowerCase().includes(search)||nativeName.toLowerCase().includes(search)).map(({view})=>view)};
    }
    if(segments[0]==='history' && segments.length===2 && method==='GET') {const h=this.store.one('SELECT * FROM histories WHERE id=?',segments[1]);if(!h||!this.historyVisible(h,user,true))return fail(404,'比赛记录不存在或无权查看');return {data:this.historyView(h,true,user)};}
    if(segments[0]==='client') {
      if(segments.length===4 && segments[1]==='rooms' && segments[3]==='authorize' && method==='POST') {const room=this.visibleRoom(segments[2],user),p=this.participant(room.id,user.id);if(!p)return fail(403,'请先接受 VP 参赛邀请');const history=this.store.one('SELECT * FROM histories WHERE participant_id=?',p.id);return {data:{room:{id:room.id,name:room.name,contestUrl:room.contest_url,startsAt:room.starts_at,freezeMinutes:room.freeze_minutes??0,scoringSystem:room.scoring_system||'ICPC',externalRankUrl:room.external_rank_url??null},participant:this.participantView(p),canSubmit:p.captain_id===user.id,canRestore:Boolean(history&&this.restoreEnded(history)),serverNow:now()}};}
      if(segments.length===4 && segments[1]==='rooms' && segments[3]==='restore' && method==='POST') {const room=this.visibleRoom(segments[2],user),p=this.participant(room.id,user.id);if(!p)return fail(403,'请先接受 VP 参赛邀请');const history=this.store.one('SELECT * FROM histories WHERE participant_id=?',p.id);if(!history)return fail(404,'此参赛记录尚未同步到云端，请让队长先同步');return {data:this.restoreView(history,user)};}
      if(path==='client/restore' && method==='POST') {const url=contestUrl(body.contestUrl),histories=this.store.all('SELECT h.* FROM histories h WHERE h.contest_url=? AND (h.owner_id=? OR EXISTS(SELECT 1 FROM participants p,json_each(p.members_json) m WHERE p.id=h.participant_id AND json_extract(m.value,\'$.id\')=?)) ORDER BY CASE WHEN h.mode=\'bt\' THEN 1 ELSE 0 END,h.created_at DESC,h.id DESC',url,user.id,user.id);const history=histories.find(h=>this.restoreEnded(h));return {data:{snapshot:history?this.restoreView(history,user):null}};}
      if(path==='client/sync' && method==='POST')return {data:this.sync(user,body)};
      if(path==='client/prints' && method==='POST') {
        const rid=text(body.roomId,'VP房间ID',80),p=this.participant(rid,user.id);if(!p)return fail(403,'你未报名此 VP，不能打印');
        this.checkParticipantContext(body,p,this.room(rid),user.id);
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
    if(error instanceof ApiError) {
      if(error.status===429)headers.set('Retry-After','60');
      return new Response(JSON.stringify({error:error.message}),{status:error.status,headers});
    }
    if(error instanceof Error && /UNIQUE constraint/.test(error.message))return new Response(JSON.stringify({error:'数据已存在或请求发生冲突，请刷新后重试'}),{status:409,headers});
    console.error('XCPC API internal failure:',error instanceof Error?error.name:'unknown');
    return new Response(JSON.stringify({error:'服务器暂时无法处理请求，请稍后重试'}),{status:500,headers});
  }
}
