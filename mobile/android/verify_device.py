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
    tap(next(n for n in nodes.iter("node") if n.get("class")=="android.widget.EditText"))
    adb("shell","input","text","Android%smobile%soperating%ssystem")
    tap(next(n for n in tree().iter("node") if n.get("text")=="联网搜索"))
    def shows_search_sources():
        return any(n.get("text","").startswith("1. ") and "http" in n.get("text","") for n in tree().iter("node"))
    wait_for(shows_search_sources,120)
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
    open_app()
    native_search()
    adb("uninstall",PACKAGE)
    adb("install",ROOT/"data/mobile-build/ci.apk")
    adb("install",ROOT/"data/mobile-build/tests.apk")
    adb("shell","pm","grant",PACKAGE,"android.permission.POST_NOTIFICATIONS")
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
    wait_for(service_online,90)
    instrument("after-reboot")
    print("Phone recovered the service, encrypted credential and chat from its own storage.",flush=True)


if __name__=="__main__":
    main()
