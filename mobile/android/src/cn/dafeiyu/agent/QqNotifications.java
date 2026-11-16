package cn.dafeiyu.agent;

import android.app.*;
import android.content.*;
import android.os.*;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.util.*;

public final class QqNotifications extends NotificationListenerService {
    static volatile boolean connected;
    static boolean qqPackage(String name) {
        return "com.tencent.mobileqq".equals(name) || "com.tencent.tim".equals(name);
    }
    public void onListenerConnected() { connected=true; QqInbox.get(this).changed(); }
    public void onListenerDisconnected() { connected=false; QqInbox.get(this).changed(); }
    static Notification.Action replyAction(Notification n,String creator) {
        if(n.actions!=null) for(Notification.Action a:n.actions) {
            if(a==null || a.actionIntent==null || a.getRemoteInputs()==null || !creator.equals(a.actionIntent.getCreatorPackage())) continue;
            if(Build.VERSION.SDK_INT>=28 && a.getSemanticAction()!=Notification.Action.SEMANTIC_ACTION_NONE
                    && a.getSemanticAction()!=Notification.Action.SEMANTIC_ACTION_REPLY) continue;
            if(Build.VERSION.SDK_INT>=31 && a.actionIntent.isImmutable()) continue;
            for(RemoteInput input:a.getRemoteInputs()) if(input.getAllowFreeFormInput()) return a;
        }
        return null;
    }
    static void submit(Context ctx,Notification.Action action,String answer) throws PendingIntent.CanceledException {
        for(RemoteInput input:action.getRemoteInputs()) if(input.getAllowFreeFormInput()) {
            Bundle results=new Bundle(); results.putCharSequence(input.getResultKey(),answer);
            Intent response=new Intent(); RemoteInput.addResultsToIntent(new RemoteInput[]{input},response,results);
            action.actionIntent.send(ctx,0,response); return;
        }
    }
    public void onNotificationPosted(StatusBarNotification sbn) {
        if(sbn==null || !qqPackage(sbn.getPackageName())) return;
        Notification n=sbn.getNotification(); QqInbox inbox=QqInbox.get(this);
        if(!inbox.prefs.getBoolean("notifications",false) || QqServer.online || (n.flags&Notification.FLAG_GROUP_SUMMARY)!=0) return;
        capture(this,inbox,sbn.getPackageName(),sbn.getKey(),sbn.getPostTime(),n);
    }
    static void capture(Context ctx,QqInbox inbox,String pkg,String key,long posted,Notification n) {
        if(!qqPackage(pkg) || n==null || n.extras==null) return;
        Bundle extras=n.extras;
        String title=String.valueOf(extras.getCharSequence(Notification.EXTRA_TITLE,"")).trim();
        String body=String.valueOf(extras.getCharSequence(Notification.EXTRA_TEXT,"")).trim();
        boolean group=extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION,false) || extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)!=null;
        boolean knownChat=extras.containsKey(Notification.EXTRA_IS_GROUP_CONVERSATION);
        long stamp=n.when>0?n.when:posted;
        String author=""; Parcelable[] messages=extras.getParcelableArray(Notification.EXTRA_MESSAGES);
        if(messages!=null && messages.length>0 && messages[messages.length-1] instanceof Bundle) {
            Bundle last=(Bundle)messages[messages.length-1]; body=String.valueOf(last.getCharSequence("text","")).trim();
            stamp=last.getLong("time",stamp); author=String.valueOf(last.getCharSequence("sender","")).trim();
            if(Build.VERSION.SDK_INT>=28) {
                Person p=last.getParcelable("sender_person"); Person self=extras.getParcelable(Notification.EXTRA_MESSAGING_PERSON);
                if(p!=null && self!=null && (p.getKey()!=null && p.getKey().equals(self.getKey())
                        || p.getUri()!=null && p.getUri().equals(self.getUri()))) return;
            }
            if(author.isEmpty() && last.getParcelable("sender_person")==null) return;
        }
        if(title.isEmpty() || body.isEmpty() || body.matches(".*(?:收到.{0,6}条新消息|有新消息|内容已隐藏|解锁后查看).*")) return;
        if(stamp < inbox.prefs.getLong("notice_since",0)-1500 || System.currentTimeMillis()-stamp>180000) return;
        String shortcut=n.getShortcutId();
        String room="notice:"+pkg+":"+(shortcut!=null&&!shortcut.isEmpty()?shortcut:title);
        String receipt=QqInbox.hash(room+"|"+stamp+"|"+author+"|"+body);
        final Notification.Action action=replyAction(n,pkg);
        boolean addressed=inbox.called(body) || (knownChat&&!group) || inbox.prefs.getStringSet("private_titles",Collections.emptySet()).contains(title);
        boolean shouldReply=inbox.prefs.getInt("mode",0)!=2 && addressed && action!=null && !QqServer.online;
        String status=action==null?"已收到，QQ 未提供快捷回复":shouldReply?"等待回复":"同步接收";
        inbox.receive("notice:"+receipt,room,title,body,status,shouldReply?(id,reply)->{
            if(QqServer.online || !inbox.prefs.getBoolean("notifications",false) || inbox.prefs.getInt("mode",0)==2)
                { inbox.status(id,"已交给当前连接",null); return; }
            if(System.currentTimeMillis()-posted>120000) { inbox.status(id,"通知已过期",null); return; }
            submit(ctx,action,reply);
            inbox.status(id,"已提交给手机 QQ",reply);
        }:null);
    }
}
