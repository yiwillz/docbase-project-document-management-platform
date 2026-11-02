"""容器就绪检查：HTTP 200 且数据库、文件存储均正常才成功。"""

import json
import sys
import urllib.request


def main() -> int:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4) as response:
            health = json.load(response)
        return 0 if health.get("ok") is True else 1
    except (OSError, ValueError, AttributeError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
