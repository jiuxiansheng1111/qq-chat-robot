package cn.dafeiyu.agent;

import android.content.*;
import android.database.Cursor;
import android.database.sqlite.*;
import java.util.*;
import java.util.concurrent.*;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

final class QqInbox extends SQLiteOpenHelper {
    static final String UPDATED="cn.dafeiyu.agent.QQ_UPDATED";
    private static final long RETENTION_MS=60*60*1000L;
    private static QqInbox instance;
    private final Context app;
    private final ThreadPoolExecutor worker=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,new ArrayBlockingQueue<Runnable>(32));
    final SharedPreferences prefs;
    static synchronized QqInbox get(Context ctx) {
        if(instance==null) {
            instance=new QqInbox(ctx.getApplicationContext());
            instance.prune();
            instance.getWritableDatabase().execSQL("UPDATE inbox SET state='发送未确认' WHERE state IN ('正在回复','等待回执','等待回复')");
        }
        return instance;
    }
    private QqInbox(Context ctx) { super(ctx,"qq-messages.db",null,1); app=ctx; prefs=ctx.getSharedPreferences("qq-channel",Context.MODE_PRIVATE); }
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE inbox (id INTEGER PRIMARY KEY,receipt TEXT UNIQUE NOT NULL,room TEXT NOT NULL,title TEXT NOT NULL,question TEXT NOT NULL,answer TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,time INTEGER NOT NULL)");
        db.execSQL("CREATE INDEX qq_room ON inbox(room,id)");
    }
    public void onUpgrade(SQLiteDatabase db,int before,int after) {}
    synchronized String token() {
        String token=prefs.getString("token","");
        if(token.isEmpty()) {
            byte[] bytes=new byte[30]; new java.security.SecureRandom().nextBytes(bytes);
            token=android.util.Base64.encodeToString(bytes,android.util.Base64.URL_SAFE|android.util.Base64.NO_WRAP|android.util.Base64.NO_PADDING);
            prefs.edit().putString("token",token).commit();
        }
        return token;
    }
    static String hash(String value) {
        try { return android.util.Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)),android.util.Base64.NO_WRAP); }
        catch(java.security.NoSuchAlgorithmException e) { throw new AssertionError(e); }
    }
    interface Delivery { void send(long id,String answer) throws Exception; }
    synchronized void prune() {
        SQLiteDatabase db=getWritableDatabase();
        db.delete("inbox","time<?",new String[]{String.valueOf(System.currentTimeMillis()-RETENTION_MS)});
        db.execSQL("DELETE FROM inbox WHERE id < (SELECT id FROM inbox ORDER BY id DESC LIMIT 1 OFFSET 199)");
    }
    synchronized long save(String receipt,String room,String title,String message,String state) {
        ContentValues data=new ContentValues(); data.put("receipt",receipt); data.put("room",room); data.put("title",Agent.clip(title,100));
        data.put("question",Agent.clip(message,2500)); data.put("state",state); data.put("time",System.currentTimeMillis());
        long id=getWritableDatabase().insertWithOnConflict("inbox",null,data,SQLiteDatabase.CONFLICT_IGNORE);
        if(id>0) { prune(); changed(); } return id;
    }
    void changed() { app.sendBroadcast(new Intent(UPDATED).setPackage(app.getPackageName())); }
    synchronized void status(long id,String state,String answer) {
        ContentValues c=new ContentValues(); c.put("state",state); if(answer!=null)c.put("answer",answer);
        getWritableDatabase().update("inbox",c,"id=?",new String[]{String.valueOf(id)}); changed();
    }
    void receive(String receipt,String room,String title,String text,String reason,Delivery deliver) {
        long id=save(receipt,room,title,text,reason);
        if(id<1 || deliver==null) return;
        try {
            worker.execute(()->{
                try {
                    status(id,"正在回复",null);
                    String reply=Agent.clip(new Agent(app).qqAnswer(history(room,id),text),1600);
                    status(id,"等待回执",reply); deliver.send(id,reply);
                } catch(Exception e) {
                    status(id,new Settings(app).configured()?"回复未完成，请查看连接或模型":"已收到，请先设置回复模型",null);
                }
            });
        } catch(RejectedExecutionException full) { status(id,"已收到，手机繁忙",null); }
    }
    synchronized List<Turns.Turn> history(String room,long before) {
        List<Turns.Turn> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT question,answer FROM (SELECT id,question,answer FROM inbox WHERE room=? AND id<? AND state IN ('QQ 已确认发送','已提交给手机 QQ') ORDER BY id DESC LIMIT 8) ORDER BY id",new String[]{room,String.valueOf(before)})) {
            while(c.moveToNext()) result.add(new Turns.Turn(c.getString(0),c.getString(1)));
        }
        return result;
    }
    synchronized List<String> recent() {
        prune();
        List<String> out=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT title,question,answer,state FROM inbox ORDER BY id DESC LIMIT 20",null)) {
            while(c.moveToNext()) out.add(c.getString(0)+" · "+c.getString(3)+"\n"+c.getString(1)+(c.getString(2).isEmpty()?"":"\n大肥鱼："+c.getString(2)));
        }
        return out;
    }
    boolean called(String text) {
        String name=prefs.getString("name","大肥鱼").trim();
        return (!name.isEmpty() && text.contains(name)) || text.contains("大肥鱼") || text.contains("大肥") || text.contains("欧鲸鲸");
    }
}
