package cn.dafeiyu.agent;

import android.app.*;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.BroadcastReceiver;
import android.os.*;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class AgentService extends Service {
    static final String UPDATED="cn.dafeiyu.agent.TURNS_UPDATED";
    static final String DIRECT="cn.dafeiyu.agent.DIRECT_REPLY";
    private static final String CHANNEL="agent";
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private Turns turns;
    private Settings settings;
    private Agent agent;
    private boolean busy;
    private boolean waitingForUnlock;
    private int latestStart;
    private final BroadcastReceiver unlocked=new BroadcastReceiver() {
        public void onReceive(Context context,Intent intent) { if(Intent.ACTION_USER_UNLOCKED.equals(intent.getAction())) start(context); }
    };
    static void start(Context context) {
        context.startForegroundService(new Intent(context,AgentService.class));
    }
    public void onCreate() {
        super.onCreate();
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"大肥鱼手机助手",NotificationManager.IMPORTANCE_LOW));
    }
    private Notification notice(String status) {
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Intent reply=new Intent(this,AgentService.class).setAction(DIRECT);
        int flags=PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0);
        PendingIntent receive=PendingIntent.getForegroundService(this,1,reply,flags);
        RemoteInput message=new RemoteInput.Builder("question").setLabel("给大肥鱼发消息").build();
        Notification.Action action=new Notification.Action.Builder(null,"发消息",receive).addRemoteInput(message).build();
        return new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.whale).setContentTitle("大肥鱼")
                .setContentText(status).setContentIntent(open).setOnlyAlertOnce(true).setOngoing(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE).addAction(action).build();
    }
    public int onStartCommand(Intent intent,int flags,int startId) {
        latestStart=startId;
        if(!getSystemService(UserManager.class).isUserUnlocked()) {
            startForeground(41,notice("解锁手机后即可聊天"));
            if(!waitingForUnlock) {
                if(Build.VERSION.SDK_INT>=33) registerReceiver(unlocked,new IntentFilter(Intent.ACTION_USER_UNLOCKED),Context.RECEIVER_NOT_EXPORTED);
                else registerReceiver(unlocked,new IntentFilter(Intent.ACTION_USER_UNLOCKED));
                waitingForUnlock=true;
            }
            return START_STICKY;
        }
        if(waitingForUnlock) { unregisterReceiver(unlocked); waitingForUnlock=false; }
        if(turns==null) { turns=Turns.get(this); settings=new Settings(this); agent=new Agent(this); }
        startForeground(41,notice("手机助手已就绪"));
        if(intent!=null && DIRECT.equals(intent.getAction())) {
            Bundle input=RemoteInput.getResultsFromIntent(intent);
            CharSequence q=input==null?null:input.getCharSequence("question");
            if(q!=null && !q.toString().trim().isEmpty()) turns.enqueue(Agent.clip(q.toString().trim(),3000),"chat");
        }
        if(!busy) { busy=true; worker.execute(this::drain); }
        return settings.running()?START_STICKY:START_NOT_STICKY;
    }
    void changed() { sendBroadcast(new Intent(UPDATED).setPackage(getPackageName())); }
    private void drain() {
        Turns.Turn t;
        while((t=turns.next())!=null) {
            PowerManager.WakeLock wake=getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"DaFeiYu:reply");
            wake.acquire(120000);
            try {
                turns.finish(t.id,agent.answer(t),true);
            } catch(Exception e) {
                String error;
                if(e instanceof java.net.SocketTimeoutException) error="手机等候服务回复超时，网络恢复后可以点重试。";
                else if(e instanceof java.net.UnknownHostException || e instanceof java.net.ConnectException)
                    error="手机连不上服务，请检查手机网络和模型地址，再点重试。";
                else error=e instanceof IOException && e.getMessage()!=null?e.getMessage():"未取得有效回复，请检查网络与模型设置后重试。";
                turns.finish(t.id,Agent.clip(error,500),false);
            } finally { if(wake.isHeld()) wake.release(); }
            changed();
        }
        main.post(()->{
            busy=false;
            if(turns.hasQueued()) { busy=true; worker.execute(this::drain); return; }
            if(settings.running()) getSystemService(NotificationManager.class).notify(41,notice("手机助手已就绪，点此查看对话"));
            else { stopForeground(STOP_FOREGROUND_REMOVE); stopSelfResult(latestStart); }
        });
    }
    public void onDestroy() { if(waitingForUnlock) unregisterReceiver(unlocked); worker.shutdown(); super.onDestroy(); }
    public IBinder onBind(Intent intent) { return null; }
}
