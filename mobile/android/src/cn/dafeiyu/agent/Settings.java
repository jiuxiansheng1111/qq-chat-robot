package cn.dafeiyu.agent;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.net.URI;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class Settings {
    static final String[][] SERVICES = {
        {"DeepSeek","https://api.deepseek.com/v1","deepseek-flash","chat"},
        {"智谱 GLM","https://open.bigmodel.cn/api/paas/v4","glm-4-flash-250414","chat"},
        {"OpenAI GPT","https://api.openai.com/v1","","responses"},
        {"Anthropic Claude","https://api.anthropic.com","","messages"},
        {"Google Gemini","https://generativelanguage.googleapis.com/v1beta/openai","","chat"},
        {"Ollama（手机本机）","http://127.0.0.1:11434/v1","","chat"},
        {"自定义接口","","","chat"}
    };
    final SharedPreferences prefs;
    private final Context context;
    Settings(Context context) {
        this.context=context.getApplicationContext();
        prefs=context.getSharedPreferences("agent", Context.MODE_PRIVATE);
        SharedPreferences startup=startupPrefs(context);
        if(!startup.contains("running") && prefs.contains("running")) setRunning(prefs.getBoolean("running",false));
    }
    int service() { return prefs.getInt("service", 1); }
    String base() { return prefs.getString("base", SERVICES[service()][1]); }
    String model() { return prefs.getString("model", SERVICES[service()][2]); }
    String protocol() { return prefs.getString("protocol", SERVICES[service()][3]); }
    boolean configured() { return prefs.getBoolean("configured", false); }
    private static SharedPreferences startupPrefs(Context ctx) {
        return ctx.createDeviceProtectedStorageContext().getSharedPreferences("startup",Context.MODE_PRIVATE);
    }
    static boolean startAtBoot(Context ctx) { return startupPrefs(ctx).getBoolean("running",false); }
    boolean running() { return startAtBoot(context); }
    void setRunning(boolean enabled) { startupPrefs(context).edit().putBoolean("running",enabled).commit(); }
    boolean local() { return localHost(URI.create(base()).getHost()); }
    private static boolean localHost(String host) { return "127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host); }
    private SecretKey vaultKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias("model_api")) return (SecretKey)ks.getKey("model_api", null);
        KeyGenerator gen = KeyGenerator.getInstance("AES","AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder("model_api",KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return gen.generateKey();
    }
    String secret() throws Exception {
        String sealed = prefs.getString("credential", "");
        if (sealed.isEmpty()) return "";
        String[] pair = sealed.split("\\.", 2);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, vaultKey(), new GCMParameterSpec(128, Base64.decode(pair[0], Base64.NO_WRAP)));
        return new String(c.doFinal(Base64.decode(pair[1], Base64.NO_WRAP)), java.nio.charset.StandardCharsets.UTF_8);
    }
    void save(int selection, String address, String name, String format, String key) throws Exception {
        address = address.trim().replaceAll("/+$", "");
        name = name.trim();
        URI uri = new URI(address);
        if (uri.getHost()==null || uri.getUserInfo()!=null || uri.getFragment()!=null || uri.getQuery()!=null
                || !("https".equalsIgnoreCase(uri.getScheme()) || ("http".equals(uri.getScheme()) && localHost(uri.getHost()))))
            throw new IllegalArgumentException("请输入 HTTPS 接口地址；手机本机可以用 http://127.0.0.1。");
        if (name.isEmpty() || name.length()>200) throw new IllegalArgumentException("请填写服务商提供的模型名。");
        String sealed = selection==service() && address.equals(base()) && format.equals(protocol()) ? prefs.getString("credential","") : "";
        if (!key.isEmpty()) {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.ENCRYPT_MODE, vaultKey());
            sealed = Base64.encodeToString(c.getIV(),Base64.NO_WRAP) + "." + Base64.encodeToString(c.doFinal(key.getBytes(java.nio.charset.StandardCharsets.UTF_8)),Base64.NO_WRAP);
        }
        if (sealed.isEmpty() && !localHost(uri.getHost())) throw new IllegalArgumentException("请填写这个服务的 API 密钥。");
        if (!prefs.edit().putInt("service",selection).putString("base",address).putString("model",name)
                .putString("protocol",format).putString("credential",sealed).putBoolean("configured",true).commit())
            throw new java.io.IOException("模型配置未保存。");
    }
}
