async function update() {
  const state = await chrome.storage.local.get(['connected','updatedAt','error']);
  document.getElementById('status').textContent = state.connected
    ? '已连接本机 XCPC。最近同步：' + new Date(state.updatedAt).toLocaleString()
    : state.error || '请先安装 XCPC，然后点击同步。';
}
document.getElementById('sync').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type:'sync'}); setTimeout(update, 500);
});
update();
