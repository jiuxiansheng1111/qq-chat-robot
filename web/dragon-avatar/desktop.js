// QWebChannel is available only in the user's local desktop wrapper.
if(new URLSearchParams(location.search).has('desktop')){
 const script=document.createElement('script');script.src='/static/qwebchannel.js';
 script.onload=()=>new QWebChannel(qt.webChannelTransport,channel=>{
  const bridge=channel.objects.desktop;let dragging=false;
  document.addEventListener('pointerdown',event=>{if(event.button!==0)return;dragging=true;bridge.begin_drag(event.screenX,event.screenY);event.target.setPointerCapture(event.pointerId);});
  document.addEventListener('pointermove',event=>{if(dragging)bridge.drag(event.screenX,event.screenY);});
  document.addEventListener('pointerup',()=>{if(dragging){dragging=false;bridge.end_drag();}});
  document.addEventListener('contextmenu',event=>{event.preventDefault();bridge.menu();});
  document.addEventListener('dblclick',()=>bridge.open_control());
  document.addEventListener('wheel',event=>{event.preventDefault();bridge.zoom(event.deltaY<0?1:-1);},{passive:false});
 });document.head.append(script);
}
