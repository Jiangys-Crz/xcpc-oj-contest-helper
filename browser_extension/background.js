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

async function synchronize() {
  if (sending) return;
  sending = true;
  try {
    let saved = await chrome.storage.local.get('profileId');
    const profileId = saved.profileId || crypto.randomUUID();
    if (!saved.profileId) await chrome.storage.local.set({profileId});
    if (!port) {
      port = chrome.runtime.connectNative('com.xcpc.browser');
      port.onMessage.addListener(async message => {
        if (message.type === 'stored') await chrome.storage.local.set({connected:true, updatedAt:Date.now(), error:'',
          counts, readErrors, profile:profileId.slice(-8)});
      });
      port.onDisconnect.addListener(async () => {
        // Consume the platform error; never copy cookie payloads into diagnostics.
        void chrome.runtime.lastError;
        port = null;
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
      profile_id:profileId, user_agent:navigator.userAgent, cookies:[...selected.values()], read_errors:readErrors});
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
synchronize();
