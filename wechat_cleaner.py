import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QProgressBar, QTextEdit, QRadioButton,
                             QTabWidget, QMessageBox, QGroupBox, QSpinBox, QCheckBox)
from scanner import CoreLogic, Utils, ScannerThread
from db_manager import DatabaseManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.target_dir = None
        self.global_migration_dir = None
        self.scan_thread = None

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('WeChat Cleaner Pro (Engineer Edition)')
        self.setGeometry(300, 300, 800, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top_group = QGroupBox("基础设置")
        top_layout = QVBoxLayout()

        path_layout = QHBoxLayout()
        self.lbl_path = QLabel("请选择'WeChat Files'目录或具体微信号目录")
        self.lbl_path.setStyleSheet("color: gray;")
        btn_path = QPushButton("📂 选择微信数据目录")
        btn_path.clicked.connect(self.select_source_dir)
        path_layout.addWidget(btn_path)
        path_layout.addWidget(self.lbl_path)
        top_layout.addLayout(path_layout)

        mig_layout = QHBoxLayout()
        self.lbl_mig_path = QLabel("默认隔离/归档目录: (未设置，将在扫描时询问)")
        self.lbl_mig_path.setStyleSheet("color: gray;")
        btn_mig = QPushButton("📦 设置全局迁移目录")
        btn_mig.clicked.connect(self.select_migration_dir)
        mig_layout.addWidget(btn_mig)
        mig_layout.addWidget(self.lbl_mig_path)
        top_layout.addLayout(mig_layout)

        top_group.setLayout(top_layout)
        layout.addWidget(top_group)

        # --- 区域 2：功能 Tabs ---
        self.tabs = QTabWidget()
        self.tab_dedup = QWidget()
        self.tab_cold = QWidget()

        self.init_dedup_tab()
        self.init_cold_tab()

        self.tabs.addTab(self.tab_dedup, "🧹 重复/版本清理")
        self.tabs.addTab(self.tab_cold, "❄️ 冷数据归档 (MsgAttach)")
        layout.addWidget(self.tabs)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.txt_log = QTextEdit()
        self.txt_log.setPlaceholderText("日志输出区域...")
        layout.addWidget(self.txt_log)

    def init_dedup_tab(self):
        layout = QVBoxLayout(self.tab_dedup)

        filter_group = QGroupBox("文件类型筛选")
        filter_layout = QHBoxLayout()
        self.chk_doc = QCheckBox("文档 (Word/PDF/Excel)");
        self.chk_doc.setChecked(True)
        self.chk_vid = QCheckBox("视频 (MP4/MOV)");
        self.chk_vid.setChecked(True)
        self.chk_img = QCheckBox("图片 (JPG/PNG)");
        self.chk_img.setChecked(False)
        self.chk_zip = QCheckBox("压缩包 (ZIP/RAR)");
        self.chk_zip.setChecked(True)

        filter_layout.addWidget(self.chk_doc)
        filter_layout.addWidget(self.chk_vid)
        filter_layout.addWidget(self.chk_img)
        filter_layout.addWidget(self.chk_zip)
        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)

        # 2. 策略选择
        mode_layout = QHBoxLayout()
        self.rb_strict = QRadioButton("严格去重 (MD5)")
        self.rb_strict.setToolTip("内容完全一致才清理")
        self.rb_strict.setChecked(True)

        self.rb_fuzzy = QRadioButton("版本去重 (Fuzzy)")
        self.rb_fuzzy.setToolTip("大小差异<30%且同后缀，保留最新版")

        mode_layout.addWidget(QLabel("模式:"))
        mode_layout.addWidget(self.rb_strict)
        mode_layout.addWidget(self.rb_fuzzy)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        # 3. 操作按钮
        btn_layout = QHBoxLayout()
        btn_scan = QPushButton("开始扫描")
        btn_scan.clicked.connect(self.start_dedup_scan)
        self.btn_clean_dedup = QPushButton("执行清理 (移入隔离区)")
        self.btn_clean_dedup.setEnabled(False)
        self.btn_clean_dedup.clicked.connect(self.run_clean_dedup)

        btn_layout.addWidget(btn_scan)
        btn_layout.addWidget(self.btn_clean_dedup)
        layout.addLayout(btn_layout)

    def init_cold_tab(self):
        layout = QVBoxLayout(self.tab_cold)

        info = QLabel("自动识别选定目录下的所有微信号 (wxid_xxx/FileStorage/MsgAttach)，"
                      "将超过指定时间的加密/未知文件迁移走。")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QHBoxLayout()
        form.addWidget(QLabel("迁移超过"))
        self.spin_days = QSpinBox()
        self.spin_days.setRange(30, 3650)
        self.spin_days.setValue(180)
        form.addWidget(self.spin_days)
        form.addWidget(QLabel("天未修改的文件"))
        form.addStretch()
        layout.addLayout(form)

        btn_run = QPushButton("扫描并迁移冷数据")
        btn_run.clicked.connect(self.run_cold_move)
        layout.addWidget(btn_run)
        layout.addStretch()


    def log(self, text):
        self.txt_log.append(text)
        self.txt_log.moveCursor(self.txt_log.textCursor().End)

    def select_source_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择微信数据目录 (WeChat Files)")
        if d:
            self.target_dir = d
            self.lbl_path.setText(d)
            self.lbl_path.setStyleSheet("color: black; font-weight: bold;")

    def select_migration_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择默认迁移/归档目录")
        if d:
            self.global_migration_dir = d
            self.lbl_mig_path.setText(d)
            self.lbl_mig_path.setStyleSheet("color: black;")

    def get_selected_extensions(self):
        exts = []
        if self.chk_doc.isChecked(): exts.extend(['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf'])
        if self.chk_vid.isChecked(): exts.extend(['.mp4', '.mov', '.avi', '.mkv'])
        if self.chk_img.isChecked(): exts.extend(['.jpg', '.png', '.jpeg', '.dat'])
        if self.chk_zip.isChecked(): exts.extend(['.zip', '.rar', '.7z'])
        return exts

    def start_dedup_scan(self):
        if not self.target_dir:
            QMessageBox.warning(self, "提示", "请先在顶部选择微信文件夹！")
            return

        mode = 'strict' if self.rb_strict.isChecked() else 'fuzzy'
        exts = self.get_selected_extensions()

        self.btn_clean_dedup.setEnabled(False)
        self.txt_log.clear()
        self.progress.setValue(0)

        self.scan_thread = ScannerThread(self.target_dir, mode, self.db, extensions=exts)
        self.scan_thread.progress_val.connect(self.progress.setValue)
        self.scan_thread.progress_text.connect(self.log)
        self.scan_thread.finished.connect(self.on_scan_finished)
        self.scan_thread.error.connect(lambda e: QMessageBox.critical(self, "扫描出错", e))
        self.scan_thread.start()

        self.log(f"启动扫描... 模式: {mode}")

    def on_scan_finished(self, report):
        self.log("\n" + "=" * 30)
        self.log(report)
        self.btn_clean_dedup.setEnabled(True)
        QMessageBox.information(self, "扫描完成", "分析结束，请查看日志。\n如需清理，请点击'执行清理'按钮。")

    def run_clean_dedup(self):
        dest = self.global_migration_dir
        if not dest:
            dest = QFileDialog.getExistingDirectory(self, "选择隔离区存储目录")

        if not dest: return

        rows = self.db.get_results()
        files_to_move = [r[0] for r in rows]

        if not files_to_move:
            self.log("数据库中没有待清理记录。")
            return

        try:
            folder, count, size = CoreLogic.move_files(files_to_move, dest, "dedup")
            self.log(f"清理成功！已移至{folder}")
            QMessageBox.information(self, "成功", f"移动了{count}个文件\n释放空间: {Utils.format_size(size)}")
            self.btn_clean_dedup.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, "清理失败", str(e))

    def run_cold_move(self):
        if not self.target_dir:
            QMessageBox.warning(self, "提示", "请先选择微信文件夹！")
            return

        dest = self.global_migration_dir
        if not dest:
            dest = QFileDialog.getExistingDirectory(self, "选择冷数据存放目录")
        if not dest: return

        days = self.spin_days.value()
        self.log(f"正在识别微信号目录并查找超过{days}天的文件...")

        targets = Utils.detect_wechat_paths(self.target_dir, "FileStorage/MsgAttach")
        if not targets:
            QMessageBox.warning(self, "未找到目标", f"在 {self.target_dir} 下未找到任何wxid目录或MsgAttach文件夹。")
            return

        self.log(f"已识别到 {len(targets)} 个目标文件夹: \n" + "\n".join(targets))
        QApplication.processEvents()

        files = CoreLogic.scan_cold_files_multi_path(targets, days)

        if not files:
            self.log("未发现符合条件的冷数据。")
            return

        reply = QMessageBox.question(self, "确认迁移", f"扫描到 {len(files)} 个冷数据文件。\n确定要全部迁移吗？",
                                     QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            folder, count, size = CoreLogic.move_files(files, dest, f"cold_{days}days")
            self.log(
                f"\n[冷数据迁移报告]\n迁移文件数: {count}\n释放空间: {Utils.format_size(size)}\n存放位置: {folder}")
            QMessageBox.information(self, "完成", "冷数据迁移完成！")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())