import contextlib
import json
import logging
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Set


logger = logging.getLogger("laogu-ai-agent.history-pool")

DEFAULT_RETENTION_SECONDS = 30 * 86400  # 30 天保留期 (秒)


class HistoryPoolError(RuntimeError):
    """去重池数据操作异常，用于保护历史数据防止在故障时被静默清空."""
    pass


def get_default_pool_file() -> Path:
    # 优先存放在控制中心根目录 (如果是打包运行环境，存放于主程序 exe 同级目录，避免放在 _internal 内部)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        target = exe_dir / "visited_history_pool.json"
        internal_file = Path(__file__).resolve().parent.parent / "visited_history_pool.json"
        if not target.exists() and internal_file.is_file():
            try:
                import shutil
                shutil.copy2(internal_file, target)
            except Exception:
                pass
        return target
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "visited_history_pool.json"


class PersistentHistoryPool:
    """
    持久化记忆去重池：
    1. 存储在控制中心同目录下的 visited_history_pool.json 文件中。
    2. 按账号（account_tag / username）分槽隔离，不同账号数据绝对不互通。
    3. 数据保留 30 天（保留一个月），读写时自动淘汰清理过期记录。
    4. 线程安全 + 跨进程文件排他锁 (msvcrt / fcntl) + 进程间原子写入防损坏。
    5. 读取冲突/异常时拒绝无脑重建空字典覆写，防止历史记录被意外清空。
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, storage_file: Path | str | None = None, retention_seconds: int = DEFAULT_RETENTION_SECONDS):
        self.file_path = Path(storage_file) if storage_file else get_default_pool_file()
        self.retention_seconds = retention_seconds
        self._rw_lock = threading.Lock()
        self._lock_path = self.file_path.with_name(self.file_path.name + ".lock")

    @classmethod
    def get_instance(cls, storage_file: Path | str | None = None) -> "PersistentHistoryPool":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(storage_file)
            return cls._instance

    @contextlib.contextmanager
    def _acquire_file_lock(self, timeout: float = 10.0):
        """跨进程排他文件锁 (Windows msvcrt / Unix fcntl)，带有超时与随机退避."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_t = time.monotonic()
        lock_file = None
        try:
            lock_file = open(self._lock_path, "a+b")
            while True:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError, PermissionError):
                    if time.monotonic() - start_t >= timeout:
                        raise TimeoutError(f"Failed to acquire history pool file lock within {timeout}s on {self._lock_path}")
                    time.sleep(0.02 + random.uniform(0.01, 0.03))
            yield
        finally:
            if lock_file is not None:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    lock_file.close()
                except Exception:
                    pass

    @contextlib.contextmanager
    def _transaction(self):
        """同时持有线程锁与进程间文件锁的完整事务上下文管理器."""
        with self._rw_lock:
            with self._acquire_file_lock():
                yield

    def _clean_and_load(self) -> Dict[str, Dict[str, float]]:
        if not self.file_path.exists():
            return {}

        raw_data = None
        read_err = None
        for attempt in range(5):
            try:
                with open(self.file_path, "r", encoding="utf-8-sig") as f:
                    content = f.read()
                clean_content = content.strip("\x00 \t\r\n")
                if not clean_content:
                    return {}
                raw_data = json.loads(clean_content)
                read_err = None
                break
            except (PermissionError, OSError) as e:
                read_err = e
                time.sleep(0.02 * (2 ** attempt) + random.uniform(0.01, 0.02))
            except json.JSONDecodeError as je:
                read_err = je
                time.sleep(0.05)

        if read_err is not None:
            logger.critical("读取去重池数据文件异常 %s: %s (拒绝重建空记录，保护历史数据)", self.file_path, read_err)
            raise HistoryPoolError(f"PersistentHistoryPool read failed: {read_err}")

        if not isinstance(raw_data, dict):
            logger.critical("去重池数据根结构非字典: %s (保留原文件并中止写入)", type(raw_data))
            raise HistoryPoolError(f"PersistentHistoryPool root structure invalid: {type(raw_data)}")

        now = time.time()
        cleaned_data: Dict[str, Dict[str, float]] = {}
        has_expired = False

        for account_key, handles in raw_data.items():
            if not isinstance(handles, dict):
                continue
            account_dict: Dict[str, float] = {}
            for handle, ts in handles.items():
                try:
                    ts_float = float(ts)
                    # 检查是否在保留期内
                    if (now - ts_float) <= self.retention_seconds:
                        account_dict[str(handle).lower()] = ts_float
                    else:
                        has_expired = True
                except (ValueError, TypeError):
                    has_expired = True
            if account_dict:
                cleaned_data[str(account_key)] = account_dict

        if has_expired:
            self._atomic_save(cleaned_data)

        return cleaned_data

    def _atomic_save(self, data: Dict[str, Dict[str, float]]) -> None:
        temp_name = None
        try:
            parent_dir = self.file_path.parent
            parent_dir.mkdir(parents=True, exist_ok=True)
            # 使用临时文件做原子替换写入，防止断电或写入中途损坏 JSON
            with tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8") as tmp:
                temp_name = tmp.name
                json.dump(data, tmp, ensure_ascii=False, indent=2)

            # 针对 Windows WinError 32 共享冲突进行指数退避重试
            replace_success = False
            last_replace_err = None
            for attempt in range(5):
                try:
                    os.replace(temp_name, self.file_path)
                    replace_success = True
                    temp_name = None  # 替换成功，清空变量避免在 finally 中被误删
                    break
                except (PermissionError, OSError) as e:
                    last_replace_err = e
                    time.sleep(0.05 * (2 ** attempt) + random.uniform(0.01, 0.03))

            if not replace_success:
                logger.error("原子保存去重池多次重试后仍然失败: %s", last_replace_err)
                raise HistoryPoolError(f"Failed to replace history pool file: {last_replace_err}")
        finally:
            # 无论发生任何序列化或重命名异常，孤立临时文件必须严格清理，杜绝磁盘泄漏
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except Exception:
                    pass

    def load_account_handles(self, account_key: str) -> Set[str]:
        """获取指定账号近 30 天内所有互动的博主 Handle 集合"""
        with self._transaction():
            data = self._clean_and_load()
            account_data = data.get(str(account_key), {})
            return set(account_data.keys())

    def is_visited(self, account_key: str, handle: str) -> bool:
        """检查指定账号是否在近 30 天内已触达过该博主"""
        if not account_key or not handle:
            return False
        with self._transaction():
            data = self._clean_and_load()
            account_data = data.get(str(account_key), {})
            return str(handle).lower() in account_data

    def mark_visited(self, account_key: str, handle: str) -> None:
        """为指定账号记录一个触达博主，时间戳为当前时刻"""
        if not account_key or not handle:
            return
        handle_lower = str(handle).lower()
        with self._transaction():
            data = self._clean_and_load()
            acc_str = str(account_key)
            if acc_str not in data:
                data[acc_str] = {}
            data[acc_str][handle_lower] = time.time()
            self._atomic_save(data)

    def mark_batch_visited(self, account_key: str, handles: list[str]) -> None:
        """批量记录博主"""
        if not account_key or not handles:
            return
        now = time.time()
        with self._transaction():
            data = self._clean_and_load()
            acc_str = str(account_key)
            if acc_str not in data:
                data[acc_str] = {}
            for h in handles:
                if h:
                    data[acc_str][str(h).lower()] = now
            self._atomic_save(data)

    def get_account_active_days(self, account_key: str) -> int:
        """获取某个账号历史互动的不同日期天数."""
        if not account_key:
            return 0
        with self._transaction():
            data = self._clean_and_load()
            handles = data.get(str(account_key), {})
            if not handles:
                return 0
            distinct_days = set()
            for ts in handles.values():
                day_str = time.strftime("%Y-%m-%d", time.localtime(ts))
                distinct_days.add(day_str)
            return len(distinct_days)

    def get_handles_older_than(self, account_key: str, days: int) -> set[str]:
        """获取互动时间距今超过指定天数的博主用户名列表 (用于未回关清理)."""
        if not account_key or days <= 0:
            return set()
        threshold_ts = time.time() - (days * 86400)
        with self._transaction():
            data = self._clean_and_load()
            handles = data.get(str(account_key), {})
            return {h for h, ts in handles.items() if ts <= threshold_ts}

    def remove_handle(self, account_key: str, handle: str) -> None:
        """从触达历史中移除指定博主 (用于取关后防止再次被作为未回关目标检索)"""
        if not account_key or not handle:
            return
        handle_lower = str(handle).lower()
        with self._transaction():
            data = self._clean_and_load()
            acc_str = str(account_key)
            if acc_str in data and handle_lower in data[acc_str]:
                del data[acc_str][handle_lower]
                self._atomic_save(data)

