import {execFile} from 'node:child_process';
import {existsSync,readdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {promisify} from 'node:util';
import type {Row} from './db';
import {publicScore,roomTiming} from './scoreboard';

type BoardCell={solved:boolean;tries:number;pending:number;solveTime?:number;points?:number};
type ProblemSlot={label:string|null;cell?:BoardCell;first?:boolean};
export type VirtualRow={
  id:string;name:string;organization?:string;members?:string[];kind:'official'|'starred'|'vp';female?:boolean;
  solved:number;penalty:number;points?:number|null;cells:Record<string,BoardCell>;
  rank?:number|null;medal?:'gold'|'silver'|'bronze';first?:string[];waiting?:boolean;problemSlots?:ProblemSlot[];
};
export type VirtualBoard={
  rows:VirtualRow[];mine:VirtualRow|null;problems:{label:string|null;submitted:number|null;attempted:number|null;accepted:number|null;first:number|null;last:number|null}[];
  scoringSystem:string;sourceUrl:string|null;originalAvailable:boolean;frozen:boolean;rowProblemOrder:boolean;
  medalState?:'provisional'|'final';
};
type Snapshot={elapsed:number;released:boolean;revealed:string[];rows:VirtualRow[]};
type Team={id:string;name:string;official:boolean;organization?:string;team_name?:string;members?:string[];female?:boolean};
type Archive={
  metadata:{duration:number;problems:string[];penalty_minutes:number;penalty_rounding:string;medals?:Record<'gold'|'silver'|'bronze',number>;teams:Team[]};
  events:{team:string;id:string;problem:string;time:number;verdict:string}[];
};

export function roomBoardSource(room:Row):string|null {
  if (!['ICPC','CCPC'].includes(room.scoring_system||'ICPC')) return null;
  if (room.external_rank_url) return room.external_rank_url;
  if (room.contest_url==='https://codeforces.com/gym/104787') return 'https://board.xcpcio.com/ccpc/9th/qinhuangdao';
  return null;
}

const archiveCache=new Map<string,{value:Promise<Archive|null>;expires:number;last?:Archive}>();
let pendingArchives=0;
export function loadRoomArchive(url:string):Promise<Archive|null> {
  const now=Date.now(),existing=archiveCache.get(url);
  if (existing && existing.expires>now) return existing.last?Promise.resolve(existing.last):existing.value;
  if (pendingArchives>=2) return Promise.resolve(existing?.last??null);
  if (!existing && archiveCache.size>=24) archiveCache.delete(archiveCache.keys().next().value!);
  const packaged=resolve('board-runtime');
  const cwd=process.cwd();
  const root=existsSync(resolve(cwd,'wheels'))?cwd:resolve(/*turbopackIgnore: true*/ cwd,'..');
  const wheels=resolve(root,'wheels');
  const packagedExists=existsSync(packaged);
  if (!packagedExists && !existsSync(wheels)) return Promise.resolve(existing?.last??null);
  const pythonPath=packagedExists?packaged:[root,...readdirSync(wheels)
    .filter(file=>/^(beautifulsoup4|certifi|charset_normalizer|idna|requests|soupsieve|urllib3|typing_extensions)-.*-none-any\.whl$/.test(file))
    .sort().reverse().map(file=>resolve(wheels,file))].join(':');
  pendingArchives++;
  const value:Promise<Archive|null>=promisify(execFile)('python3',['-m','vp_helper.board_export',url],{
    cwd:packagedExists?packaged:root,env:{...process.env,PYTHONPATH:pythonPath,PYTHONDONTWRITEBYTECODE:'1'},
    timeout:25000,maxBuffer:12*1024*1024
  }).then(({stdout})=>{
    const archive=JSON.parse(stdout) as Archive;
    entry.last=archive;
    return archive;
  }).catch(()=>{
    entry.expires=Date.now()+30000;
    return entry.last??null;
  }).finally(()=>{pendingArchives--;});
  const entry={value,expires:now+30*60000,last:existing?.last};
  archiveCache.set(url,entry);
  return entry.last?Promise.resolve(entry.last):value;
}

const finite=(value:unknown):value is number=>typeof value==='number' && Number.isFinite(value) && value>=0;
const count=(value:unknown):value is number=>finite(value) && Number.isInteger(value) && value<=20000;
export function normalizeRoomSnapshot(value:unknown,problems:string[],room:Row,at:number):Snapshot|null {
  const data=value as Partial<Snapshot>|null;
  const timing=roomTiming(room,at);
  const elapsed=Math.min(timing.durationSeconds,Math.floor((at-Date.parse(room.starts_at))/1000));
  if (!data || !Number.isInteger(data.elapsed) || data.elapsed!<0 || data.elapsed!>elapsed
      || typeof data.released!=='boolean' || (timing.frozen && data.released)
      || !Array.isArray(data.revealed) || !data.revealed.every(label=>problems.includes(label))
      || !Array.isArray(data.rows) || !data.rows.length || data.rows.length>2000) return null;
  const rows:VirtualRow[]=[],ids=new Set<string>();
  const revealed=problems.filter(label=>data.revealed!.includes(label));
  for (const row of data.rows) {
    if (!row || typeof row.id!=='string' || !row.id || row.id==='me' || row.id.length>200 || ids.has(row.id)
        || typeof row.name!=='string' || !row.name.trim() || row.name.length>1500
        || !['official','starred'].includes(row.kind) || !count(row.solved) || row.solved>problems.length
        || !finite(row.penalty) || row.points!=null && !finite(row.points) || !row.cells || typeof row.cells!=='object') return null;
    ids.add(row.id);
    const cells:VirtualRow['cells']=Object.create(null);
    for (const label of revealed) {
      const cell=row.cells[label];
      if (!cell) continue;
      if (typeof cell.solved!=='boolean' || !count(cell.tries) || !count(cell.pending)
          || cell.solveTime!=null && (!count(cell.solveTime) || cell.solveTime>Math.floor(data.elapsed!/60))
          || cell.points!=null && !finite(cell.points)) return null;
      if (timing.frozen && cell.solved && (cell.solveTime==null || cell.solveTime>=Math.floor((Date.parse(timing.freezeAt!)-Date.parse(room.starts_at))/60000))) return null;
      if (cell.solved || cell.tries || cell.pending || cell.points) cells[label]={solved:cell.solved,tries:cell.tries,pending:cell.pending,
        ...(cell.solveTime==null?{}:{solveTime:cell.solveTime}),...(cell.points==null?{}:{points:cell.points})};
    }
    rows.push({id:row.id,name:row.name,kind:row.kind,solved:row.solved,penalty:row.penalty,points:row.points??null,cells});
  }
  if (!rows.some(row=>row.kind==='official')) return null;
  return {elapsed:data.elapsed!,released:data.released,revealed,rows};
}

export function buildVirtualBoard(room:Row,histories:Row[],myId:string|undefined,archive:Archive|null,snapshot:Snapshot|null,at=Date.now()):VirtualBoard {
  const initialTiming=roomTiming(room,at);
  const nativeProblems:string[]=JSON.parse(histories.find(row=>row.problems_json)?.problems_json||'[]');
  const nativeRules:Row=JSON.parse(histories.find(row=>row.rules_json)?.rules_json||'{}');
  const sourceUrl=roomBoardSource(room);
  if (archive && (room.duration_seconds && archive.metadata.duration!==initialTiming.durationSeconds
      || nativeProblems.length && JSON.stringify(nativeProblems)!==JSON.stringify(archive.metadata.problems)
      || nativeRules.penalty_minutes!==undefined && nativeRules.penalty_minutes!==archive.metadata.penalty_minutes
      || nativeRules.compile_penalty)) archive=null;
  if (sourceUrl) snapshot=null;
  const contest=archive?{...room,duration_seconds:archive.metadata.duration}:room;
  const timing=roomTiming(contest,at),started=at>=Date.parse(room.starts_at);
  const problems=archive?.metadata.problems??nativeProblems;
  const system=room.scoring_system||'ICPC', graded=['OI','IOI'].includes(system);
  const rowProblemOrder=system==='CCPC' && !timing.frozen && at<Date.parse(timing.endsAt);
  const solveTimes=new WeakMap<VirtualRow,Record<string,number>>();
  const penaltyRules=archive?{...nativeRules,penalty_minutes:archive.metadata.penalty_minutes,penalty_rounding:archive.metadata.penalty_rounding}:null;
  const official=snapshot?.rows.map(row=>({...row,id:'original:'+row.id}))??[];
  const eventsByTeam=new Map<string,Row[]>();
  const counts=problems.map(()=>({submitted:0,attempted:new Set<string>()}));
  const problemIndex=new Map(problems.map((label,index)=>[label,index]));
  const start=Date.parse(room.starts_at);
  const eventLimit=system==='CCPC' && timing.frozen?Date.parse(timing.freezeAt!)-1:at;
  const participantLimit=snapshot?Math.min(eventLimit,start+snapshot.elapsed*1000):eventLimit;
  if (archive) {
    for (const event of archive.events) {
      const when=start+event.time*1000;
      if (when>eventLimit) continue;
      const index=problemIndex.get(event.problem)!;
      counts[index].submitted++;
      counts[index].attempted.add(event.team);
      const submission={id:event.id,problem:event.problem,submittedAt:new Date(when).toISOString(),verdict:event.verdict};
      const entries=eventsByTeam.get(event.team);
      if (entries) entries.push(submission); else eventsByTeam.set(event.team,[submission]);
    }
    for (const team of archive.metadata.teams) {
      const score=publicScore({started_at:room.starts_at,problems_json:JSON.stringify(problems),rules_json:JSON.stringify(penaltyRules),
        submissions_json:JSON.stringify(eventsByTeam.get(team.id)??[])},contest,at,timing.frozen,true);
      const row:VirtualRow={id:'original:'+team.id,name:team.team_name||team.name,organization:team.organization,members:team.members,female:team.female,
        kind:team.official?'official':'starred',solved:score.solved,penalty:score.penalty,cells:score.cells};
      official.push(row);
      solveTimes.set(row,score.acceptedSeconds);
    }
  }
  const visible=new Set(problems.filter(label=>!rowProblemOrder ||
    (archive?official.filter(row=>row.cells[label]?.solved).length>=50:snapshot?.revealed.includes(label))));
  const hidden=problems.filter(label=>!visible.has(label));
  const solveAt=(row:VirtualRow,label:string)=>solveTimes.get(row)?.[label]??(row.cells[label]?.solveTime??Infinity)*60;
  const firstRows=new Map<string,string>();
  const stats=problems.map((label,index)=>{
    const solvedRows=official.filter(row=>row.cells[label]?.solved);
    const earliestTime=Math.min(...solvedRows.map(row=>solveAt(row,label)));
    const earliest=Number.isFinite(earliestTime)?solvedRows.filter(row=>solveAt(row,label)===earliestTime):[];
    if (earliest.length===1) firstRows.set(label,earliest[0].id);
    if (!visible.has(label)) return {label:null,submitted:null,attempted:null,accepted:null,first:null,last:null};
    const solved=solvedRows.map(row=>row.cells[label].solveTime).filter((t):t is number=>t!=null);
    const first=solved.length?Math.min(...solved):null;
    const attempts=official.map(row=>(row.cells[label]?.tries??0)+(row.cells[label]?.pending??0));
    return {label,submitted:archive?counts[index].submitted:snapshot?attempts.reduce((sum,count)=>sum+count,0):null,
      attempted:archive?counts[index].attempted.size:snapshot?attempts.filter(Boolean).length:null,
      accepted:archive||snapshot?solvedRows.length:null,first,last:solved.length?Math.max(...solved):null};
  });
  const roomRows=histories.map(row=>{
    const waiting=!row.id || ['scheduled','cancelled','failed'].includes(row.status);
    const result=waiting?null:publicScore({...row,...(penaltyRules?{rules_json:JSON.stringify({...JSON.parse(row.rules_json||'{}'),...penaltyRules})}:{})},
      contest,participantLimit,timing.frozen,true);
    const participant:VirtualRow={id:row.participant_key,name:row.participant_name,organization:'Virtual Programming',
      members:JSON.parse(row.participant_members_json).map((member:Row)=>member.realname||member.username||member.useraccount).filter(Boolean),
      kind:'vp',solved:result?.solved??0,penalty:result?.penalty??0,points:result?.points??null,cells:result?.cells??{},waiting};
    if (rowProblemOrder && result) solveTimes.set(participant,result.acceptedSeconds);
    return participant;
  });
  const compare=(a:VirtualRow,b:VirtualRow)=>graded?(b.points??-1)-(a.points??-1):b.solved-a.solved||a.penalty-b.penalty;
  const originalAvailable=Boolean(archive||snapshot);
  const rows=[...official,...roomRows].sort((a,b)=>Number(Boolean(a.waiting))-Number(Boolean(b.waiting))||compare(a,b));
  let place=0,rankedSeen=0,previous:VirtualRow|undefined;
  let officialPlace=0,previousOfficial:VirtualRow|undefined,officialSeen=0;
  const medals=archive?.metadata.medals;
  const medalState=medals&&Object.values(medals).some(Boolean)
    ? at>=Date.parse(timing.endsAt)?'final':'provisional'
    : undefined;
  for (const row of rows) {
    const referencePlace=previousOfficial && compare(row,previousOfficial)===0?officialPlace:officialSeen+1;
    if (row.kind==='official') {
      officialSeen++;
      officialPlace=referencePlace;
      previousOfficial=row;
    }
    if (medals && medalState && row.kind!=='starred' && !row.waiting && row.solved>0) {
      if (referencePlace<=medals.gold) row.medal='gold';
      else if (referencePlace<=medals.gold+medals.silver) row.medal='silver';
      else if (referencePlace<=medals.gold+medals.silver+medals.bronze) row.medal='bronze';
    }
    if (!started || !originalAvailable || row.kind==='starred' || row.waiting || (graded && row.points==null)) row.rank=null;
    else {
      rankedSeen++;
      if (!previous || compare(row,previous)!==0) place=rankedSeen;
      row.rank=place;
      previous=row;
    }
    if (rowProblemOrder) {
      const solved=hidden.filter(label=>row.cells[label]?.solved).sort((a,b)=>solveAt(row,a)-solveAt(row,b));
      const slots:ProblemSlot[]=solved.map(label=>({label:null,cell:row.cells[label],first:firstRows.get(label)===row.id}));
      const attempted=hidden.map(label=>row.cells[label])
        .filter(cell=>cell && !cell.solved && (cell.tries>0 || cell.pending>0))
        .sort((a,b)=>b.tries+b.pending-a.tries-a.pending||b.pending-a.pending);
      // Older terminal snapshots omit the hidden cells but retain the total.
      const knownSolved=[...visible].filter(label=>row.cells[label]?.solved).length;
      const anonymous=Math.min(row.solved-knownSolved-solved.length,hidden.length-solved.length-attempted.length);
      for (let i=0;i<anonymous;i++) slots.push({label:null,cell:{solved:true,tries:0,pending:0}});
      for (const cell of attempted) slots.push({label:null,cell});
      while (slots.length<hidden.length) slots.push({label:null});
      row.problemSlots=[...Array.from(visible,label=>({label,cell:row.cells[label]})),...slots];
    }
    row.cells=Object.fromEntries(Object.entries(row.cells).filter(([label])=>visible.has(label)));
    row.first=[...visible].filter(label=>firstRows.get(label)===row.id);
  }
  return {rows,mine:rows.find(row=>row.kind==='vp' && row.id===myId)??null,
    problems:rowProblemOrder?[...stats.filter(problem=>problem.label!==null),...stats.filter(problem=>problem.label===null)]:stats,
    scoringSystem:system,sourceUrl,originalAvailable,frozen:timing.frozen,rowProblemOrder,medalState};
}
