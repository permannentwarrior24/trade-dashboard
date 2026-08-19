import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import server


class ServerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app, base_url="http://127.0.0.1")

    def test_untrusted_host_and_cross_origin_mutation_are_rejected(self):
        response = self.client.get("/api/health", headers={"host": "attacker.example"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/api/analyze/cancel", headers={"origin": "https://attacker.example"}
        )
        self.assertEqual(response.status_code, 403)

    def test_account_responses_are_not_browser_cached(self):
        with patch.object(server.okx, "get_positions", return_value=[]):
            response = self.client.get("/api/account/positions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_static_pages_still_load_with_security_middleware(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_api_rejects_invalid_symbols_before_calling_exchange(self):
        response = self.client.get(r"/api/market/ticker/..%5Csecret")
        self.assertEqual(response.status_code, 422)

    def test_api_caps_remote_work(self):
        response = self.client.get("/api/market/candles/BTC-USDT?limit=1001")
        self.assertEqual(response.status_code, 422)

        response = self.client.get(
            "/api/market/indicators/BTC-USDT?timeframes=1H,2H,3H,4H,5H,6H,7H"
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.get(
            "/api/market/indicators/BTC-USDT?timeframes=1H,1H"
        )
        self.assertEqual(response.status_code, 400)

    def test_models_reject_injection_and_ignored_extra_symbols(self):
        with self.assertRaises(ValidationError):
            server.BitgetCredentials(
                api_key="ok\nBITGET_PROXY=http://attacker", secret_key="secret", passphrase="pass"
            )
        with self.assertRaises(ValidationError):
            server.AnalyzeRequest(symbols=["BTC-USDT", "ETH-USDT"])
        with self.assertRaises(ValidationError):
            server.ReportSaveRequest(symbol="BTC-USDT", html="<p>ok</p>", timestamp="invalid")

    def test_report_save_uses_safe_unique_name_and_sanitizes_html(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server, "REPORTS_DIR", Path(temp_dir)
        ):
            report_id = server.save_report(
                "BTC-USDT",
                '<p onclick="steal()">ok</p><img src=x onerror="steal()">',
                datetime.now().isoformat(),
            )
            self.assertNotIn("/", report_id)
            self.assertNotIn("\\", report_id)
            data = json.loads((Path(temp_dir) / f"{report_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["html"], "<p>ok</p>")
            self.assertTrue((Path(temp_dir) / f"{report_id}.md").exists())

    def test_env_update_preserves_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server, "__file__", str(Path(temp_dir) / "server.py")
        ):
            env_path = Path(temp_dir) / ".env"
            env_path.write_text('BITGET_PROXY="http://127.0.0.1:7897"\n# keep me\n', encoding="utf-8")
            server._update_env_file({"BITGET_API_KEY": "abc$def"})
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("BITGET_PROXY", content)
            self.assertIn("# keep me", content)
            self.assertIn('BITGET_API_KEY="abc$def"', content)


if __name__ == "__main__":
    unittest.main()
