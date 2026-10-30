import type { Row } from './db';

const DEFAULT_DURATION = 5 * 60 * 60;
export const FREEZE_DURATION = 60 * 60;
const accepted = new Set(['CORRECT','AC','ACCEPTED','OK','PRETESTS-PASSED']);
const incorrect = new Set(['WRONG-ANSWER','WA','RUN-ERROR','RUNTIME-ERROR','RE','MEMORY-LIMIT','MEMORY-LIMIT-EXCEEDED','MLE','TIMELIMIT','TIME-LIMIT-EXCEEDED','TLE','OUTPUT-LIMIT','OUTPUT-LIMIT-EXCEEDED','OLE','NO-OUTPUT','IDLENESS-LIMIT','IDLENESS-LIMIT-EXCEEDED','ILE','FORMAT-ERROR','PRESENTATION-ERROR','PE','SECURITY-VIOLATED','CHALLENGED','PARTIAL']);
const ignored = new Set(['TOO-LATE','IGNORED','HACK-SUCCESSFUL','HACK-UNSUCCESSFUL']);
const compileErrors = new Set(['COMPILER-ERROR','COMPILATION-ERROR','CE']);
const normalize = (value: string) => value.trim().toUpperCase().replace(/[ _]+/g,'-');

/** Room scheduling controls the freeze, not a client's completion flag or clock. */
export function roomTiming(room: Row, at = Date.now()) {
  const durationSeconds = room.duration_seconds || DEFAULT_DURATION;
  const start = Date.parse(room.starts_at), end = start + durationSeconds * 1000;
  const freeze = Math.max(start, end - FREEZE_DURATION * 1000);
  return {durationSeconds,durationSource:room.duration_seconds?'platform':'default',endsAt:new Date(end).toISOString(),freezeAt:new Date(freeze).toISOString(),freezeDurationSeconds:FREEZE_DURATION,frozen:at>=freeze && at<end};
}

/** Rebuild public totals from timestamps; aggregate native cells may contain hidden ACs. */
export function publicScore(history: Row, room: Row, at: number, freeze: boolean, hidePractice = false) {
  const timing=roomTiming(room,at), cutoff=Date.parse(timing.freezeAt), end=Date.parse(timing.endsAt);
  const start=Date.parse(history.started_at || room.starts_at), rules=JSON.parse(history.rules_json || '{}');
  const problems:string[]=JSON.parse(history.problems_json), cells:Row=Object.create(null), practiceCells:Row=Object.create(null);
  for(const problem of problems) {cells[problem]={tries:0,pending:0,solved:false,practiceSolved:false};practiceCells[problem]={tries:0,pending:0,solved:false};}
  const submissions:Row[]=JSON.parse(history.submissions_json);
  const visible:Row[]=[];
  for(const submission of [...submissions].sort((a,b)=>a.submittedAt.localeCompare(b.submittedAt)||a.id.localeCompare(b.id,undefined,{numeric:true}))) {
    const time=Date.parse(submission.submittedAt);
    if(time>at)continue;
    const practice=submission.practice || time>=end;
    if((freeze || hidePractice) && practice)continue;
    const hidden=freeze && time>=cutoff;
    visible.push(hidden?{id:submission.id,problem:submission.problem,submittedAt:submission.submittedAt,verdict:'FROZEN',practice:false}:{...submission,practice});
    if(time<start)continue;
    const cell=(practice?practiceCells:cells)[submission.problem];
    if(!cell || cell.solved)continue;
    if(hidden) {cell.pending++;continue;}
    const verdict=normalize(submission.verdict);
    if(ignored.has(verdict) || (compileErrors.has(verdict) && !rules.compile_penalty))continue;
    if(!accepted.has(verdict) && !incorrect.has(verdict) && !compileErrors.has(verdict)) {cell.pending++;continue;}
    cell.tries++;
    if(accepted.has(verdict)) {cell.solved=true;cell.solveTime=Math.max(0,Math.floor((time-start)/60000));}
  }
  const solved=Object.values(cells).filter(c=>c.solved).length;
  const penalty=Object.values(cells).reduce((total,c)=>total+(c.solved?c.solveTime+Math.max(0,c.tries-1)*(rules.penalty_minutes??20):0),0);
  for(const problem of problems) {cells[problem].practiceSolved=practiceCells[problem].solved;cells[problem].tries+=practiceCells[problem].tries;cells[problem].pending+=practiceCells[problem].pending;}
  return {cells,solved,penalty,submissions:visible};
}

export function frozenRank(history:Row,room:Row) {
  const at=history.freeze_rank_at?Date.parse(history.freeze_rank_at):NaN;
  return Number.isFinite(at) && at<Date.parse(roomTiming(room).freezeAt) ? history.freeze_rank || '?' : '?';
}
