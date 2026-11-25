"""Native desktop shell for the existing local Studio; shares its jobs/server."""
import ctypes
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, QBuffer, QIODevice, QEvent
from PySide6.QtGui import QDesktopServices, QIcon, QAction, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / 'studio_config.json').read_text(encoding='utf-8-sig'))
URL = f"http://127.0.0.1:{CONFIG['port']}/"


def server_ready():
    try:
        with urllib.request.urlopen(URL + 'api/state', timeout=.5) as response:
            return json.load(response).get('version') == 3
    except (OSError, ValueError):
        return False


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme()=='ez3d' and url.host()=='close':
            self.parent().window().close()
            return False
        if url.scheme() == 'about' or (url.host() in ('127.0.0.1', 'localhost') and url.port() == CONFIG['port']):
            return True
        if is_main_frame and url.scheme() in ('https', 'http'):
            QDesktopServices.openUrl(url)
        return False


class StudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ez3D')
        self.resize(1500, 960)
        self.setMinimumSize(1000, 650)
        self.setWindowIcon(QIcon(str(HERE / 'ez3d.ico')))
        QApplication.instance().installEventFilter(self)
        self.setStyleSheet('QMainWindow,QLabel {background:#1b1b1e;color:#bba9f3;}')
        self.status = QLabel('Iniciando ez3D…')
        self.setCentralWidget(self.status)
        self.started = time.monotonic()
        if not server_ready():
            logs = Path(CONFIG['work_root']) / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / 'desktop-server.log').open('ab') as stream:
                subprocess.Popen([sys.executable, '-u', str(HERE / 'server.py')], cwd=HERE,
                                 stdout=stream, stderr=stream,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.connect_studio)
        self.timer.start(500)

    def connect_studio(self):
        if server_ready():
            self.timer.stop()
            self.view = QWebEngineView(self)
            self.view.setPage(LocalPage(self.view))
            self.view.loadFinished.connect(lambda ok:self.view.page().runJavaScript('window.ez3dDesktop=true;') if ok else None)
            self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            self.setCentralWidget(self.view)
            self.view.load(QUrl(URL))
            for shortcut in ('F5', 'Ctrl+R', 'Ctrl+F5'):
                action = QAction(self)
                action.setShortcut(QKeySequence(shortcut))
                action.triggered.connect(self.view.reload)
                self.addAction(action)
        elif time.monotonic() - self.started > 30:
            self.timer.stop()
            QMessageBox.critical(self, 'ez3D', 'O servidor não iniciou. Consulte Work/logs/desktop-server.log.')
            self.close()

    def eventFilter(self, watched, event):
        if (self.isActiveWindow() and hasattr(self, 'view') and
            event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and
            event.matches(QKeySequence.StandardKey.Paste) and
            QApplication.clipboard().mimeData().hasImage()):
            event.accept()
            if event.type() == QEvent.Type.KeyPress:
                self.paste_reference()
            return True
        return super().eventFilter(watched, event)

    def paste_reference(self):
        clipboard = QApplication.clipboard()
        if not clipboard.mimeData().hasImage():
            self.view.page().triggerAction(QWebEnginePage.WebAction.Paste)
            return
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if clipboard.image().save(buffer, 'PNG'):
            data = 'data:image/png;base64,' + bytes(buffer.data().toBase64()).decode('ascii')
            self.view.page().runJavaScript(
                'window.dispatchEvent(new CustomEvent("ez3d-clipboard-image", {detail:' + json.dumps(data) + '}));')


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ez3D.Studio.Desktop')
    app = QApplication(sys.argv)
    app.setApplicationName('ez3D')
    app.setWindowIcon(QIcon(str(HERE / 'ez3d.ico')))
    socket = QLocalSocket()
    socket.connectToServer('ez3DDesktop')
    if socket.waitForConnected(400):
        socket.write(b'show')
        socket.waitForBytesWritten(400)
        return
    QLocalServer.removeServer('ez3DDesktop')
    server = QLocalServer()
    server.listen('ez3DDesktop')
    window = StudioWindow()

    def activate():
        connection = server.nextPendingConnection()
        if connection:
            connection.close()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(activate)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
