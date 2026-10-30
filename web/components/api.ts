'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
export class ApiError extends Error { constructor(message:string,public status:number){ super(message); this.name='ApiError'; } }
export async function api<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
  const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',signal,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const json=await response.json().catch(()=>({error:'服务器暂时没有返回有效数据，请稍后重试。'}));
  if(!response.ok) throw new ApiError(json.error||'请求未完成，请稍后重试。',response.status);
  return json.data as T;
}
export function useResource<T>(path:string|null,poll=0){
  const [data,setData]=useState<T>(); const [loading,setLoading]=useState(!!path); const [error,setError]=useState(''); const version=useRef(0);
  const load=useCallback(async(quiet=false)=>{if(!path)return; const current=++version.current; if(!quiet)setLoading(true); try{ const value=await api<T>(path); if(current===version.current){setData(value);setError('');}}catch(e){if(current===version.current)setError(e instanceof Error?e.message:'加载失败。');}finally{if(current===version.current)setLoading(false);}},[path]);
  useEffect(()=>{setData(undefined);setError('');setLoading(!!path);void load(); const timer=poll?setInterval(()=>{if(document.visibilityState==='visible')void load(true);},poll):undefined;return()=>{version.current++;if(timer)clearInterval(timer);};},[load,path,poll]);
  return {data,loading,error,reload:()=>load(true)};
}
export const errorMessage=(e:unknown)=>e instanceof Error?e.message:'操作未完成，请稍后重试。';
