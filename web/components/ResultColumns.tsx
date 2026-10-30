/** Reserve compact metadata columns and share the remaining width across problems. */
export function ResultColumns({problems,history=false,practice=false}:{problems:string[];history?:boolean;practice?:boolean}) {
  const metadata=history?[19,5,7,3.5,4,6.5]:practice?[24]:[4,24,5,6];
  const details=history?5:0;
  const remaining=100-metadata.reduce((total,width)=>total+width,0)-details;
  return <colgroup>{metadata.map((width,index)=><col key={'metadata-'+index} style={{width:width+'%'}}/>)}{problems.map(problem=><col key={problem} style={{width:(remaining/Math.max(1,problems.length))+'%'}}/>)}{history&&<col style={{width:details+'%'}}/>}</colgroup>;
}
