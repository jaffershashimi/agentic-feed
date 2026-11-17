import unittest
from datetime import datetime, timedelta, timezone

from engine import health

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)


class Fake:
    def __init__(self, positions=(), orders=(), news=None, spy_last="2026-11-16"):
        self.positions, self.orders, self.news, self.spy_last = list(positions), list(orders), news, spy_last

    def __call__(self, method, path, body=None, host="paper"):
        if path == "/v2/positions":
            return self.positions
        if path.startswith("/v2/orders"):
            return self.orders
        if path.startswith("/v1beta1/news"):
            return {"news": self.news or []}
        if path.startswith("/v2/stocks/bars"):
            return {"bars": {"SPY": [{"t": self.spy_last + "T05:00:00Z"}]}}
        raise AssertionError(path)


def pos(sym, qty): return {"symbol": sym, "qty": str(qty)}
def stop(sym, qty, status="new"): return {"symbol": sym, "side": "sell", "type": "stop", "qty": str(qty), "status": status, "client_order_id": "s042v3-x", "legs": []}


class HealthTests(unittest.TestCase):
    def test_a_position_without_a_full_stop_is_a_fail_and_names_the_symbol(self):
        s, d = health.stops_present(Fake([pos("AAA", 10), pos("BBB", 5)], [stop("AAA", 10), stop("BBB", 3)]))
        self.assertEqual(s, health.FAIL)
        self.assertIn("BBB", d)
        self.assertNotIn("AAA", d)
        self.assertEqual(health.stops_present(Fake([pos("AAA", 10)], [stop("AAA", 10, "held")]))[0], health.OK)
        self.assertEqual(health.stops_present(Fake([pos("AAA", 10)], []))[0], health.FAIL)

    def test_stop_inside_a_nested_leg_counts(self):
        parent = {"symbol": "AAA", "side": "buy", "type": "market", "status": "filled", "client_order_id": "s042v3-a", "legs": [stop("AAA", 10)]}
        self.assertEqual(health.stops_present(Fake([pos("AAA", 10)], [parent]))[0], health.OK)

    def test_orphan_and_stale_orders(self):
        mine = {"symbol": "AAA", "side": "buy", "client_order_id": "s042v3-AAA-1", "parent_order_id": None, "submitted_at": "2026-11-17T17:00:00Z"}
        odd = {"symbol": "ZZZ", "side": "buy", "client_order_id": "manual-1", "parent_order_id": None, "submitted_at": "2026-11-15T00:00:00Z"}
        self.assertEqual(health.orphan_orders(Fake(orders=[mine]))[0], health.OK)
        self.assertEqual(health.orphan_orders(Fake(orders=[mine, odd]))[0], health.WARN)
        held_stop = {"symbol": "AAA", "side": "sell", "type": "stop", "client_order_id": "9f2c-auto", "parent_order_id": None, "submitted_at": "2026-11-17T17:00:00Z"}
        self.assertEqual(health.orphan_orders(Fake(positions=[pos("AAA", 5)], orders=[held_stop]))[0], health.OK)
        self.assertEqual(health.orphan_orders(Fake(positions=[], orders=[held_stop]))[0], health.WARN)  # a sell stop with nothing to protect is suspicious
        self.assertEqual(health.stale_pending(Fake(orders=[mine]), NOW)[0], health.OK)
        self.assertEqual(health.stale_pending(Fake(orders=[odd]), NOW)[0], health.WARN)

    def test_safe_render_withholds_stock_names_but_keeps_the_counts(self):
        rows = health.check_all(Fake([pos("AAA", 10), pos("BBB", 5)], [stop("AAA", 3)]), NOW)
        shown = health.render(rows, safe=True)
        self.assertNotIn("AAA", shown)
        self.assertNotIn("BBB", shown)
        self.assertIn("2 name(s) withheld", shown)
        self.assertIn("AAA", health.render(rows))   # the local view still names them

    def test_news_source_drift_and_staleness_are_caught(self):
        fresh_bz = [{"source": "benzinga", "created_at": "2026-11-17T17:58:00Z"}]
        self.assertEqual(health.news_source(Fake(news=fresh_bz), NOW, True)[0], health.OK)
        drift = [{"source": "fiscal.ai", "created_at": "2026-11-17T17:58:00Z"}]
        s, d = health.news_source(Fake(news=drift), NOW, True)
        self.assertEqual(s, health.WARN)
        self.assertIn("absent", d)
        stale = [{"source": "benzinga", "created_at": "2026-11-17T15:00:00Z"}]
        self.assertEqual(health.news_source(Fake(news=stale), NOW, True)[0], health.WARN)
        self.assertEqual(health.news_source(Fake(news=stale), NOW, False)[0], health.OK)
        self.assertEqual(health.news_source(Fake(news=[]), NOW, True)[0], health.FAIL)

    def test_data_freshness_and_a_check_that_itself_breaks_is_a_fail_not_a_crash(self):
        self.assertEqual(health.data_fresh(Fake(spy_last="2026-11-16"), NOW)[0], health.OK)
        self.assertEqual(health.data_fresh(Fake(spy_last="2026-11-12"), NOW)[0], health.FAIL)

        class Boom(Fake):
            def __call__(self, *a, **k):
                raise RuntimeError("HTTP 500")

        rows = health.check_all(Boom(), NOW)
        self.assertTrue(all(r["status"] == health.FAIL for r in rows))
        self.assertIn("check itself failed", rows[0]["detail"])


if __name__ == "__main__":
    unittest.main()
