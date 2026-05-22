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
    QComboBox,
    QCheckBox,
)
from dotenv import load_dotenv
from config import INNO_SETUP_EXE, APPS
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


def create_spec_file(
    original_spec_file, new_spec_file, exe_name, folder_name, project_dir
):
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

    escaped_project_dir = project_dir.replace("\\", "/")
    spec_content = re.sub(
        r"APP_PATH\s*=\s*.*",
        f"APP_PATH = '{escaped_project_dir}'",
        spec_content,
        count=1,
    )

    with open(new_spec_file, "w", encoding="utf-8") as file:
        file.write(spec_content)


def build_exe_from_spec(
    spec_file, output_directory, version, app_config, log_func=None
):
    def log(msg):
        if log_func:
            log_func(msg)
        else:
            print(msg)

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    log(f"Building exe for spec: {spec_file}")

    app_name = app_config["APP_NAME"]
    project_dir = app_config["PROJECT_DIR"]
    venv_python = app_config["VENV_PYTHON"]

    folder_name = f"{app_name}_{version}"
    new_spec_file = os.path.join(output_directory, f"{folder_name}.spec")
    create_spec_file(spec_file, new_spec_file, app_name, folder_name, project_dir)

    try:
        cmd = [
            venv_python,
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

        log(f"Finished building {app_name} inside {folder_name}")
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


def read_latest_built_version(exe_directory, version_pattern) -> str | None:
    if not os.path.exists(exe_directory):
        return None

    versions = []
    for name in os.listdir(exe_directory):
        match = re.match(version_pattern, name)
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
        app_config,
        version_mode,
        custom_version,
        upload_installer=False,
        parent=None,
    ):
        super().__init__(parent)
        self.app_config = app_config
        self.version_mode = version_mode
        self.custom_version = custom_version.strip()
        self.upload_installer = upload_installer

        self.app_name = app_config["APP_NAME"]
        self.project_dir = app_config["PROJECT_DIR"]
        self.version_pattern = app_config["VERSION_PATTERN"]

        base_dir = (
            self.project_dir
            if self.project_dir
            else os.path.dirname(os.path.abspath(__file__))
        )
        self.spec_file = os.path.join(base_dir, app_config["DEFAULT_SPEC_NAME"])
        self.iss_path = os.path.join(base_dir, app_config["DEFAULT_ISS_NAME"])

        self.exe_directory = app_config["EXE_DIRECTORY"]
        self.output_directory = app_config["OUTPUT_DIRECTORY"]
        self._is_running = True

    def stop(self):
        self._is_running = False

    def _log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_signal.emit(f"[{timestamp}] {msg}")

    def run(self):
        start_time = datetime.now()
        try:
            if not os.path.exists(self.exe_directory):
                os.makedirs(self.exe_directory)
            if not os.path.exists(self.output_directory):
                os.makedirs(self.output_directory)

            current_version = read_latest_built_version(
                self.exe_directory, self.version_pattern
            )
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
                self.exe_directory, f"{self.app_name}_{target_version}"
            )
            if os.path.exists(same_version_path):
                shutil.rmtree(same_version_path)
                self._log(f"이전 동일 버전 디렉토리 삭제: {same_version_path}")

            old_update_exe_path = os.path.join(
                self.exe_directory, f"{self.app_name}_{target_version}_update.exe"
            )
            if os.path.exists(old_update_exe_path):
                os.remove(old_update_exe_path)
                self._log(f"이전 동일 버전 업데이트 파일 삭제: {old_update_exe_path}")

            self._log("PyInstaller 빌드 시작")
            build_exe_from_spec(
                self.spec_file,
                self.exe_directory,
                target_version,
                self.app_config,
                log_func=self._log,
            )
            self._log("PyInstaller 빌드 완료")

            built_folder_path = os.path.join(
                self.exe_directory, f"{self.app_name}_{target_version}"
            )
            raw_exe_new_path = os.path.join(built_folder_path, f"{self.app_name}.exe")

            update_exe_name = f"{self.app_name}_{target_version}_update.exe"
            update_exe_path = os.path.join(self.exe_directory, update_exe_name)

            if os.path.exists(raw_exe_new_path):
                shutil.copy2(raw_exe_new_path, update_exe_path)

            # Inno Setup 먼저 실행
            self._log("Inno Setup 버전 정보 업데이트")
            temp_iss_path = update_inno_version(self.iss_path, target_version)

            self._log("Inno Setup 실행 중...")
            inno_cmd = [
                INNO_SETUP_EXE,
                f"/DProjectBaseDir={os.path.dirname(self.exe_directory)}",
                f"/DSourceIconPath={os.path.join(self.project_dir, 'assets', 'imgs', 'icon.ico')}",
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

            setup_filename = f"{self.app_name}_{target_version}.exe"
            setup_file_path = os.path.join(self.output_directory, setup_filename)

            if self.upload_installer:
                # 체크O: 설치파일을 _update.exe 자리에도 복사해서 둘 다 설치파일로 업로드
                self._log("설치 파일을 업데이트용으로도 사용 (파일 크기 동일)")
                shutil.copy2(setup_file_path, update_exe_path)
                upload_file(update_exe_path)
                self._log(f"업데이트용 파일 업로드 완료: {update_exe_name}")
                upload_file(setup_file_path)
                self._log(f"설치용 파일 업로드 완료: {setup_filename}")
            else:
                # 체크X: 기존 방식 - 단일 exe를 업데이트용으로, 설치파일은 설치파일로 업로드
                upload_file(update_exe_path)
                self._log(f"업데이트용 파일 업로드 완료: {update_exe_name}")
                upload_file(setup_file_path)
                self._log(f"설치용 파일 업로드 완료: {setup_filename}")

            end_time = datetime.now()
            elapsed = end_time - start_time

            self._log("Pushover 알림 전송 완료")
            self.finished.emit(target_version, elapsed.total_seconds())

        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread: QThread | None = None
        self.worker: BuildWorker | None = None

        self.init_ui()
        self.on_app_changed()

    def init_ui(self):
        self.setWindowTitle("멀티 앱 빌드 및 배포 시스템")
        self.setMinimumSize(800, 600)

        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout()
        central.setLayout(main_layout)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        title_layout = QHBoxLayout()
        title_label = QLabel("통합 빌드 시스템")
        title_label.setStyleSheet("font-size: 22px; font-weight: 600;")

        self.app_combo = QComboBox()
        for app in APPS:
            self.app_combo.addItem(app["APP_NAME"], app)
        self.app_combo.setMinimumWidth(180)
        self.app_combo.currentIndexChanged.connect(self.on_app_changed)
        self.app_combo.setStyleSheet("""
            QComboBox {
                background-color: #2b2b2b;
                border: 1px solid #5f6368;
                border-radius: 4px;
                padding: 5px;
                color: #f0f0f0;
            }
        """)

        title_layout.addWidget(title_label)
        title_layout.addStretch(1)
        title_layout.addWidget(self.app_combo)
        main_layout.addLayout(title_layout)

        self.current_version_label = QLabel("현재 버전: -")
        self.current_version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_version_label.setStyleSheet("font-size: 14px; color: #bbbbbb;")
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

        self.upload_installer_check = QCheckBox(
            "업데이트를 전체 설치 파일로 배포 (Inno Setup .exe)"
        )
        self.upload_installer_check.setChecked(False)
        self.upload_installer_check.setStyleSheet("color: #e8eaed;")
        mid_layout.addWidget(self.upload_installer_check)

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
            QMainWindow { background-color: #202124; color: #f0f0f0; }
            QWidget { background-color: #202124; color: #f0f0f0; }
            QPushButton { background-color: #3c4043; border-radius: 6px; padding: 8px 14px; }
            QPushButton:hover { background-color: #5f6368; }
            QPushButton:disabled { background-color: #3c4043; color: #777777; }
            QRadioButton, QLabel { color: #e8eaed; }
            QLineEdit { background-color: #2b2b2b; border-radius: 4px; padding: 4px 8px; border: 1px solid #5f6368; color: #f0f0f0; }
        """)

    def on_app_changed(self):
        current_app_config = self.app_combo.currentData()
        if not current_app_config:
            return

        version = read_latest_built_version(
            current_app_config["EXE_DIRECTORY"], current_app_config["VERSION_PATTERN"]
        )
        if version:
            self.current_version_label.setText(
                f"[{current_app_config['APP_NAME']}] 현재 버전: {version}"
            )
        else:
            self.current_version_label.setText(
                f"[{current_app_config['APP_NAME']}] 현재 버전: 1.0.0 (기본값)"
            )

    def append_log(self, text: str):
        self.log_edit.appendPlainText(text)
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum()
        )

    def start_build(self):
        if self.thread is not None:
            QMessageBox.warning(self, "빌드 실행 중", "이미 빌드가 진행 중입니다.")
            return

        current_app_config = self.app_combo.currentData()
        if not current_app_config:
            QMessageBox.warning(
                self, "선택 오류", "선택된 앱 설정이 올바르지 않습니다."
            )
            return

        if self.radio_reuse.isChecked():
            mode = "reuse"
        elif self.radio_next.isChecked():
            mode = "next"
        else:
            mode = "custom"

        custom_version = self.custom_version_edit.text()

        self.log_edit.clear()
        self.append_log(f"=== [{current_app_config['APP_NAME']}] 빌드 작업 시작 ===")
        self.build_button.setEnabled(False)
        self.app_combo.setEnabled(False)

        self.thread = QThread(self)
        self.worker = BuildWorker(
            app_config=current_app_config,
            version_mode=mode,
            custom_version=custom_version,
            upload_installer=self.upload_installer_check.isChecked(),
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
        self.app_combo.setEnabled(True)

    def on_build_finished(self, version: str, elapsed_seconds: float):
        current_app_config = self.app_combo.currentData()
        app_name = current_app_config["APP_NAME"] if current_app_config else "APP"
        self.append_log(
            f"=== 빌드 완료: {app_name}_{version} "
            f"({int(elapsed_seconds // 60)}분 {int(elapsed_seconds % 60)}초) ==="
        )
        QMessageBox.information(
            self, "빌드 완료", f"{app_name} {version} 빌드 및 배포가 완료되었습니다."
        )
        self.on_app_changed()

    def on_build_error(self, message: str):
        self.append_log(f"[오류] {message}")
        QMessageBox.critical(
            self, "빌드 오류", f"빌드 중 오류가 발생했습니다:\n\n{message}"
        )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
