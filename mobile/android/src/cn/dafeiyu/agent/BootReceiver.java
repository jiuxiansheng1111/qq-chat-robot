package cn.dafeiyu.agent;

import android.content.*;

public final class BootReceiver extends BroadcastReceiver {
    public void onReceive(Context context, Intent intent) {
        if(Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) && new Settings(context).running()) AgentService.start(context);
    }
}
