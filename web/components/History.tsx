'use client';
import {FormEvent,useMemo,useState} from 'react';
import Link from 'next/link';
import {ArrowUpRight,ChevronRight,ExternalLink,Search} from 'lucide-react';
import {useResource} from './api';
import {BackLink,Empty,ErrorBox,Legend,Loading,ModeBadge,PageHeader,ProblemCell,ParticipationBadge} from './shared';
import {ContestLegend,ContestScoreboard,ContestTiming,SubmissionsTable} from './ContestDetail';
import type {History} from './types';
export function HistoryList(){const [term,setTerm]=useState('');const [search,setSearch]=useState('');const [type,setType]=useState('');const result=useResource<History[]>('/api/history?search='+encodeURIComponent(search)+'&type='+type);const items=result.data||[];const problems=useMemo(()=>Array.from(new Set(items.flatMap(item=>item.problems))).sort((a,b)=>a.localeCompare(b,'en',{numeric:true})),[items]);function filter(e:FormEvent){e.preventDefault();setSearch(term.trim());}return <><PageHeader eyebrow="YOUR TRAINING JOURNEY" title="历史记录" actions={<Link className="button button-primary" href="/vp">参加虚拟赛<ArrowUpRight size={16}/></Link>}/><section className="card"><div className="table-toolbar"><form onSubmit={filter} className="filter-form"><div className="input-with-icon"><Search size={17}/><input aria-label="搜索比赛名称" placeholder="搜索比赛名称" value={term} onChange={e=>setTerm(e.target.value)}/></div><select value={type} aria-label="筛选比赛类型" onChange={e=>setType(e.target.value)}><option value="">全部类型</option><option value="vp">虚拟参赛 VP</option><option value="live">实时比赛 LIVE</option><option value="bt">补题 BT</option></select><button className="button button-secondary" type="submit">搜索</button></form><span className="muted small">{items.length} 场记录</span></div><ErrorBox message={result.error} onRetry={result.reload}/>{result.loading?<Loading/>:!items.length?<Empty title={search||type?'没有符合条件的记录':'还没有比赛记录'} description={search||type?'试试其他比赛名称或类型。':'终端登录后，VP、live 和补题数据会自动保存到这里。'} action={<Link className="button button-secondary" href="/vp">查看 VP 列表<ChevronRight size={15}/></Link>}/>:<div className="table-scroll"><table className="data-table scoreboard history-table"><thead><tr><th className="text-left contest-column">比赛名称</th><th>类型</th><th>参赛方式</th><th>Rank</th><th>Solved</th><th>Penalty</th>{problems.map(p=><th className="problem-heading" key={p}>{p}</th>)}<th>操作</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td className="text-left"><Link className="contest-link" href={'/history/'+item.id}>{item.contestName}</Link><small className="row-subtitle">{item.participantLabel}{item.status==='failed'?' · 未启动':item.status==='cancelled'?' · 已取消':''}</small></td><td><ModeBadge mode={item.mode}/></td><td><ParticipationBadge type={item.participantType}/></td><td className="rank-value">{item.rank||'—'}</td><td className="metric-value">{item.solved}</td><td>{item.penalty}</td>{problems.map(p=><ProblemCell key={p} label={p} cell={item.cells[p]}/>)}<td><Link className="table-action" href={'/history/'+item.id}>详情<ChevronRight size={13}/></Link></td></tr>)}</tbody></table></div>}<div className="table-footer"><Legend/><span>Rank 为原站排名</span></div></section></>}
export function HistoryDetail({id}:{id:string}){
  const result=useResource<History>('/api/history/'+encodeURIComponent(id),5000);
  if(result.loading)return <Loading/>;
  if(result.error&&!result.data)return <ErrorBox message={result.error} onRetry={result.reload}/>;
  const history=result.data;
  if(!history)return null;
  return <div className="judge-page">
    <BackLink href="/history" label="历史记录"/>
    <PageHeader title={history.contestName} actions={<><ModeBadge mode={history.mode}/><ParticipationBadge type={history.participantType}/><a className="button button-secondary" href={history.contestUrl} target="_blank" rel="noreferrer">原站比赛<ExternalLink size={14}/></a></>}/>
    <ContestTiming history={history}/>
    <ErrorBox message={result.error} onRetry={result.reload}/>
    <ContestScoreboard history={history}/>
    <SubmissionsTable key={id} submissions={history.submissions||[]}/>
    <ContestLegend/>
  </div>;
}
