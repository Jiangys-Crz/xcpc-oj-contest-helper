'use client';
import {useMemo,useState} from 'react';
import {ArrowUpRight,ChevronDown,Clock,Filter,HelpCircle,LockKeyhole,ThumbsDown,ThumbsUp} from 'lucide-react';
import {Empty,ProblemCell,dateTime} from './shared';
import type {History,Submission} from './types';
import {judgingState} from './judging';

const problemColors=['#7668c6','#ca5a3e','#9b751d','#328198','#a05c8c','#547f67','#527caa','#b66b31'];

export function ContestTiming({history}:{history:History}){
  return <div className="judge-contest-times">
    {history.mode!=='bt'&&<><span>开始：<strong>{dateTime(history.startedAt)}</strong></span><span>结束：<strong>{history.endedAt?dateTime(history.endedAt):history.status==='failed'?'未启动':history.status==='cancelled'?'已取消':history.endsAt?dateTime(history.endsAt):'尚未结束'}</strong></span></>}
    <span className="judge-updated">最后同步：{dateTime(history.updatedAt)}</span>
    {history.frozen&&<span className="judge-freeze"><LockKeyhole size={13}/>封榜中</span>}
  </div>;
}

export function ContestScoreboard({history,summary=true}:{history:History;summary?:boolean}){
  const submissions=history.submissions||[];
  const totals=history.problems.map(problem=>{
    const records=submissions.filter(record=>record.problem===problem);
    return {accepted:records.filter(record=>judgingState(record.verdict)==='accepted').length,rejected:records.filter(record=>judgingState(record.verdict)==='rejected').length,pending:records.filter(record=>judgingState(record.verdict)==='pending').length};
  });
  return <section className="judge-score-section" aria-label="比赛得分">
    <div className="table-scroll judge-score-scroll"><table className="data-table scoreboard judge-scoreboard">
      <caption className="sr-only">{history.participantLabel} 的比赛得分和题目状态</caption>
      <thead><tr><th className="judge-rank">Rank <small>排名</small></th><th className="judge-team">Team <small>队伍 / 参赛者</small></th><th className="judge-solved">Solved <small>解题</small></th><th className="judge-penalty">Penalty <small>罚时</small></th>{history.problems.map((problem,i)=><th className="judge-problem-heading" key={problem}><span style={{background:problemColors[i%problemColors.length]}}>{problem}</span></th>)}</tr></thead>
      <tbody><tr><td className="rank-value">{history.rank||'—'}</td><td className="judge-team-name"><strong>{history.participantLabel}</strong><small>{history.participantType==='team'?'队伍参赛':'个人参赛'}</small></td><td className="metric-value">{history.solved}</td><td>{history.penalty}</td>{history.problems.map(problem=><ProblemCell key={problem} label={problem} cell={history.cells[problem]}/>)}</tr></tbody>
      {summary&&<tfoot><tr className="judge-summary"><th colSpan={2}>Summary <small>摘要</small></th><td colSpan={2}><strong>{history.solved}</strong><small> / {history.problems.length}</small></td>{history.problems.map((problem,i)=><td key={problem}><div className="judge-problem-summary"><span title="通过的提交"><ThumbsUp size={12}/>{totals[i].accepted}</span><span title="未通过的提交"><ThumbsDown size={12}/>{totals[i].rejected}</span><span title="待判或封榜中的提交"><HelpCircle size={12}/>{totals[i].pending}</span>{history.mode!=='bt'&&<span title="赛时通过用时（分钟）"><Clock size={12}/>{history.cells[problem]?.solveTime??'—'}</span>}</div></td>)}</tr></tfoot>}
    </table></div>
  </section>;
}

export function ContestLegend(){return <aside className="judge-legend" aria-label="单元格颜色说明"><h3>Cell colours <span>单元格颜色</span></h3><div className="judge-legend-accepted">Solved <span>赛时通过</span></div><div className="judge-legend-rejected">Tried, incorrect <span>尝试未通过</span></div><div className="judge-legend-pending">Tried, pending <span>待判 / 封榜</span></div><div className="judge-legend-upsolved">Upsolved <span>赛后补题通过</span></div><div className="judge-legend-untried">Untried <span>尚未尝试</span></div></aside>}

export function SubmissionsTable({submissions,title='提交记录',participantLabel,historyHref}:{submissions:Submission[];title?:string;participantLabel?:string;historyHref?:string}){
  const [filters,setFilters]=useState(false),[problem,setProblem]=useState(''),[state,setState]=useState(''),[stage,setStage]=useState('');
  const problems=useMemo(()=>Array.from(new Set(submissions.map(record=>record.problem))).sort((a,b)=>a.localeCompare(b,'en',{numeric:true})),[submissions]);
  const records=useMemo(()=>[...submissions].filter(record=>(!problem||record.problem===problem)&&(!state||judgingState(record.verdict)===state)&&(!stage||record.practice===(stage==='practice'))).sort((a,b)=>new Date(b.submittedAt).getTime()-new Date(a.submittedAt).getTime()),[submissions,problem,state,stage]);
  return <section className="judge-submissions">
    <div className="judge-section-heading"><div><h2>{title}<span className="count-label">{submissions.length}</span></h2>{participantLabel&&<p>{participantLabel}</p>}</div><div className="judge-table-actions">{historyHref&&<a href={historyHref} className="text-button">历史详情<ArrowUpRight size={13}/></a>}<button className={'button button-secondary button-small '+(filters?'is-active':'')} onClick={()=>setFilters(!filters)} aria-expanded={filters}><Filter size={14}/>筛选<ChevronDown size={12}/></button></div></div>
    {filters&&<div className="judge-filters"><label>题目<select value={problem} onChange={event=>setProblem(event.target.value)}><option value="">全部题目</option>{problems.map(value=><option key={value}>{value}</option>)}</select></label><label>结果<select value={state} onChange={event=>setState(event.target.value)}><option value="">全部结果</option><option value="accepted">通过</option><option value="rejected">未通过</option><option value="pending">待判 / 未确认</option><option value="ignored">不计成绩</option></select></label><label>阶段<select value={stage} onChange={event=>setStage(event.target.value)}><option value="">全部阶段</option><option value="contest">赛时</option><option value="practice">补题</option></select></label><button className="text-button" onClick={()=>{setProblem('');setState('');setStage('');}}>重置</button></div>}
    {!records.length?<Empty title={submissions.length?'没有符合条件的提交':'暂无提交记录'}/>:<div className="table-scroll"><table className="data-table judge-submissions-table"><thead><tr><th>Submission <small>提交编号</small></th><th>Time <small>提交时间</small></th><th>Problem <small>题目</small></th><th className="text-left">Result <small>判题结果</small></th><th>Stage <small>阶段</small></th></tr></thead><tbody>{records.map((submission,i)=><tr key={submission.id+'-'+i}><td>{submission.url?<a href={submission.url} target="_blank" rel="noreferrer" className="submission-link">#{submission.id}<ArrowUpRight size={12}/></a>:'#'+submission.id}</td><td className="time-value">{dateTime(submission.submittedAt)}</td><td className="problem-letter">{submission.problem}</td><td className="text-left"><span className={'judge-verdict judge-verdict-'+judgingState(submission.verdict)}>{submission.verdict==='FROZEN'?'FROZEN · 封榜中':submission.verdict}</span></td><td><span className={'stage-label '+(submission.practice?'stage-practice':'')}>{submission.practice?'补题':'赛时'}</span></td></tr>)}</tbody></table></div>}
    <div className="judge-table-footer"><span>{records.length} 条提交</span><span>时间均为北京时间</span></div>
  </section>;
}
