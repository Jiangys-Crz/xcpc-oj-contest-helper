'use client';
import {useLayoutEffect,useRef,useState} from 'react';

/** Keep the complete table visible when its readable column widths exceed the viewport. */
export function TableFit({minimumWidth,children,className=''}:{minimumWidth:number;children:React.ReactNode;className?:string}){
  const viewport=useRef<HTMLDivElement>(null),surface=useRef<HTMLDivElement>(null);
  const [width,setWidth]=useState(0),[height,setHeight]=useState(0);
  useLayoutEffect(()=>{
    const outer=viewport.current,inner=surface.current;if(!outer||!inner)return;
    const measure=()=>{setWidth(outer.clientWidth);setHeight(inner.offsetHeight);};
    measure();const observer=new ResizeObserver(measure);observer.observe(outer);observer.observe(inner);
    return()=>observer.disconnect();
  },[]);
  const contentWidth=Math.max(width,minimumWidth),scale=width?Math.min(1,width/contentWidth):1;
  return <div ref={viewport} className={'table-fit-viewport '+className} data-table-scale={scale.toFixed(3)} style={{height:height?Math.ceil(height*scale):undefined}}>
    <div ref={surface} className="table-fit-content" style={{width:contentWidth,transform:`scale(${scale})`,visibility:width?'visible':'hidden'}}>{children}</div>
  </div>;
}
