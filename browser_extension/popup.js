async function update() {
  const state = await chrome.storage.local.get(['connected','updatedAt','error','counts','readErrors','profile']);
  document.getElementById('status').textContent = state.connected
    ? '已连接本机 XCPC。最近同步：' + new Date(state.updatedAt).toLocaleString()
    : state.error || '请先安装 XCPC，然后点击同步。';
  document.getElementById('sites').textContent = Object.entries(state.counts || {}).map(([site,count])=>site+': '+count+' Cookie').join(' · ')
    + (state.profile ? ' · 配置 '+state.profile : '')
    + (state.readErrors?.length ? '。部分网站未授权或读取失败，请在扩展详情检查站点访问权限。' : '');
}
document.getElementById('sync').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type:'sync'}); setTimeout(update, 500);
});
update();

async function cloudUpdate(message={type:'cloud-status'}) {
  const output=document.getElementById('cloud-status');
  try {
    const result=await chrome.runtime.sendMessage(message);
    output.textContent=result?.error || (result?.user ? '已登录：'+result.user.username+' · '+result.user.realname : '尚未登录。可使用协会账号或邮箱登录。');
    if(result?.user) document.getElementById('cloud-account').value=result.user.useraccount;
  } catch (_) { output.textContent='无法连接本机 XCPC，请先更新并安装客户端。'; }
}
document.getElementById('cloud-login').addEventListener('submit',async event=>{
  event.preventDefault();
  document.getElementById('cloud-status').textContent='正在登录…';
  const input=document.getElementById('cloud-password');
  const password=input.value; input.value='';
  await cloudUpdate({type:'cloud-login',account:document.getElementById('cloud-account').value.trim(),password});
});
document.getElementById('cloud-logout').addEventListener('click',()=>cloudUpdate({type:'cloud-logout'}));
cloudUpdate();
