// Judge cookies stay local. Cloud account RPC is handled by the local XCPC host.
const bases = ['codeforces.com','qoj.ac','contest.ucup.ac','oj.cwnupaa.com','cwnupaa.com','ac.nowcoder.com','nowcoder.com'];
const hosts = bases.flatMap(host => [host,'www.'+host]);
const domains = new Set(hosts.flatMap(host => [host,'.'+host]));
const labels = {'Codeforces':['codeforces.com'],'QOJ':['qoj.ac','contest.ucup.ac'],
  'CWNUOJ':['oj.cwnupaa.com','cwnupaa.com'],'牛客':['ac.nowcoder.com','nowcoder.com']};
const firstPartySite = domain => 'https://' + domain.replace(/^\./,'').split('.').slice(-2).join('.');
let port = null;
let sending = false;
let counts = {};
let readErrors = [];
let rpcBusy = false;
let rpcReady = false;

// This fixed function runs in the existing page's isolated world. Jobs contain
// only validated URLs/form values; they can never provide executable script.
async function codeforcesFetch(job) {
  let sent = false;
  try {
    const allowedHost = host => ['codeforces.com','www.codeforces.com'].includes(host);
    const url = new URL(job.url);
    if (location.protocol !== 'https:' || !allowedHost(location.hostname) || !allowedHost(url.hostname) || url.protocol !== 'https:' || url.username || url.password || url.port || url.hash) throw Error();
    url.hostname = location.hostname;
    const contest = /^\/(contest|gym)\/[0-9]+(?:\/(?:my|status|submit(?:\/[A-Za-z][0-9]*)?))?\/?$/;
    if (!['GET','POST'].includes(job.method) || (job.method === 'GET' && !['/','/enter'].includes(url.pathname) && !contest.test(url.pathname) && !/^\/submission\/[0-9]+\/?$/.test(url.pathname)) || (job.method === 'POST' && !/^\/(contest|gym)\/[0-9]+\/submit(?:\/[A-Za-z][0-9]*)?\/?$/.test(url.pathname))) throw Error();
    if([...url.searchParams].some(([key,value])=>!(job.method==='GET'?['locale','mobile','csrf_token','prevId','page']:['csrf_token']).includes(key)||value.length>256))throw Error();
    const allowed = ['csrf_token','ftaa','bfaa','_tta','action','contestId','submittedProblemIndex','programTypeId','source','sourceFile','tabSize','sourceCodeConfirmed','captcha','cf-turnstile-response','g-recaptcha-response'];
    const entries = Object.entries(job.data || {});
    if (entries.some(([key,values])=>!allowed.includes(key) || !Array.isArray(values) || values.some(value=>typeof value!=='string'))) throw Error();
    if (JSON.stringify(job).length > 524288) throw Error();
    const read = async (response,headerOnly=false) => {
      const reader = response.body?.getReader();
      if (!reader) return '';
      const parts=[];let total=0;
      while (true) {const {done,value}=await reader.read();if(done)break;if(total+value.byteLength>524288){await reader.cancel();if(headerOnly){parts.push(value.slice(0,524288-total));total=524288;break;}throw Error('too-large');}total+=value.byteLength;parts.push(value);}
      const data=new Uint8Array(total);let offset=0;for(const part of parts){data.set(part,offset);offset+=part.byteLength;}
      const text=new TextDecoder().decode(data);
      if(headerOnly){const header=new DOMParser().parseFromString(text,'text/html').querySelector('#header');if(header)return '<html><body>'+header.outerHTML+'</body></html>';}
      return text;
    };
    let body;
    if (job.method === 'POST') {
      const current = [...document.querySelectorAll('#header a[href]')].map(a=>new URL(a.href,location.href).pathname).filter(path=>path.startsWith('/profile/')).map(path=>path.slice(9).replace(/\/$/,''));
      if (!current.some(handle=>handle.toLowerCase() === job.expected_handle.toLowerCase())) return {error:'account-mismatch',sent:false};
      // Confirmation selected the source/problem/compiler. Refresh native
      // hidden fields in this same browser immediately before the single POST.
      const formUrl=new URL(url);formUrl.search='';
      const before=await fetch(formUrl.href,{credentials:'same-origin',cache:'no-store',redirect:'error'});
      const html=await read(before);
      if (before.status!==200 || html.includes('cf-chl-') || html.includes('Just a moment...'))return {error:'verification',sent:false};
      const doc=new DOMParser().parseFromString(html,'text/html');
      const identities=[...doc.querySelectorAll('#header a[href]')].map(a=>new URL(a.getAttribute('href'),location.href).pathname).filter(path=>path.startsWith('/profile/')).map(path=>path.slice(9).replace(/\/$/,''));
      if (!identities.some(handle=>handle.toLowerCase()===job.expected_handle.toLowerCase())) return {error:'account-mismatch',sent:false};
      const form=doc.querySelector('form.submit-form');
      if(!form || (form.getAttribute('method')||'').toLowerCase()!=='post')return {error:'verification',sent:false};
      const action=new URL(form.getAttribute('action')||formUrl.href,formUrl.href);
      if(action.origin!==location.origin || !new RegExp('^/'+url.pathname.split('/').slice(1,3).join('/')+'/submit(?:/[A-Za-z][0-9]*)?/?$').test(action.pathname))throw Error();
      const fields=Object.fromEntries(entries.map(([key,values])=>[key,[...values]]));
      const pieces=url.pathname.split('/').filter(Boolean);
      if(fields.contestId && (fields.contestId.length!==1||fields.contestId[0]!==pieces[1]) || pieces.length===4 && fields.submittedProblemIndex?.[0]!==pieces[3].toUpperCase())throw Error();
      for(const key of ['csrf_token','ftaa','bfaa','action']) {const value=form.querySelector('[name="'+key+'"]')?.value;if(value)fields[key]=[value];}
      for(const key of ['ftaa','bfaa']) {const match=html.match(new RegExp('(?:window\\.)?_'+key+'\\s*=\\s*["\']([^"\'\\r\\n]{1,256})["\']'));if(match)fields[key]=[match[1]];}
      delete fields._tta;
      const signature=document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('39ce7='));
      if(signature){let value=unescape(signature.slice(6)),result=0;for(let i=0;i<value.length;i++){result=(result+(i+1)*(i+2)*value.charCodeAt(i))%1009;if(i%3===0)result++;if(i%2===0)result*=2;if(i)result-=Math.floor(value.charCodeAt(Math.floor(i/2))/2)*(result%5);result%=1009;if(result<0)result+=1009;}if(result)fields._tta=[String(result)];}
      const problem=fields.submittedProblemIndex?.[0],compiler=fields.programTypeId?.[0];
      if(!fields.csrf_token?.[0] || !problem || !compiler || !([...form.querySelectorAll('select[name="submittedProblemIndex"] option')].some(o=>o.value===problem)||form.querySelector('input[name="submittedProblemIndex"]')?.value===problem) || ![...form.querySelectorAll('select[name="programTypeId"] option')].some(o=>o.value===compiler))throw Error();
      body=new FormData();for(const [key,values] of Object.entries(fields))for(const value of values)body.append(key,value);
      if(job.file){if(typeof job.file.name!=='string'||/[\\/\r\n]/.test(job.file.name)||typeof job.file.content!=='string')throw Error();body.delete('sourceFile');body.append('sourceFile',new Blob([job.file.content],{type:'text/plain'}),job.file.name);}
      url.search='';
      url.searchParams.set('csrf_token',fields.csrf_token[0]);
      if(Date.now()/1000>=job.expires_at)return {error:'expired',sent:false};
      sent=true;
    }
    let response=await fetch(url.href,{method:job.method,body,credentials:'same-origin',cache:'no-store',redirect:job.method==='POST'?'manual':'follow'});
    if(job.method==='POST' && (response.type==='opaqueredirect' || response.status>=300&&response.status<400)) {
      // A 307/308 must never forward source to another origin. Do not inspect
      // or follow Location: read only this contest's fixed My submissions page.
      const parts=url.pathname.split('/').filter(Boolean);
      const mine=location.origin+'/'+parts.slice(0,2).join('/')+'/my';
      response=await fetch(mine,{method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error'});
    }
    if(!allowedHost(new URL(response.url).hostname))throw Error();
    return {status:response.status,url:response.url,text:await read(response,job.method==='GET'&&url.pathname==='/'),headers:{'Content-Type':response.headers.get('content-type')||'','Date':response.headers.get('date')||''},sent};
  } catch(error) {return {error:error?.message==='too-large'?'too-large':'browser-error',sent};}
}

async function performCodeforcesRequest(job, connection) {
  if (!job || rpcBusy) return;
  rpcBusy=true;
  let result={error:'browser-error',sent:false};
  try {
    const saved=await chrome.storage.local.get('profileId');
    const source=navigator.userAgent.includes('Edg/')?'edge':'chrome';
    if(job.profile_id!==saved.profileId || job.source!==source)throw Error();
    const tabs=await chrome.tabs.query({url:['https://codeforces.com/*','https://www.codeforces.com/*']});
    const ordinary=tabs.filter(tab=>!tab.incognito && tab.id!==undefined && ['codeforces.com','www.codeforces.com'].includes(new URL(tab.url).hostname));
    const tab=ordinary.find(tab=>new URL(tab.url).hostname===new URL(job.url).hostname)||ordinary[0];
    if(!tab) result={error:'no-tab',sent:false};
    else {
      // No tab navigation, special browser profile, debugger or page-supplied JS.
      const execution=await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[0]},world:'ISOLATED',func:codeforcesFetch,args:[job]});
      result=execution?.[0]?.result||result;
      const cookies=await chrome.cookies.getAll({domain:'codeforces.com'});
      result.cookies=cookies.filter(c=>['codeforces.com','www.codeforces.com'].includes(c.domain.replace(/^\./,''))&&c.path==='/'&&!c.incognito&&(!c.partitionKey||!c.partitionKey.hasCrossSiteAncestor&&c.partitionKey.topLevelSite==='https://codeforces.com'));
      if(new TextEncoder().encode(JSON.stringify(result)).byteLength>900000)result={error:'too-large',sent:job.method==='POST'};
    }
  } catch (_) {result={error:'browser-error',sent:job.method==='POST'};}
  try {connection.postMessage({type:'cf-request-result',id:job.id,result});}catch (_) {/* A lost result never replays the request. */}
  rpcBusy=false;
}

