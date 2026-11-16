// Layered anime artwork, deformable mesh and live audio envelope; no remote dependencies.
const canvas = document.querySelector('#puppet');
const gl = canvas.getContext('webgl', {alpha:true, antialias:true, premultipliedAlpha:false});
const failure = document.querySelector('#failure');
let state = {expression:'neutral', motion:.65, zoom:1, mouth:0, mouth_at:0, nod_at:0, voice:null};
let clockOffset = 0, audio = null, audioId = null, mouth = 0, elapsed = 0;
let previousExpression = 'neutral', changedAt = -1;
let muted = new URLSearchParams(location.search).get('muted') === '1';
const outputRole=new URLSearchParams(location.search).has('desktop')?'desktop':(muted?'preview':'obs');
let nextBlink = 2.5, blinkStart = -5;
const expressions = {neutral:0, smile:1, angry:2, pout:3, cry:4, shy:5, tsundere:6, surprise:7};
const closed = {neutral:8, smile:9, angry:10, pout:8, cry:11, shy:9, tsundere:10, surprise:8};
const previewParams = new URLSearchParams(location.search);
const inspectedExpression = previewParams.get('expression-preview');
const inspecting = Object.hasOwn(expressions, inspectedExpression);
if (inspecting) { state.expression=inspectedExpression; state.motion=0; state.zoom=1; muted=true; }
function report(type, extra={}) { if (parent !== window) parent.postMessage({source:'dragon-avatar',type,...extra}, location.origin); }
window.addEventListener('message', event => {
  if(event.origin !== location.origin || event.data?.type !== 'monitor') return;
  muted = !event.data.enabled;
  if(audio) { audio.muted = muted; audio.play().catch(()=>{}); }
});
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws?role=${outputRole}`);
  ws.onopen = () => report('connected');
  ws.onmessage = event => {
    const next = JSON.parse(event.data);
    clockOffset = next.server_time - Date.now();
    if(next.expression !== state.expression) { previousExpression=state.expression; changedAt=performance.now(); }
    state=next;
    if(!state.voice && audio) {audio.pause();audio=null;audioId=null;}
    if(state.voice && state.voice.id !== audioId) {
      audio?.pause(); audioId=state.voice.id;
      audio=new Audio(state.voice.url); audio.muted=muted||state.voice.output!==outputRole;
      const localAudio=audio, voice=state.voice;
      localAudio.addEventListener('canplay', () => {
        const start=() => {
          if(audio!==localAudio) return;
          localAudio.currentTime=Math.max(0,(Date.now()+clockOffset-voice.start)/1000);
          localAudio.play().then(()=>report('audio-playing')).catch(()=>report('audio-blocked'));
        };
        setTimeout(start, Math.max(0,voice.start-Date.now()-clockOffset));
      }, {once:true});
    }
  };
  ws.onclose=()=>{report('disconnected');setTimeout(connect,1500);};
}
function shader(type, source) { const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s; }
function texture(url) { return new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>{const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tex);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);resolve(tex);};img.onerror=reject;img.src=url;}); }
const vertex=`
attribute vec2 position; attribute vec2 uv;
uniform vec2 resolution; uniform vec4 camera; uniform float time, motion, zoom, nod;
varying vec2 vUV;
void main(){
 vec2 p=position;
 float h=1.-smoothstep(340.,700.,p.y);
 float angle=(sin(time*.75)*.024+sin(time*.31)*.012)*motion;
 vec2 d=p-vec2(512.,510.);
 float a=angle*h; p=vec2(cos(a)*d.x-sin(a)*d.y,sin(a)*d.x+cos(a)*d.y)+vec2(512.,510.);
 p.y+=h*(sin(time*1.8)*2.7*motion+nod*7.);
 float chest=exp(-pow((p.y-580.)/240.,2.));
 p.x+=(p.x-512.)*chest*sin(time*1.8)*.008*motion;
 float side=smoothstep(140.,350.,abs(p.x-512.));
 p.x+=side*sin(time*1.3+p.y*.004)*5.*motion;
 p.y+=side*sin(time*.95+p.x*.004)*3.*motion;
 p.x+=sin(time*.6)*4.*motion;
 float fit=min(resolution.x/camera.z,resolution.y/camera.w)*zoom;
 vec2 screen=(p-camera.xy)*fit+resolution*.5;
 gl_Position=vec4(screen.x/resolution.x*2.-1.,1.-screen.y/resolution.y*2.,0.,1.);vUV=uv;
}`;
const fragment=`precision mediump float;
varying vec2 vUV;
uniform sampler2D atlas;
uniform float opacity, feather;
uniform vec4 region;
void main(){
 vec4 c=texture2D(atlas,vUV);
 vec2 q=(vUV-region.xy)/region.zw;
 float edge=min(min(q.x,1.-q.x),min(q.y,1.-q.y));
 float mask=feather>0.?smoothstep(0.,feather,edge)*(1.-smoothstep(.72,1.,dot((q-.5)*2.,(q-.5)*2.))):1.;
 gl_FragColor=vec4(c.rgb,c.a*opacity*mask);
}`;
function mesh(x,y,w,h,u0=0,v0=0,u1=1,v1=1,cols=1,rows=1,tilt=0) {
  const vertices=[];
  function point(i,j) {vertices.push(x+w*i/cols,y+h*j/rows+tilt*(w*i/cols-w/2),u0+(u1-u0)*i/cols,v0+(v1-v0)*j/rows);}
  for(let j=0;j<rows;j++)for(let i=0;i<cols;i++){point(i,j);point(i+1,j);point(i,j+1);point(i,j+1);point(i+1,j);point(i+1,j+1);}
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);return {buffer,count:vertices.length/4,region:[u0,v0,u1-u0,v1-v0]};
}
async function init() {
 if(!gl)throw Error('当前浏览器未启用 WebGL，请开启硬件加速');
 const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vertex));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
 const pos=gl.getAttribLocation(program,'position'),uv=gl.getAttribLocation(program,'uv');gl.enableVertexAttribArray(pos);gl.enableVertexAttribArray(uv);
 const uniforms=Object.fromEntries(['resolution','camera','time','motion','zoom','nod','opacity','feather','region'].map(k=>[k,gl.getUniformLocation(program,k)]));
 const [base,atlas,coherent]=await Promise.all([texture('/assets/base-v3-blank.png'),texture('/assets/face-atlas.png'),texture('/assets/faces-v2.png')]);
 const body=mesh(0,0,1024,1536,0,0,1,1,48,72);
 // Each generated tile has a different origin; register anatomical landmarks,
 // not nominal grid cells. Shared target sockets keep expressions from jumping.
 const anchors=[
  [[143.7,251.1],[275,248.7],[208,311]],
  [[496.5,252.6],[629,249.8],[562,312]],
  [[848.9,254.8],[981.2,251.6],[915,314]],
  [[1201.1,251.8],[1334,249.8],[1268,313]],
  [[145.3,614.5],[275.5,611.2],[209,675]],
  [[496.3,614.4],[628,610.6],[563,672]],
  [[859.7,620.8],[999.2,610.8],[919,677]],
  [[1204.8,611.9],[1329.6,605.9],[1270,674]],
  [[144,975],[275,973],[208,1030]],
  [[496.5,976],[629,974],[562,1030]],
  [[849,979],[981,976],[915,1031]],
  [[1201,976],[1334,974],[1268,1032]],
 ];
 function patch(target,source,scale,width,height){
  const sw=width/scale,sh=height/scale;
  return mesh(target[0]-width/2,target[1]-height/2,width,height,
   (source[0]-sw/2)/1448,(source[1]-sh/2)/1086,
   (source[0]+sw/2)/1448,(source[1]+sh/2)/1086,10,8,-.108);
 }
 const faces=anchors.map(a=>[
  patch([448.8,332.7],a[0],.80,126,96),
  patch([571.1,319.5],a[1],.80,126,96),
 ]);
 const rests=anchors.map(a=>patch([512,380],a[2],.90,58,36));
 const lips=[null,
  mesh(496,369,32,23,532/1536,866/1024,622/1536,928/1024,6,4,-.108),
  mesh(492,366,40,29,898/1536,858/1024,1024/1536,945/1024,6,4,-.108),
 ];
 function draw(item, tex, opacity=1,feather=0){gl.bindTexture(gl.TEXTURE_2D,tex);gl.bindBuffer(gl.ARRAY_BUFFER,item.buffer);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,16,0);gl.vertexAttribPointer(uv,2,gl.FLOAT,false,16,8);gl.uniform1f(uniforms.opacity,opacity);gl.uniform1f(uniforms.feather,feather);gl.uniform4fv(uniforms.region,item.region);gl.drawArrays(gl.TRIANGLES,0,item.count);}
 gl.enable(gl.BLEND);gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);gl.clearColor(0,0,0,0);
 let last=performance.now();
 function frame(now){
  const dt=Math.min(.1,(now-last)/1000);last=now;elapsed+=dt;
  const dpr=Math.min(devicePixelRatio,2),width=Math.round(canvas.clientWidth*dpr),height=Math.round(canvas.clientHeight*dpr);
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;gl.viewport(0,0,width,height);}
  const wall=Date.now()+clockOffset;
  let target=wall-state.mouth_at<300?state.mouth:0;
  if(inspecting&&previewParams.has('mouth-preview'))target=.8;
  const voice=state.voice;
  if(voice){let ms=wall-voice.start;if(audio&&audioId===voice.id&&!audio.paused)ms=audio.currentTime*1000; if(ms>=0&&ms<voice.duration*1000) target=Math.max(target,voice.envelope[Math.floor(ms/voice.step)]||0);}
  mouth+=(target-mouth)*(1-Math.exp(-dt*22));
  if(elapsed>nextBlink){blinkStart=elapsed;nextBlink=elapsed+3+Math.random()*3;}
  let b=elapsed-blinkStart;
  let blink=b<.09?b/.09:b<.15?1:b<.26?1-(b-.15)/.11:0;blink=inspecting?(previewParams.has('blink-preview')?1:0):Math.max(0,blink);
  const nodAge=(wall-state.nod_at)/1000;const nod=nodAge>=0&&nodAge<1.2?Math.sin(nodAge*12)*Math.exp(-nodAge*3):0;
  gl.clear(gl.COLOR_BUFFER_BIT);gl.uniform2f(uniforms.resolution,width,height);gl.uniform1f(uniforms.time,elapsed);gl.uniform1f(uniforms.motion,state.motion);gl.uniform1f(uniforms.zoom,state.zoom);gl.uniform1f(uniforms.nod,nod);
  gl.uniform4fv(uniforms.camera,inspecting&&previewParams.has('portrait')?[512,310,400,300]:[512,785,1120,1600]);
  const mix=Math.min(1,(now-changedAt)/180);
  draw(body,base);
  const face=(expression,alpha)=>{for(const item of faces[expressions[expression]??0])draw(item,coherent,alpha,.045);if(blink>0)for(const item of faces[closed[expression]??8])draw(item,coherent,alpha*blink,.045);draw(rests[expressions[expression]??0],coherent,alpha,.09);};
  if(mix<1)face(previousExpression,1);face(state.expression,mix);
  if(mouth>.06){const open=mouth>.65?2:1;const blend=Math.min(1,mouth*7);draw(lips[open],atlas,blend);}
  if(!canvas.dataset.renderReady)canvas.dataset.renderReady='true';
  requestAnimationFrame(frame);
 }
 if(!inspecting)connect();requestAnimationFrame(frame);report('ready');
}
init().catch(error=>{failure.textContent=error.message;failure.style.display='block';report('error',{message:error.message});});
