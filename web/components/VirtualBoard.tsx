'use client';

import {useId,useLayoutEffect,useRef,useState,type CSSProperties} from 'react';
import {ExternalLink,ListOrdered,LockKeyhole,Pin,Search,Users,X} from 'lucide-react';
import {useResource} from './api';
import {Empty,ErrorBox,Loading} from './shared';
import type {VirtualBoard as Board,VirtualRow} from '../lib/virtual-board';
import styles from './VirtualBoard.module.css';

const categories={official:'Official',starred:'Unofficial',vp:'Room VP'};
const summaries=[['submitted','提交','Submitted'],['attempted','尝试','Attempted'],['accepted','通过','Accepted'],['first','首答','First solve'],['last','末答','Last solve']] as const;
const text=(value:number|null|undefined)=>value??'—';

function BoardRow({row,board,pin}:{row:VirtualRow;board:Board;pin?:'mine'|'search'}) {
  const points=['OI','IOI'].includes(board.scoringSystem);
  const rank=row.kind==='starred'?'*':text(row.rank);
  const category=row.female&&row.kind==='official'?'Female Team':categories[row.kind];
  return <article className={styles.row} data-pinned={pin??'false'} aria-label={`${pin==='mine'?'My rank · ':''}${row.name} · ${rank}`}>
    <div className={styles.sidebar}>
      <div className={styles.people}>
        <div className={styles.metadata}><span className={styles[row.kind]}>{category}</span>
          {pin==='mine'&&<strong><Pin size={12}/>我的排名 · My rank</strong>}
          {row.waiting&&<span>Awaiting sync</span>}
        </div>
        {!!row.members?.length&&<div className={styles.members} title={row.members.join('、')}>
          <Users size={12} aria-hidden="true"/><span>{row.members.join('、')}</span>
        </div>}
      </div>
      <div className={styles.metrics}>
        <strong className={styles.rank} data-medal={row.medal} data-starred={row.kind==='starred'}>{rank}</strong>
        {points?<strong className={styles.score}>{text(row.points)}</strong>:<><strong>{row.solved}</strong><strong className={styles.penalty}>{row.penalty}</strong></>}
      </div>
    </div>
    <div className={styles.results}>
      <div className={styles.identity}>
        {row.organization&&<strong className={styles.organization} title={row.organization}>{row.organization}</strong>}
        <div className={styles.team} title={row.name}><strong>{row.name}</strong></div>
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

function ParticipantSearch({rows,selected,onChange}:{rows:VirtualRow[];selected:VirtualRow[];onChange:(ids:string[])=>void}) {
  const id=useId(),input=useRef<HTMLInputElement>(null);
  const [query,setQuery]=useState(''),[open,setOpen]=useState(false),[active,setActive]=useState(0);
  const ids=selected.map(row=>row.id);
  const normalize=(value:string)=>value.normalize('NFKC').toLocaleLowerCase();
  const terms=normalize(query).trim().split(/\s+/).filter(Boolean);
  const matches=terms.length?rows.filter(row=>{
    if (ids.includes(row.id)) return false;
    const fields=[row.name,row.organization??'',...row.members??[]].map(normalize);
    return terms.every(term=>fields.some(field=>field.includes(term)));
  }):[];
  const options=matches.slice(0,8);
  const expanded=open&&terms.length>0;
  const index=Math.min(active,options.length-1);
  function choose(row:VirtualRow) {
    onChange([...ids,row.id]);
    setQuery('');
    setActive(0);
    input.current?.focus();
  }
  return <div className={styles.search}>
    <div className={styles.searchField}>
      <Search size={16} aria-hidden="true"/>
      {selected.map(row=><button key={row.id} type="button" className={styles.tag} aria-label={`Unpin ${row.name}`} onClick={()=>onChange(ids.filter(id=>id!==row.id))}>
        <span>{row.name}</span><X size={12} aria-hidden="true"/>
      </button>)}
      <input ref={input} role="combobox" aria-label="Search team, school or member to pin" aria-autocomplete="list"
        aria-expanded={expanded} aria-controls={expanded?id:undefined} aria-activedescendant={expanded&&index>=0?`${id}-${index}`:undefined}
        placeholder={selected.length?'Search and pin more…':'Search team or institution to pin…'} value={query}
        onFocus={()=>setOpen(true)} onBlur={()=>setOpen(false)}
        onChange={event=>{setQuery(event.target.value);setActive(0);setOpen(true);}}
        onKeyDown={event=>{
          if (event.nativeEvent.isComposing) return;
          if (event.key==='Escape') {setOpen(false);return;}
          if (event.key==='ArrowDown'||event.key==='ArrowUp') {
            event.preventDefault();
            setOpen(true);
            setActive(Math.max(0,Math.min(options.length-1,index+(event.key==='ArrowDown'?1:-1))));
          }
          if (event.key==='Enter'&&expanded&&options[index]) {event.preventDefault();choose(options[index]);}
        }}/>
      {!!selected.length&&<button type="button" className={styles.clearPins} aria-label="Clear pinned participants" onClick={()=>onChange([])}><X size={14}/></button>}
    </div>
    {expanded&&<div className={styles.options}>
      <div id={id} role="listbox" aria-label="Matching participants">
        {options.map((row,i)=><button type="button" tabIndex={-1} key={row.id} id={`${id}-${i}`} role="option"
          aria-selected={i===index} onPointerDown={event=>event.preventDefault()} onClick={()=>choose(row)}>
          <strong>{row.name}</strong><small>{[row.organization,`Rk ${row.kind==='starred'?'*':text(row.rank)}`].filter(Boolean).join(' · ')}</small>
        </button>)}
      </div>
      {!options.length&&<p role="status">No matching participants</p>}
      {matches.length>options.length&&<p role="status">{matches.length} matches · refine your search</p>}
    </div>}
  </div>;
}

function Standings({board,rows,pins,filtered}:{board:Board;rows:VirtualRow[];pins:VirtualRow[];filtered:boolean}) {
  const frame=useRef<HTMLDivElement>(null),heading=useId();
  const [scale,setScale]=useState(1);
  const width=Math.max(460,225+board.problems.length*48);
  const points=['OI','IOI'].includes(board.scoringSystem);
  useLayoutEffect(()=>{
    const container=frame.current;
    if (!container) return;
    const navigation=container.closest('.room-view')?.querySelector('.room-tabs');
    const measure=()=>{
      setScale(Math.min(1,container.clientWidth/width));
      container.style.setProperty('--board-sticky-top',`${navigation?.getBoundingClientRect().height??0}px`);
    };
    measure();
    const observer=new ResizeObserver(measure);
    observer.observe(container);
    if (navigation) observer.observe(navigation);
    return ()=>observer.disconnect();
  },[width]);
  return <div ref={frame} className={styles.standings} style={{
    '--problem-count':Math.max(board.problems.length,1), '--metric-columns':points?'58px 1fr':'58px 57px 1fr',
    '--board-min-width':`${width}px`, '--board-scale':scale,
  } as CSSProperties}>
    <div className={styles.sticky}>
      <div className={styles.header}>
        <div className={styles.metrics}><span>排名<small>Rank</small></span>{points?<span>总分<small>Score</small></span>:<><span>题数<small>Solved</small></span><span>罚时<small>Penalty</small></span></>}</div>
        <div className={styles.problems}>{board.problems.map((problem,index)=><span key={index}><strong>{problem.label||'?'}</strong>
          <small>{text(problem.accepted)}/{text(problem.submitted)}</small></span>)}</div>
      </div>
    </div>
    <div className={styles.board}>
      {board.mine&&<BoardRow row={board.mine} board={board} pin="mine"/>}
      {!!pins.length&&<>
        <div className={styles.divider}><Pin size={14}/><strong>筛选置顶结果</strong><small>Pinned Filtered Rows</small><span/></div>
        {pins.map(row=><BoardRow key={row.id} row={row} board={board} pin="search"/>)}
      </>}
      <section aria-labelledby={heading}>
        <h3 className={styles.divider} id={heading}><ListOrdered size={14}/><strong>{filtered?'分类榜单':'完整榜单'}</strong>
          <small>{filtered?'Filtered Standings':'Full Standings'}</small><span/></h3>
        {rows.map(row=><BoardRow key={row.id} row={row} board={board}/>)}
        {!rows.length&&<Empty title="No matching participants"/>}
      </section>
      {board.originalAvailable&&<footer className={styles.summary} aria-label="Original problem statistics">
        {summaries.map(([key,label,en])=><div key={key} className={styles.summaryRow}>
          <span>{label}<small>{en}</small></span><div className={styles.problems}>{board.problems.map((problem,index)=><span key={index}>
            {problem[key]===null?'—':key==='first'||key==='last'?`${problem[key]}′`:problem[key]}
          </span>)}</div>
        </div>)}
      </footer>}
    </div>
  </div>;
}

export function VirtualBoard({roomId}:{roomId:string}) {
  const result=useResource<Board>(`/api/rooms/${encodeURIComponent(roomId)}/virtual-board`,5000);
  const [selected,setSelected]=useState<string[]>([]),[category,setCategory]=useState('all');
  const board=result.data;
  const all=board?.rows??[];
  const rows=all.filter(row=>category==='all'||row.kind===category||category==='female'&&row.kind==='official'&&row.female);
  const chosen=all.filter(row=>selected.includes(row.id));
  const pins=rows.filter(row=>selected.includes(row.id)&&row.id!==board?.mine?.id);
  return <section className={styles.panel} aria-label="Virtual Board">
    <div className={styles.toolbar}>
      <div className={styles.title}><h2>Virtual Board</h2>{board&&<span className={styles.format}>{board.scoringSystem}</span>}
        {board?.frozen&&<span className={styles.freeze}><LockKeyhole size={12}/>Frozen</span>}</div>
      <div className={styles.filters}>
        <select aria-label="Participation category" value={category} onChange={event=>setCategory(event.target.value)}>
          <option value="all">All Participants</option>
          {Object.entries(categories).map(([key,label])=><option key={key} value={key}>{label}</option>)}
          {all.some(row=>row.female&&row.kind==='official')&&<option value="female">Female Team</option>}
        </select>
        <ParticipantSearch rows={rows} selected={chosen} onChange={setSelected}/>
        {board&&<span className={styles.count}>{rows.length} / {board.rows.length}</span>}
      </div>
      {board?.sourceUrl&&<a className={styles.source} href={board.sourceUrl} target="_blank" rel="noreferrer">Original scoreboard <ExternalLink size={13}/></a>}
    </div>
    <ErrorBox message={result.error} englishMessage="Unable to load the Virtual Board." onRetry={result.reload}/>
    {result.loading&&!board&&<Loading/>}
    {board&&<>
      {!board.originalAvailable&&<p className={styles.notice}>{board.sourceUrl?'The original scoreboard is currently unavailable for this room.':'Waiting for the original scoreboard to sync from the terminal.'}</p>}
      <Standings board={board} rows={rows} pins={pins} filtered={category!=='all'}/>
    </>}
  </section>;
}