async function synchronize() {
  if (sending) return;
  sending = true;
  try {
    let saved = await chrome.storage.local.get('profileId');
    const profileId = saved.profileId || crypto.randomUUID();
    if (!saved.profileId) await chrome.storage.local.set({profileId});
    if (!port) {
      port = chrome.runtime.connectNative('com.xcpc.browser');
      const connection=port;
      connection.onMessage.addListener(async message => {
        if(port!==connection)return;
        if (message.type === 'stored') {rpcReady=true;await chrome.storage.local.set({connected:true, updatedAt:Date.now(), error:'',
          counts, readErrors, profile:profileId.slice(-8)});}
        if (message.type === 'cf-request-job' && message.job) await performCodeforcesRequest(message.job,connection);
      });
      connection.onDisconnect.addListener(async () => {
        if(port!==connection)return;
        // Consume the platform error; never copy cookie payloads into diagnostics.
        void chrome.runtime.lastError;
        port = null;
        rpcReady = false;
        await chrome.storage.local.set({connected:false, error:'本地连接未建立。请先安装或更新 XCPC，再点击同步。'});
        setTimeout(synchronize, 3000);
      });
    }
    const queries = hosts.flatMap(host => [{domain:host},
      {domain:host,partitionKey:{topLevelSite:firstPartySite(host),hasCrossSiteAncestor:false}}]);
    const results = await Promise.allSettled(queries.map(query => chrome.cookies.getAll(query)));
    const all = results.flatMap(result => result.status === 'fulfilled' ? result.value : []);
    readErrors = queries.filter((query,index) => !query.partitionKey && results[index].status === 'rejected').map(query=>query.domain);
    if (chrome.permissions?.contains) {
      // Chromium checks non-Secure cookies against http:// permissions even
      // when the website itself is used over HTTPS. Cookie reads need both.
      const permissions = await Promise.allSettled(hosts.map(host => chrome.permissions.contains({origins:['http://'+host+'/*','https://'+host+'/*']})));
      readErrors.push(...hosts.filter((host,index)=>permissions[index].status==='fulfilled' && !permissions[index].value));
    }
    const selected = new Map();
    for (const c of all) {
      if (!domains.has(c.domain) || c.path !== '/' || c.incognito) continue;
      if (c.partitionKey && (c.partitionKey.hasCrossSiteAncestor || c.partitionKey.topLevelSite !== firstPartySite(c.domain))) continue;
      selected.set(c.domain + '\n' + c.name + '\n' + c.path + '\n' + (c.partitionKey?.topLevelSite || ''), {
        domain:c.domain, name:c.name, value:c.value, path:c.path,
        ...(c.partitionKey ? {partitionKey:c.partitionKey} : {}),
        ...(c.expirationDate === undefined ? {} : {expirationDate:c.expirationDate})
      });
    }
    counts = Object.fromEntries(Object.entries(labels).map(([label,sites]) => [label,
      [...selected.values()].filter(c=>sites.includes(c.domain.replace(/^\./,'').replace(/^www\./,''))).length]));
    port.postMessage({type:'snapshot', source:navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome',
      profile_id:profileId, user_agent:navigator.userAgent, cookies:[...selected.values()], read_errors:readErrors,
      capabilities:chrome.scripting?.executeScript && chrome.tabs?.query ? ['cf-request-v1'] : []});
  } catch (_) {
    await chrome.storage.local.set({connected:false, error:'同步暂未完成，请检查 XCPC 安装及扩展权限。'});
  } finally { sending = false; }
}

chrome.cookies.onChanged.addListener(change => {
  if (domains.has(change.cookie.domain)) synchronize();
});
chrome.runtime.onInstalled.addListener(synchronize);
chrome.runtime.onStartup.addListener(synchronize);
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (['cloud-login','cloud-status','cloud-logout'].includes(message.type)) {
    chrome.runtime.sendNativeMessage('com.xcpc.browser',message,response => {
      const error = chrome.runtime.lastError;
      reply(error ? {error:'本地连接未建立，请先更新 XCPC。'} : response);
    });
    return true;
  }
  if (message.type === 'sync') { synchronize().then(() => reply({ok:true})); return true; }
});
chrome.alarms.onAlarm.addListener(synchronize);
chrome.alarms.create('xcpc-login', {periodInMinutes:0.5});
setInterval(synchronize, 10000);
setInterval(()=>{if(port&&rpcReady&&!rpcBusy)try{port.postMessage({type:'cf-request-poll'});}catch(_){}},1000);
synchronize();
