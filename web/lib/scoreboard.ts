import type { Row } from './db';

const DEFAULT_DURATION = 5 * 60 * 60;
const accepted = new Set(['CORRECT','AC','ACCEPTED','OK','PRETESTS-PASSED']);
const incorrect = new Set(['WRONG-ANSWER','WA','RUN-ERROR','RUNTIME-ERROR','RE','MEMORY-LIMIT','MEMORY-LIMIT-EXCEEDED','MLE','TIMELIMIT','TIME-LIMIT-EXCEEDED','TLE','OUTPUT-LIMIT','OUTPUT-LIMIT-EXCEEDED','OLE','NO-OUTPUT','IDLENESS-LIMIT','IDLENESS-LIMIT-EXCEEDED','ILE','FORMAT-ERROR','PRESENTATION-ERROR','PE','SECURITY-VIOLATED','CHALLENGED','PARTIAL']);
const ignored = new Set(['TOO-LATE','IGNORED','HACK-SUCCESSFUL','HACK-UNSUCCESSFUL']);
const compileErrors = new Set(['COMPILER-ERROR','COMPILATION-ERROR','CE']);
const normalize = (value: string) => value.trim().toUpperCase().replace(/[ _]+/g,'-');
export const pointScoring = (rules:Row) => ['CF','OI','IOI','WEEKLY'].includes(rules.scoring);

/** Room scheduling controls the freeze, not a client's completion flag or clock. */
export function roomTiming(room: Row, at = Date.now()) {
  const durationSeconds = room.duration_seconds || DEFAULT_DURATION;
  const freezeMinutes = Number.isFinite(room.freeze_minutes) && room.freeze_minutes > 0 ? room.freeze_minutes : 0;
  const start = Date.parse(room.starts_at), end = start + durationSeconds * 1000;
  const freeze = freezeMinutes ? Math.max(start, end - freezeMinutes * 60000) : null;
  return {durationSeconds,durationSource:room.duration_seconds?'platform':'default',endsAt:new Date(end).toISOString(),freezeMinutes,freezeAt:freeze===null?null:new Date(freeze).toISOString(),freezeDurationSeconds:freezeMinutes*60,frozen:freeze!==null && at>=freeze && at<end};
}

/** Rebuild public totals from timestamped native results; never estimate a point formula. */
export function publicScore(history: Row, room: Row, at: number, freeze: boolean, hidePractice = false) {
  const timing=roomTiming(room,at), cutoff=timing.freezeAt?Date.parse(timing.freezeAt):Infinity, end=Date.parse(timing.endsAt);
  const start=Date.parse(history.started_at || room.starts_at), rules=JSON.parse(history.rules_json || '{}');
  const graded=pointScoring(rules), latest=rules.score_policy==='latest', subtasks=rules.score_policy==='subtasks', practiceOnly=history.mode==='bt';
  const problems:string[]=JSON.parse(history.problems_json), cells:Row=Object.create(null), practiceCells:Row=Object.create(null);
  for(const problem of problems) {
    cells[problem]={tries:0,pending:0,solved:false,practiceSolved:false};
    if(graded && !practiceOnly) {if(!subtasks)cells[problem].points=0;if(rules.problem_max_scores?.[problem]!==undefined)cells[problem].maxPoints=rules.problem_max_scores[problem];}
    practiceCells[problem]={tries:0,pending:0,solved:false};
  }
  const submissions:Row[]=JSON.parse(history.submissions_json), visible:Row[]=[];
  for(const submission of [...submissions].sort((a,b)=>a.submittedAt.localeCompare(b.submittedAt)||a.id.localeCompare(b.id,undefined,{numeric:true}))) {
    const time=Date.parse(submission.submittedAt);
    if(time>at)continue;
    const practice=practiceOnly || submission.practice || time>=end;
    if((freeze || hidePractice) && practice)continue;
    const hidden=freeze && time>=cutoff;
    visible.push(hidden?{id:submission.id,problem:submission.problem,submittedAt:submission.submittedAt,verdict:'FROZEN',practice:false}:{...submission,practice});
    if(!practiceOnly && time<start)continue;
    const cell=(practice?practiceCells:cells)[submission.problem];
    if(!cell || (!graded && !practice && cell.solved))continue;
    if(hidden) {cell.pending++;continue;}
    const verdict=normalize(submission.verdict);
    if(ignored.has(verdict) || (!graded && !practice && compileErrors.has(verdict) && !rules.compile_penalty))continue;
    if(!accepted.has(verdict) && !incorrect.has(verdict) && !compileErrors.has(verdict)) {cell.pending++;continue;}
    cell.tries++;
    if(practice) {if(accepted.has(verdict))cell.solved=true;continue;}
    if(graded) {
      // OI keeps the latest judged score; IOI/CF retain the best native score.
      // A missing score must not be guessed from an AC or a problem's maximum.
      if(!subtasks && typeof submission.points==='number' && Number.isFinite(submission.points)) {
        if(latest || submission.points > (cell.points??0) || (submission.points === (cell.points??0) && accepted.has(verdict))) {
          cell.points=submission.points;
          cell.solved=accepted.has(verdict);
          if(cell.solved)cell.solveTime=Math.max(0,Math.floor((time-start)/60000));else delete cell.solveTime;
        }
        if(latest || cell.maxPoints!==undefined && cell.points>=cell.maxPoints)delete cell.unknownPoints;
      } else if(accepted.has(verdict)) {
        cell.solved=true;cell.solveTime=Math.max(0,Math.floor((time-start)/60000));
      }
      if(!subtasks && submission.points===undefined && (latest || cell.maxPoints===undefined || (cell.points??0)<cell.maxPoints)) {
        cell.unknownPoints=true;
        if(latest)delete cell.points;
      }
    } else if(accepted.has(verdict)) {cell.solved=true;cell.solveTime=Math.max(0,Math.floor((time-start)/60000));}
  }
  const penalty=graded || practiceOnly?0:Object.values(cells).reduce((total,c)=>total+(c.solved?c.solveTime+Math.max(0,c.tries-1)*(rules.penalty_minutes??20):0),0);
  const unknownPoints=Object.values(cells).some(c=>c.unknownPoints);
  let points=graded && !practiceOnly && !subtasks && !unknownPoints?Object.values(cells).reduce((total,c)=>total+(c.points??0),0):undefined;
  if(subtasks && freeze && history.freeze_cells_json && history.freeze_points!==null && history.freeze_points!==undefined) {
    const nativeCells=JSON.parse(history.freeze_cells_json);
    for(const problem of problems)if(nativeCells[problem])for(const key of ['points','maxPoints','solved','solveTime'])if(nativeCells[problem][key]!==undefined)cells[problem][key]=nativeCells[problem][key];
    points=history.freeze_points;
  }
  const solved=Object.values(cells).filter(c=>c.solved).length;
  for(const cell of Object.values(cells))if(cell.unknownPoints) {delete cell.points;delete cell.unknownPoints;}
  for(const problem of problems) {cells[problem].practiceSolved=practiceCells[problem].solved;cells[problem].tries+=practiceCells[problem].tries;cells[problem].pending+=practiceCells[problem].pending;}
  return {cells,solved,penalty,points,submissions:visible};
}

export function frozenRank(history:Row,room:Row) {
  const at=history.freeze_rank_at?Date.parse(history.freeze_rank_at):NaN,freezeAt=roomTiming(room).freezeAt;
  return freezeAt && Number.isFinite(at) && at<Date.parse(freezeAt) ? history.freeze_rank || '?' : '?';
}
