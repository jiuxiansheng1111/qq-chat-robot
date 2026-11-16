package cn.dafeiyu.agent;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.IOException;
import java.net.URLEncoder;
import java.util.Collections;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class MobileWeather {
    private static final Pattern SAVE=Pattern.compile("^(?:我的天气城市是|把我的天气城市设为)(.{2,32})$");
    private static final Pattern CITY=Pattern.compile("^(?:请|帮我)?(?:看看|查一下|查查)?([㐀-鿿a-zA-Z ·-]{2,32}?)(?:的)?(?:今天|今日|明天)?(?:的)?天气(?:怎么样|如何|预报)?$");
    private static final Pattern AFTER=Pattern.compile("^天气[：:，, ]+(.{2,32})$");
    private static JSONObject get(String url) throws Exception {
        return new JSONObject(Agent.http(url,null,Collections.emptyMap(),true));
    }
    static String ask(String raw, Settings config) throws Exception {
        String q=raw.trim().replaceFirst("[。？?！!]+$","");
        Matcher saving=SAVE.matcher(q), city=CITY.matcher(q), after=AFTER.matcher(q);
        boolean save=saving.matches();
        boolean local=q.matches("(?:我这(?:里|边)?|当地|手机所在地|按手机IP|今天|今日|明天)?(?:的)?天气(?:怎么样|如何|预报)?");
        if(!save && !local && !city.matches() && !after.matches()) return null;
        String name=save?saving.group(1):local?config.prefs.getString("weather_city",""):after.matches()?after.group(1):city.group(1);
        name=name.trim().replaceFirst("市$","");
        boolean estimated=name.isEmpty();
        String place; double lat,lon;
        if(estimated) {
            try {
                JSONObject ip=get("https://ipwho.is/?fields=success,city,latitude,longitude&lang=zh-CN");
                if(!ip.optBoolean("success") || ip.optString("city").isEmpty()) throw new IOException();
                place=ip.getString("city"); lat=ip.getDouble("latitude"); lon=ip.getDouble("longitude");
            } catch(Exception unavailable) { throw new IOException("手机网络没有提供城市。可以直接问「深圳天气」，或说「我的天气城市是深圳」。"); }
        } else {
            JSONArray rows=get("https://geocoding-api.open-meteo.com/v1/search?count=8&language=zh&name="+URLEncoder.encode(name,"UTF-8")).optJSONArray("results");
            if(rows==null || rows.length()==0) throw new IOException("没有找到「"+name+"」。请补充城市全名。");
            JSONObject best=rows.getJSONObject(0);
            for(int i=0;i<rows.length();i++) {
                JSONObject candidate=rows.getJSONObject(i);
                if(name.equals(candidate.optString("name")) && "CN".equals(candidate.optString("country_code"))) { best=candidate; break; }
            }
            place=best.getString("name"); lat=best.getDouble("latitude"); lon=best.getDouble("longitude");
        }
        if(save) {
            config.prefs.edit().putString("weather_city",name).apply();
            return "以后问天气，我会先看"+place+"。";
        }
        String url="https://api.open-meteo.com/v1/forecast?latitude="+lat+"&longitude="+lon+
                "&timezone=auto&forecast_days=2&current=temperature_2m,apparent_temperature&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max";
        JSONObject data=get(url);
        return format(place,estimated,data);
    }
    static String format(String place,boolean estimated,JSONObject data) throws Exception {
        JSONObject now=data.getJSONObject("current"), daily=data.getJSONObject("daily");
        int current=(int)Math.round(now.getDouble("temperature_2m")), feels=(int)Math.round(now.getDouble("apparent_temperature"));
        JSONArray highs=daily.getJSONArray("temperature_2m_max"), lows=daily.getJSONArray("temperature_2m_min"), dates=daily.getJSONArray("time");
        if(dates.length()<2 || highs.length()<2 || lows.length()<2) throw new IOException("气象服务没有给出完整预报。");
        StringBuilder s=new StringBuilder(place+(estimated?"（按手机网络估算）":"")+" · "+now.getString("time").replace("T"," ")+" 当地时间\n");
        s.append("现在 ").append(current).append("℃，体感 ").append(feels).append("℃。");
        JSONArray rain=daily.optJSONArray("precipitation_probability_max");
        for(int i=0;i<2;i++) {
            s.append(String.format(Locale.ROOT,"\n%s（%s）%d～%d℃",i==0?"今天":"明天",dates.getString(i),Math.round(lows.getDouble(i)),Math.round(highs.getDouble(i))));
            if(rain!=null && !rain.isNull(i)) s.append("，降水概率 ").append(rain.getInt(i)).append("%");
        }
        if(feels<=10) s.append("\n今天体感偏冷，出门多带件外套吧。");
        else if(feels>=32) s.append("\n外面体感挺热，记得补水。");
        double change=highs.getDouble(1)-highs.getDouble(0);
        if(change<=-5) s.append("\n明天最高气温会低至少 5℃，衣服可以提前备好。");
        else if(change>=5) s.append("\n明天最高气温会高至少 5℃，中午可能更热。");
        if(rain!=null && Math.max(rain.optInt(0,-1),rain.optInt(1,-1))>=60) s.append("\n这两天有下雨的可能，出门带把伞。");
        return s.append("\n数据来自 Open-Meteo · https://open-meteo.com/").toString();
    }
}
