import sys
import os
import re
import shutil
import subprocess
from datetime import datetime

from packaging.version import Version

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QButtonGroup,
    QMessageBox,
    QFrame,
)
from dotenv import load_dotenv
from config import (
    VENV_PYTHON,
    INNO_SETUP_EXE,
    EXE_DIRECTORY,
    OUTPUT_DIRECTORY,
    APP_NAME,
    VERSION_PATTERN,
    DEFAULT_SPEC_NAME,
    DEFAULT_ISS_NAME,
    PROJECT_DIR,
)
from cloudflare import upload_file

load_dotenv()


def update_inno_version(iss_path: str, new_version: str):
    temp_iss_path = os.path.join(os.path.dirname(iss_path), "setup_temp.iss")

    with open(iss_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = re.sub(
        r'#define\s+MyAppVersion\s+"[\w.\-]+"',
        f'#define MyAppVersion "{new_version}"',
        content,
        count=1,
    )

    with open(temp_iss_path, "w", encoding="utf-8") as f:
        f.write(content)

    return temp_iss_path


def create_spec_file(original_spec_file, new_spec_file, exe_name, folder_name):
    with open(original_spec_file, "r", encoding="utf-8") as file:
        spec_content = file.read()

    spec_content = re.sub(
        r"name\s*=\s*['\"]SkyBoxAuto_VersionPlaceHolder['\"]",
        f"name='{folder_name}'",
        spec_content,
    )

    spec_content = re.sub(
        r"name\s*=\s*['\"]SkyBoxAuto['\"]", f"name='{exe_name}'", spec_content
    )

    escaped_project_dir = PROJECT_DIR.replace("\\", "/")
    spec_content = re.sub(
        r"APP_PATH\s*=\s*.*",
        f"APP_PATH = '{escaped_project_dir}'",
        spec_content,
        count=1,
    )

    with open(new_spec_file, "w", encoding="utf-8") as file:
        file.write(spec_content)


def build_exe_from_spec(spec_file, output_directory, version, log_func=None):
    def log(msg):
        if log_func:
            log_func(msg)
        else:
            print(msg)

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    log(f"Building exe for spec: {spec_file}")

    folder_name = f"{APP_NAME}_{version}"
    new_spec_file = os.path.join(output_directory, f"{folder_name}.spec")
    create_spec_file(spec_file, new_spec_file, APP_NAME, folder_name)

    try:
        cmd = [
            VENV_PYTHON,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            output_directory,
            "--workpath",
            os.path.join(output_directory, "build"),
            new_spec_file,
        ]
        log(f"Running PyInstaller: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in process.stdout:
            log(line.rstrip())
        process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)

        log(f"Finished building {APP_NAME} inside {folder_name}")
    finally:
        try:
            if os.path.exists(new_spec_file):
                os.remove(new_spec_file)
            build_path = os.path.join(os.path.dirname(new_spec_file), "build")
            if os.path.exists(build_path):
                shutil.rmtree(build_path)
            log(f"Cleaned temporary files in {os.path.dirname(new_spec_file)}")
        except Exception as e:
            log(f"Error: {e}")


def read_latest_built_version() -> str | None:
    if not os.path.exists(EXE_DIRECTORY):
        return None

    versions = []
    for name in os.listdir(EXE_DIRECTORY):
        match = re.match(VERSION_PATTERN, name)
        if match:
            try:
                versions.append(Version(match.group(1)))
            except:
                continue

    if not versions:
        return None

    return str(max(versions))


class BuildWorker(QObject):
    log_signal = Signal(str)
    finished = Signal(str, float)
    error = Signal(str)

    def __init__(
        self,
        version_mode: str,
        custom_version: str,
        spec_file: str,
        iss_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self.version_mode = version_mode
        self.custom_version = custom_version.strip()
        self.spec_file = spec_file
        self.iss_path = iss_path
        self.output_directory = EXE_DIRECTORY
        self._is_running = True

    def stop(self):
        self._is_running = False

    def _log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_signal.emit(f"[{timestamp}] {msg}")

    def run(self):
        start_time = datetime.now()
        try:
            current_version = read_latest_built_version()
            if not current_version:
                self._log("이전 빌드 버전을 찾을 수 없어 기본값 '1.0.0'을 설정합니다.")
                current_version = "1.0.0"
            else:
                self._log(f"현재 버전: {current_version}")

            if self.version_mode == "reuse":
                target_version = current_version
            elif self.version_mode == "next":
                current = Version(current_version)
                next_version = Version(
                    f"{current.major}.{current.minor}.{current.micro + 1}"
                )
                target_version = str(next_version)
            elif self.version_mode == "custom":
                if not self.custom_version:
                    raise ValueError("직접 입력 버전이 비어 있습니다.")
                _ = Version(self.custom_version)
                target_version = self.custom_version
            else:
                raise ValueError(f"알 수 없는 version_mode: {self.version_mode}")

            self._log(f"빌드 대상 버전: {target_version}")

            same_version_path = os.path.join(
                self.output_directory, f"{APP_NAME}_{target_version}"
            )
            if os.path.exists(same_version_path):
                shutil.rmtree(same_version_path)
                self._log(f"이전 동일 버전 디렉토리 삭제: {same_version_path}")

            old_update_exe_path = os.path.join(
                self.output_directory, f"{APP_NAME}_{target_version}_update.exe"
            )
            if os.path.exists(old_update_exe_path):
                os.remove(old_update_exe_path)
                self._log(f"이전 동일 버전 업데이트 파일 삭제: {old_update_exe_path}")

            self._log("PyInstaller 빌드 시작")
            build_exe_from_spec(
                self.spec_file,
                self.output_directory,
                target_version,
                log_func=self._log,
            )
            self._log("PyInstaller 빌드 완료")

            built_folder_path = os.path.join(
                self.output_directory, f"{APP_NAME}_{target_version}"
            )
            raw_exe_new_path = os.path.join(built_folder_path, f"{APP_NAME}.exe")

            update_exe_name = f"{APP_NAME}_{target_version}_update.exe"
            update_exe_path = os.path.join(self.output_directory, update_exe_name)

            if os.path.exists(raw_exe_new_path):
                shutil.copy2(raw_exe_new_path, update_exe_path)

            upload_file(os.path.join(self.output_directory, update_exe_name))
            self._log("업로드 완료")

            self._log("Inno Setup 버전 정보 업데이트")
            temp_iss_path = update_inno_version(self.iss_path, target_version)

            self._log("Inno Setup 실행 중...")
            inno_cmd = [
                INNO_SETUP_EXE,
                f"/DProjectBaseDir={os.path.dirname(EXE_DIRECTORY)}",
                f"/DSourceIconPath={os.path.join(PROJECT_DIR, 'assets', 'imgs', 'icon.ico')}",
                temp_iss_path,
            ]

            process = subprocess.Popen(
                inno_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in process.stdout:
                self._log(line.rstrip())
            process.wait()

            if process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode, [INNO_SETUP_EXE]
                )
            self._log("Inno Setup 완료")

            try:
                os.remove(temp_iss_path)
                self._log("임시 setup_temp.iss 삭제")
            except Exception as e:
                self._log(f"[경고] 임시 파일 삭제 실패: {e}")

            setup_filename = f"{APP_NAME}_{target_version}.exe"
            self._log(f"신규 설치용 파일 업로드 시작: {setup_filename}")
            upload_file(os.path.join(OUTPUT_DIRECTORY, setup_filename))

            end_time = datetime.now()
            elapsed = end_time - start_time

            self._log("Pushover 알림 전송 완료")
            self.finished.emit(target_version, elapsed.total_seconds())

        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, spec_file: str, iss_path: str):
        super().__init__()
        self.spec_file = spec_file
        self.iss_path = iss_path

        self.thread: QThread | None = None
        self.worker: BuildWorker | None = None

        self.init_ui()
        self.load_current_version()

    def init_ui(self):
        self.setWindowTitle(f"{APP_NAME} 빌드 및 배포 시스템")
        self.setMinimumSize(800, 600)

        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout()
        central.setLayout(main_layout)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        title_label = QLabel(f"{APP_NAME} 빌드 및 배포 시스템")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("""
            QLabel {
                font-size: 22px;
                font-weight: 600;
            }
        """)

        self.current_version_label = QLabel("현재 버전: -")
        self.current_version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_version_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                color: #bbbbbb;
            }
        """)

        main_layout.addWidget(title_label)
        main_layout.addWidget(self.current_version_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)

        mid_layout = QVBoxLayout()
        mid_layout.setSpacing(10)

        mode_label = QLabel("빌드 버전 선택")
        mode_label.setStyleSheet("font-size: 14px; font-weight: 500;")
        mid_layout.addWidget(mode_label)

        btn_layout = QHBoxLayout()
        self.radio_reuse = QRadioButton("현재 버전 재사용")
        self.radio_next = QRadioButton("패치 버전 +1 (x.y.z → x.y.z+1)")
        self.radio_custom = QRadioButton("직접 입력")

        self.radio_next.setChecked(True)

        self.version_group = QButtonGroup(self)
        self.version_group.addButton(self.radio_reuse)
        self.version_group.addButton(self.radio_next)
        self.version_group.addButton(self.radio_custom)

        btn_layout.addWidget(self.radio_reuse)
        btn_layout.addWidget(self.radio_next)
        btn_layout.addWidget(self.radio_custom)
        mid_layout.addLayout(btn_layout)

        custom_layout = QHBoxLayout()
        custom_label = QLabel("직접 입력 버전:")
        self.custom_version_edit = QLineEdit()
        self.custom_version_edit.setPlaceholderText("예: 1.2.3")
        custom_layout.addWidget(custom_label)
        custom_layout.addWidget(self.custom_version_edit)

        mid_layout.addLayout(custom_layout)

        build_btn_layout = QHBoxLayout()
        build_btn_layout.addStretch(1)
        self.build_button = QPushButton("빌드 시작")
        self.build_button.setFixedWidth(140)
        self.build_button.clicked.connect(self.start_build)
        build_btn_layout.addWidget(self.build_button)
        mid_layout.addLayout(build_btn_layout)

        main_layout.addLayout(mid_layout)

        log_label = QLabel("빌드 로그")
        log_label.setStyleSheet("font-size: 14px; font-weight: 500;")
        main_layout.addWidget(log_label)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet("""
            QPlainTextEdit {
                background-color: #151515;
                color: #f0f0f0;
                border-radius: 8px;
                padding: 8px;
                font-family: Consolas, 'JetBrains Mono', 'Courier New', monospace;
                font-size: 12px;
            }
        """)
        main_layout.addWidget(self.log_edit, stretch=1)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #202124;
                color: #f0f0f0;
            }
            QWidget {
                background-color: #202124;
                color: #f0f0f0;
            }
            QPushButton {
                background-color: #3c4043;
                border-radius: 6px;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background-color: #5f6368;
            }
            QPushButton:disabled {
                background-color: #3c4043;
                color: #777777;
            }
            QRadioButton, QLabel {
                color: #e8eaed;
            }
            QLineEdit {
                background-color: #2b2b2b;
                border-radius: 4px;
                padding: 4px 8px;
                border: 1px solid #5f6368;
                color: #f0f0f0;
            }
        """)

    def append_log(self, text: str):
        self.log_edit.appendPlainText(text)
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum()
        )

    def load_current_version(self):
        version = read_latest_built_version()
        if version:
            self.current_version_label.setText(f"현재 버전: {version}")
        else:
            self.current_version_label.setText("현재 버전: 1.0.0 (기본값)")

    def start_build(self):
        if self.thread is not None:
            QMessageBox.warning(self, "빌드 실행 중", "이미 빌드가 진행 중입니다.")
            return

        if self.radio_reuse.isChecked():
            mode = "reuse"
        elif self.radio_next.isChecked():
            mode = "next"
        else:
            mode = "custom"

        custom_version = self.custom_version_edit.text()

        self.log_edit.clear()
        self.append_log("=== 빌드 작업 시작 ===")
        self.build_button.setEnabled(False)

        self.thread = QThread(self)
        self.worker = BuildWorker(
            version_mode=mode,
            custom_version=custom_version,
            spec_file=self.spec_file,
            iss_path=self.iss_path,
        )
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished.connect(self.on_build_finished)
        self.worker.error.connect(self.on_build_error)
        self.worker.finished.connect(lambda *_: self.cleanup_thread())
        self.worker.error.connect(lambda *_: self.cleanup_thread())

        self.thread.start()

    def cleanup_thread(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None
        self.build_button.setEnabled(True)

    def on_build_finished(self, version: str, elapsed_seconds: float):
        self.append_log(
            f"=== 빌드 완료: {APP_NAME}_{version} "
            f"({int(elapsed_seconds // 60)}분 {int(elapsed_seconds % 60)}초) ==="
        )
        QMessageBox.information(
            self, "빌드 완료", f"{APP_NAME} {version} 빌드 및 배포가 완료되었습니다."
        )
        self.load_current_version()

    def on_build_error(self, message: str):
        self.append_log(f"[오류] {message}")
        QMessageBox.critical(
            self, "빌드 오류", f"빌드 중 오류가 발생했습니다:\n\n{message}"
        )


def main():
    app = QApplication(sys.argv)

    base_dir = (
        PROJECT_DIR if PROJECT_DIR else os.path.dirname(os.path.abspath(__file__))
    )
    spec_file = os.path.join(base_dir, DEFAULT_SPEC_NAME)
    iss_path = os.path.join(base_dir, DEFAULT_ISS_NAME)

    window = MainWindow(spec_file, iss_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
