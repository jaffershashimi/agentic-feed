import unittest

from consumer import news_events as ne


def h(headline, t, summary="", symbols=("SPY",), i=0):
    return {"id": i, "created_at": t, "headline": headline, "summary": summary, "symbols": list(symbols)}


class TextRuleTests(unittest.TestCase):
    def test_shock_needs_an_action_word_and_a_place(self):
        self.assertTrue(ne.is_shock(h("US launches missile strike on Iran", "2024-04-01T14:00:00Z")))
        self.assertFalse(ne.is_shock(h("Iran oil exports rise", "2024-04-01T14:00:00Z")))
        self.assertFalse(ne.is_shock(h("Company launches strike price tool", "2024-04-01T14:00:00Z")))

    def test_short_words_match_whole_tokens_only(self):
        self.assertFalse(ne.is_shock(h("Warning on Iranian shipping rates", "2024-04-01T14:00:00Z")))  # 'war' in 'warning', 'iran' in 'iranian' are not tokens
        self.assertTrue(ne.is_shock(h("Israel war escalates", "2024-04-01T14:00:00Z")))
        self.assertTrue(ne.is_shock(h("Iranian missile attack reported", "2024-04-01T14:00:00Z")))
        self.assertFalse(ne.is_shock(h("Indiana plant explosion", "2024-04-01T14:00:00Z")))
        self.assertTrue(ne.is_shock(h("Retaliation expected after Israel strike", "2024-04-01T14:00:00Z")))

    def test_templates_are_not_shocks_even_with_keywords(self):
        self.assertFalse(ne.is_shock(h("Stocks Moving In Friday's Session After Iran missile attack", "2024-04-01T14:00:00Z")))

    def test_informative_and_analyst_and_subgroup(self):
        self.assertTrue(ne.is_informative(h("Acme cuts full-year guidance", "t", symbols=("ACME",))))
        self.assertFalse(ne.is_informative(h("Acme shares are trading lower", "t", symbols=("ACME",))))
        self.assertFalse(ne.is_informative(h("Big roundup", "t", symbols=("A", "B", "C", "D"))))
        self.assertFalse(ne.is_informative(h("Acme price target cut to $10", "t", symbols=("ACME",))))
        self.assertTrue(ne.is_analyst(h("Acme price target cut to $10", "t", symbols=("ACME",))))
        self.assertEqual(ne.subgroup(h("Acme cuts full-year guidance", "t")), "GUIDANCE")
        self.assertEqual(ne.subgroup(h("SEC opens investigation into Acme", "t")), "LEGAL")
        self.assertEqual(ne.subgroup(h("Acme names new product", "t")), "OTHER")


class EventTests(unittest.TestCase):
    def test_event_fires_at_the_third_headline_and_respects_refractory(self):
        hs = [h("Iran missile attack on Israel", "2024-04-13T21:00:00Z", i=1), h("Israel says missiles launched from Iran", "2024-04-13T21:20:00Z", i=2),
              h("Iran attack: troops on alert", "2024-04-13T22:10:00Z", i=3), h("Iran attack continues, war fears", "2024-04-14T01:00:00Z", i=4),
              h("Iran missile strikes again", "2024-04-14T01:10:00Z", i=5), h("Iran drone attack Israel", "2024-04-14T01:20:00Z", i=6)]
        ev = ne.shock_events(hs)
        self.assertEqual(len(ev), 1)  # the second cluster is inside the 24 hour refractory period
        self.assertTrue(ev[0]["t0"].startswith("2024-04-13T22:10"))

    def test_two_headlines_are_not_enough_and_far_apart_ones_do_not_cluster(self):
        two = [h("Iran missile attack", "2024-04-13T21:00:00Z", i=1), h("Iran war fears", "2024-04-13T21:10:00Z", i=2)]
        self.assertEqual(ne.shock_events(two), [])
        spread = [h("Iran missile attack", "2024-04-13T10:00:00Z", i=1), h("Iran war fears", "2024-04-13T13:00:00Z", i=2), h("Iran attack troops", "2024-04-13T16:00:00Z", i=3)]
        self.assertEqual(ne.shock_events(spread), [])

    def test_duplicates_do_not_count_twice(self):
        a = h("Iran missile attack", "2024-04-13T21:00:00Z", i=1)
        self.assertEqual(ne.shock_events([a, dict(a), dict(a)]), [])


class FetchTests(unittest.TestCase):
    def test_paging_dedupes_and_stops(self):
        pages = [{"news": [{"id": 1, "created_at": "a", "headline": "x", "symbols": []}, {"id": 2, "created_at": "b", "headline": "y", "symbols": []}], "next_page_token": "p2"},
                 {"news": [{"id": 2, "created_at": "b", "headline": "y", "symbols": []}, {"id": 3, "created_at": "c", "headline": "z", "symbols": []}], "next_page_token": None}]
        calls = []

        def call(method, path, body=None, host="paper"):
            calls.append(path)
            return pages[len(calls) - 1]

        out = list(ne.fetch_news(call, "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", symbols=["SPY", "QQQ"], sleep=0))
        self.assertEqual([n["id"] for n in out], [1, 2, 3])
        self.assertIn("symbols=SPY%2CQQQ", calls[0])
        self.assertIn("page_token=p2", calls[1])


if __name__ == "__main__":
    unittest.main()
