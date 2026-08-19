import asyncio
import sys
import unittest

from process_utils import stop_process
from security import (
    sanitize_report_html,
    validate_report_id,
    validate_secret,
    validate_symbol,
)


class SecurityBoundaryTests(unittest.TestCase):
    def test_html_sanitizer_removes_active_content_and_attributes(self):
        payload = (
            '<img src=x onerror="alert(1)">'
            '<script />'
            '<script>alert(2)</script>'
            '<iframe srcdoc="<script>alert(3)</script>"></iframe>'
            '<svg onload="alert(4)"><circle /></svg>'
            '<p class="positive ok!" onclick="alert(5)" style="color:red">safe & text</p>'
        )
        cleaned = sanitize_report_html(payload)

        self.assertEqual(cleaned, '<p class="positive">safe &amp; text</p>')
        for dangerous in ("script", "iframe", "svg", "onerror", "onclick", "style="):
            self.assertNotIn(dangerous, cleaned.lower())

    def test_html_sanitizer_keeps_report_tables_and_adds_even_rows(self):
        source = "<table><tbody><tr><td>A</td></tr><tr><td>B</td></tr></tbody></table>"
        cleaned = sanitize_report_html(source)
        self.assertIn('<tr class="even"><td>B</td></tr>', cleaned)

    def test_symbol_rejects_paths_and_prompt_text(self):
        self.assertEqual(validate_symbol("BTC-USDT-SWAP"), "BTC-USDT-SWAP")
        for value in ("../secret", r"..\secret", "BTC-USDT\nignore", "", "A" * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_symbol(value)

    def test_report_id_accepts_legacy_and_current_formats(self):
        validate_report_id("20260819_120000_BTC-USDT")
        validate_report_id("20260819_120000_123456_BTC-USDT_a1b2c3d4")
        for value in (r"..\outside", "../outside", "not-a-report"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_report_id(value)

    def test_secret_rejects_env_line_injection(self):
        self.assertEqual(validate_secret("abc$def'ghi"), "abc$def'ghi")
        for value in ("", "abc\nMALICIOUS=1", "abc\x00def"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_secret(value)

    def test_stop_process_terminates_a_live_child(self):
        async def run():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await stop_process(process, grace_seconds=0.5)
            return process.returncode

        self.assertIsNotNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
