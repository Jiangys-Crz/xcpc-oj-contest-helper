'use client';

import {useState,type CSSProperties} from 'react';
import {ExternalLink,LockKeyhole,Pin,Search} from 'lucide-react';
import {useResource} from './api';
import {Empty,ErrorBox,Loading} from './shared';
import type {VirtualBoard as Board,VirtualRow} from '../lib/virtual-board';
import styles from './VirtualBoard.module.css';

const categories={official:'Official',starred:'Starred',vp:'Room VP'};
const summaries=[['submitted','提交','Submitted'],['attempted','尝试','Attempted'],['accepted','通过','Accepted'],['first','首答','First solve'],['last','末答','Last solve']] as const;
const text=(value:number|null|undefined)=>value??'—';

function BoardRow({row,board,pinned=false}:{row:VirtualRow;board:Board;pinned?:boolean}) {
  const points=['OI','IOI'].includes(board.scoringSystem);
  const rank=row.kind==='starred'?'*':text(row.rank);
  return <article className={styles.row} data-pinned={pinned} aria-label={`${pinned?'My rank · ':''}${row.name} · ${rank}`}>
    <div className={styles.sidebar}>
      <div className={styles.metadata}><span className={styles[row.kind]}>{categories[row.kind]}</span>
        {pinned&&<strong><Pin size={12}/>我的排名 · My rank</strong>}
        {row.waiting&&<span>Awaiting sync</span>}
      </div>
      <div className={styles.metrics}>
        <strong className={styles.rank} data-medal={row.medal} data-starred={row.kind==='starred'}>{rank}</strong>
        {points?<strong className={styles.score}>{text(row.points)}</strong>:<><strong>{row.solved}</strong><strong className={styles.penalty}>{row.penalty}</strong></>}
      </div>
    </div>
    <div className={styles.results}>
      <div className={styles.identity}>
        {row.organization&&<strong className={styles.organization} title={row.organization}>{row.organization}</strong>}
        <div className={styles.team} title={[row.name,...row.members??[]].join(' · ')}><strong>{row.name}</strong>
          {row.members?.length?<small>{row.members.join(' · ')}</small>:null}
        </div>
      </div>
      <div className={styles.problems}>{board.problems.map(({label},index)=>{
        const cell=label?row.cells[label]:undefined,first=label&&row.first?.includes(label);
        const kind=cell?.solved?(first?'first':'accepted'):cell?.pending?'pending':cell?.tries?'wrong':'empty';
        const attempts=(cell?.tries??0)+(cell?.pending??0);
        const result={first:'First solve',accepted:'Accepted',pending:'Pending',wrong:'Incorrect',empty:label?'Untried':'Hidden'}[kind];
        const description=[label||'Hidden problem',result,attempts?`${attempts} submissions`:'',cell?.solveTime!=null?`${cell.solveTime} min`:''].filter(Boolean).join(' · ');
        return <span key={index} className={styles.cell} data-result={kind} title={description} aria-label={description}>
          {points&&cell?.points!=null?cell.points:kind==='empty'?label||'?':<>
            <span className={styles.attempts}>{attempts}</span><span>{cell?.solveTime!=null?`${cell.solveTime}′`:kind==='pending'?'?':'−'}</span>
            {first&&<span className={styles.star} aria-hidden="true">★</span>}
          </>}
        </span>;
      })}</div>
    </div>
  </article>;
}

export function VirtualBoard({roomId}:{roomId:string}) {
  const result=useResource<Board>(`/api/rooms/${encodeURIComponent(roomId)}/virtual-board`,5000);
  const [term,setTerm]=useState(''),[category,setCategory]=useState('all');
  const board=result.data;
  const query=term.trim().toLocaleLowerCase();
  const rows=board?.rows.filter(row=>(category==='all'||row.kind===category)
    && (!query||[row.name,row.organization,...row.members??[]].some(value=>value?.toLocaleLowerCase().includes(query))))??[];
  const points=['OI','IOI'].includes(board?.scoringSystem||'');
  return <section className={styles.panel} aria-label="Virtual Board">
    <div className={styles.toolbar}>
      <div className={styles.title}><h2>Virtual Board</h2>{board&&<span className={styles.format}>{board.scoringSystem}</span>}
        {board?.frozen&&<span className={styles.freeze}><LockKeyhole size={12}/>Frozen</span>}</div>
      <div className={styles.filters}>
        <label className={styles.search}><Search size={16}/><input aria-label="Search by team, school or member" type="search" placeholder="Search team, school or member" value={term} onChange={event=>setTerm(event.target.value)}/></label>
        <label><span className="sr-only">Participation category</span><select value={category} onChange={event=>setCategory(event.target.value)}>
          <option value="all">All participants</option>
          {Object.entries(categories).map(([key,label])=><option key={key} value={key}>{label}</option>)}
        </select></label>
        {board&&<span className={styles.count}>{rows.length} / {board.rows.length}</span>}
      </div>
      {board?.sourceUrl&&<a className={styles.source} href={board.sourceUrl} target="_blank" rel="noreferrer">Original scoreboard <ExternalLink size={13}/></a>}
    </div>
    <ErrorBox message={result.error} englishMessage="Unable to load the Virtual Board." onRetry={result.reload}/>
    {result.loading&&!board&&<Loading/>}
    {board&&<>
      {!board.originalAvailable&&<p className={styles.notice}>{board.sourceUrl?'The original scoreboard is currently unavailable for this room.':'Waiting for the original scoreboard to sync from the terminal.'}</p>}
      <div className={styles.scroll} tabIndex={0} aria-label="Scrollable standings">
        <div className={styles.board} style={{'--problem-count':Math.max(board.problems.length,1), '--metric-columns':points?'58px 1fr':'58px 57px 1fr','--board-min-width':`${Math.max(460,225+board.problems.length*48)}px`} as CSSProperties}>
          <div className={styles.header}>
            <div className={styles.metrics}><span>排名<small>Rank</small></span>{points?<span>总分<small>Score</small></span>:<><span>题数<small>Solved</small></span><span>罚时<small>Penalty</small></span></>}</div>
            <div className={styles.problems}>{board.problems.map((problem,index)=><span key={index}><strong>{problem.label||'?'}</strong>
              <small>{text(problem.accepted)}/{text(problem.submitted)}</small></span>)}</div>
          </div>
          {board.mine&&<><BoardRow row={board.mine} board={board} pinned/><div className={styles.divider}><Pin size={14}/><strong>我的排名</strong><small>My rank</small><span/></div></>}
          {rows.map(row=><BoardRow key={row.id} row={row} board={board}/>)}
          {!rows.length&&<Empty title="No matching participants"/>}
          {board.originalAvailable&&<footer className={styles.summary} aria-label="Original problem statistics">
            {summaries.map(([key,label,en])=><div key={key} className={styles.summaryRow}>
              <span>{label}<small>{en}</small></span><div className={styles.problems}>{board.problems.map((problem,index)=><span key={index}>
                {problem[key]===null?'—':key==='first'||key==='last'?`${problem[key]}′`:problem[key]}
              </span>)}</div>
            </div>)}
          </footer>}
        </div>
      </div>
    </>}
  </section>;
}
