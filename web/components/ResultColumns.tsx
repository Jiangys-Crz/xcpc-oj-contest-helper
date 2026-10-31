type ResultRow={name:string;rank?:string|number;solved?:number;total?:number|null};

/** Intrinsic, unwrapped text determines metadata widths; problems share the rest. */
export function ResultColumns({problems,history=false,practice=false}:{problems:string[];history?:boolean;practice?:boolean;rows?:ResultRow[];score?:boolean}) {
  const metadata=history?6:practice?1:4;
  return <colgroup className={history?'compact-history-columns':practice?'compact-practice-columns':'compact-score-columns'}>{Array.from({length:metadata},(_,index)=><col key={'metadata-'+index} style={{width:1}}/>)}{problems.map(problem=><col key={problem}/>)}{history&&<col style={{width:1}}/>}</colgroup>;
}
