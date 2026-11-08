'use client';
import {cloneElement,useId,useMemo,useState} from 'react';
import {ArrowUpRight,ChevronDown,ChevronLeft,ChevronRight,ExternalLink,FileText,Filter,LockKeyhole} from 'lucide-react';
import {Empty,ErrorBox,ModeBadge,PageHeader,ParticipationBadge,ProblemCell,dateTime} from './shared';
import {ResultColumns} from './ResultColumns';
import {TableFit} from './TableFit';
import type {Cell,History,Submission} from './types';
import {judgingState} from './judging';
import {submissionLanguage} from '../lib/submission-language';

const problemColors=[
  ['#a4b68b','#111827'],['#99c8e6','#111827'],['#f6e33e','#111827'],['#e9b8cd','#111827'],
  ['#80b957','#111827'],['#4f595e','#ffffff'],['#eea12b','#111827'],['#008c4a','#ffffff'],
  ['#5d2c7e','#ffffff'],['#00acd8','#111827'],['#cc2935','#ffffff'],['#aa89bd','#ffffff'],
  ['#ddbd91','#111827'],['#99c9bd','#111827'],['#a9b6df','#111827'],['#e5bd98','#111827'],
];

export function ProblemBadge({problem,order}:{problem:string;order:string[]}){
  const index=Math.max(0,order.indexOf(problem)),[backgroundColor,color]=problemColors[index%problemColors.length];
  return <span className="judge-problem-badge" style={{backgroundColor,color}}>{problem}</span>;
}

export function DetailProblemCell({cell,label}:{cell?:Cell;label:string}){
  const original=ProblemCell({cell,label});
  return cloneElement(original,{children:<div className="judge-detail-cell-content">{original.props.children}</div>});
}

export function ParticipantIdentity({title,subtitle,label}:{title?:string;subtitle?:string;label?:string}){
  return <div className="participant-identity"><strong>{title||label||'—'}</strong>{subtitle&&<small>{subtitle}</small>}</div>;
}

export function RankPrize({rank,prize}:{rank:string;prize?:string|null}){
  const labels:Record<string,string>={Gold:'Gold Prize',Silver:'Silver Prize',Bronze:'Bronze Prize',None:'No Prize',Unknown:'Prize unavailable'};
  const label=prize?labels[prize]:null;
  if(!label)return <>{rank||'—'}</>;
  return <div className="judge-rank-prize"><span>{rank||'—'}</span>{label&&<small className={'judge-prize judge-prize-'+prize!.toLowerCase()} aria-label={'Prize: '+label}>{label}</small>}</div>;
}

export function ContestDetailView({history,error='',onRetry}:{history:History;error?:string;onRetry?:()=>void}){
  return <div className="judge-page judge-contest-detail" data-contest-detail={history.id}>
    <PageHeader title={history.contestName}/>
    <ErrorBox message={error} englishMessage="Unable to load this contest. Refresh or check your access." onRetry={onRetry}/>
    <ContestScoreboard history={history}/>
    <div className="judge-detail-columns">
      <SubmissionsTable key={history.id} submissions={history.submissions||[]} detailStyle problemOrder={history.problems}/>
      <aside className="judge-detail-information" aria-label="Contest information"><h2>Contest information</h2><ContestMetadata history={history} originalContest/></aside>
    </div>
  </div>;
}

export function ContestTiming({history,originalContest=false}:{history:History;originalContest?:boolean}){
  return <div className="judge-contest-times">
    {originalContest&&<a className="judge-original-contest" href={history.contestUrl} target="_blank" rel="noreferrer">Original contest<ExternalLink size={11}/></a>}
    {history.externalRankUrl&&<a className="judge-original-contest" href={history.externalRankUrl} target="_blank" rel="noreferrer">External scoreboard<ExternalLink size={11}/></a>}
    {history.mode!=='bt'&&<><span>Start: <strong>{dateTime(history.startedAt)}</strong></span><span>End: <strong>{history.endedAt?dateTime(history.endedAt):history.status==='failed'?'Not started':history.status==='cancelled'?'Cancelled':history.endsAt?dateTime(history.endsAt):'In progress'}</strong></span></>}
    <span className="judge-updated">Last synced: {dateTime(history.updatedAt)}</span>
    {history.frozen&&<span className="judge-freeze"><LockKeyhole size={13}/>Scoreboard frozen</span>}
  </div>;
}

