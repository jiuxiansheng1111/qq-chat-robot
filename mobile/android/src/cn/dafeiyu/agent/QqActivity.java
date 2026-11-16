package cn.dafeiyu.agent;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;
import java.util.*;
import org.json.JSONObject;

public final class QqActivity extends Activity {
    private LinearLayout page;
    private TextView status,address,recent;
    private EditText self,alias,titles;
    private QqInbox qq;
    private final BroadcastReceiver refresh=new BroadcastReceiver(){public void onReceive(Context c,Intent i){drawStatus();}};
    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private TextView text(String s,int size){TextView v=new TextView(this); v.setText(s); v.setTextSize(size); v.setPadding(0,dp(5),0,dp(5)); page.addView(v); return v;}
    private Button button(String s,View.OnClickListener click){Button b=new Button(this);b.setText(s);b.setAllCaps(false);b.setOnClickListener(click);page.addView(b);return b;}
    private EditText field(String title,String value){text(title,14);EditText e=new EditText(this);e.setSingleLine();e.setText(value);page.addView(e);return e;}
    private void keepRunning() {
        new cn.dafeiyu.agent.Settings(this).setRunning(true);
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},43);
        AgentService.start(this);
    }
    private Switch toggle(String title,String pref,boolean initial) {
        Switch b=new Switch(this);b.setText(title);b.setPadding(0,dp(8),0,dp(8));b.setChecked(qq.prefs.getBoolean(pref,initial));page.addView(b);
        b.setOnCheckedChangeListener((v,on)->{
            SharedPreferences.Editor edit=qq.prefs.edit().putBoolean(pref,on);
            if("notifications".equals(pref)&&on)edit.putLong("notice_since",System.currentTimeMillis());
            edit.apply();
            if(on && ("onebot".equals(pref)||"notifications".equals(pref))) keepRunning();
            else if(qq.prefs.getBoolean("onebot",false)||"onebot".equals(pref)) AgentService.start(this);
            drawStatus();
        });return b;
    }
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);qq=QqInbox.get(this);
        ScrollView scroll=new ScrollView(this);page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(18),dp(12),dp(18),dp(18));scroll.addView(page);setContentView(scroll);
        if(Build.VERSION.SDK_INT>=30){getWindow().setDecorFitsSystemWindows(false);scroll.setOnApplyWindowInsetsListener((v,i)->{
            android.graphics.Insets bars=i.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());v.setPadding(bars.left,bars.top,bars.right,bars.bottom);return WindowInsets.CONSUMED;});}
        text("QQ 收件箱",24);status=text("",15);
        text("两端收消息，谁来回复",17);
        String[] choices={"电脑主答，断开后使用手机 QQ 通知","手机主答（独立协议端）","只接收"};
        Spinner mode=new Spinner(this);mode.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,choices));page.addView(mode);
        mode.setSelection(qq.prefs.getInt("mode",0));
        mode.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            public void onNothingSelected(AdapterView<?> a){}
            public void onItemSelected(AdapterView<?> a,View v,int i,long id){qq.prefs.edit().putInt("mode",i).apply();}
        });
        toggle("接收这台手机的 QQ 通知","notifications",false);
        text("用机器人 QQ 登录手机 QQ。授权后能读 QQ 的新通知；通知带快捷回复时，手机可以回到原会话。",14);
        button("打开系统通知使用权限",v->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        toggle("接入 QQ 协议端（OneBot）","onebot",false);
        toggle("允许同一私网的电脑连接","lan",true);
        address=text("",14);address.setTextIsSelectable(true);
        button("复制电脑配对信息",v->{
            String host=QqServer.host(this,qq.prefs.getBoolean("lan",true));
            if(!qq.prefs.getBoolean("onebot",false)||!qq.prefs.getBoolean("lan",true)||host.isEmpty()){
                Toast.makeText(this,"请启用 OneBot 和同一私网连接，再接入 Wi-Fi。",Toast.LENGTH_LONG).show();return;}
            try {
                String setup=new JSONObject().put("url","ws://"+host+":"+QqServer.PORT+"/ws").put("token",qq.token()).toString();
                ClipData clip=ClipData.newPlainText("大肥鱼 QQ 配对",setup);
                if(Build.VERSION.SDK_INT>=33){PersistableBundle hints=new PersistableBundle();hints.putBoolean(ClipDescription.EXTRA_IS_SENSITIVE,true);clip.getDescription().setExtras(hints);}
                getSystemService(ClipboardManager.class).setPrimaryClip(clip);
                Toast.makeText(this,"已复制地址和配对令牌，请只粘到自己的电脑。",Toast.LENGTH_LONG).show();
            }catch(Exception unused){Toast.makeText(this,"未能复制配对信息。",Toast.LENGTH_SHORT).show();}
        });
        self=field("机器人 QQ 号（可选，用于核对 OneBot）",qq.prefs.getString("self",""));
        self.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        alias=field("群里称呼机器人时用的名字",qq.prefs.getString("name","大肥鱼"));
        titles=field("允许直接回复的通知会话（完整名称，用逗号隔开）",String.join("，",qq.prefs.getStringSet("private_titles",Collections.emptySet())));
        button("保存 QQ 设置",v->{
            String id=self.getText().toString().trim(), name=alias.getText().toString().trim();
            if(!id.isEmpty()&&!id.matches("[0-9]{5,18}")) { self.setError("请填写机器人 QQ 号");return; }
            if(name.isEmpty()||name.length()>40){alias.setError("请输入简短称呼");return;}
            Set<String> rooms=new LinkedHashSet<>();for(String n:titles.getText().toString().split("[，,]"))if(!n.trim().isEmpty())rooms.add(n.trim());
            qq.prefs.edit().putString("self",id).putString("name",name).putStringSet("private_titles",rooms).apply();
            if(qq.prefs.getBoolean("onebot",false)) AgentService.start(this);
            drawStatus();
            Toast.makeText(this,"已保存",Toast.LENGTH_SHORT).show();
        });
        text("最近一小时收到的 QQ 消息",18);recent=text("",15);recent.setTextIsSelectable(true);
    }
    private void drawStatus(){
        if(status==null || address==null || recent==null)return;
        cn.dafeiyu.agent.Settings model=new cn.dafeiyu.agent.Settings(this);
        boolean listening=qq.prefs.getBoolean("notifications",false);
        status.setText(QqServer.state+"\n手机 QQ 通知 · "+(!listening?"未启用":QqNotifications.connected?"已获系统授权":"等待系统授权")+"\n回复模型 · "+(model.configured()?model.model():"尚未设置"));
        String host=QqServer.host(this,qq.prefs.getBoolean("lan",true));
        address.setText(host.isEmpty()?"连接 Wi-Fi 后会显示配对地址。":"手机接收地址　ws://"+host+":"+QqServer.PORT+"/ws");
        List<String> messages=qq.recent();recent.setText(messages.isEmpty()?"还没有收到 QQ 消息。":String.join("\n\n",messages));
    }
    public void onStart(){super.onStart();if(Build.VERSION.SDK_INT>=33)registerReceiver(refresh,new IntentFilter(QqInbox.UPDATED),Context.RECEIVER_NOT_EXPORTED);else registerReceiver(refresh,new IntentFilter(QqInbox.UPDATED));drawStatus();}
    public void onStop(){unregisterReceiver(refresh);super.onStop();}
}
