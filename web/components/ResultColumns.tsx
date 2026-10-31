type ResultRow={name:string;rank?:string|number;solved?:number;total?:number|null};
const textUnits=(value:string|number)=>Array.from(String(value)).reduce((length,char)=>length+(/[^\u0000-\u00ff]/.test(char)?2:1),0);
function contentWidth(header:string,values:(string|number|null|undefined)[],maximum:number){
  const longest=values.reduce<number>((length,value)=>Math.max(length,value==null?0:textUnits(value)),textUnits(header));
  const units=Math.min(maximum,longest+1.5);
  return `calc(${units}ch + 8px)`;
}

/** Size the leading cells from their text; unassigned problem columns share all free space. */
export function ResultColumns({problems,history=false,practice=false,rows=[],score=false}:{problems:string[];history?:boolean;practice?:boolean;rows?:ResultRow[];score?:boolean}) {
  const names=rows.map(row=>row.name),ranks=rows.map(row=>row.rank),solved=rows.map(row=>row.solved),totals=rows.map(row=>row.total);
  const rank=contentWidth('Rank',ranks,9),solves=contentWidth('Solved',solved,9);
  const total=contentWidth(history?'Penalty / Score':score?'Score':'Penalty',totals,17);
  const metadata=history?[contentWidth('Contest',names,28),'7.5ch','14.5ch',rank,solves,total]:practice?[contentWidth('Team',names,20)]:[rank,contentWidth('Team',names,20),solves,total];
  return <colgroup className={history?'compact-history-columns':practice?'compact-practice-columns':'compact-score-columns'}>{metadata.map((width,index)=><col key={'metadata-'+index} style={{width}}/>)}{problems.map(problem=><col key={problem}/>)}{history&&<col style={{width:'8.5ch'}}/>}</colgroup>;
}
