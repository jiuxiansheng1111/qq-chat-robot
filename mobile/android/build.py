"""Build and sign the phone app with JDK 17 and the standard Android 35 SDK."""
import argparse
import hashlib
import os
import secrets
import subprocess
import tempfile
import zipfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parent.parent


def run(*args, env=None):
    # The Windows aapt2 binary parses absolute non-ASCII arguments using the active code page.
    command = [str(args[0])]
    for arg in args[1:]:
        if os.name == "nt" and isinstance(arg, Path) and arg.drive.lower() == ROOT.drive.lower():
            command.append(os.path.relpath(arg, ROOT))
        else:
            command.append(str(arg))
    subprocess.run(command, check=True, env=env, cwd=ROOT)


def choose_key(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key, password = directory / "dafeiyu.p12", directory / "password"
    if key.exists() != password.exists():
        raise SystemExit("手机签名资料不完整，请从原备份恢复。")
    if not password.exists():
        with password.open("x", encoding="utf-8") as out:
            out.write(secrets.token_urlsafe(45))
        password.chmod(0o600)
    env = {**os.environ, "DAFEIYU_STORE_PASSWORD": password.read_text(encoding="utf-8").strip()}
    if not key.exists():
        run("keytool","-genkeypair","-storetype","PKCS12","-keystore",key,"-alias","dafeiyu","-keyalg","RSA","-keysize","3072",
            "-validity","10000","-dname","CN=DaFeiYu Mobile","-storepass:env","DAFEIYU_STORE_PASSWORD",env=env)
        key.chmod(0o600)
    return key, env


def build(sdk, destination, key, env, test):
    tools = sdk / "build-tools/35.0.0"
    platform = sdk / "platforms/android-35/android.jar"
    source = SOURCE / "test" if test else SOURCE
    ext = ".exe" if os.name=="nt" else ""
    if not platform.is_file() or not (tools / "lib/d8.jar").is_file():
        raise SystemExit("请准备 Android SDK Platform 35 和 Build Tools 35.0.0。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "data/mobile-build"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        temp = Path(directory)
        classes, dex, gen = (temp / name for name in ("classes","dex","generated"))
        for path in (classes,dex,gen): path.mkdir()
        apk = temp / "app.apk"
        resources=[]
        if (source / "res").exists():
            run(tools / ("aapt2"+ext),"compile","--dir",source / "res","-o",temp / "resources.zip")
            resources=[temp / "resources.zip"]
        run(tools / ("aapt2"+ext),"link","-o",apk,"-I",platform,"--manifest",source / "AndroidManifest.xml",
            "--min-sdk-version","26","--target-sdk-version","35","--version-code","2","--version-name","1.1","--java",gen,*resources)
        files = [*gen.rglob("*.java"), *(source / "src").rglob("*.java")]
        classpath = platform
        if test:
            stubs = temp / "app-api"
            stubs.mkdir()
            impl = SOURCE / "src/cn/dafeiyu/agent"
            run("javac","-encoding","UTF-8","--release","8","-classpath",platform,"-d",stubs,
                impl / "Agent.java",impl / "Settings.java",impl / "Turns.java",impl / "MobileWeather.java")
            classpath = os.pathsep.join(os.path.relpath(p, ROOT) for p in (platform, stubs))
        run("javac","-encoding","UTF-8","--release","8","-classpath",classpath,"-d",classes,*files)
        compiled=temp / "classes.jar"
        with zipfile.ZipFile(compiled,"w") as jar:
            for path in sorted(classes.rglob("*.class")): jar.write(path,path.relative_to(classes))
        run("java","-cp",tools / "lib/d8.jar","com.android.tools.r8.D8","--min-api","26","--lib",platform,"--output",dex,compiled)
        with zipfile.ZipFile(apk,"a") as archive:
            for path in dex.glob("*.dex"): archive.write(path,path.name)
        aligned=temp / "aligned.apk"
        run(tools / ("zipalign"+ext),"-f","4",apk,aligned)
        signer=tools / "lib/apksigner.jar"
        signed=temp / "signed.apk"
        run("java","-jar",signer,"sign","--ks",key,"--ks-key-alias","dafeiyu","--ks-pass","env:DAFEIYU_STORE_PASSWORD",
            "--v4-signing-enabled","false","--out",signed,aligned,env=env)
        run("java","-jar",signer,"verify","--verbose",signed)
        data=signed.read_bytes()
        draft=destination.with_suffix(".tmp")
        draft.write_bytes(data)
        draft.replace(destination)
        print(destination.name,len(data),"bytes · SHA-256",hashlib.sha256(data).hexdigest())


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--sdk",type=Path,default=Path(os.environ.get("ANDROID_HOME") or ROOT / "data/mobile-build/sdk"))
    parser.add_argument("--signing-dir",type=Path,default=ROOT / "data/mobile-signing")
    parser.add_argument("--output",type=Path,default=SOURCE / "downloads/Dafeiyu.apk")
    parser.add_argument("--test",action="store_true")
    args=parser.parse_args()
    build(args.sdk.resolve(),args.output.resolve(),*choose_key(args.signing_dir.resolve()),args.test)
