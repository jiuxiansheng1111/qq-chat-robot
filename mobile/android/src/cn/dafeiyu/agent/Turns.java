package cn.dafeiyu.agent;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.ArrayList;
import java.util.List;

final class Turns extends SQLiteOpenHelper {
    static final class Turn {
        final long id;
        final String question, answer, mode, state;
        Turn(Cursor row) { id=row.getLong(0); question=row.getString(1); answer=row.getString(2); mode=row.getString(3); state=row.getString(4); }
    }
    private static Turns instance;
    static synchronized Turns get(Context context) {
        if (instance==null) {
            instance=new Turns(context.getApplicationContext());
            instance.recover();
        }
        return instance;
    }
    private Turns(Context context) { super(context,"conversations.db",null,1); }
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE turns (id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL, state TEXT NOT NULL)");
    }
    public void onUpgrade(SQLiteDatabase db, int previous, int next) {}
    synchronized long enqueue(String q, String mode) {
        ContentValues v = new ContentValues();
        v.put("question",q); v.put("mode",mode); v.put("state","queued");
        return getWritableDatabase().insertOrThrow("turns",null,v);
    }
    synchronized Turn next() {
        try (Cursor cursor=getReadableDatabase().rawQuery("SELECT id,question,answer,mode,state FROM turns WHERE state='queued' ORDER BY id LIMIT 1",null)) {
            if (!cursor.moveToFirst()) return null;
            Turn t = new Turn(cursor);
            getWritableDatabase().execSQL("UPDATE turns SET state='processing' WHERE id=?",new Object[]{t.id});
            return t;
        }
    }
    synchronized boolean hasQueued() {
        try(Cursor c=getReadableDatabase().rawQuery("SELECT 1 FROM turns WHERE state='queued' LIMIT 1",null)) {
            return c.moveToFirst();
        }
    }
    synchronized void finish(long id, String answer, boolean success) {
        getWritableDatabase().execSQL("UPDATE turns SET answer=?,state=? WHERE id=? AND state='processing'",new Object[]{answer,success?"done":"failed",id});
    }
    synchronized void recover() {
        getWritableDatabase().execSQL("UPDATE turns SET state='failed',answer='手机中断了这次请求，点重试可重新发送。' WHERE state='processing'");
    }
    synchronized boolean retry(long id) {
        ContentValues v = new ContentValues(); v.put("state","queued"); v.put("answer","");
        return getWritableDatabase().update("turns",v,"id=? AND state='failed'",new String[]{String.valueOf(id)})==1;
    }
    synchronized List<Turn> recent(boolean history, long before) {
        String filter = history ? " WHERE state='done' AND id<?" : " WHERE id<?";
        List<Turn> result = new ArrayList<>();
        String sql="SELECT id,question,answer,mode,state FROM (SELECT * FROM turns"+filter+" ORDER BY id DESC LIMIT "+(history?8:50)+") ORDER BY id";
        try (Cursor c=getReadableDatabase().rawQuery(sql,new String[]{String.valueOf(before)})) {
            while(c.moveToNext()) result.add(new Turn(c));
        }
        return result;
    }
}
