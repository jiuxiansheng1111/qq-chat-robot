package cn.dafeiyu.agent;

import android.content.Context;
import android.net.*;
import android.os.Handler;
import android.os.Looper;
import org.java_websocket.WebSocket;
import org.java_websocket.drafts.Draft;
import org.java_websocket.exceptions.InvalidDataException;
import org.java_websocket.handshake.*;
import org.java_websocket.server.WebSocketServer;
import org.json.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

final class QqServer extends WebSocketServer {
    static final int PORT=6199;
    static volatile String state="未启用 OneBot";
    static volatile boolean online=false;
    private final QqInbox inbox;
    private final String generation=UUID.randomUUID().toString();
    private final Handler timer=new Handler(Looper.getMainLooper());
    private final Map<String,Long> pending=new ConcurrentHashMap<>();
    private volatile WebSocket peer;
    private volatile String identity="";
    private volatile boolean stopping;
    final String bindHost;
    final String expectedId;
    static String host(Context ctx,boolean lan) {
        if(!lan) return "127.0.0.1";
        ConnectivityManager cm=ctx.getSystemService(ConnectivityManager.class);
        if(cm!=null) for(Network n:cm.getAllNetworks()) {
            NetworkCapabilities caps=cm.getNetworkCapabilities(n);
            if(caps==null || !(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) continue;
            LinkProperties link=cm.getLinkProperties(n);
            if(link==null) continue;
            for(LinkAddress addr:link.getLinkAddresses()) {
                InetAddress ip=addr.getAddress(); byte[] bytes=ip.getAddress();
                if(ip instanceof Inet4Address && (ip.isSiteLocalAddress() || (bytes[0]==100 && (bytes[1]&0xC0)==0x40))) return ip.getHostAddress();
            }
        }
        return "";
    }
    QqServer(String host,QqInbox inbox) {
        super(new InetSocketAddress(host,PORT),1);
        bindHost=host; this.inbox=inbox; expectedId=inbox.prefs.getString("self","");
        setReuseAddr(true); setConnectionLostTimeout(20);
    }
    void announce(String text) { if(!stopping) { state=text; inbox.changed(); } }
    public ServerHandshakeBuilder onWebsocketHandshakeReceivedAsServer(WebSocket conn,Draft draft,ClientHandshake req) throws InvalidDataException {
        String id=req.getFieldValue("X-Self-ID");
        boolean token=MessageDigest.isEqual(("Bearer "+inbox.token()).getBytes(StandardCharsets.UTF_8),req.getFieldValue("Authorization").getBytes(StandardCharsets.UTF_8));
        String role=req.getFieldValue("X-Client-Role");
        if(!token || !"/ws".equals(req.getResourceDescriptor()) || !id.matches("[0-9]{5,18}")
                || !(role.isEmpty()||"Universal".equalsIgnoreCase(role)) || (!expectedId.isEmpty()&&!expectedId.equals(id)) || stopping)
            throw new InvalidDataException(1008,"Pairing or account mismatch");
        return super.onWebsocketHandshakeReceivedAsServer(conn,draft,req);
    }
    public synchronized void onOpen(WebSocket conn,ClientHandshake req) {
        if(peer!=null && peer.isOpen()) { conn.close(1008,"A QQ session is already connected"); return; }
        peer=conn; identity=req.getFieldValue("X-Self-ID"); online=false;
        try { conn.send(new JSONObject().put("action","get_login_info").put("params",new JSONObject()).put("echo","login-"+generation).toString()); }
        catch(Exception e) { conn.close(1011); }
        announce("已连接，正在核对 QQ 身份");
        timer.postDelayed(()->{ if(conn==peer && !online) conn.close(1008,"QQ identity timeout"); },15000);
    }
    public void onMessage(WebSocket conn,String payload) {
        if(conn!=peer || payload.length()>160000 || stopping) return;
        try {
            JSONObject msg=new JSONObject(payload);
            if(msg.has("echo")) {
                String echo=msg.optString("echo");
                if(echo.equals("login-"+generation)) {
                    JSONObject data=msg.optJSONObject("data");
                    if(msg.optInt("retcode",-1)!=0 || !"ok".equals(msg.optString("status")) || data==null || !identity.equals(data.optString("user_id"))) {
                        conn.close(1008,"Invalid QQ identity"); return;
                    }
                    online=true; announce("QQ 已连接 · "+identity);
                } else {
                    Long id=pending.remove(echo);
                    if(id!=null) inbox.status(id,msg.optInt("retcode",-1)==0 && "ok".equals(msg.optString("status"))?"QQ 已确认发送":"QQ 返回发送失败",null);
                }
                return;
            }
            if(!online || !identity.equals(msg.optString("self_id"))) return;
            if("meta_event".equals(msg.optString("post_type")) && "heartbeat".equals(msg.optString("meta_event_type"))) {
                JSONObject health=msg.optJSONObject("status");
                if(health!=null && (health.has("online") && !health.optBoolean("online") || health.has("good") && !health.optBoolean("good"))) conn.close(1001,"QQ offline");
                return;
            }
            if(!"message".equals(msg.optString("post_type"))) return;
            String type=msg.optString("message_type"), sender=msg.optString("user_id"),mid=msg.optString("message_id");
            boolean group="group".equals(type);
            String target=group?msg.optString("group_id"):sender;
            if((!group && !"private".equals(type)) || sender.equals(identity) || !target.matches("[0-9]{1,18}") || !mid.matches("-?[0-9]{1,20}")) return;
            StringBuilder text=new StringBuilder(); boolean mentioned=false;
            JSONArray parts=msg.optJSONArray("message");
            if(parts!=null) for(int i=0;i<parts.length();i++) {
                JSONObject seg=parts.optJSONObject(i); if(seg==null)continue; JSONObject data=seg.optJSONObject("data"); if(data==null)continue;
                if("text".equals(seg.optString("type"))) text.append(data.optString("text"));
                if("at".equals(seg.optString("type")) && identity.equals(data.optString("qq"))) mentioned=true;
            } else {
                String raw=msg.optString("raw_message",msg.optString("message",""));
                mentioned=raw.matches("(?s).*\\[CQ:at,qq="+identity+"(?:,|\\]).*");
                text.append(raw.replaceAll("\\[CQ:[^\\]]+\\]"," ").replace("&#91;","[").replace("&#93;","]").replace("&amp;","&"));
            }
            String q=text.toString().trim(); if(q.isEmpty())return;
            String room="onebot:"+identity+":"+type+":"+target;
            boolean reply=inbox.prefs.getInt("mode",0)==1 && (!group || mentioned || inbox.called(q));
            inbox.receive(room+":"+mid,room,(group?"QQ群 ":"QQ 私聊 ")+target,q,reply?"等待回复":"同步接收",
                reply?(id,answer)->sendReply(conn,id,type,target,answer):null);
        } catch(JSONException ignored) { /* Unknown OneBot events do not change delivery state. */ }
    }
    private void sendReply(WebSocket socket,long id,String type,String target,String text) throws Exception {
        if(socket!=peer || !online || !socket.isOpen() || inbox.prefs.getInt("mode",0)!=1) throw new IllegalStateException();
        String echo=generation+"-"+id; pending.put(echo,id);
        JSONObject params=new JSONObject().put("group".equals(type)?"group_id":"user_id",Long.parseLong(target))
                .put("message",new JSONArray().put(new JSONObject().put("type","text").put("data",new JSONObject().put("text",text))));
        try { socket.send(new JSONObject().put("action","group".equals(type)?"send_group_msg":"send_private_msg").put("params",params).put("echo",echo).toString()); }
        catch(RuntimeException e) { pending.remove(echo); throw e; }
        timer.postDelayed(()->{ if(pending.remove(echo)!=null) inbox.status(id,"发送未确认",null); },15000);
    }
    public synchronized void onClose(WebSocket conn,int code,String reason,boolean remote) {
        if(conn!=peer)return;
        peer=null; identity=""; if(!stopping) online=false;
        for(Long id:pending.values()) inbox.status(id,"发送未确认",null);
        pending.clear(); announce("等待 QQ 连接");
    }
    public void onError(WebSocket conn,Exception error) {
        if(conn==null && !online) announce("监听失败，请检查 Wi-Fi 地址或端口 6199");
    }
    public void onStart() { announce("等待 QQ 连接"); }
    void end() {
        stopping=true; online=false;
        try { stop(700); } catch(InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
