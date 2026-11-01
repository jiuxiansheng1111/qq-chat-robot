"""Frameless transparent, draggable desktop dragon maid. Right click for controls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--autoplay-policy=no-user-gesture-required')
from PySide6.QtCore import Qt, QUrl, QTimer, QObject, Slot, QPoint
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QMenu
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ROOT = Path(__file__).resolve().parents[1]
POSITION = ROOT/'artifacts/dragon-maid-live2d/desktop-position.json'


class Bridge(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.widget=widget
        self.anchor=None

    @Slot(float, float)
    def begin_drag(self, x, y):
        self.anchor=QPoint(round(x),round(y))-self.widget.pos()

    @Slot(float, float)
    def drag(self, x, y):
        if self.anchor is not None:
            self.widget.move(QPoint(round(x),round(y))-self.anchor)

    @Slot()
    def end_drag(self):
        self.anchor=None
        self.widget.save_position()

    @Slot()
    def menu(self):
        self.widget.context_menu()

    @Slot()
    def open_control(self):
        QDesktopServices.openUrl(QUrl('http://127.0.0.1:6187/'))

    @Slot(int)
    def zoom(self, direction):
        self.widget.change_size(1.07 if direction > 0 else 1/1.07)


class Desktop(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('龙女仆 · 桌面皮套')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(330,500)
        screen=QApplication.primaryScreen().availableGeometry()
        self.move(screen.right()-370,screen.bottom()-530)
        try:
            pos=json.loads(POSITION.read_text(encoding='utf-8'))
            if any(s.availableGeometry().contains(pos['x'],pos['y']) for s in QApplication.screens()):
                self.move(pos['x'],pos['y'])
            self.resize(max(220,min(660,pos.get('width',330))),max(340,min(1000,pos.get('height',500))))
        except (OSError,ValueError,KeyError,TypeError):
            pass
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.view=QWebEngineView(self)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet('background: transparent;')
        self.view.page().setBackgroundColor(QColor(0,0,0,0))
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,False)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.channel=QWebChannel(self.view.page());self.bridge=Bridge(self)
        self.channel.registerObject('desktop',self.bridge);self.view.page().setWebChannel(self.channel)
        self.view.setUrl(QUrl('http://127.0.0.1:6187/avatar?desktop=1'))
        layout.addWidget(self.view)
        self.reload_shortcut=QShortcut(QKeySequence('F5'),self)
        self.reload_shortcut.activated.connect(self.view.reload)
        self.view.page().recentlyAudibleChanged.connect(lambda audible:(ROOT/'artifacts/dragon-maid-live2d/desktop-audio-status.json').write_text(json.dumps({'audible':audible,'muted':self.view.page().isAudioMuted()}),encoding='utf-8'))

    def save_position(self):
        POSITION.write_text(json.dumps({'x':self.x(),'y':self.y(),'width':self.width(),'height':self.height()}),encoding='utf-8')

    def context_menu(self):
        menu=QMenu(self)
        menu.addAction('打开表情与语音控制台',self.bridge.open_control)
        menu.addSeparator()
        for title,factor in [('放大一点',1.15),('缩小一点',.87)]:
            menu.addAction(title,lambda f=factor:self.change_size(f))
        sound=menu.addAction('桌面语音');sound.setCheckable(True);sound.setChecked(not self.view.page().isAudioMuted())
        sound.triggered.connect(lambda checked:self.view.page().setAudioMuted(not checked))
        top=menu.addAction('始终在最前');top.setCheckable(True);top.setChecked(bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        top.triggered.connect(self.set_top)
        menu.addSeparator();menu.addAction('退出桌面龙女仆',self.close)
        menu.exec(QCursor.pos())

    def change_size(self,factor):
        self.resize(max(220,min(660,round(self.width()*factor))),max(340,min(1000,round(self.height()*factor))))
        self.save_position()

    def set_top(self,checked):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,checked);self.show()

    def closeEvent(self,event):
        self.save_position();super().closeEvent(event);QApplication.quit()


if __name__=='__main__':
    app=QApplication(sys.argv)
    app.setApplicationName('DragonMaidDesktop')
    socket=QLocalSocket();socket.connectToServer('qqchat-dragon-maid-desktop-v1')
    if socket.waitForConnected(300):
        socket.write(b'show');socket.flush();socket.waitForBytesWritten(300);sys.exit(0)
    QLocalServer.removeServer('qqchat-dragon-maid-desktop-v1')
    server=QLocalServer();server.listen('qqchat-dragon-maid-desktop-v1')
    widget=Desktop()
    def wake():
        connection=server.nextPendingConnection()
        widget.show();widget.raise_();connection.disconnectFromServer()
    server.newConnection.connect(wake)
    widget.show()
    QTimer.singleShot(1200, lambda: (ROOT/'artifacts/dragon-maid-live2d/desktop-status.json').write_text(json.dumps({
        'pid':os.getpid(),'visible':widget.isVisible(),'x':widget.x(),'y':widget.y(),
        'width':widget.width(),'height':widget.height(),'window_id':int(widget.winId()),
    }),encoding='utf-8'))
    sys.exit(app.exec())
