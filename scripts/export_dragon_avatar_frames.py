"""Export production stills from our own local WebGL renderer, off screen."""
import os
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--autoplay-policy=no-user-gesture-required')
from pathlib import Path
import argparse,json,time
from PySide6.QtCore import Qt,QUrl,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
names=['neutral','smile','angry','pout','cry','shy','tsundere','surprise']
app=QApplication([])
view=QWebEngineView()
view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
view.resize(1000,750)
view.page().setBackgroundColor(QColor('#eee8f3'))
index=0;started=0;checking=False;written=[]
def load_next():
 global started
 if index==len(names):
  (out/'export-manifest.json').write_text(json.dumps({'files':written,'size':[1000,750],'renderer':'local WebGL; independent muted preview'},indent=2),encoding='utf-8')
  app.exit(0);return
 started=time.monotonic()
 view.setUrl(QUrl('http://127.0.0.1:6187/avatar?expression-preview='+names[index]+'&portrait=1'))
def inspect(value):
 global index,checking
 checking=False
 if value is True:
  name='expression-'+names[index]+'.png'
  if not view.grab().save(str(out/name)):
   app.exit(2);return
  written.append(name);print('Exported '+names[index],flush=True)
  index+=1;load_next()
def check():
 global checking
 if time.monotonic()-started>20:
  print('Frame render did not become ready',flush=True);app.exit(1);return
 if not checking and index<len(names):
  checking=True
  expected=json.dumps(names[index])
  view.page().runJavaScript("new URLSearchParams(location.search).get('expression-preview') === "+expected+" && document.querySelector('canvas')?.dataset.renderReady === 'true'",inspect)
view.show();timer=QTimer();timer.timeout.connect(check);timer.start(400)
load_next()
raise SystemExit(app.exec())
