'use strict';
const csrf = document.querySelector('meta[name="bridge-csrf"]').content;
const statusElement = document.getElementById('status');
const qrImage = document.getElementById('qr');
const generateButton = document.getElementById('generate');
const logoutButton = document.getElementById('logout');
let timer = null;
let attempt = 0;

async function api(path, body = {}) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Bridge-CSRF': csrf },
    body: JSON.stringify(body), cache: 'no-store',
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '连接暂时失败，请稍后重试。');
  return result;
}
function stopPolling() { attempt += 1; clearTimeout(timer); timer = null; }
function showStatus(result) {
  logoutButton.hidden = !result.logged_in;
  generateButton.textContent = result.logged_in ? '重新扫码登录' : '生成登录二维码';
  if (result.logged_in) {
    stopPolling(); qrImage.hidden = true;
    statusElement.textContent = '已连接：' + (result.nickname || '网易云账号') + '。机器人将使用此账号获取歌曲。';
  } else statusElement.textContent = '尚未连接，请用网易云音乐 App 扫码登录。';
}
async function refresh() {
  try {
    const result = await api('/api/status');
    showStatus(result);
    return result;
  }
  catch (error) { statusElement.textContent = error.message; }
}
async function poll(current) {
  if (current !== attempt) return;
  try {
    const result = await api('/api/qr/check');
    if (current !== attempt) return;
    if (result.code === 803) return showStatus(result);
    if (result.code === 800) {
      stopPolling(); qrImage.hidden = true;
      statusElement.textContent = '二维码已过期，请重新生成。'; return;
    }
    statusElement.textContent = result.code === 802 ? '已扫码，请在手机上确认登录。' : '请用网易云音乐 App 扫描二维码。';
    timer = setTimeout(() => poll(current), 3000);
  } catch (error) { stopPolling(); statusElement.textContent = error.message; }
}
generateButton.addEventListener('click', async () => {
  stopPolling(); const current = attempt; generateButton.disabled = true;
  statusElement.textContent = '正在生成登录二维码…'; qrImage.hidden = true;
  try {
    const result = await api('/api/qr/start');
    if (current !== attempt) return;
    qrImage.src = result.qrimg; qrImage.hidden = false;
    statusElement.textContent = '请用网易云音乐 App 扫描二维码。';
    timer = setTimeout(() => poll(current), 3000);
  } catch (error) { statusElement.textContent = error.message; }
  finally { generateButton.disabled = false; }
});
document.getElementById('refresh').addEventListener('click', refresh);
logoutButton.addEventListener('click', async () => {
  stopPolling();
  try { showStatus(await api('/api/logout')); qrImage.hidden = true; }
  catch (error) { statusElement.textContent = error.message; }
});
document.getElementById('import').addEventListener('click', async () => {
  const input = document.getElementById('cookie');
  const value = input.value.trim();
  if (!value) { statusElement.textContent = '请填写你自己的登录信息。'; return; }
  input.value = '';
  try { showStatus(await api('/api/login/import', { cookie: value })); }
  catch (error) { statusElement.textContent = error.message; }
});
refresh().then(result => {
  if (result && !result.logged_in) generateButton.click();
});
