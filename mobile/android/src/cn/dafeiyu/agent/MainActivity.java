package cn.dafeiyu.agent;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.speech.tts.TextToSpeech;
import android.speech.tts.Voice;
import android.text.InputType;
import android.text.util.Linkify;
import android.view.*;
import android.widget.*;
import java.util.List;

public final class MainActivity extends Activity {
    private final int ink=Color.rgb(24,53,73), ocean=Color.rgb(26,84,119), paper=Color.rgb(244,249,250);
    private LinearLayout root, feed;
    private TextView status;
    private ScrollView scroll;
    private EditText input;
    private Settings config;
    private Turns turns;
    private TextToSpeech speech;
    private boolean canRead;
    private final BroadcastReceiver updates=new BroadcastReceiver() {
        public void onReceive(Context c,Intent i) { render(); }
    };
    int dp(int n) { return Math.round(n*getResources().getDisplayMetrics().density); }
    GradientDrawable surface(int color,int radius) { GradientDrawable d=new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius)); return d; }
    TextView label(String value,int size) {
        TextView t=new TextView(this); t.setText(value); t.setTextSize(size); t.setTextColor(ink); return t;
    }
    LinearLayout column() { LinearLayout c=new LinearLayout(this); c.setOrientation(LinearLayout.VERTICAL); return c; }
    LinearLayout row() { LinearLayout r=new LinearLayout(this); r.setGravity(Gravity.CENTER_VERTICAL); return r; }
    void pad(View v,int x,int y) { v.setPadding(dp(x),dp(y),dp(x),dp(y)); }
    Button button(String name,View.OnClickListener click,boolean filled) {
        Button b=new Button(this); b.setText(name); b.setAllCaps(false); b.setTextSize(15); b.setMinWidth(dp(60));
        b.setMinimumWidth(dp(60)); b.setMinHeight(dp(44)); b.setMinimumHeight(dp(44)); pad(b,10,4);
        b.setTextColor(filled?Color.WHITE:ocean); b.setBackground(surface(filled?ocean:Color.rgb(225,239,242),14)); b.setOnClickListener(click); return b;
    }
    LinearLayout.LayoutParams grow() { return new LinearLayout.LayoutParams(0,-2,1); }
    public void onCreate(Bundle state) {
        super.onCreate(state); config=new Settings(this); turns=Turns.get(this);
        getWindow().setStatusBarColor(paper); getWindow().setNavigationBarColor(paper);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        if(Build.VERSION.SDK_INT>=30) getWindow().setDecorFitsSystemWindows(false);
        root=column(); root.setBackgroundColor(paper); setContentView(root);
        if(Build.VERSION.SDK_INT>=30) root.setOnApplyWindowInsetsListener((v,i)->{
            android.graphics.Insets bars=i.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());
            v.setPadding(bars.left,bars.top,bars.right,bars.bottom); return WindowInsets.CONSUMED;
        });
        LinearLayout top=row(); pad(top,16,10); root.addView(top);
        LinearLayout titles=column(); TextView name=label("大肥鱼",26); name.setTypeface(Typeface.DEFAULT,Typeface.BOLD); titles.addView(name);
        status=label("",13); titles.addView(status); top.addView(titles,grow());
        top.addView(button("模型",v->settings(),false));
        scroll=new ScrollView(this); scroll.setFillViewport(true); feed=column(); pad(feed,16,12); scroll.addView(feed);
        root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout entry=column(); pad(entry,12,8); entry.setBackgroundColor(Color.WHITE); root.addView(entry);
        input=new EditText(this); input.setTextSize(16); input.setMaxLines(5); input.setMinLines(1); input.setHint("给大肥鱼发消息，或贴一个网页地址");
        input.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_MULTI_LINE); input.setBackground(surface(paper,12)); pad(input,12,9); entry.addView(input);
        LinearLayout actions=row(); actions.setGravity(Gravity.END|Gravity.CENTER_VERTICAL); entry.addView(actions);
        actions.addView(button("联网搜索",v->send("search"),false));
        LinearLayout.LayoutParams gap=new LinearLayout.LayoutParams(-2,-2); gap.leftMargin=dp(8); gap.topMargin=dp(5);
        actions.addView(button("发送",v->send("chat"),true),gap);
        if(state!=null) input.setText(state.getString("draft",""));
        input.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_SEND);
        input.setOnEditorActionListener((v,action,event)->{
            if(action!=android.view.inputmethod.EditorInfo.IME_ACTION_SEND) return false;
            send("chat"); return true;
        });
        speech=new TextToSpeech(this,code->runOnUiThread(()->{
            if(code!=TextToSpeech.SUCCESS || speech==null || isDestroyed() || speech.getVoices()==null) return;
            for(Voice voice:speech.getVoices()) {
                if("zh".equals(voice.getLocale().getLanguage()) && !voice.isNetworkConnectionRequired()
                        && (voice.getFeatures()==null || !voice.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED))
                        && speech.setVoice(voice)==TextToSpeech.SUCCESS) { canRead=true; render(); break; }
            }
        }));
        if(config.running() || turns.hasQueued()) AgentService.start(this);
    }
    public void onSaveInstanceState(Bundle b) { super.onSaveInstanceState(b); b.putString("draft",input.getText().toString()); }
    public void onStart() {
        super.onStart();
        if(Build.VERSION.SDK_INT>=33) registerReceiver(updates,new IntentFilter(AgentService.UPDATED),Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(updates,new IntentFilter(AgentService.UPDATED));
        render();
    }
    public void onStop() { unregisterReceiver(updates); super.onStop(); }
    public void onDestroy() { if(speech!=null) speech.shutdown(); super.onDestroy(); }
    void send(String mode) {
        String q=input.getText().toString().trim();
        if(q.isEmpty()) { input.setError("先写一句话"); return; }
        if(q.length()>3000) { input.setError("一条最多 3000 字"); return; }
        turns.enqueue(q,mode); input.setText(""); render(); AgentService.start(this);
    }
    void bubble(String text,boolean user) {
        TextView t=label(text,16); pad(t,13,10); t.setTextIsSelectable(true); t.setLineSpacing(dp(2),1);
        Linkify.addLinks(t,Linkify.WEB_URLS); t.setLinkTextColor(ocean);
        t.setBackground(surface(user?Color.rgb(210,235,242):Color.WHITE,15));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-2,-2); lp.gravity=user?Gravity.END:Gravity.START;
        lp.leftMargin=user?dp(30):0; lp.rightMargin=user?0:dp(20); lp.bottomMargin=dp(8); feed.addView(t,lp);
    }
    void render() {
        status.setText((config.configured()?config.model():"联网搜索可用")+(config.running()?" · 已启用后台":""));
        feed.removeAllViews(); List<Turns.Turn> items=turns.recent(false,Long.MAX_VALUE);
        if(items.isEmpty()) { bubble("我在手机上。想查什么，直接说「帮我搜一下」；点右上角选好模型，我们就可以聊天了。",false); return; }
        for(Turns.Turn t:items) {
            bubble(t.question,true); bubble(t.answer.isEmpty()?("queued".equals(t.state)?"等候处理…":"正在回复…"):t.answer,false);
            if("failed".equals(t.state)) feed.addView(button("重试",v->{ if(turns.retry(t.id)) { render(); AgentService.start(this); } },false),new LinearLayout.LayoutParams(-2,-2));
        }
        Turns.Turn last=items.get(items.size()-1);
        if(canRead && "done".equals(last.state)) feed.addView(button("朗读回复",v->{
            if(speech.isSpeaking()) { speech.stop(); return; }
            String prose=last.answer.replaceAll("https?://\\S+","");
            speech.speak(Agent.clip(prose,TextToSpeech.getMaxSpeechInputLength()),TextToSpeech.QUEUE_FLUSH,null,"last-reply");
        },false),new LinearLayout.LayoutParams(-2,-2));
        scroll.post(()->scroll.fullScroll(View.FOCUS_DOWN));
    }
    EditText field(LinearLayout box,String title,int type) {
        box.addView(label(title,14)); EditText v=new EditText(this); v.setSingleLine(true); v.setInputType(type); box.addView(v); return v;
    }
    Spinner select(LinearLayout box,String title,String[] options) {
        box.addView(label(title,14)); Spinner s=new Spinner(this);
        ArrayAdapter<String> a=new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,options); s.setAdapter(a); box.addView(s); return s;
    }
    void settings() {
        ScrollView frame=new ScrollView(this); LinearLayout box=column(); pad(box,18,6); frame.addView(box);
        String[] names=new String[Settings.SERVICES.length];
        for(int i=0;i<names.length;i++) names[i]=Settings.SERVICES[i][0];
        Spinner service=select(box,"模型服务",names);
        EditText base=field(box,"接口地址",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
        EditText model=field(box,"模型名称",InputType.TYPE_CLASS_TEXT); model.setHint("填账号可用的模型名");
        String[] protocols={"chat","responses","messages"};
        Spinner protocol=select(box,"协议",new String[]{"Chat Completions","OpenAI Responses","Anthropic Messages"});
        EditText key=field(box,"API 密钥",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        key.setHint("已保存的同一服务可留空"); key.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        TextView warning=label("",14); warning.setTextColor(Color.rgb(143,52,35)); box.addView(warning);
        service.setSelection(config.service());
        service.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(AdapterView<?> a) {}
            public void onItemSelected(AdapterView<?> a,View v,int i,long id) {
                boolean saved=i==config.service(); String[] preset=Settings.SERVICES[i];
                base.setText(saved?config.base():preset[1]); model.setText(saved?config.model():preset[2]); key.setText("");
                protocol.setSelection(java.util.Arrays.asList(protocols).indexOf(saved?config.protocol():preset[3]));
            }
        });
        Switch background=new Switch(this); background.setText("允许后台运行和开机恢复"); background.setChecked(config.running()); pad(background,0,10); box.addView(background);
        background.setOnCheckedChangeListener((b,checked)->{
            config.prefs.edit().putBoolean("running",checked).apply();
            if(checked && Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},42);
            AgentService.start(this); render();
        });
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("手机上的大肥鱼").setView(frame)
                .setNegativeButton("关闭",null).setPositiveButton("保存模型",null).create();
        dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try {
                config.save(service.getSelectedItemPosition(),base.getText().toString(),model.getText().toString(),
                        protocols[protocol.getSelectedItemPosition()],key.getText().toString().trim());
                dialog.dismiss(); render();
            } catch(Exception e) { warning.setText(e instanceof IllegalArgumentException?e.getMessage():"设置未保存，请检查地址与手机密钥存储。"); }
        });
    }
}
