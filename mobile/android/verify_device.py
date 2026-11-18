"""Exercise the shipped APK and the instrumented build on a clean Android emulator."""
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "cn.dafeiyu.agent"


def adb(*args, timeout=75):
    return subprocess.check_output(["adb", *map(str,args)], text=True, encoding="utf-8", errors="replace", timeout=timeout)


def wait_for(check, seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if check(): return
        time.sleep(2)
    raise RuntimeError("Android did not reach " + check.__name__)


def tree():
    adb("shell","uiautomator","dump","/data/local/tmp/dafeiyu-ui.xml")
    return ET.fromstring(adb("shell","cat","/data/local/tmp/dafeiyu-ui.xml"))


def tap(node):
    x1,y1,x2,y2=map(int,re.findall(r"\d+",node.attrib["bounds"]))
    adb("shell","input","tap",(x1+x2)//2,(y1+y2)//2)


def open_app():
    adb("shell","am","start","-W","-n",PACKAGE+"/.MainActivity")


def native_search():
    nodes=tree()
    tap(next(n for n in nodes.iter("node") if n.get("package")==PACKAGE and n.get("class")=="android.widget.EditText"))
    adb("shell","input","text","Android%smobile%soperating%ssystem")
    ime=adb("shell","dumpsys","input_method")
    if "mInputShown=true" in ime or "mIsInputViewShown=true" in ime: adb("shell","input","keyevent","KEYCODE_BACK")
    nodes=tree()
    actions=[n for n in nodes.iter("node") if n.get("package")==PACKAGE and n.get("text")=="联网搜索"]
    if not actions:
        visible=[(n.get("package"),n.get("class"),n.get("text","")[:64]) for n in nodes.iter("node") if n.get("text")]
        raise RuntimeError("Search button was not visible: "+str(visible)[:1400])
    tap(actions[0])
    def shows_search_sources():
        return any(n.get("text","").startswith("1. ") and "http" in n.get("text","") for n in tree().iter("node"))
    for attempt in range(3):
        try:
            wait_for(shows_search_sources,90)
            break
        except RuntimeError:
            visible=[n.get("text","")[:220] for n in tree().iter("node") if n.get("package")==PACKAGE and n.get("text")]
            print("Shipped search screen:", visible,flush=True)
            print("Public search connection:",adb("logcat","-d","-s","DaFeiYuSearch:W","*:S")[-1600:],flush=True)
            if attempt==2:
                raise
            retry=next((n for n in tree().iter("node") if n.get("package")==PACKAGE and n.get("text")=="重试"),None)
            if retry is not None:
                tap(retry)
    print("Shipped APK returned live web search sources directly on Android.",flush=True)


def instrument(phase):
    output=adb("shell","am","instrument","-w","-e","phase",phase,"cn.dafeiyu.agent.tests/cn.dafeiyu.agent.MobileChecks",timeout=180)
    if "MOBILE_PASS "+phase not in output: raise RuntimeError(output[-3000:])
    print("Android instrumentation passed:",phase,flush=True)


def service_online():
    details=adb("shell","dumpsys","activity","services",PACKAGE)
    return "AgentService" in details and "isForeground=true" in details


def main():
    if adb("shell","getprop","ro.kernel.qemu").strip()!="1":
        raise SystemExit("Run this clean-install and reboot check only on the test emulator.")
    adb("install","-r",ROOT/"mobile/android/downloads/Dafeiyu.apk")
    adb("shell","pm","grant",PACKAGE,"android.permission.POST_NOTIFICATIONS")
    adb("shell","input","keyevent","KEYCODE_WAKEUP")
    adb("shell","wm","dismiss-keyguard")
    open_app()
    native_search()
    adb("uninstall",PACKAGE)
    adb("install",ROOT/"data/mobile-build/ci.apk")
    adb("install",ROOT/"data/mobile-build/tests.apk")
    adb("shell","pm","grant",PACKAGE,"android.permission.POST_NOTIFICATIONS")
    # This is the same owner opt-in used to receive mobile QQ when the PC is off.
    adb("shell","cmd","notification","allow_listener",PACKAGE+"/.QqNotifications")
    open_app()
    instrument("first")
    open_app()
    adb("shell","input","keyevent","KEYCODE_HOME")
    adb("shell","input","keyevent","KEYCODE_SLEEP")
    wait_for(service_online,35)
    print("Foreground service is running with the screen off.",flush=True)
    adb("reboot")
    adb("wait-for-device",timeout=120)
    wait_for(lambda: adb("shell","getprop","sys.boot_completed").strip()=="1",150)
    adb("shell","input","keyevent","KEYCODE_WAKEUP")
    adb("shell","wm","dismiss-keyguard")
    wait_for(service_online,150)
    wait_for(lambda: ":6199" in adb("shell","ss","-ltn"),30)
    print("The phone's QQ listener recovered after reboot without opening the app.",flush=True)
    instrument("after-reboot")
    print("Phone recovered the service, encrypted credential and chat from its own storage.",flush=True)


if __name__=="__main__":
    try:
        main()
    except Exception:
        # This harness only runs on a clean emulator populated with our test queries.
        try:
            print("Android foreground window:",next((s for s in adb("shell","dumpsys","window").splitlines() if "mCurrentFocus" in s),""),flush=True)
            print("Android application crash:",adb("logcat","-d","-b","crash")[-2600:],flush=True)
            print("Boot signals:",adb("logcat","-d","-s","DaFeiYuBoot:I","*:S")[-800:],flush=True)
            print("Android user:",adb("shell","dumpsys","user").split("State:")[-1][:100],flush=True)
            print("App state:",[s.strip() for s in adb("shell","dumpsys","package",PACKAGE).splitlines() if "stopped=" in s][:1],flush=True)
            print("Broadcast diagnostics:",[s.strip()[:240] for s in adb("shell","dumpsys","activity","broadcasts").splitlines() if PACKAGE in s][-12:],flush=True)
        except (OSError, subprocess.SubprocessError) as diagnostic:
            print("Additional Android diagnostics unavailable:",type(diagnostic).__name__,flush=True)
        raise
