// No page scripts or network uploads: only judge cookies -> local Native Messaging.
const urls = ['https://codeforces.com/', 'https://qoj.ac/', 'https://oj.cwnupaa.com/',
              'https://cwnupaa.com/', 'https://ac.nowcoder.com/', 'https://nowcoder.com/'];
const domains = new Set(['codeforces.com','.codeforces.com','qoj.ac','.qoj.ac',
  'oj.cwnupaa.com','.oj.cwnupaa.com','cwnupaa.com','.cwnupaa.com',
  'ac.nowcoder.com','.ac.nowcoder.com','nowcoder.com','.nowcoder.com']);
let port = null;
let sending = false;

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
        if (message.type === 'stored') await chrome.storage.local.set({connected:true, updatedAt:Date.now(), error:''});
      });
      port.onDisconnect.addListener(async () => {
        // Consume the platform error; never copy cookie payloads into diagnostics.
        void chrome.runtime.lastError;
        port = null;
        await chrome.storage.local.set({connected:false, error:'本地连接未建立。请先安装或更新 XCPC，再点击同步。'});
        setTimeout(synchronize, 3000);
      });
    }
    const all = (await Promise.all(urls.map(url => chrome.cookies.getAll({url})))).flat();
    const selected = new Map();
    for (const c of all) {
      if (!domains.has(c.domain) || c.path !== '/' || c.partitionKey || c.incognito) continue;
      selected.set(c.domain + '\n' + c.name + '\n' + c.path, {
        domain:c.domain, name:c.name, value:c.value, path:c.path,
        ...(c.expirationDate === undefined ? {} : {expirationDate:c.expirationDate})
      });
    }
    port.postMessage({type:'snapshot', source:navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome',
      profile_id:profileId, user_agent:navigator.userAgent, cookies:[...selected.values()]});
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
  if (message.type === 'sync') { synchronize().then(() => reply({ok:true})); return true; }
});
chrome.alarms.onAlarm.addListener(synchronize);
chrome.alarms.create('xcpc-login', {periodInMinutes:0.5});
setInterval(synchronize, 10000);
synchronize();