export function ContestScoreboard({history,summary=true}:{history:History;summary?:boolean}){
  const practice=history.mode==='bt',graded=!!history.scoring&&history.scoring!=='ICPC';
  const publicColumns=history.publicProblemStatistics;
  const leadingColumns=practice?1:4;
  return <section className="judge-score-section" aria-label="Contest scoreboard">
    <TableFit compact center minimumWidth={(practice?150:330)+history.problems.length*42} className="judge-score-scroll"><table className="data-table scoreboard judge-scoreboard fit-table compact-result-table" data-dense={history.problems.length>16||undefined}><ResultColumns problems={history.problems} practice={practice} score={graded} rows={[{name:history.participantTitle||history.participantLabel,rank:history.rank,solved:history.solved,total:graded?history.points:history.penalty}]}/>
      <caption className="sr-only">Contest results for {history.participantLabel}</caption>
      <thead><tr>{!practice&&<th scope="col" className="judge-rank">Rank</th>}<th scope="col" className="judge-team">Team</th>{!practice&&<><th scope="col" className="judge-solved">Solved</th><th scope="col" className="judge-penalty">{graded?'Score':'Penalty'}</th></>}{history.problems.map(problem=><th scope="col" className="judge-problem-heading" key={problem}><ProblemBadge problem={problem} order={history.problems}/></th>)}</tr></thead>
      <tbody><tr>{!practice&&<td className="rank-value"><RankPrize rank={history.rank} prize={history.prize}/></td>}<td className="judge-team-name"><ParticipantIdentity title={history.participantTitle} subtitle={history.participantSubtitle} label={history.participantLabel}/></td>{!practice&&<><td className="metric-value">{history.solved}</td><td>{graded?history.points??'—':history.penalty}</td></>}{history.problems.map(problem=><DetailProblemCell key={problem} label={problem} cell={history.cells[problem]}/>)}</tr></tbody>
      {summary&&!practice&&<tfoot><tr className="judge-summary judge-summary-public"><th scope="row" colSpan={leadingColumns}>public</th>{history.problems.map((problem,i)=><td key={problem}>{publicColumns?publicColumns[i]?.label||'?':problem}</td>)}</tr>{(['submitted','attempted','accepted'] as const).map(kind=><tr className={'judge-summary judge-summary-'+kind} key={kind}><th scope="row" colSpan={leadingColumns}>{kind}</th>{history.problems.map((problem,i)=><td key={problem}>{publicColumns?.[i]?.[kind]??'—'}</td>)}</tr>)}</tfoot>}
    </table></TableFit>
  </section>;
}

export function ContestMetadata({history,originalContest=false}:{history:History;originalContest?:boolean}){return <div className={'judge-metadata'+(originalContest?' judge-metadata-with-link':'')}><div className="judge-identities"><ModeBadge mode={history.mode} english/><ParticipationBadge type={history.participantType} english/></div><ContestTiming history={history} originalContest={originalContest}/><ContestLegend/></div>}

export function ContestLegend(){return <aside className="judge-legend" aria-label="Cell colours"><h3>Cell colours</h3><div className="judge-legend-accepted">Solved</div><div className="judge-legend-rejected">Tried, incorrect</div><div className="judge-legend-pending">Tried, pending</div><div className="judge-legend-upsolved">Upsolved</div><div className="judge-legend-untried">Untried</div></aside>}

