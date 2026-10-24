import copy
import random
import unittest
from feed_pipeline.releases import collect

NOW = '2026-10-24T19:00:00Z'
def series(sid='NFCI', rows=None):
    return dict(series_id=sid, last_updated='2026-10-23T12:00:00Z', title='Synthetic', frequency='Weekly', units='Index', seasonal_adjustment='Adjusted', copyright_id='fixture', observations=rows or [dict(date='2026-10-16', value='0.1')])
def page(more=False, cursor=None):
    return dict(release={'release_id':221}, has_more=more, next_cursor=cursor, series=[series(),series('ANFCI')])
class ReleasesTest(unittest.TestCase):
    def test_complete_missing_and_precision(self):
        p=page(); p['series'][0]['observations'][0]['value']='.'
        p['series'][1]['observations'][0]['value']='0.123456789012345678901'
        r=collect(lambda c:p,NOW)
        self.assertIsNone(r['records'][1]['value'])
        self.assertEqual(r['records'][0]['value'],'0.123456789012345678901')
        self.assertFalse(r['signalAuthority'])
    def test_pagination_and_change(self):
        pages={None:page(True,'a'),'a':page()}
        r=collect(pages.get,NOW)
        self.assertEqual(r['pages'],2)
        self.assertTrue(all(x['changedSincePrevious'] is False for x in collect(pages.get,NOW,r)['records']))
    def test_failures(self):
        for mutate in [lambda p:p.update(release={'release_id':1}), lambda p:p.update(series=[]),lambda p:p.update(has_more='maybe'),lambda p:p['series'][0].update(last_updated='2099-01-01T00:00:00Z'),lambda p:p['series'][0]['observations'][0].update(value='NaN')]:
            p=page(); mutate(p)
            with self.assertRaises((ValueError,KeyError)):collect(lambda c:p,NOW)
    def test_repeated_cursor_and_budget(self):
        with self.assertRaises(ValueError): collect(lambda c:page(True,'a'),NOW)
        n=[0]
        def fetch(c):
            n[0]+=1
            return page(True,str(n[0]))
        with self.assertRaises(ValueError):collect(fetch,NOW)
    def test_mixed_versions_and_conflicting_duplicates(self):
        a=page(True,'a'); b=page(); b['series'][0]['last_updated']='2026-10-24T12:00:00Z'
        with self.assertRaises(ValueError):collect(lambda c:a if c is None else b,NOW)
        b=page(); b['series'][0]['observations'][0]['value']='1'
        with self.assertRaises(ValueError):collect(lambda c:a if c is None else b,NOW)
    def test_seeded_stress_dedup_order(self):
        rng=random.Random(221)
        for _ in range(300):
            p=page(); rng.shuffle(p['series'])
            for s in p['series']:s['observations']*=rng.randint(1,20)
            r=collect(lambda c:copy.deepcopy(p),NOW)
            self.assertEqual(len(r['records']),2)
            self.assertEqual(r['records'][0]['availableAt'],NOW)
    def test_transport_failure_is_unavailable(self):
        with self.assertRaises(RuntimeError):collect(lambda c:(_ for _ in ()).throw(RuntimeError('offline')),NOW)
