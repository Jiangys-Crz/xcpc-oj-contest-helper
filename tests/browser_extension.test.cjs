// Browser API harness: no personal profile, credentials or judge requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function test() {
  const listeners = {};
  const event = name => ({addListener(fn) {listeners[name] = fn;}});
  const values = {};
  const messages = [];
  const pending = [];
  let cookies = [
    {domain:'.qoj.ac',name:'sid',value:'synthetic-only',path:'/'},
    {domain:'.nowcoder.com',name:'t',value:'synthetic-parent',path:'/'},
    {domain:'unrelated.test',name:'bad',value:'exclude',path:'/'},
    {domain:'qoj.ac',name:'partition',value:'exclude',path:'/',partitionKey:{}},
    {domain:'qoj.ac',name:'private',value:'exclude',path:'/',incognito:true},
    {domain:'qoj.ac',name:'path',value:'exclude',path:'/private'},
  ];
  const port = {onMessage:event('ack'),onDisconnect:event('disconnect'),postMessage(value) {messages.push(value);}};
  const chrome = {
    storage:{local:{async get() {return {...values};},async set(next) {Object.assign(values,next);}}},
    runtime:{connectNative(name) {assert.equal(name,'com.xcpc.browser');return port;},
      onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')},
    cookies:{async getAll({domain}) {assert.ok(domain.includes('.'));return cookies;},onChanged:event('changed')},
    permissions:{async contains({origins}) {
      assert.equal(origins.length,2);
      assert.ok(origins.some(origin=>origin.startsWith('http://')));
      assert.ok(origins.some(origin=>origin.startsWith('https://')));
      return true;
    }},
    alarms:{onAlarm:event('alarm'),create(name,options) {assert.equal(name,'xcpc-login');assert.equal(options.periodInMinutes,.5);}},
  };
  const context = {chrome,navigator:{userAgent:'Synthetic Edg/150'},crypto:{randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef'},
    setTimeout:fn=>pending.push(fn),setInterval:fn=>{listeners.interval=fn;}};
  const script = fs.readFileSync(path.join(__dirname,'../browser_extension/background.js'),'utf8');
  vm.runInNewContext(script,context);
  const flush = () => new Promise(resolve=>setImmediate(resolve));
  await flush();
  assert.equal(messages.length,1);
  const sent = messages[0];
  assert.equal(sent.source,'edge');
  assert.equal(sent.cookies.length,2);  // getAll duplicates across URLs must be deduplicated.
  assert.equal(sent.cookies[0].value,'synthetic-only');
  assert.equal(sent.profile_id,values.profileId);
  await listeners.ack({type:'stored'});
  assert.equal(values.connected,true);
  assert.ok(!JSON.stringify(values).includes('synthetic-only')); // Cookies never enter extension storage.

  cookies=[{domain:'www.codeforces.com',name:'sid',value:'cf-www',path:'/'},
    {domain:'codeforces.com',name:'partition',value:'first-party',path:'/',
      partitionKey:{topLevelSite:'https://codeforces.com',hasCrossSiteAncestor:false}},
    {domain:'codeforces.com',name:'cross-site',value:'exclude',path:'/',
      partitionKey:{topLevelSite:'https://codeforces.com',hasCrossSiteAncestor:true}}];
  listeners.changed({cookie:{domain:'www.codeforces.com'}});
  await flush();
  assert.equal(messages.at(-1).cookies.length,2);
  await listeners.ack({type:'stored'});
  assert.equal(values.counts.Codeforces,2);
  chrome.cookies.getAll=async({domain})=>{if(domain==='codeforces.com')throw Error('permission denied');return cookies;};
  listeners.changed({cookie:{domain:'qoj.ac'}});
  await flush();
  assert.ok(messages.at(-1).read_errors.includes('codeforces.com'));
  assert.equal(messages.at(-1).cookies.length,2); // A failed site read must not drop successful site reads.

  cookies = [];
  listeners.changed({cookie:{domain:'qoj.ac'}});
  await flush();
  assert.equal(messages.at(-1).cookies.length,0);  // Logout replaces, rather than merges, the old snapshot.
  const count = messages.length;
  listeners.changed({cookie:{domain:'unrelated.test'}});
  await flush();
  assert.equal(messages.length,count);
  await listeners.disconnect();
  assert.equal(values.connected,false);
  assert.ok(pending.length);
  await pending.shift()();
  await flush();
  assert.ok(messages.length>count);
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../browser_extension/manifest.json')));
  assert.equal(manifest.incognito,'not_allowed');
  assert.ok(!manifest.host_permissions.some(host=>host.includes('*://')||host.includes('<all_urls>')));
  assert.ok(!manifest.permissions.includes('debugger'));
  for (const origin of manifest.host_permissions.filter(origin=>origin.startsWith('https://'))) {
    assert.ok(manifest.host_permissions.includes(origin.replace('https://','http://')));
  }
  console.log('Chrome / Edge extension synchronization, logout, filtering and reconnect: OK');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
