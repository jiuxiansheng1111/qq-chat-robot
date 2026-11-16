package cn.dafeiyu.agent;

import android.app.*;
import android.content.*;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MobileChecks extends Instrumentation {
    private String phase;
    public void onCreate(Bundle args) { phase=args==null?"first":args.getString("phase","first"); start(); }
    static void check(boolean value,String message) { if(!value) throw new AssertionError(message); }
    static View find(View v, boolean editor) {
        if(editor && v instanceof EditText || !editor && v instanceof Button && "发送".contentEquals(((Button)v).getText())) return v;
        if(v instanceof ViewGroup) for(int i=0;i<((ViewGroup)v).getChildCount();i++) {
            View found=find(((ViewGroup)v).getChildAt(i),editor); if(found!=null) return found;
        }
        return null;
    }
    void send(Activity screen,String text) {
        runOnMainSync(()->{ View root=screen.getWindow().getDecorView(); ((EditText)find(root,true)).setText(text); find(root,false).performClick(); });
    }
    static Turns.Turn await(Turns store,String q) throws Exception {
        for(int i=0;i<200;i++) {
            for(Turns.Turn t:store.recent(false,Long.MAX_VALUE)) if(q.equals(t.question) && ("done".equals(t.state)||"failed".equals(t.state))) return t;
            Thread.sleep(100);
        }
        throw new AssertionError("手机回复超时");
    }
    static String line(InputStream in) throws Exception {
        ByteArrayOutputStream s=new ByteArrayOutputStream(); int ch;
        while((ch=in.read())!=-1 && ch!=10) if(ch!=13) s.write(ch);
        return s.toString("UTF-8");
    }
    public void onStart() {
        Bundle report=new Bundle(); int result=Activity.RESULT_CANCELED;
        try {
            Context ctx=getTargetContext(); Turns store=Turns.get(ctx); Settings cfg=new Settings(ctx);
            if("after-reboot".equals(phase)) {
                check(cfg.running(),"后台选项没有保存"); check("mobile-fixture".equals(cfg.secret()),"密钥重启后未能解密");
                check("记得小鲸鱼".equals(await(store,"我刚才说了什么").answer),"重启后对话未保存");
            } else {
                String rss="<rss><channel><item><title>蜂鸟</title><link>https://example.org/bird</link><description>鸟类资料</description></item></channel></rss>";
                check(Agent.rss(rss).get("https://example.org/bird").contains("蜂鸟"),"手机解析搜索来源失败");
                JSONObject forecast=new JSONObject().put("current",new JSONObject().put("temperature_2m",9).put("apparent_temperature",7).put("time","2026-11-17T08:00"))
                    .put("daily",new JSONObject().put("time",new JSONArray().put("2026-11-17").put("2026-11-18"))
                    .put("temperature_2m_max",new JSONArray().put(15).put(9)).put("temperature_2m_min",new JSONArray().put(7).put(4))
                    .put("precipitation_probability_max",new JSONArray().put(10).put(80)));
                String weather=MobileWeather.format("深圳",false,forecast);
                check(weather.contains("外套") && weather.contains("带把伞") && weather.contains("低至少 5℃"),"天气温差及降水提醒");
                try(ServerSocket server=new ServerSocket(0,4,InetAddress.getByName("127.0.0.1"))) {
                    server.setSoTimeout(25000);
                    AtomicReference<Throwable> failure=new AtomicReference<>();
                    Thread fixture=new Thread(()->{
                        try {
                            String[] paths={"/v1/chat/completions","/v1/chat/completions","/v1/responses","/v1/messages"};
                            for(int n=0;n<paths.length;n++) try(Socket socket=server.accept()) {
                                InputStream input=new BufferedInputStream(socket.getInputStream());
                                check(line(input).startsWith("POST "+paths[n]+" "),"模型路径");
                                int length=0; String h; boolean authenticated=false,version=false;
                                while(!(h=line(input)).isEmpty()) {
                                    if(h.toLowerCase(Locale.ROOT).startsWith("content-length:")) length=Integer.parseInt(h.substring(h.indexOf(':')+1).trim());
                                    String auth=n==3?"x-api-key: mobile-fixture":"Authorization: Bearer mobile-fixture";
                                    if(h.equalsIgnoreCase(auth)) authenticated=true;
                                    if(h.equalsIgnoreCase("anthropic-version: 2023-06-01")) version=true;
                                }
                                check(authenticated && length>0,"手机没有自行构造模型请求");
                                byte[] bytes=new byte[length]; new DataInputStream(input).readFully(bytes);
                                JSONObject body=new JSONObject(new String(bytes,StandardCharsets.UTF_8));
                                JSONArray msgs=body.getJSONArray(n==2?"input":"messages");
                                if(n==1) check(msgs.toString().contains("记住小鲸鱼") && msgs.toString().contains("记住了"),"历史对话没有发送");
                                JSONObject wire;
                                if(n==2) {
                                    check(!body.getBoolean("store") && body.has("instructions"),"Responses 参数");
                                    wire=new JSONObject().put("output",new JSONArray().put(new JSONObject().put("type","message").put("content",
                                        new JSONArray().put(new JSONObject().put("type","output_text").put("text","海风")))));
                                } else if(n==3) {
                                    check(version && body.has("system") && body.getInt("max_tokens")>0,"Messages 参数");
                                    wire=new JSONObject().put("content",new JSONArray().put(new JSONObject().put("type","text").put("text","潮汐")));
                                } else {
                                    wire=new JSONObject().put("choices",new JSONArray().put(new JSONObject()
                                        .put("message",new JSONObject().put("content",n==0?"记住了":"记得小鲸鱼"))));
                                }
                                byte[] out=wire.toString().getBytes(StandardCharsets.UTF_8);
                                OutputStream sink=socket.getOutputStream(); sink.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+out.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                                sink.write(out); sink.flush();
                            }
                        } catch(Throwable error) { failure.set(error); }
                    });
                    fixture.start();
                    cfg.save(6,"http://127.0.0.1:"+server.getLocalPort()+"/v1","phone-test","chat","mobile-fixture");
                    cfg.setRunning(true);
                    Activity screen=startActivitySync(new Intent().setClassName(ctx,"cn.dafeiyu.agent.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    send(screen,"记住小鲸鱼"); check("记住了".equals(await(store,"记住小鲸鱼").answer),"第一轮手机对话");
                    send(screen,"我刚才说了什么"); check("记得小鲸鱼".equals(await(store,"我刚才说了什么").answer),"第二轮手机对话");
                    cfg.save(6,cfg.base(),"phone-test","responses","mobile-fixture");
                    send(screen,"说说海风"); check("海风".equals(await(store,"说说海风").answer),"Responses 文字对话");
                    cfg.save(6,cfg.base(),"phone-test","messages","mobile-fixture");
                    send(screen,"说说潮汐"); check("潮汐".equals(await(store,"说说潮汐").answer),"Messages 文字对话");
                    fixture.join(2000); check(failure.get()==null && !fixture.isAlive(),"手机模型服务没有收到完整请求");
                }
            }
            report.putString("stream","\nMOBILE_PASS "+phase+"\n"); result=Activity.RESULT_OK;
        } catch(Throwable e) { report.putString("stream","\nMOBILE_FAIL "+e+"\n"+android.util.Log.getStackTraceString(e)); }
        finish(result,report);
    }
}
