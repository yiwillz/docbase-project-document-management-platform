"""日志：按天写文件（如 api-2026-10-26.log），保留 log_days 天、总大小不超过 log_max_mb。

不用标准库的按时间切分（它靠改文件名切分，Windows 上别的进程开着文件时改名会失败）：
每天直接写一个新文件，换天时顺手删掉过期的。后端（api）和 worker 各写各的文件。
没配置 log_dir 时只打印到控制台（开发时）。
"""

import logging
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class DailyFileHandler(logging.Handler):
    """写 目录/名字-YYYY-MM-DD.log；日期变了就换文件并清理旧日志。"""

    def __init__(self, directory: Path, name: str, keep_days: int, max_bytes: int) -> None:
        super().__init__()
        self.directory, self.name_prefix = Path(directory), name
        self.keep_days, self.max_bytes = keep_days, max_bytes
        self.day: date | None = None
        self.stream = None
        self.lock_ = threading.Lock()

    def _open(self, day: date) -> None:
        if self.stream:
            self.stream.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stream = open(self.directory / f"{self.name_prefix}-{day.isoformat()}.log", "a", encoding="utf-8")
        self.day = day
        cleanup(self.directory, self.keep_days, self.max_bytes, today=day)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            day = datetime.fromtimestamp(record.created).date()
            with self.lock_:
                if day != self.day:
                    self._open(day)
                self.stream.write(msg + "\n")
                self.stream.flush()
        except Exception:  # noqa: BLE001  写日志失败不能影响业务
            self.handleError(record)

    def close(self) -> None:
        with self.lock_:
            if self.stream:
                self.stream.close()
                self.stream = None
        super().close()


def cleanup(directory: Path, keep_days: int, max_bytes: int, today: date | None = None) -> list[Path]:
    """删掉超过保留天数的日志；总大小超了再从最旧的删（今天的不删）。返回删掉的文件。"""
    today = today or date.today()
    files = sorted(Path(directory).glob("*-????-??-??.log"), key=lambda f: (f.name[-14:-4], f.name))
    removed: list[Path] = []

    def day_of(f: Path) -> date | None:
        try:
            return date.fromisoformat(f.name[-14:-4])
        except ValueError:
            return None

    oldest = today - timedelta(days=keep_days - 1)
    for f in list(files):
        d = day_of(f)
        if d is not None and d < oldest:
            f.unlink(missing_ok=True)
            files.remove(f)
            removed.append(f)
    total = sum(f.stat().st_size for f in files if f.exists())
    for f in list(files):
        if total <= max_bytes:
            break
        if day_of(f) == today:
            continue
        size = f.stat().st_size
        f.unlink(missing_ok=True)
        total -= size
        removed.append(f)
    return removed


def setup(name: str) -> None:
    """进程启动时调用：name 是 api / worker。"""
    from app.config import get_settings

    s = get_settings()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_docbase", False):
            root.removeHandler(h)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if s.log_dir:
        handlers.append(DailyFileHandler(s.log_dir, name, s.log_days, s.log_max_mb * 1024 * 1024))
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
        h._docbase = True  # type: ignore[attr-defined]
        root.addHandler(h)
    # uvicorn 自己的日志也写进文件
    for lg in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(lg).handlers = []
        logging.getLogger(lg).propagate = True
