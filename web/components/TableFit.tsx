'use client';
import {useLayoutEffect,useRef,useState} from 'react';

/** Fit complete, unwrapped content while preserving its natural column widths. */
export function TableFit({minimumWidth=0,children,className='',compact=false,center=false,widthFraction,fullHeader=false}:{minimumWidth?:number;children:React.ReactNode;className?:string;compact?:boolean;center?:boolean;widthFraction?:number;fullHeader?:boolean}){
  const viewport=useRef<HTMLDivElement>(null),surface=useRef<HTMLDivElement>(null);
  const [width,setWidth]=useState(0),[height,setHeight]=useState(0),[naturalWidth,setNaturalWidth]=useState(0),[headerHeight,setHeaderHeight]=useState(0);
  useLayoutEffect(()=>{
    const outer=viewport.current,inner=surface.current;if(!outer||!inner)return;
    const measure=()=>{
      setWidth(outer.clientWidth);
      const table=inner.querySelector('table');
      const oldWidth=inner.style.width,oldTransform=inner.style.transform,oldTableWidth=table?.style.width;
      // Measure intrinsic width independently of the last viewport size, so a
      // wide window cannot leave a narrow one with an unnecessarily small scale.
      inner.style.width='max-content';
      inner.style.transform='none';
      if(table)table.style.width='max-content';
      const problemLines=table?.querySelectorAll('.problem-cell span,.problem-cell small');
      if(problemLines?.length){
        let problemWidth=58;
        problemLines.forEach(line=>{const range=document.createRange();range.selectNodeContents(line);problemWidth=Math.max(problemWidth,Math.ceil(range.getBoundingClientRect().width)+8);});
        inner.style.setProperty('--fit-problem-width',problemWidth+'px');
      }
      setNaturalWidth(Math.ceil(inner.scrollWidth));
      inner.style.width=oldWidth;
      inner.style.transform=oldTransform;
      if(table)table.style.width=oldTableWidth||'';
      setHeight(inner.offsetHeight);
      setHeaderHeight(table?.tHead?(table.tHead.offsetHeight+(parseFloat(getComputedStyle(table).borderTopWidth)||0)):0);
    };
    measure();const observer=new ResizeObserver(measure);observer.observe(outer);observer.observe(inner);
    const mutations=new MutationObserver(measure);mutations.observe(inner,{childList:true,subtree:true,characterData:true});
    document.fonts.ready.then(measure);
    return()=>{observer.disconnect();mutations.disconnect();};
  },[]);
  const preferredWidth=widthFraction===undefined?(compact?0:width):width*widthFraction;
  const contentWidth=Math.max(preferredWidth,minimumWidth,naturalWidth),scale=width&&contentWidth?Math.min(1,width/contentWidth):1;
  return <div ref={viewport} className={'table-fit-viewport '+className} data-table-scale={scale.toFixed(3)} style={{height:height?Math.ceil(height*scale):undefined}}>
    {fullHeader&&headerHeight>0&&<div className="table-fit-header-band" aria-hidden="true" style={{height:headerHeight*scale}}/>}
    <div ref={surface} className="table-fit-content" style={{width:contentWidth||'max-content',transform:`scale(${scale})`,left:center?Math.max(0,(width-contentWidth*scale)/2):undefined,visibility:width?'visible':'hidden'}}>{children}</div>
  </div>;
}
