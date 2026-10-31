// Fixed injected fetch harness: no browser profile, network or real source.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

async function main(){
  const profile='01234567-89ab-cdef-0123-456789abcdef',messages=[],listeners={},values={profileId:profile},injections=[],calls=[],intervals=[];
  let tabs=[{id:7,url:'https://codeforces.com/gym/106554',incognito:false}],account='MyHandle',freshAccount='MyHandle',challenge=false,expiredAfterRead=false,opaqueRedirect=false;
  const event=name=>({addListener(fn){listeners[name]=fn;}});
  const header=()=>({href:'https://codeforces.com/profile/'+account});
  const form={getAttribute:name=>name==='method'?'post':'',querySelector:selector=>selector==='[name="csrf_token"]'?{value:'fresh-csrf'}:null,
    querySelectorAll:selector=>selector.includes('submittedProblemIndex')?[{value:'H'}]:selector.includes('programTypeId')?[{value:'89'}]:[]};
  const document={cookie:'39ce7=abc',querySelectorAll:()=>[header()]};
  class DOMParser{parseFromString(){return {querySelector:selector=>selector==='form.submit-form'?form:selector==='#header'?{outerHTML:'<div id="header">account fixture</div>'}:null,
    querySelectorAll:()=>[{getAttribute:()=>'/profile/'+freshAccount}]};}}
  const port={postMessage:value=>messages.push(value),onMessage:event('ack'),onDisconnect:event('disconnect')};
  const chrome={storage:{local:{async get(){return {...values};},async set(v){Object.assign(values,v);}}},runtime:{connectNative:()=>port,onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')},
    cookies:{getAll:async()=>[],onChanged:event('changed')},alarms:{onAlarm:event('alarm'),create(){}},
    tabs:{query:async()=>tabs},scripting:{executeScript:async spec=>{injections.push(spec);return [{result:await spec.func(...spec.args)}];}}};
  const response=(text,status=200,url='https://codeforces.com/gym/106554/my')=>({status,url,body:new Response(text).body,headers:new Headers({'content-type':'text/html'})});
  const fetch=async(url,options)=>{
    calls.push({url,options});
    if(options.method==='POST')return opaqueRedirect?{type:'opaqueredirect',status:0,url:'',headers:new Headers({'Location':'https://foreign.invalid/collect'})}:response('accepted native page');
    if(challenge)return response('<title>Just a moment...</title>',403,url);
    if(expiredAfterRead)context.testJob.expires_at=Date.now()/1000-1;
    return response('<form>fixture</form><script>window._ftaa="fresh-ftaa";window._bfaa="fresh-bfaa";</script>',200,url);
  };
  const context={chrome,navigator:{userAgent:'Fixture Chrome/150'},crypto:{randomUUID:()=>profile},setInterval(fn){intervals.push(fn);},setTimeout(){},
    URL,TextEncoder,TextDecoder,DOMParser,document,location:new URL('https://codeforces.com/gym/106554'),fetch,FormData,Blob,Date,unescape};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser_extension/background.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual([...messages[0].capabilities],['cf-request-v1']);
  intervals.at(-1)();assert.equal(messages.length,1); // A port cannot poll until its first snapshot is acknowledged.
  await listeners.ack({type:'stored'});intervals.at(-1)();assert.equal(messages.at(-1).type,'cf-request-poll');
  const job=()=>({id:'a'.repeat(32),source:'chrome',profile_id:profile,method:'POST',url:'https://codeforces.com/gym/106554/submit/H?csrf_token=old',expected_handle:'myhandle',
    data:{contestId:['106554'],submittedProblemIndex:['H'],programTypeId:['89'],csrf_token:['old'],_tta:['old'],source:['fixture-code']},expires_at:Date.now()/1000+30});
  context.testJob=job();await vm.runInContext('performCodeforcesRequest(testJob,port)',vm.createContext({...context,port}));
  assert.equal(injections[0].world,'ISOLATED');assert.deepEqual([...injections[0].target.frameIds],[0]);
  assert.equal(calls.length,2);assert.equal(calls[0].options.method,undefined);assert.equal(calls[1].options.method,'POST');
  assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.body.get('source'),'fixture-code');
  assert.equal(calls[1].options.redirect,'manual');
  assert.equal(calls[1].options.body.get('csrf_token'),'fresh-csrf');assert.equal(calls[1].options.body.get('ftaa'),'fresh-ftaa');
  assert.equal(calls[1].options.body.get('_tta'),'870');assert(!calls[1].url.includes('old'));
  assert.equal(messages.at(-1).result.status,200);
  opaqueRedirect=true;calls.length=0;
  const redirected=await context.codeforcesFetch(job());
  assert.equal(redirected.status,200);assert.equal(calls.length,3);
  assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
  assert.equal(calls[2].url,'https://codeforces.com/gym/106554/my');assert.equal(calls[2].options.redirect,'error');assert.equal(calls[2].options.body,undefined);
  assert(!calls.some(call=>call.url.includes('foreign.invalid')));opaqueRedirect=false;
  calls.length=0;account='OtherUser';context.testJob=job();
  const mismatch=await context.codeforcesFetch(context.testJob);assert.equal(mismatch.error,'account-mismatch');assert.equal(calls.length,0);
  account='MyHandle';freshAccount='OtherUser';calls.length=0;
  assert.equal((await context.codeforcesFetch(job())).error,'account-mismatch');assert.equal(calls.length,1);
  freshAccount='MyHandle';challenge=true;calls.length=0;
  assert.equal((await context.codeforcesFetch(job())).error,'verification');assert.equal(calls.length,1);
  challenge=false;expiredAfterRead=true;calls.length=0;context.testJob=job();
  assert.equal((await context.codeforcesFetch(context.testJob)).error,'expired');assert.equal(calls.length,1);
  expiredAfterRead=false;tabs=[{id:9,url:'https://codeforces.com/',incognito:true}];calls.length=0;
  await context.performCodeforcesRequest(job(),port);assert.equal(messages.at(-1).result.error,'no-tab');assert.equal(calls.length,0);
  assert(!JSON.stringify(values).includes('fixture-code'));assert(!('debugger' in chrome));
  console.log('Browser RPC ordinary-profile selection, account guard, fresh form, expiry and one POST: OK');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
