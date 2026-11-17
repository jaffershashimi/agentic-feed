import unittest
from datetime import datetime, timezone

from consumer import news_digest as nd

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
RSS = """<rss><channel>
<item><title>ACME plunges after SEC investigation disclosed</title><pubDate>Tue, 17 Nov 2026 14:30:00 GMT</pubDate><link>http://x/1</link></item>
<item><title>Old story</title><pubDate>Fri, 06 Nov 2026 14:30:00 GMT</pubDate></item>
<item><title>Quiet day for ACME</title><pubDate>Tue, 17 Nov 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fed statement</title><updated>2026-11-17T13:00:00Z</updated></entry></feed>"""


class Digest(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        self.assertEqual([i["title"] for i in nd.parse_rss(RSS)][:1], ["ACME plunges after SEC investigation disclosed"])
        self.assertEqual(nd.parse_rss(ATOM)[0]["published"], datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(nd.parse_rss("not xml"), [])

    def test_build_filters_old_flags_risk_and_survives_dead_feeds(self):
        def get(url):
            if "federalreserve" in url:
                raise OSError("down")
            return RSS
        d = nd.build(["ACME"], NOW, 36, get)
        titles = [r["title"] for r in d["symbols"]["ACME"]]
        self.assertNotIn("Old story", titles)
        self.assertEqual(d["flags"][0]["sym"], "ACME")
        self.assertIn("fed", d["errors"])
        self.assertIn("FLAGGED", nd.render(d))


if __name__ == "__main__":
    unittest.main()