export function SubmissionsTable({submissions,title='Submissions',participantLabel,historyHref,detailStyle=false,problemOrder}:{submissions:Submission[];title?:string;participantLabel?:string;historyHref?:string;detailStyle?:boolean;problemOrder?:string[]}){
  const [filters,setFilters]=useState(false),[problem,setProblem]=useState(''),[state,setState]=useState(''),[stage,setStage]=useState('');
  const [page,setPage]=useState(1);
  const filterId=useId();
  const problems=useMemo(()=>Array.from(new Set(submissions.map(record=>record.problem))).sort((a,b)=>a.localeCompare(b,'en',{numeric:true})),[submissions]);
  const order=problemOrder?.length?problemOrder:problems;
  const records=useMemo(()=>[...submissions].filter(record=>(!problem||record.problem===problem)&&(!state||judgingState(record.verdict)===state)&&(!stage||record.practice===(stage==='practice'))).sort((a,b)=>new Date(b.submittedAt).getTime()-new Date(a.submittedAt).getTime()),[submissions,problem,state,stage]);
  const pageCount=Math.max(1,Math.ceil(records.length/10)),currentPage=Math.min(page,pageCount);
  const visibleRecords=detailStyle?records.slice((currentPage-1)*10,currentPage*10):records;
  const showScore=submissions.some(record=>!record.practice&&record.points!==undefined);
  return <section className={'judge-submissions'+(detailStyle?' domjudge-submissions':'')}>
    <div className="judge-section-heading"><div><h2>{title}{!detailStyle&&<span className="count-label">{submissions.length}</span>}</h2>{participantLabel&&<p>{participantLabel}</p>}</div><div className="judge-table-actions">{historyHref&&<a href={historyHref} className="text-button">History details<ArrowUpRight size={13}/></a>}<button className={'button button-secondary button-small '+(filters?'is-active':'')} onClick={()=>setFilters(!filters)} aria-label="Filter submissions" aria-controls={filterId} aria-expanded={filters}><Filter size={14} aria-hidden="true"/>{!detailStyle&&<>Filter<ChevronDown size={12} aria-hidden="true"/></>}</button></div></div>
    {filters&&<div className="judge-filters" id={filterId}><label>Problem<select value={problem} onChange={event=>{setProblem(event.target.value);setPage(1);}}><option value="">All problems</option>{problems.map(value=><option key={value}>{value}</option>)}</select></label><label>Result<select value={state} onChange={event=>{setState(event.target.value);setPage(1);}}><option value="">All results</option><option value="accepted">Accepted</option><option value="rejected">Incorrect</option><option value="pending">Pending / unconfirmed</option><option value="ignored">Not counted</option></select></label><label>Stage<select value={stage} onChange={event=>{setStage(event.target.value);setPage(1);}}><option value="">All stages</option><option value="contest">Contest</option><option value="practice">Practice</option></select></label><button className="text-button" onClick={()=>{setProblem('');setState('');setStage('');setPage(1);}}>Reset</button></div>}
    {!records.length?<Empty title={submissions.length?'No matching submissions':'No submissions yet'}/>:<TableFit widthFraction={detailStyle?1:0.5} fullHeader={!detailStyle} className="judge-submissions-fit"><table className="data-table judge-submissions-table fit-table">
      <caption className="sr-only">Submission history</caption>
      <thead><tr>{detailStyle?<><th scope="col">Time</th><th scope="col">Problem</th><th scope="col">Lang</th><th scope="col" className="text-left">Result</th>{showScore&&<th scope="col">Score</th>}<th scope="col" className="judge-record-heading"><span className="sr-only">Submission</span></th></>:<><th scope="col">Submission</th><th scope="col">Time</th><th scope="col">Problem</th><th scope="col" className="text-left">Result</th><th scope="col">Stage</th>{showScore&&<th scope="col">Score</th>}</>}</tr></thead>
      <tbody>{visibleRecords.map((submission,i)=>detailStyle?<tr key={submission.id+'-'+i} data-stage={submission.practice?'practice':'contest'}>
        <td className="time-value">{dateTime(submission.submittedAt)}</td>
        <td className="problem-letter"><ProblemBadge problem={submission.problem} order={order}/></td>
        <td className="language-value">{submissionLanguage(submission.language)}</td>
        <td className="text-left"><span className={'judge-verdict judge-verdict-'+judgingState(submission.verdict)} title={submission.practice?'Practice submission':'Contest submission'}>{submission.verdict}</span></td>
        {showScore&&<td>{submission.practice?'—':submission.points??'—'}</td>}
        <td className="judge-record-cell">{submission.url?<a href={submission.url} target="_blank" rel="noreferrer" className="submission-link judge-submission-record" aria-label={'View submission #'+submission.id} title={'Submission #'+submission.id}><FileText size={15} aria-hidden="true"/><span>#{submission.id}</span></a>:<span className="judge-submission-record" title={'Submission #'+submission.id}><FileText size={15} aria-hidden="true"/><span>#{submission.id}</span></span>}</td>
      </tr>:<tr key={submission.id+'-'+i}><td>{submission.url?<a href={submission.url} target="_blank" rel="noreferrer" className="submission-link">#{submission.id}<ArrowUpRight size={12} aria-hidden="true"/></a>:'#'+submission.id}</td><td className="time-value">{dateTime(submission.submittedAt)}</td><td className="problem-letter">{submission.problem}</td><td className="text-left"><span className={'judge-verdict judge-verdict-'+judgingState(submission.verdict)}>{submission.verdict==='FROZEN'?'FROZEN':submission.verdict}</span></td><td><span className={'stage-label '+(submission.practice?'stage-practice':'')}>{submission.practice?'Practice':'Contest'}</span></td>{showScore&&<td>{submission.practice?'—':submission.points??'—'}</td>}</tr>)}</tbody>
    </table></TableFit>}
    <div className="judge-table-footer"><span>{detailStyle&&pageCount>1?`${(currentPage-1)*10+1}–${Math.min(currentPage*10,records.length)} of `:''}{records.length} submissions</span><span>All times in Asia/Shanghai</span>
      {detailStyle&&pageCount>1&&<nav className="judge-pagination" aria-label="Submission pagination">
        <button type="button" disabled={currentPage===1} onClick={()=>setPage(currentPage-1)} aria-label="Previous page"><ChevronLeft size={14} aria-hidden="true"/>Previous</button>
        <span aria-live="polite" aria-atomic="true">{currentPage} / {pageCount}</span>
        <button type="button" disabled={currentPage===pageCount} onClick={()=>setPage(currentPage+1)} aria-label="Next page">Next<ChevronRight size={14} aria-hidden="true"/></button>
      </nav>}
    </div>
  </section>;
}
