"""PySide6 GUI: 앱 선택 → 버전 선택 → 빌드/배포 파이프라인 실행.

실제 빌드 로직은 builder.py (BuildPipeline) 에 있고, 여기서는 워커 스레드로 감싸
로그를 화면에 흘려보내는 역할만 한다.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from builder import (
    BuildCancelled,
    BuildError,
    BuildOptions,
    BuildPipeline,
    BuildResult,
    format_elapsed,
    read_latest_built_version,
    resolve_target_version,
)
from config import AppConfig, ConfigError, Settings, load_settings

STYLE_SHEET = """
QMainWindow, QWidget { background-color: #202124; color: #e8eaed; }
QGroupBox { border: 1px solid #3c4043; border-radius: 6px; margin-top: 10px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #bdc1c6; }
QPushButton { background-color: #3c4043; border-radius: 6px; padding: 8px 14px; }
QPushButton:hover { background-color: #5f6368; }
QPushButton:disabled { background-color: #2b2b2b; color: #777777; }
QPushButton#primary { background-color: #1a73e8; font-weight: 600; }
QPushButton#primary:hover { background-color: #3b8bf0; }
QPushButton#primary:disabled { background-color: #2b2b2b; color: #777777; }
QPushButton#danger { background-color: #b3261e; }
QPushButton#danger:hover { background-color: #d93025; }
QLineEdit, QComboBox {
    background-color: #2b2b2b; border: 1px solid #5f6368; border-radius: 4px;
    padding: 5px 8px; color: #f0f0f0;
}
QLineEdit:disabled { color: #777777; }
QComboBox QAbstractItemView { background-color: #2b2b2b; color: #f0f0f0; selection-background-color: #3c4043; }
QPlainTextEdit {
    background-color: #151515; color: #f0f0f0; border-radius: 8px; padding: 8px;
    font-family: Consolas, 'JetBrains Mono', 'Courier New', monospace; font-size: 12px;
}
QLabel#muted { color: #9aa0a6; }
QLabel#title { font-size: 20px; font-weight: 600; }
"""


class BuildWorker(QObject):
    """QThread 안에서 BuildPipeline 을 실행하고 결과를 시그널로 알린다."""

    log_signal = Signal(str)
    succeeded = Signal(object)  # BuildResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, app: AppConfig, settings: Settings, options: BuildOptions):
        super().__init__()
        self.app = app
        self.settings = settings
        self.options = options
        self.cancel_event = threading.Event()
        self.pipeline: BuildPipeline | None = None

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.pipeline is not None:
            self.pipeline.cancel()

    def _log(self, msg: str) -> None:
        self.log_signal.emit(f"[{datetime.now():%H:%M:%S}] {msg}")

    @Slot()
    def run(self) -> None:
        self.pipeline = BuildPipeline(
            self.app, self.settings, self.options, log=self._log, cancel_event=self.cancel_event
        )
        try:
            result = self.pipeline.run()
        except BuildCancelled:
            self.cancelled.emit()
        except BuildError as e:
            self.failed.emit(str(e))
        except Exception as e:  # 예상 못 한 예외도 UI 에 보여준다
            self.failed.emit(f"[예상치 못한 오류] {type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.thread: QThread | None = None
        self.worker: BuildWorker | None = None
        self._current_version: str | None = None

        self._build_ui()
        self._populate_apps()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.setWindowTitle("py2exe - 빌드 및 배포")
        # 최소 크기는 레이아웃이 계산하도록 두고 초기 크기만 지정한다 (고정 최소값이 레이아웃보다 작으면 내용이 눌린다)
        self.resize(920, 780)
        self.setStyleSheet(STYLE_SHEET)

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 제목 + 앱 선택
        header = QHBoxLayout()
        title = QLabel("통합 빌드 시스템")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("앱:"))
        self.app_combo = QComboBox()
        self.app_combo.setMinimumWidth(200)
        self.app_combo.currentIndexChanged.connect(self.on_app_changed)
        header.addWidget(self.app_combo)
        self.reload_button = QPushButton("설정 다시 읽기")
        self.reload_button.clicked.connect(self.reload_settings)
        header.addWidget(self.reload_button)
        root.addLayout(header)

        # 앱 정보
        info_box = QGroupBox("앱 정보")
        info = QGridLayout(info_box)
        info.setColumnStretch(1, 1)
        self.info_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate(
            [
                ("version", "현재 빌드 버전"),
                ("project", "프로젝트 경로"),
                ("exe_dir", "PyInstaller 출력"),
                ("output_dir", "인스톨러 출력"),
                ("python", "Python"),
                ("iscc", "Inno Setup"),
                ("r2", "Cloudflare R2"),
                ("api", "버전 서버"),
            ]
        ):
            cap = QLabel(caption)
            cap.setObjectName("muted")
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # 긴 경로가 창 폭을 늘리지 않도록 가로 크기 요구를 무시한다 (한 줄 라벨)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            info.addWidget(cap, row, 0)
            info.addWidget(value, row, 1)
            self.info_labels[key] = value
        root.addWidget(info_box)

        # 버전 선택
        version_box = QGroupBox("빌드 버전")
        version_layout = QVBoxLayout(version_box)
        radios = QHBoxLayout()
        self.radio_reuse = QRadioButton("현재 버전 재사용")
        self.radio_next = QRadioButton("패치 +1 (x.y.z → x.y.z+1)")
        self.radio_custom = QRadioButton("직접 입력")
        self.radio_next.setChecked(True)
        self.version_group = QButtonGroup(self)
        for radio in (self.radio_reuse, self.radio_next, self.radio_custom):
            self.version_group.addButton(radio)
            radios.addWidget(radio)
            radio.toggled.connect(self.update_version_preview)
        radios.addStretch(1)
        version_layout.addLayout(radios)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("직접 입력 버전:"))
        self.custom_version_edit = QLineEdit()
        self.custom_version_edit.setPlaceholderText("예: 1.2.3")
        self.custom_version_edit.setEnabled(False)
        self.custom_version_edit.textChanged.connect(self.update_version_preview)
        custom_row.addWidget(self.custom_version_edit, 1)
        version_layout.addLayout(custom_row)
        self.preview_label = QLabel("→ 빌드될 버전: -")
        self.preview_label.setObjectName("muted")
        self.preview_label.setWordWrap(True)
        version_layout.addWidget(self.preview_label)
        root.addWidget(version_box)

        # 배포 옵션
        options_box = QGroupBox("배포 옵션")
        options_layout = QGridLayout(options_box)
        self.check_installer = QCheckBox("Inno Setup 인스톨러 생성")
        self.check_installer.setChecked(True)
        self.check_installer.toggled.connect(self._sync_option_states)
        self.check_upload = QCheckBox("Cloudflare R2 업로드")
        self.check_upload.toggled.connect(self._sync_option_states)
        self.check_installer_as_update = QCheckBox("업데이트도 전체 설치 파일로 배포 (인스톨러를 _update.exe 로 사용)")
        self.check_register = QCheckBox("버전 서버에 최신 버전 등록")
        options_layout.addWidget(self.check_installer, 0, 0)
        options_layout.addWidget(self.check_upload, 0, 1)
        options_layout.addWidget(self.check_installer_as_update, 1, 0)
        options_layout.addWidget(self.check_register, 1, 1)
        root.addWidget(options_box)

        # 버튼
        buttons = QHBoxLayout()
        self.open_output_button = QPushButton("출력 폴더 열기")
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.clear_log_button = QPushButton("로그 지우기")
        self.clear_log_button.clicked.connect(lambda: self.log_edit.clear())
        buttons.addWidget(self.open_output_button)
        buttons.addWidget(self.clear_log_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_build)
        self.build_button = QPushButton("빌드 시작")
        self.build_button.setObjectName("primary")
        self.build_button.setFixedWidth(140)
        self.build_button.clicked.connect(self.start_build)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.build_button)
        root.addLayout(buttons)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        log_label = QLabel("빌드 로그")
        root.addWidget(log_label)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(20000)
        root.addWidget(self.log_edit, stretch=1)

        self.statusBar().showMessage(f"설정 파일: {self.settings.config_path}")

    # --------------------------------------------------------------- 상태
    def current_app(self) -> AppConfig | None:
        return self.app_combo.currentData()

    def _populate_apps(self) -> None:
        self.app_combo.blockSignals(True)
        previous = self.app_combo.currentText()
        self.app_combo.clear()
        for app in self.settings.apps:
            self.app_combo.addItem(app.app_name, app)
        if previous:
            idx = self.app_combo.findText(previous)
            if idx >= 0:
                self.app_combo.setCurrentIndex(idx)
        self.app_combo.blockSignals(False)

        # 기본 옵션: 자격 정보/서버 주소가 있으면 켠다
        self.check_upload.setChecked(self.settings.r2.is_configured)
        self.check_register.setChecked(bool(self.settings.version_api_url))
        self.check_register.setEnabled(bool(self.settings.version_api_url))
        self.check_register.setToolTip(
            "" if self.settings.version_api_url else "common.VERSION_API_URL 을 설정하면 사용할 수 있습니다."
        )
        self._sync_option_states()
        self.on_app_changed()

    def _sync_option_states(self) -> None:
        both = self.check_installer.isChecked() and self.check_upload.isChecked()
        self.check_installer_as_update.setEnabled(both)
        if not both:
            self.check_installer_as_update.setChecked(False)

    @Slot()
    def on_app_changed(self) -> None:
        app = self.current_app()
        if not app:
            for label in self.info_labels.values():
                label.setText("-")
            return

        self._current_version = read_latest_built_version(app.exe_directory, app.version_pattern)
        inno = self.settings.inno_setup_exe
        r2 = self.settings.r2
        values = {
            "version": self._current_version or "(없음 - 1.0.0 부터 시작)",
            "project": app.project_dir,
            "exe_dir": app.exe_directory,
            "output_dir": app.output_directory,
            "python": app.venv_python + ("" if os.path.isfile(app.venv_python) else "  (없음)"),
            "iscc": inno if inno and os.path.isfile(inno) else "(찾을 수 없음 - 인스톨러 생성 불가)",
            "r2": f"설정됨 ({r2.bucket_name})" if r2.is_configured else "미설정 (.env 에 " + ", ".join(r2.missing_fields()) + ")",
            "api": self.settings.version_api_url or "(미설정 - common.VERSION_API_URL)",
        }
        for key, text in values.items():
            self.info_labels[key].setText(text)
            self.info_labels[key].setToolTip(text)
        self.update_version_preview()

    @Slot()
    def update_version_preview(self) -> None:
        mode = self._selected_mode()
        self.custom_version_edit.setEnabled(mode == "custom")
        try:
            target = resolve_target_version(self._current_version, mode, self.custom_version_edit.text())
            self.preview_label.setText(f"→ 빌드될 버전: {target}")
        except ValueError as e:
            self.preview_label.setText(f"→ {e}")

    def _selected_mode(self) -> str:
        if self.radio_reuse.isChecked():
            return "reuse"
        if self.radio_custom.isChecked():
            return "custom"
        return "next"

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.build_button,
            self.app_combo,
            self.reload_button,
            self.radio_reuse,
            self.radio_next,
            self.radio_custom,
            self.check_installer,
            self.check_upload,
            self.check_installer_as_update,
        ):
            widget.setEnabled(not busy)
        self.check_register.setEnabled(not busy and bool(self.settings.version_api_url))
        self.custom_version_edit.setEnabled(not busy and self.radio_custom.isChecked())
        self.cancel_button.setEnabled(busy)
        if not busy:
            self._sync_option_states()

    # --------------------------------------------------------------- 동작
    @Slot()
    def reload_settings(self) -> None:
        try:
            self.settings = load_settings(self.settings.config_path)
        except ConfigError as e:
            QMessageBox.critical(self, "설정 오류", str(e))
            return
        self._populate_apps()
        self.statusBar().showMessage(f"설정을 다시 읽었습니다: {self.settings.config_path}", 5000)

    @Slot()
    def open_output_folder(self) -> None:
        app = self.current_app()
        if not app:
            return
        for path in (app.output_directory, app.exe_directory, app.project_dir):
            if os.path.isdir(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                return
        QMessageBox.information(self, "폴더 없음", "아직 생성된 출력 폴더가 없습니다.")

    @Slot(str)
    def append_log(self, text: str) -> None:
        self.log_edit.appendPlainText(text)
        bar = self.log_edit.verticalScrollBar()
        bar.setValue(bar.maximum())

    @Slot()
    def start_build(self) -> None:
        if self.thread is not None:
            QMessageBox.warning(self, "빌드 실행 중", "이미 빌드가 진행 중입니다.")
            return
        app = self.current_app()
        if not app:
            QMessageBox.warning(self, "선택 오류", "빌드할 앱을 선택하세요.")
            return

        options = BuildOptions(
            version_mode=self._selected_mode(),
            custom_version=self.custom_version_edit.text(),
            build_installer=self.check_installer.isChecked(),
            upload=self.check_upload.isChecked(),
            installer_as_update=self.check_installer_as_update.isChecked(),
            register_version=self.check_register.isChecked(),
        )

        # 값비싼 빌드를 돌리기 전에 설정 문제를 먼저 보여준다
        try:
            BuildPipeline(app, self.settings, options, log=lambda _: None).preflight()
        except BuildError as e:
            QMessageBox.critical(self, "사전 검증 실패", e.message)
            return

        if options.version_mode == "reuse" and self._current_version:
            answer = QMessageBox.question(
                self,
                "버전 재사용",
                f"{app.app_name} {self._current_version} 빌드를 덮어씁니다. 계속할까요?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.log_edit.clear()
        self._set_busy(True)

        self.thread = QThread(self)
        self.worker = BuildWorker(app, self.settings, options)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log_signal.connect(self.append_log)
        self.worker.succeeded.connect(self.on_build_succeeded)
        self.worker.failed.connect(self.on_build_failed)
        self.worker.cancelled.connect(self.on_build_cancelled)
        self.thread.start()

    @Slot()
    def cancel_build(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.append_log("취소 요청됨... 실행 중인 단계가 끝나면 중단됩니다.")

    def _finish_thread(self) -> None:
        if self.thread:
            self.thread.quit()
            self.thread.wait()
            self.thread.deleteLater()
        if self.worker:
            self.worker.deleteLater()
        self.thread = None
        self.worker = None
        self._set_busy(False)
        self.on_app_changed()

    @Slot(object)
    def on_build_succeeded(self, result: BuildResult) -> None:
        self._finish_thread()
        lines = [f"{result.app_name} {result.version} 빌드가 완료되었습니다. ({format_elapsed(result.elapsed_seconds)})"]
        if result.installer_path:
            lines.append(f"인스톨러: {result.installer_path}")
        if result.uploaded:
            lines.append("업로드: " + ", ".join(result.uploaded))
        if result.registered:
            lines.append("버전 서버에 등록되었습니다.")
        QMessageBox.information(self, "빌드 완료", "\n".join(lines))

    @Slot(str)
    def on_build_failed(self, message: str) -> None:
        self._finish_thread()
        self.append_log(f"[오류] {message}")
        QMessageBox.critical(self, "빌드 오류", message)

    @Slot()
    def on_build_cancelled(self) -> None:
        self._finish_thread()
        self.append_log("=== 빌드가 취소되었습니다 ===")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 규약)
        if self.thread is not None:
            answer = QMessageBox.question(self, "빌드 진행 중", "빌드를 중단하고 종료할까요?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_build()
            self._finish_thread()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    try:
        settings = load_settings()
    except ConfigError as e:
        QMessageBox.critical(None, "설정 오류", str(e))
        return 2
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
