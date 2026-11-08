'use client';
import {FormEvent,useState} from 'react';
import Link from 'next/link';
import {ArrowUpRight,ChevronRight,Link2,Search} from 'lucide-react';
import {useResource} from './api';
import {dateTime,Empty,ErrorBox,Loading,ModeBadge,PageHeader,ParticipationBadge} from './shared';
import {ContestDetailView} from './ContestDetail';
import type {Cell,History} from './types';

const problemStates={accepted:'AC · 赛内通过',rejected:'RJ · 尝试未通过',upsolved:'补题通过',partial:'部分得分',pending:'待评测',untried:'未尝试'};
type ProblemState=keyof typeof problemStates;

function problemState(cell:Cell|undefined,practice:boolean,graded:boolean):ProblemState {
  if(cell?.solved)return practice?'upsolved':'accepted';
  if(cell?.practiceSolved)return 'upsolved';
  if(cell?.pending)return 'pending';
  if(graded&&!practice&&(cell?.points??0)>0)return 'partial';
  return cell?.tries?'rejected':'untried';
}

function HistoryProblem({label,cell,practice,graded}:{label:string;cell?:Cell;practice:boolean;graded:boolean}){
  const state=problemState(cell,practice,graded);
  const score=graded&&!practice&&cell?.points!==undefined?` · 赛内得分 ${cell.points}${cell.maxPoints!==undefined?' / '+cell.maxPoints:''}`:'';
  const description=`${label} · ${problemStates[state]}${score}`;
  return <span className={'history-problem history-state-'+state} role="img" aria-label={description} title={description}>{label}</span>;
}

export function HistoryLegend({graded=false,pending=false}:{graded?:boolean;pending?:boolean}){
  const states:ProblemState[]=['accepted','rejected','upsolved',...(graded?['partial' as const]:[]),...(pending?['pending' as const]:[]),'untried'];
  return <div className="history-legend" aria-label="题目状态图例">{states.map(state=><span key={state}><i className={'history-state-'+state} aria-hidden="true"/>{problemStates[state]}</span>)}</div>;
}

const isGraded=(item:History)=>!!item.scoring&&!['ICPC','CCPC'].includes(item.scoring.toUpperCase());
const prizes:Record<string,string>={Gold:'金奖',Silver:'银奖',Bronze:'铜奖'};

function HistorySummary({item}:{item:History}){
  const practice=item.mode==='bt',graded=isGraded(item);
  const upsolved=item.problems.filter(problem=>{
    const cell=item.cells[problem];
    return practice?(cell?.solved||cell?.practiceSolved):(!cell?.solved&&cell?.practiceSolved);
  }).length;
  const solved=practice?upsolved:item.solved;
  const rank=!practice&&item.rank&&!['—','?','-'].includes(item.rank)?item.rank:null;
  const prize=!practice&&item.prize?prizes[item.prize]:null;
  return <div className="history-result">
    {graded&&!practice?<>
      <div className="history-result-main" aria-label={'赛内总分：'+(item.points??'未知')}><strong>{item.points??'—'}</strong><span>分</span></div>
      <div className="history-result-secondary">{item.solved} / {item.problems.length} AC{upsolved>0&&<span className="history-upsolve-count"> · +{upsolved} 补题</span>}</div>
    </>:<>
      <div className={'history-result-main '+(practice?'history-practice-result':'')} aria-label={`${practice?'补题通过':'赛内 AC'} ${solved} / ${item.problems.length} 题`}><strong>{solved}</strong><span>/ {item.problems.length}</span><small>{practice?'补题':'AC'}</small></div>
      {!practice&&upsolved>0&&<div className="history-result-secondary history-upsolve-count">+{upsolved} 补题</div>}
    </>}
    {(rank||prize)&&<div className="history-result-secondary history-placement">{rank&&<span title={item.rankSource==='original'?'原比赛正式参赛者中的排名':'比赛排名'}>排名 {rank}</span>}{prize&&<span className={'history-prize-'+item.prize!.toLowerCase()}>{prize}</span>}</div>}
  </div>;
}

