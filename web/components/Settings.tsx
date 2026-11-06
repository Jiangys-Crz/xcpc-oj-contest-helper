'use client';
import {FormEvent,useEffect,useState} from 'react';
import {KeyRound,Plus,Save,ShieldCheck,Trash2} from 'lucide-react';
import {api,errorMessage,useResource} from './api';
import {CopyButton,dateTime,ErrorBox,Loading,PageHeader,SuccessBox} from './shared';
import styles from './Settings.module.css';

interface LoginSettings {externalBaseUrl:string;externalAppKey?:string;externalAppKeyConfigured?:boolean;configured?:boolean;appKeyConfigured?:boolean}
interface ApiKey {id:string;name:string;prefix:string;createdAt:string;lastUsedAt:string|null;revokedAt:string|null}
interface CreatedApiKey extends ApiKey {apiKey:string}

export function ApiKeySettings(){
  const result=useResource<ApiKey[]>('/api/admin/api-keys');
  const [name,setName]=useState('');
  const [created,setCreated]=useState<CreatedApiKey|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [success,setSuccess]=useState('');
  async function create(e:FormEvent){
    e.preventDefault();setBusy(true);setError('');setSuccess('');
    try{
      const key=await api<CreatedApiKey>('/api/admin/api-keys','POST',{name:name.trim()});
      setCreated(key);setName('');await result.reload();
    }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  }
  async function revoke(key:ApiKey){
    if(!window.confirm(`撤销「${key.name}」的 API Key？使用此密钥的调用将立即失效。`))return;
    setBusy(true);setError('');setSuccess('');
    try{
      await api('/api/admin/api-keys/'+key.id,'DELETE');
      if(created?.id===key.id)setCreated(null);
      setSuccess('API Key 已撤销。');await result.reload();
    }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  }
  return <section className="card settings-card">
    <div className="section-heading"><h2><KeyRound size={19}/>历史 VP 数据 API Key</h2><span className="badge badge-neutral">只读查询</span></div>
    <div className={'form-stack settings-form '+styles.content}>
      <p className="muted">允许外部系统使用协会官网 ID 和原站比赛 URL 查询已结束的 VP，包括个人参赛和组队参赛。每个密钥均可查询所有已绑定用户，请仅提供给可信的调用方。</p>
      <form className={styles.createForm} onSubmit={create}>
        <label>密钥名称<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：协会官网训练统计"/></label>
        <button className="button button-primary" disabled={busy||!!created||!name.trim()}><Plus size={16}/>{busy?'正在处理…':'添加 API Key'}</button>
      </form>
      <ErrorBox message={error}/><SuccessBox message={success}/>
      {created&&<div className={styles.newKey} role="status">
        <strong>API Key 已创建，请现在保存</strong>
        <p>完整密钥仅显示这一次，关闭后无法再次查看。</p>
        <label>完整 API Key<input readOnly value={created.apiKey} autoComplete="off" spellCheck={false} onFocus={e=>e.target.select()}/></label>
        <div className={styles.keyActions}><CopyButton value={created.apiKey} label="复制 API Key"/><button className="button button-secondary button-small" onClick={()=>setCreated(null)}>已保存，关闭</button></div>
      </div>}
      <ErrorBox message={result.error} onRetry={result.reload}/>
      {result.loading?<Loading/>:result.data?.length?<div className="table-scroll"><table className={'data-table '+styles.keysTable}>
        <thead><tr><th className="text-left">名称 / 密钥前缀</th><th>创建时间</th><th>最近调用</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{result.data.map(key=><tr key={key.id}>
          <td className="text-left"><strong>{key.name}</strong><code className="row-subtitle">{key.prefix}…</code></td>
          <td>{dateTime(key.createdAt)}</td><td>{key.lastUsedAt?dateTime(key.lastUsedAt):'尚未调用'}</td>
          <td><span className={'badge '+(key.revokedAt?'badge-neutral':'badge-configured')}>{key.revokedAt?'已撤销':'有效'}</span></td>
          <td>{key.revokedAt?'—':<button className="button button-secondary button-small" disabled={busy} onClick={()=>void revoke(key)}><Trash2 size={14}/>撤销</button>}</td>
        </tr>)}</tbody>
      </table></div>:!result.error&&<p className="muted">尚未添加 API Key。</p>}
      <p className="form-hint">接口：<code>GET /api/external/vp-history</code><br/>查询参数：<code>externalId</code>（协会官网 ID）、<code>contestUrl</code>（原站比赛 URL）。在请求头中传入 <code>Authorization: Bearer API_KEY</code>。只返回已同步到云端且已结束的 VP，同一比赛多次参赛会分别返回。</p>
    </div>
  </section>;
}

export function Settings(){
  const result=useResource<LoginSettings>('/api/admin/settings');
  const [base,setBase]=useState('');
  const [key,setKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [success,setSuccess]=useState('');
  useEffect(()=>{if(result.data)setBase(result.data.externalBaseUrl||'https://www.cwnupaa.com/api');},[result.data]);
  async function save(e:FormEvent){
    e.preventDefault();setBusy(true);setError('');setSuccess('');
    try{
      await api('/api/admin/settings','PUT',{externalBaseUrl:base.trim(),...(key.trim()?{externalAppKey:key.trim()}:{})});
      setKey('');setSuccess('网站设置已保存。');await result.reload();
    }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  }
  if(result.loading)return <Loading/>;
  const configured=!!(result.data?.externalAppKeyConfigured||result.data?.configured||result.data?.appKeyConfigured||result.data?.externalAppKey);
  return <><PageHeader eyebrow="ADMINISTRATION" title="网站设置"/>
    <ErrorBox message={result.error} onRetry={result.reload}/>
    <section className="card settings-card">
      <div className="section-heading"><h2><ShieldCheck size={19}/>协会外部登录</h2><span className={'badge '+(configured?'badge-configured':'badge-neutral')}>{configured?'已配置密钥':'尚未配置密钥'}</span></div>
      <form className="form-stack settings-form" onSubmit={save}>
        <label>协会 API 基础地址<input type="url" required value={base} onChange={e=>setBase(e.target.value)} placeholder="https://www.cwnupaa.com/api"/></label>
        <label>外部应用 AppKey<div className="input-with-icon"><KeyRound size={17}/><input type="password" autoComplete="new-password" spellCheck={false} value={key} onChange={e=>setKey(e.target.value)} placeholder={configured?'已配置；留空保留当前密钥':'输入协会分配的 AppKey'}/></div></label>
        <p className="form-hint">留空保留已配置密钥。</p><ErrorBox message={error}/><SuccessBox message={success}/>
        <div className="form-actions"><button className="button button-primary" disabled={busy||!base}><Save size={16}/>{busy?'正在保存…':'保存设置'}</button></div>
      </form>
    </section>
    <ApiKeySettings/>
  </>;
}
