package cn.dafeiyu.agent;

import android.content.*;

public final class BootReceiver extends BroadcastReceiver {
    public void onReceive(Context context, Intent intent) {
        String action=intent.getAction();
        if(!Intent.ACTION_BOOT_COMPLETED.equals(action) && !Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) return;
        boolean enabled=Settings.startAtBoot(context);
        android.util.Log.i("DaFeiYuBoot","Boot or update received; background enabled="+enabled);
        if(enabled) AgentService.start(context);
    }
}
