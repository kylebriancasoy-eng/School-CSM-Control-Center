from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
WORKER_ROOT = ROOT / "cloudflare_worker"


class CloudflareWorkerContractTests(unittest.TestCase):
    def test_template_is_placeholder_only_and_uses_a_narrow_vpc_service(self) -> None:
        template = WORKER_ROOT / "wrangler.example.jsonc"
        document = json.loads(template.read_text(encoding="utf-8"))

        self.assertEqual(document["main"], "src/index.js")
        self.assertTrue(document["workers_dev"])
        self.assertFalse(document["preview_urls"])
        self.assertNotIn("account_id", document)
        self.assertEqual(document["name"], "REPLACE-WITH-SCHOOL-ID")
        self.assertIn("REPLACE-WITH", document["vars"]["PUBLIC_HOST"])
        self.assertEqual(
            document["vpc_services"],
            [
                {
                    "binding": "SCHOOL_CSM_ORIGIN",
                    "service_id": "REPLACE-WITH-VPC-SERVICE-UUID",
                }
            ],
        )
        self.assertNotIn("vpc_networks", document)

    def test_proxy_source_contains_fail_closed_request_boundary(self) -> None:
        source = (WORKER_ROOT / "src" / "index.js").read_text(encoding="utf-8")

        self.assertIn('["GET", "HEAD", "POST", "OPTIONS"]', source)
        self.assertIn('const ORIGIN_BASE_URL = "http://127.0.0.1:8080"', source)
        self.assertIn('headers.set("x-forwarded-proto", "https")', source)
        self.assertIn('headers.set("x-forwarded-host", publicHost)', source)
        self.assertIn('headers.set("x-forwarded-for", clientIp)', source)
        self.assertIn('headers.set("cf-connecting-ip", clientIp)', source)
        self.assertIn('request.headers.get("cf-connecting-ip")', source)
        self.assertIn("env.SCHOOL_CSM_ORIGIN.fetch(originRequest)", source)
        self.assertIn('plainText("Service temporarily unavailable.\\n", 503', source)
        self.assertNotIn("console.log", source)
        self.assertNotIn("console.error", source)

        for header in (
            "connection",
            "proxy-authorization",
            "transfer-encoding",
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-real-ip",
        ):
            self.assertIn(f'"{header}"', source)

    def test_worker_assets_do_not_contain_live_cloudflare_credentials(self) -> None:
        credential_markers = (
            re.compile(r"eyJhIjoi[0-9A-Za-z_-]{20,}"),
            re.compile(r"(?i)cloudflare_api_token\s*[:=]\s*[0-9A-Za-z_-]{20,}"),
            re.compile(r"(?i)tunnel_token\s*[:=]\s*[0-9A-Za-z_-]{20,}"),
        )
        for path in WORKER_ROOT.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for marker in credential_markers:
                self.assertIsNone(marker.search(text), path)

        template = (WORKER_ROOT / "wrangler.example.jsonc").read_text(encoding="utf-8")
        live_uuid = re.compile(
            r'"service_id"\s*:\s*"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-'
            r'[89ab][0-9a-f]{3}-[0-9a-f]{12}"',
            re.IGNORECASE,
        )
        self.assertIsNone(live_uuid.search(template))


if __name__ == "__main__":
    unittest.main()
