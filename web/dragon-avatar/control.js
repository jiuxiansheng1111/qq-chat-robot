const $=id=>document.getElementById(id);
const options=[['neutral','日常'],['smile','微笑'],['angry','生气'],['pout','嘟嘴'],['cry','哭泣'],['shy','害羞'],['tsundere','傲娇'],['surprise','惊讶']];
let ws, stream, context, timer;
let previewContext, previewSource, previewVoiceId;
async function unlockSound(){
 previewContext ||= new AudioContext();
 await previewContext.resume();
 $('monitor').checked=true;
}
async function previewVoice(voice){
 if(!$('monitor').checked||!voice||voice.output!=='browser'||previewVoiceId===voice.id)return;
 previewVoiceId=voice.id;
 try{
  await unlockSound();
  const raw=await (await fetch(voice.url)).arrayBuffer();
  const buffer=await previewContext.decodeAudioData(raw);
  previewSource?.stop();previewSource=previewContext.createBufferSource();previewSource.buffer=buffer;previewSource.connect(previewContext.destination);
  const age=(Date.now()-voice.start)/1000;
  if(age>=voice.duration)return;
  previewSource.start(previewContext.currentTime+Math.max(0,-age),Math.max(0,age));
  $('message').textContent='正在本页试听，桌面同步口型且不重复播放';
 }catch(error){$('message').textContent='试听未能播放，请检查系统输出设备后再点示例语音';}
}
const send=data=>{if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(data));};
for(const [i,[value,label]] of options.entries()){const b=document.createElement('button');b.dataset.value=value;b.innerHTML=`${label}<span>${i+1}</span>`;b.onclick=()=>send({type:'expression',value});$('expressions').append(b);}
function connect(){ws=new WebSocket(`ws://${location.host}/ws`);ws.onopen=()=>{$('connection').textContent='画面已连接';$('connection').classList.add('online');};ws.onclose=()=>{$('connection').textContent='连接中断 · 正在重连';$('connection').classList.remove('online');setTimeout(connect,1500);};ws.onmessage=event=>{const s=JSON.parse(event.data);if(s.voice)previewVoice(s.voice);else if(previewSource){previewSource.stop();previewSource=null;}for(const b of $('expressions').children)b.classList.toggle('active',b.dataset.value===s.expression);for(const key of ['motion','zoom']){$(key).value=Math.round(s[key]*100);$(key+'Value').value=Math.round(s[key]*100)+'%';}};}
connect();
for(const key of ['motion','zoom'])$(key).oninput=()=>{send({type:key,value:Number($(key).value)/100});$(key+'Value').value=$(key).value+'%';};
$('nod').onclick=()=>send({type:'nod'});
document.addEventListener('keydown',event=>{if(/INPUT|TEXTAREA/.test(event.target.tagName))return;const n=Number(event.key)-1;if(n>=0&&n<8){send({type:'expression',value:options[n][0]});event.preventDefault();}if(event.code==='Space'){send({type:'nod'});event.preventDefault();}});
let bg=0;$('background').onclick=()=>{bg=(bg+1)%3;const stage=document.querySelector('.stage');stage.classList.toggle('dark',bg===1);stage.classList.toggle('checkered',bg===2);};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText('http://127.0.0.1:6187/avatar');$('copy').textContent='已复制';setTimeout(()=>$('copy').textContent='复制透明画面地址',2000);}catch{$('message').textContent='请复制上方地址';}};
$('monitor').onchange=()=>{if($('monitor').checked)unlockSound();else{previewSource?.stop();previewSource=null;}};
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.data?.source!=='dragon-avatar')return;if(event.data.type==='audio-blocked')$('message').textContent='浏览器拦截了自动播放，请勾选本页试听后再播放';if(event.data.type==='error')$('message').textContent=event.data.message;});
async function request(path,body){$('voiceState').textContent='准备中';$('speak').disabled=$('demo').disabled=true;try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'请求未完成');$('voiceState').textContent='播放中';$('message').textContent=`语音已就绪，约 ${result.duration.toFixed(1)} 秒；口型已同步`;
setTimeout(()=>$('voiceState').textContent='待机',(result.duration+1)*1000);}catch(error){$('voiceState').textContent='暂未完成';$('message').textContent=error.message;}finally{$('speak').disabled=$('demo').disabled=false;}}
$('speak').onclick=async()=>{await unlockSound();await request('/api/speak',{text:$('speech').value,preview:true});};$('demo').onclick=async()=>{await unlockSound();await request('/api/demo?preview=true');};$('stop').onclick=()=>{previewSource?.stop();previewSource=null;send({type:'stop_voice'});$('voiceState').textContent='待机';};
async function stopMic(){clearInterval(timer);stream?.getTracks().forEach(t=>t.stop());await context?.close();stream=null;context=null;$('mic').textContent='启用麦克风口型';$('level').style.width='0';send({type:'mouth',value:0});}
$('mic').onclick=async()=>{if(stream){await stopMic();return;}try{stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});context=new AudioContext();await context.resume();const analyser=context.createAnalyser();analyser.fftSize=1024;context.createMediaStreamSource(stream).connect(analyser);const samples=new Float32Array(analyser.fftSize);$('mic').textContent='关闭麦克风口型';timer=setInterval(()=>{analyser.getFloatTimeDomainData(samples);let sum=0;for(const s of samples)sum+=s*s;const value=Math.max(0,Math.min(1,(Math.sqrt(sum/samples.length)-.012)*12));$('level').style.width=Math.round(value*100)+'%';send({type:'mouth',value});},50);$('message').textContent='麦克风已启用；音频由 OBS 自己的麦克风来源收取';}catch(error){await stopMic();$('message').textContent='麦克风未开启，请在浏览器中允许麦克风访问';}};
window.addEventListener('pagehide',()=>{stream?.getTracks().forEach(t=>t.stop());send({type:'mouth',value:0});});
