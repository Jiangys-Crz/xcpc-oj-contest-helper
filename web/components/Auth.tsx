'use client';
import {FormEvent,useState} from 'react';
import {ArrowRight,Eye,EyeOff,LogIn} from 'lucide-react';
import {api,errorMessage} from './api';
import {ErrorBox,PageHeader,displayUser} from './shared';
import type {User} from './types';
export function Login({onLogin}:{onLogin:(user:User)=>void}){
  const [account,setAccount]=useState('');
  const [password,setPassword]=useState('');
  const [visible,setVisible]=useState(false);
  const [admin,setAdmin]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function submit(e:FormEvent){
    e.preventDefault();
    if(busy)return;
    setBusy(true);setError('');
    try{
      const result=await api<{user:User}>('/api/auth/'+(admin?'admin-login':'login'),'POST',{account:account.trim(),password});
      setPassword('');onLogin(result.user);
    }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  }
  return <main className="login-layout login-refined">
    <section className="login-brand-panel">
      <div className="login-brand-top"><span className="login-monogram" aria-hidden="true">VP</span><span className="login-association">CWNU<br/>PROGRAMMING ASSOCIATION</span></div>
      <div className="login-wordmark"><span className="login-wordmark-rule"/><h1>CWNU <span>XCPC Vp System</span></h1><p>Virtual Participation</p></div>
      <div className="login-competition-art" aria-hidden="true">
        <div className="login-art-caption"><span>VIRTUAL PARTICIPATION</span><i/><i/><i/></div>
        <table className="login-preview-table"><thead><tr>{'ABCDEF'.split('').map(label=><th key={label}>{label}</th>)}</tr></thead><tbody><tr><td className="preview-accepted"><span>1 try</span><small>18</small></td><td className="preview-rejected"><span>2 tries</span></td><td className="preview-accepted"><span>1 try</span><small>42</small></td><td className="preview-upsolved"><span>3 tries</span><small>补题 AC</small></td><td className="preview-empty">—</td><td className="preview-empty">—</td></tr></tbody></table>
        <div className="login-art-footer"><span/><span/><span/></div>
      </div>
      <div className="login-brand-bottom"><span>西华师范大学程序设计协会</span><span className="login-square-mark" aria-hidden="true"/></div>
    </section>
    <section className="login-card">
      <div className="login-form-heading"><span className="login-form-icon"><LogIn size={19}/></span><div><h2>{admin?'管理员登录':'账号登录'}</h2><p>{admin?'使用系统管理员账号':'使用协会官网账号或邮箱'}</p></div></div>
      <form onSubmit={submit} className="form-stack">
        <label>{admin?'管理员账号':'账号或邮箱'}<input name="account" autoComplete="username" required disabled={busy} value={account} onChange={e=>setAccount(e.target.value)} placeholder={admin?'输入管理员账号':'输入账号或邮箱'}/></label>
        <label>密码<div className="login-password"><input name="password" type={visible?'text':'password'} autoComplete="current-password" required disabled={busy} value={password} onChange={e=>setPassword(e.target.value)} placeholder="输入密码"/><button type="button" disabled={busy} className="login-password-toggle" aria-label={visible?'隐藏密码':'显示密码'} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={17}/>:<Eye size={17}/>}</button></div></label>
        <ErrorBox message={error}/>
        <button className="button button-primary button-block" disabled={busy}>{busy?'正在验证…':'登录'}<ArrowRight size={17}/></button>
      </form>
      <div className="login-switch"><button className="text-button muted" disabled={busy} onClick={()=>{setAdmin(!admin);setError('');setPassword('');setVisible(false);}}>{admin?'返回协会账号登录':'管理员登录'}</button><a href="https://www.cwnupaa.com" target="_blank" rel="noreferrer">协会官网 ↗</a></div>
    </section>
  </main>;
}
export function Account({user,onSwitch}:{user:User;onSwitch:()=>void}){return <><PageHeader eyebrow="ACCOUNT" title="我的账号"/><section className="card profile-card"><div className="profile-title"><div className="avatar avatar-large">{(user.realname||user.username||user.useraccount).slice(0,1)}</div><div><h2>{displayUser(user)}</h2><p>{user.role==='admin'?'超级管理员':'协会成员'}</p></div></div><dl className="detail-grid">{[['协会官网 ID',user.externalId||''],['用户账号',user.useraccount],['用户昵称',user.username],['真实姓名',user.realname],['学号',user.numberid],['邮箱地址',user.email]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'未提供'}</dd></div>)}</dl><div className="card-bottom"><p className="muted">终端可运行 <code>xcpc login</code> 登录或切换账号。</p><button className="button button-secondary" onClick={onSwitch}>切换账号</button></div></section></>}
