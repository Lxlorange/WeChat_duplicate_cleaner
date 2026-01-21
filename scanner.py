import os
import hashlib
import shutil
import time
import re
import difflib
from datetime import datetime
from PyQt5.QtCore import QThread, pyqtSignal


class Utils:
    @staticmethod
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024: return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    @staticmethod
    def get_file_hash(filepath, sample=True):
        try:
            file_size = os.path.getsize(filepath)
            # 小文件始终全量计算，保证绝对安全
            if file_size < 2 * 1024 * 1024:  # 2MB以下全量
                sample = False

            with open(filepath, 'rb') as f:
                if not sample:
                    return hashlib.md5(f.read()).hexdigest()

                # 大文件采样：头+中+尾
                h = hashlib.md5()
                h.update(f.read(8192))
                f.seek(file_size // 2)
                h.update(f.read(8192))
                f.seek(-8192, 2)
                h.update(f.read(8192))
                return h.hexdigest()
        except Exception:
            return None

    @staticmethod
    def detect_wechat_paths(root_dir, target_sub="FileStorage/MsgAttach"):
        targets = []
        if not os.path.exists(root_dir): return targets
        root_dir = os.path.normpath(root_dir)

        # 情况1: 直接选了子目录
        if "FileStorage" in root_dir:
            return [root_dir]

        # 情况2: 选了 wxid 目录
        if os.path.exists(os.path.join(root_dir, "FileStorage")):
            p = os.path.join(root_dir, target_sub)
            if os.path.exists(p): targets.append(p)
            return targets

        # 情况3: 选了 WeChat Files 根目录 -> 遍历所有 wxid
        try:
            for item in os.listdir(root_dir):
                full_path = os.path.join(root_dir, item)
                if os.path.isdir(full_path) and (item.startswith("wxid_") or item == "All Users" or item == "Applet"):
                    p = os.path.join(full_path, target_sub)
                    if os.path.exists(p):
                        targets.append(p)
        except Exception:
            pass
        return targets


class CoreLogic:
    @staticmethod
    def normalize_filename(filename):
        """
        [阀门一优化]：使用正则提取文件“核心名”。
        去除 (1), (2), _副本, _1, Copy 等常见后缀。
        """
        name, ext = os.path.splitext(filename)
        # 1. 去除括号数字: file(1).txt -> file
        name = re.sub(r'\(\d+\)$', '', name)
        name = re.sub(r'（\d+）$', '', name)  # 中文括号
        # 2. 去除常见副本标记
        name = re.sub(r'_副本$', '', name)
        name = re.sub(r' - Copy$', '', name)
        name = re.sub(r'_\d+$', '', name)  # file_1.txt
        return name.strip().lower()

    @staticmethod
    def is_name_similar(name1, name2, threshold=0.6):
        """
        混合比对：先正则归一化，再算字符串相似度
        """
        core1 = CoreLogic.normalize_filename(name1)
        core2 = CoreLogic.normalize_filename(name2)

        # 如果核心名完全一样（比如 "report(1)" 和 "report"），直接返回 True
        if core1 == core2:
            return True

        # 否则计算相似度（防漏网之鱼）
        return difflib.SequenceMatcher(None, core1, core2).ratio() > threshold

    @staticmethod
    def scan_mixed_strategy(files_list, progress_callback=None):
        """
        【混合策略算法】
        1. 小文件 (<1MB) -> 走 Strict MD5 (安全、去重率高)
        2. 大文件 (>=1MB) -> 走 Fuzzy Logic (大小+文件名，处理版本差异)
        """
        small_files = []
        large_files = []

        # 1. 分流
        for f in files_list:
            try:
                s = os.path.getsize(f)
                if s < 1024 * 1024:  # 1MB 阈值
                    small_files.append(f)
                else:
                    large_files.append(f)
            except:
                pass

        results = []

        # --- Phase 1: 小文件 (Strict MD5) ---
        if small_files:
            if progress_callback: progress_callback("分析小文件 (MD5)...")
            from collections import defaultdict
            hash_map = defaultdict(list)

            # 为了性能，先按大小分组，大小一样的才算 Hash
            size_map = defaultdict(list)
            for f in small_files:
                size_map[os.path.getsize(f)].append(f)

            for size, paths in size_map.items():
                if len(paths) < 2: continue
                for p in paths:
                    h = Utils.get_file_hash(p, sample=False)  # 小文件全量哈希
                    if h: hash_map[h].append(p)

            for h, paths in hash_map.items():
                if len(paths) > 1:
                    paths.sort(key=len)  # 保留路径最短
                    keep = paths[0]
                    for p in paths[1:]:
                        results.append({
                            'file': p, 'keep': keep, 'reason': 'small_file_strict', 'group': h
                        })

        # --- Phase 2: 大文件 (Fuzzy Versioning) ---
        if large_files:
            if progress_callback: progress_callback("分析大文件 (Fuzzy)...")
            # 按后缀分组
            ext_groups = {}
            for f in large_files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in ext_groups: ext_groups[ext] = []
                ext_groups[ext].append(f)

            for ext, paths in ext_groups.items():
                if len(paths) < 2: continue

                # 只有文档和视频才做模糊去重，大图片(如RAW)建议还是严格比较，防止误删
                is_fuzzy_safe = ext in ['.doc', '.docx', '.pdf', '.ppt', '.pptx',
                                        '.xls', '.xlsx', '.mp4', '.mov', '.avi', '.zip', '.rar']

                # 如果不是安全类型，把它们扔回 MD5 逻辑处理（这里简化，暂不处理或仅做严格处理）
                # 为了代码简洁，这里假设用户选了模糊模式就是想处理这些文档
                if not is_fuzzy_safe:
                    continue

                file_meta = []
                for p in paths:
                    try:
                        stat = os.stat(p)
                        file_meta.append(
                            {'path': p, 'name': os.path.basename(p), 'size': stat.st_size, 'mtime': stat.st_mtime})
                    except:
                        continue

                file_meta.sort(key=lambda x: x['size'])
                visited = [False] * len(file_meta)

                for i in range(len(file_meta)):
                    if visited[i]: continue
                    base = file_meta[i]
                    current_group = [base]
                    visited[i] = True

                    for j in range(i + 1, len(file_meta)):
                        if visited[j]: continue
                        compare = file_meta[j]

                        # 大小差异检查 (30%)
                        if compare['size'] > base['size'] * 1.3: break

                        # 文件名相似度检查 (你的阀门一)
                        if CoreLogic.is_name_similar(base['name'], compare['name']):
                            current_group.append(compare)
                            visited[j] = True

                    if len(current_group) > 1:
                        # 保留最新的
                        current_group.sort(key=lambda x: x['mtime'], reverse=True)
                        keep = current_group[0]
                        for d in current_group[1:]:
                            results.append({
                                'file': d['path'], 'keep': keep['path'],
                                'reason': f"fuzzy_ver (base={base['name']})",
                                'group': f"{ext}_{base['size']}"
                            })

        return results

    # ... move_files 和 scan_cold_files 等方法保持不变 ...
    @staticmethod
    def scan_cold_files_multi_path(target_paths, days_threshold):
        cold_files = []
        now = time.time()
        threshold_sec = days_threshold * 86400
        for root_dir in target_paths:
            for root, _, files in os.walk(root_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(full_path)
                        if (now - mtime) > threshold_sec:
                            cold_files.append(full_path)
                    except:
                        pass
        return cold_files

    @staticmethod
    def move_files(file_list, target_base_dir, operation_name="cleanup"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_root = os.path.join(target_base_dir, f"wechat_{operation_name}_{timestamp}")
        os.makedirs(dest_root, exist_ok=True)
        log_path = os.path.join(dest_root, "move_log.txt")
        moved_count = 0
        total_size = 0
        with open(log_path, 'w', encoding='utf-8') as log:
            log.write(f"Operation: {operation_name}\nTime: {timestamp}\n\n")
            for src_path in file_list:
                try:
                    rel_path = os.path.basename(src_path)
                    parts = src_path.split(os.sep)
                    for idx, part in enumerate(parts):
                        if part.startswith("wxid_") or part == "FileStorage":
                            rel_path = os.path.join(*parts[idx:])
                            break
                    dest_path = os.path.join(dest_root, rel_path)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.move(src_path, dest_path)
                    log.write(f"MOVED: {src_path} -> {dest_path}\n")
                    moved_count += 1
                    total_size += os.path.getsize(dest_path)
                except Exception as e:
                    log.write(f"ERROR: {src_path} -> {str(e)}\n")
        return dest_root, moved_count, total_size


class ScannerThread(QThread):
    progress_val = pyqtSignal(int)
    progress_text = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, root_dir, mode, db, extensions=None):
        super().__init__()
        self.root_dir = root_dir
        self.mode = mode
        self.db = db
        self.extensions = extensions
        self.is_running = True

    def run(self):
        try:
            self.db.clear_results()
            all_files = []

            self.progress_text.emit(f"正在遍历目录: {self.root_dir} ...")
            self.progress_val.emit(5)

            for root, _, files in os.walk(self.root_dir):
                if not self.is_running: return
                for file in files:
                    if self.extensions:
                        ext = os.path.splitext(file)[1].lower()
                        if ext not in self.extensions: continue
                    all_files.append(os.path.join(root, file))

            total_count = len(all_files)
            if total_count == 0:
                self.finished.emit("未找到符合条件的文件。")
                return

            self.progress_text.emit(f"发现 {total_count} 个文件，开始分析...")
            duplicates_found = []

            # --- 核心逻辑分支 ---
            if self.mode == 'strict':
                # 纯 MD5 模式
                from collections import defaultdict
                hash_map = defaultdict(list)
                for i, f in enumerate(all_files):
                    if not self.is_running: return
                    if i % 100 == 0:
                        self.progress_val.emit(int(i / total_count * 90))
                        self.progress_text.emit(f"计算哈希: {i}/{total_count}")
                    h = Utils.get_file_hash(f, sample=True)
                    if h: hash_map[h].append(f)

                for h, paths in hash_map.items():
                    if len(paths) > 1:
                        paths.sort(key=len)
                        keep = paths[0]
                        for p in paths[1:]:
                            duplicates_found.append({'file': p, 'keep': keep, 'reason': 'strict_md5', 'group': h})
            else:
                # 混合模式 (Fuzzy + Strict)
                self.progress_val.emit(30)
                # 使用新的混合策略函数
                duplicates_found = CoreLogic.scan_mixed_strategy(
                    all_files,
                    progress_callback=lambda msg: self.progress_text.emit(msg)
                )

            # 保存
            self.progress_text.emit("正在保存结果...")
            self.db.save_duplicates(duplicates_found)
            self.progress_val.emit(100)

            dup_size = sum([os.path.getsize(d['file']) for d in duplicates_found if os.path.exists(d['file'])])
            report = (f"扫描完成！\n模式: {self.mode}\n"
                      f"文件总数: {total_count}\n"
                      f"可清理数: {len(duplicates_found)}\n"
                      f"释放空间: {Utils.format_size(dup_size)}")
            self.finished.emit(report)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(f"错误: {str(e)}")

    def stop(self):
        self.is_running = False