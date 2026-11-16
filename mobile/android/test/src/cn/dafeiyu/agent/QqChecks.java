package cn.dafeiyu.agent;

import android.app.*;
import android.content.*;
import android.os.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import org.json.*;

/** A test-only protocol peer. All chats and recipients in this class stay inside the emulator. */
final class QqChecks {
    private static void check(boolean value,String label) { MobileChecks.check(value,label); }
    interface Condition { boolean met() throws Exception; }
    static void until(Condition condition,String label) throws Exception {
        for(int tick=0;tick<160;tick++) { if(condition.met())return; Thread.sleep(75); }
        throw new AssertionError(label);
    }
    static final class Peer extends WebSocketClient {
        final BlockingQueue<JSONObject> received=new LinkedBlockingQueue<>();
        Peer(String token,String account) throws Exception {
            super(new URI("ws://127.0.0.1:6199/ws"),headers(token,account));
        }
        static Map<String,String> headers(String token,String account) {
            Map<String,String> headers=new HashMap<>();
            headers.put("Authorization","Bearer "+token); headers.put("X-Self-ID",account); headers.put("X-Client-Role","Universal"); return headers;
        }
        public void onOpen(ServerHandshake handshake) {}
        public void onClose(int code,String reason,boolean remote) {}
        public void onError(Exception failure) {}
        public void onMessage(String wire) {
            try { received.add(new JSONObject(wire)); } catch(JSONException error) { throw new AssertionError(error); }
        }
        JSONObject next(String action) throws Exception {
            JSONObject req=received.poll(12,TimeUnit.SECONDS);
            check(req!=null && action.equals(req.optString("action")),"Expected "+action);
            return req;
        }
        void ack(JSONObject command,boolean success,JSONObject data) throws Exception {
            send(new JSONObject().put("echo",command.getString("echo")).put("status",success?"ok":"failed")
                    .put("retcode",success?0:100).put("data",data).toString());
        }
        void message(int mid,String kind,String text,boolean at) throws Exception {
            JSONArray parts=new JSONArray();
            if(at)parts.put(new JSONObject().put("type","at").put("data",new JSONObject().put("qq","99001")));
            parts.put(new JSONObject().put("type","text").put("data",new JSONObject().put("text",text)));
            send(new JSONObject().put("post_type","message").put("message_type",kind).put("self_id",99001).put("user_id",99002)
                    .put("group_id",99003).put("message_id",mid).put("message",parts).toString());
        }
    }
    static void run(Context ctx,Settings settings) throws Exception {
        QqInbox inbox=QqInbox.get(ctx);
        inbox.prefs.edit().putBoolean("onebot",true).putBoolean("lan",false).putString("self","99001").putInt("mode",0).commit();
        ctx.startForegroundService(new Intent().setClassName(ctx,"cn.dafeiyu.agent.AgentService"));
        until(()->QqServer.state.equals("等待 QQ 连接"),"Phone reverse WS did not open");
        Peer denied=new Peer("wrong","99001");
        check(!denied.connectBlocking(4,TimeUnit.SECONDS),"Wrong pairing token accepted"); denied.close();
        Peer other=new Peer(inbox.token(),"99009");
        check(!other.connectBlocking(4,TimeUnit.SECONDS),"Wrong bot account accepted"); other.close();
        try(ServerSocket http=new ServerSocket(0,4,InetAddress.getByName("127.0.0.1"))) {
            http.setSoTimeout(20000);
            settings.save(6,"http://127.0.0.1:"+http.getLocalPort()+"/v1","qq-test","chat","mobile-fixture");
            AtomicReference<Throwable> modelError=new AtomicReference<>();
            Thread responder=new Thread(()->{
                try {
                    String[] questions={"私聊一","群聊点名","同一私聊二"};
                    for(int i=0;i<questions.length;i++) try(Socket conn=http.accept()) {
                        InputStream input=new BufferedInputStream(conn.getInputStream());
                        check(MobileChecks.line(input).startsWith("POST /v1/chat/completions "),"QQ used wrong model protocol");
                        String header; int count=0;
                        while(!(header=MobileChecks.line(input)).isEmpty()) if(header.toLowerCase(Locale.ROOT).startsWith("content-length:"))
                            count=Integer.parseInt(header.substring(header.indexOf(':')+1).trim());
                        byte[] body=new byte[count]; new DataInputStream(input).readFully(body);
                        JSONObject req=new JSONObject(new String(body,StandardCharsets.UTF_8));
                        String messages=req.getJSONArray("messages").toString();
                        check("qq-test".equals(req.getString("model")) && messages.contains("已经接入的 QQ") && messages.contains(questions[i]),"QQ context or model is missing");
                        check(!messages.contains("记住小鲸鱼") && (i!=1 || !messages.contains("私聊一")) && (i!=2 || !messages.contains("群聊点名")),"Crossed conversation boundary");
                        if(i==2)check(messages.contains("私聊一"),"QQ follow-up lost its history");
                        byte[] answer=new JSONObject().put("choices",new JSONArray().put(new JSONObject().put("message",new JSONObject().put("content","回复"+i)))).toString().getBytes(StandardCharsets.UTF_8);
                        OutputStream out=conn.getOutputStream();
                        out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+answer.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                        out.write(answer); out.flush();
                    }
                } catch(Throwable error) { modelError.set(error); }
            });
            responder.start();
            Peer peer=new Peer(inbox.token(),"99001");
            try {
                check(peer.connectBlocking(4,TimeUnit.SECONDS),"Phone WS pairing failed");
                peer.ack(peer.next("get_login_info"),true,new JSONObject().put("user_id",99001));
                until(()->QqServer.online,"QQ identity not confirmed");
                peer.message(40,"private","双端同步",false); peer.message(40,"private","双端同步",false);
                until(()->inbox.recent().toString().contains("双端同步"),"Did not receive copy for desktop-priority mode");
                check(inbox.recent().stream().filter(row->row.contains("双端同步")).count()==1,"Duplicate inbox entry");
                check(peer.received.poll(350,TimeUnit.MILLISECONDS)==null,"Phone replied in desktop-priority mode");
                inbox.prefs.edit().putInt("mode",1).commit();
                peer.message(41,"group","群内其他人的话",false);
                peer.message(42,"private","私聊一",false); peer.message(42,"private","私聊一",false);
                JSONObject dm=peer.next("send_private_msg"); verifyTarget(dm,"user_id",99002,"回复0"); peer.ack(dm,true,new JSONObject().put("message_id",111));
                until(()->inbox.recent().toString().contains("QQ 已确认发送"),"Missing real send receipt");
                peer.message(43,"group","群聊点名",true);
                JSONObject group=peer.next("send_group_msg"); verifyTarget(group,"group_id",99003,"回复1"); peer.ack(group,false,new JSONObject());
                until(()->inbox.recent().toString().contains("QQ 返回发送失败"),"Negative acknowledgement treated as delivered");
                peer.message(44,"private","同一私聊二",false);
                JSONObject last=peer.next("send_private_msg"); verifyTarget(last,"user_id",99002,"回复2");
                peer.closeBlocking();
                until(()->!QqServer.online && inbox.recent().toString().contains("发送未确认"),"Disconnect lost unresolved send state");
            } finally { peer.close(); }
            responder.join(1000); check(modelError.get()==null && !responder.isAlive(),"QQ provider check: "+modelError.get());
        }
        notifications(ctx,inbox);
        inbox.prefs.edit().putInt("mode",0).commit();
    }
    static void verifyTarget(JSONObject command,String endpoint,int expected,String answer) throws Exception {
        JSONObject params=command.getJSONObject("params");
        check(params.getInt(endpoint)==expected && answer.equals(params.getJSONArray("message").getJSONObject(0).getJSONObject("data").getString("text")),"Incorrect QQ recipient or reply");
    }
    static void notifications(Context ctx,QqInbox inbox) throws Exception {
        long now=System.currentTimeMillis();
        Notification.Builder builder=new Notification.Builder(ctx,"agent").setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("测试私聊").setContentText("手机通知测试").setWhen(now);
        Notification notice=builder.build();
        int before=inbox.recent().size();
        QqNotifications.capture(ctx,inbox,"unrelated.application","mock",now,notice);
        check(before==inbox.recent().size(),"Captured an unrelated app");
        QqNotifications.capture(ctx,inbox,"com.tencent.mobileqq","mock",now,notice);
        QqNotifications.capture(ctx,inbox,"com.tencent.mobileqq","mock",now,notice);
        check(inbox.recent().size()==before+1 && inbox.recent().get(0).contains("QQ 未提供快捷回复"),"Missing read-only notification or deduplication");
        String event=ctx.getPackageName()+".TEST_REPLY";
        BlockingQueue<String> result=new LinkedBlockingQueue<>();
        BroadcastReceiver receiver=new BroadcastReceiver(){public void onReceive(Context c,Intent msg) {
            Bundle reply=RemoteInput.getResultsFromIntent(msg);
            if(reply!=null)result.offer(reply.getCharSequence("text","").toString());
        }};
        if(Build.VERSION.SDK_INT>=33)ctx.registerReceiver(receiver,new IntentFilter(event),Context.RECEIVER_NOT_EXPORTED);
        else ctx.registerReceiver(receiver,new IntentFilter(event));
        try {
            PendingIntent response=PendingIntent.getBroadcast(ctx,19,new Intent(event).setPackage(ctx.getPackageName()),PendingIntent.FLAG_UPDATE_CURRENT|(Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0));
            Notification n=builder.addAction(new Notification.Action.Builder(null,"回复",response).addRemoteInput(new RemoteInput.Builder("text").build()).build()).build();
            check(QqNotifications.replyAction(n,"com.tencent.mobileqq")==null,"Accepted foreign reply action");
            Notification.Action action=QqNotifications.replyAction(n,ctx.getPackageName()); check(action!=null,"Valid test reply action missing");
            QqNotifications.submit(ctx,action,"通知快捷回复"); check("通知快捷回复".equals(result.poll(5,TimeUnit.SECONDS)),"RemoteInput did not reach its conversation action");
        } finally { ctx.unregisterReceiver(receiver); }
    }
}
