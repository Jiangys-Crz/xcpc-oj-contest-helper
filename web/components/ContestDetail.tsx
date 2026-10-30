'use client';
import {useMemo,useState} from 'react';
import {ArrowUpRight,ChevronDown,Filter,LockKeyhole} from 'lucide-react';
import {Empty,ModeBadge,ParticipationBadge,ProblemCell,dateTime} from './shared';
import {ResultColumns} from './ResultColumns';
import type {History,Submission} from './types';
import {judgingState} from './judging';

const problemColors=['#7668c6','#ca5a3e','#9b751d','#328198','#a05c8c','#547f67','#527caa','#b66b31'];

export function ContestTiming({history}:{history:History}){
  return <div className="judge-contest-times">
    {history.mode!=='bt'&&<><span>Start: <strong>{dateTime(history.startedAt)}</strong></span><span>End: <strong>{history.endedAt?dateTime(history.endedAt):history.status==='failed'?'Not started':history.status==='cancelled'?'Cancelled':history.endsAt?dateTime(history.endsAt):'In progress'}</strong></span></>}
    <span className="judge-updated">Last synced: {dateTime(history.updatedAt)}</span>
    {history.frozen&&<span className="judge-freeze"><LockKeyhole size={13}/>Scoreboard frozen</span>}
  </div>;
}

export function ContestScoreboard({history,summary=true}:{history:History;summary?:boolean}){
  const submissions=history.submissions||[],practice=history.mode==='bt',graded=!!history.scoring&&history.scoring!=='ICPC';
  const totals=history.problems.map(problem=>{
    const records=submissions.filter(record=>record.problem===problem);
    return {submitted:records.length,attempted:records.some(record=>!['ignored'].includes(judgingState(record.verdict)))?1:0,accepted:records.filter(record=>judgingState(record.verdict)==='accepted').length};
  });
  const publicColumns=history.publicProblemStatistics;
  const leadingColumns=practice?1:4;
  return <section className="judge-score-section" aria-label="Contest scoreboard">
    <div className="table-scroll judge-score-scroll"><table className="data-table scoreboard judge-scoreboard fit-table" data-dense={history.problems.length>16||undefined}><ResultColumns problems={history.problems} practice={practice}/>
      <caption className="sr-only">Contest results for {history.participantLabel}</caption>
      <thead><tr>{!practice&&<th className="judge-rank">Rank</th>}<th className="judge-team">Team</th>{!practice&&<><th className="judge-solved">Solved</th><th className="judge-penalty">{graded?'Score':'Penalty'}</th></>}{history.problems.map((problem,i)=><th className="judge-problem-heading" key={problem}><span style={{background:problemColors[i%problemColors.length]}}>{problem}</span></th>)}</tr></thead>
      <tbody><tr>{!practice&&<td className="rank-value">{history.rank||'—'}</td>}<td className="judge-team-name"><strong>{history.participantLabel}</strong><small>{history.participantType==='team'?'Team participation':'Individual participation'}</small></td>{!practice&&<><td className="metric-value">{history.solved}</td><td>{graded?history.points??'—':history.penalty}</td></>}{history.problems.map(problem=><ProblemCell key={problem} label={problem} cell={history.cells[problem]}/>)}</tr></tbody>
      {summary&&!practice&&<tfoot><tr className="judge-summary"><th colSpan={leadingColumns}>public</th>{history.problems.map((problem,i)=><td key={problem}>{publicColumns?publicColumns[i]?.label||'?':problem}</td>)}</tr>{(['submitted','attempted','accepted'] as const).map(kind=><tr className="judge-summary" key={kind}><th colSpan={leadingColumns}>{kind}</th>{history.problems.map((problem,i)=><td key={problem}>{publicColumns?publicColumns[i]?.[kind]??'—':totals[i][kind]}</td>)}</tr>)}</tfoot>}
    </table></div>
  </section>;
}

export function ContestMetadata({history}:{history:History}){return <div className="judge-metadata"><div className="judge-identities"><ModeBadge mode={history.mode} english/><ParticipationBadge type={history.participantType} english/></div><ContestTiming history={history}/><ContestLegend/></div>}

