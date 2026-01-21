import sqlite3
import os
import threading

class DatabaseManager:
    def __init__(self, db_path='wechat_files.db'):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        with self.lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT,
                original_path TEXT, 
                size INTEGER,
                reason TEXT, 
                group_id TEXT
            )
            ''')
            conn.commit()
            conn.close()

    def clear_results(self):
        with self.lock:
            conn = self._get_conn()
            conn.execute('DELETE FROM scan_results')
            conn.commit()
            conn.close()

    def save_duplicates(self, duplicates_list):
        with self.lock:
            conn = self._get_conn()
            data = [
                (d['file'], d.get('keep', ''), os.path.getsize(d['file']), d['reason'], d['group'])
                for d in duplicates_list if os.path.exists(d['file'])
            ]
            conn.executemany(
                'INSERT INTO scan_results (filepath, original_path, size, reason, group_id) VALUES (?, ?, ?, ?, ?)',
                data
            )
            conn.commit()
            conn.close()

    def get_results(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT filepath, original_path, size, reason FROM scan_results')
        rows = cursor.fetchall()
        conn.close()
        return rows