export function HistoryResultsTable({items}:{items:History[]}){
  return <table className="history-archive" role="table">
    <caption className="sr-only">比赛历史及题目状态概览，点击比赛标题或题目区域查看详情</caption>
    <colgroup><col className="history-contest-col"/><col className="history-problems-col"/><col className="history-result-col"/></colgroup>
    <thead><tr><th scope="col">比赛</th><th scope="col">题目概览</th><th scope="col" className="history-result-heading">成绩概要</th></tr></thead>
    <tbody>{items.map(item=>{
      const practice=item.mode==='bt',identity=[item.participantTitle||item.participantLabel,item.participantSubtitle].filter(Boolean).join(' · ');
      const timestamp=practice?item.updatedAt:item.startedAt;
      const formattedDate=dateTime(timestamp),dateLabel=practice?'最近补题同步':'开始时间';
      return <tr key={item.id}>
        <td className="history-contest">
          <Link className="history-contest-link" href={'/history/'+item.id}><Link2 size={16} aria-hidden="true"/><span>{item.contestName}</span></Link>
          <div className="history-meta">
            <ModeBadge mode={item.mode}/><span className="history-scoring">{item.scoring||'ICPC'}</span><ParticipationBadge type={item.participantType}/>
            <span className="history-identity" title={identity}>{identity}</span>
            {formattedDate!=='—'&&<time dateTime={timestamp!} title={dateLabel+'：'+formattedDate}>{practice?'更新 ':''}{formattedDate.slice(0,10)}</time>}
            {item.status==='failed'&&<span className="history-record-status">未开始</span>}{item.status==='cancelled'&&<span className="history-record-status">已取消</span>}
          </div>
        </td>
        <td className="history-overview"><Link className="history-overview-link" href={'/history/'+item.id} aria-label={'查看 '+item.contestName+' 的题目详情'}>
          <span className="history-problems">{item.problems.length?item.problems.map(problem=><HistoryProblem key={problem} label={problem} cell={item.cells[problem]} practice={practice} graded={isGraded(item)}/>):<span className="history-no-problems">暂无题目</span>}</span>
          <ChevronRight size={16} className="history-detail-arrow" aria-hidden="true"/>
        </Link></td>
        <td className="history-summary"><HistorySummary item={item}/></td>
      </tr>;
    })}</tbody>
  </table>;
}

export function HistoryList(){
  const [term,setTerm]=useState(''),[search,setSearch]=useState(''),[type,setType]=useState('');
  const result=useResource<History[]>('/api/history?search='+encodeURIComponent(search)+'&type='+type,5000),items=result.data||[];
  function filter(e:FormEvent){e.preventDefault();setSearch(term.trim());}
  return <div className="history-list">
    <PageHeader title="历史记录" actions={<Link className="button button-primary" href="/vp">参加虚拟赛<ArrowUpRight size={16}/></Link>}/>
    <section className="card history-list-panel" aria-label="比赛记录">
      <div className="table-toolbar"><form onSubmit={filter} className="filter-form"><div className="input-with-icon"><Search size={17}/><input aria-label="搜索比赛名称" placeholder="搜索比赛名称" value={term} onChange={e=>setTerm(e.target.value)}/></div><select value={type} aria-label="筛选比赛类型" onChange={e=>setType(e.target.value)}><option value="">全部类型</option><option value="vp">虚拟参赛 VP</option><option value="live">实时比赛 LIVE</option><option value="bt">补题 BT</option></select><button className="button button-secondary" type="submit">搜索</button></form><span className="muted small">{items.length} 场记录</span></div>
      <ErrorBox message={result.error} onRetry={result.reload}/>
      {result.loading?<Loading/>:!items.length?<Empty title={search||type?'没有符合条件的记录':'还没有比赛记录'} description={search||type?'试试其他比赛名称或类型。':'终端登录后，VP、live 和补题数据会自动保存到这里。'} action={<Link className="button button-secondary" href="/vp">查看 VP 列表<ChevronRight size={15}/></Link>}/>:<HistoryResultsTable items={items}/>}
      <div className="history-list-footer"><HistoryLegend graded={items.some(item=>item.mode!=='bt'&&isGraded(item))} pending={items.some(item=>item.problems.some(problem=>problemState(item.cells[problem],item.mode==='bt',isGraded(item))==='pending'))}/><span>点击比赛或题目区域查看详情</span></div>
    </section>
  </div>;
}

export function HistoryDetail({id}:{id:string}){
  const result=useResource<History>('/api/history/'+encodeURIComponent(id),5000);
  if(result.loading)return <Loading/>;
  if(result.error&&!result.data)return <ErrorBox englishMessage="Unable to load this contest. Refresh or check your access." message={result.error} onRetry={result.reload}/>;
  const history=result.data;
  if(!history)return null;
  return <div className="history-detail"><ContestDetailView history={history} error={result.error} onRetry={result.reload}/></div>;
}
