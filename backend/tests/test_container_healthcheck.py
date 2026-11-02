"""HTTP 200 不代表数据库和存储已就绪。"""

import importlib.util
import io
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("container_healthcheck", Path(__file__).parents[1] / "scripts/healthcheck.py")
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


class ContainerHealthcheckTests(unittest.TestCase):
    def test_health_responses(self):
        cases = [
            (b'{"ok": true, "db": true, "storage": true}', 0),
            (b'{"ok": false, "db": false, "storage": true}', 1),
            (b'{"ok": false, "db": true, "storage": false}', 1),
            (b'{"ok": "false"}', 1),
            (b'{}', 1),
            (b'[]', 1),
            (b'not json', 1),
        ]
        for body, expected in cases:
            with self.subTest(body=body), patch.object(urllib.request, "urlopen", return_value=io.BytesIO(body)):
                self.assertEqual(healthcheck.main(), expected)


    def test_connection_unavailable(self):
        with patch.object(urllib.request, "urlopen", side_effect=OSError("connection refused")):
            self.assertEqual(healthcheck.main(), 1)