export function ContestLegend(){return <aside className="judge-legend" aria-label="Cell colours"><h3>Cell colours</h3><div className="judge-legend-accepted">Solved</div><div className="judge-legend-rejected">Tried, incorrect</div><div className="judge-legend-pending">Tried, pending</div><div className="judge-legend-upsolved">Upsolved</div><div className="judge-legend-untried">Untried</div></aside>}

export function SubmissionsTable({submissions,title='Submissions',participantLabel,historyHref}:{submissions:Submission[];title?:string;participantLabel?:string;historyHref?:string}){
  const [filters,setFilters]=useState(false),[problem,setProblem]=useState(''),[state,setState]=useState(''),[stage,setStage]=useState('');
  const problems=useMemo(()=>Array.from(new Set(submissions.map(record=>record.problem))).sort((a,b)=>a.localeCompare(b,'en',{numeric:true})),[submissions]);
  const records=useMemo(()=>[...submissions].filter(record=>(!problem||record.problem===problem)&&(!state||judgingState(record.verdict)===state)&&(!stage||record.practice===(stage==='practice'))).sort((a,b)=>new Date(b.submittedAt).getTime()-new Date(a.submittedAt).getTime()),[submissions,problem,state,stage]);
  const showScore=submissions.some(record=>!record.practice&&record.points!==undefined);
  return <section className="judge-submissions">
    <div className="judge-section-heading"><div><h2>{title}<span className="count-label">{submissions.length}</span></h2>{participantLabel&&<p>{participantLabel}</p>}</div><div className="judge-table-actions">{historyHref&&<a href={historyHref} className="text-button">History details<ArrowUpRight size={13}/></a>}<button className={'button button-secondary button-small '+(filters?'is-active':'')} onClick={()=>setFilters(!filters)} aria-expanded={filters}><Filter size={14}/>Filter<ChevronDown size={12}/></button></div></div>
    {filters&&<div className="judge-filters"><label>Problem<select value={problem} onChange={event=>setProblem(event.target.value)}><option value="">All problems</option>{problems.map(value=><option key={value}>{value}</option>)}</select></label><label>Result<select value={state} onChange={event=>setState(event.target.value)}><option value="">All results</option><option value="accepted">Accepted</option><option value="rejected">Incorrect</option><option value="pending">Pending / unconfirmed</option><option value="ignored">Not counted</option></select></label><label>Stage<select value={stage} onChange={event=>setStage(event.target.value)}><option value="">All stages</option><option value="contest">Contest</option><option value="practice">Practice</option></select></label><button className="text-button" onClick={()=>{setProblem('');setState('');setStage('');}}>Reset</button></div>}
    {!records.length?<Empty title={submissions.length?'No matching submissions':'No submissions yet'}/>:<div className="table-scroll"><table className="data-table judge-submissions-table fit-table"><colgroup>{(showScore?[17,24,9,29,14,7]:[18,25,9,33,15]).map((width,index)=><col key={index} style={{width:width+'%'}}/>)}</colgroup><thead><tr><th>Submission</th><th>Time</th><th>Problem</th><th className="text-left">Result</th><th>Stage</th>{showScore&&<th>Score</th>}</tr></thead><tbody>{records.map((submission,i)=><tr key={submission.id+'-'+i}><td>{submission.url?<a href={submission.url} target="_blank" rel="noreferrer" className="submission-link">#{submission.id}<ArrowUpRight size={12}/></a>:'#'+submission.id}</td><td className="time-value">{dateTime(submission.submittedAt)}</td><td className="problem-letter">{submission.problem}</td><td className="text-left"><span className={'judge-verdict judge-verdict-'+judgingState(submission.verdict)}>{submission.verdict==='FROZEN'?'FROZEN':submission.verdict}</span></td><td><span className={'stage-label '+(submission.practice?'stage-practice':'')}>{submission.practice?'Practice':'Contest'}</span></td>{showScore&&<td>{submission.practice?'—':submission.points??'—'}</td>}</tr>)}</tbody></table></div>}
    <div className="judge-table-footer"><span>{records.length} submissions</span><span>All times in Asia/Shanghai</span></div>
  </section>;
}
