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
