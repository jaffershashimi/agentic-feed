"""Cross-check the hand-written indicators in consumer/oversold_panel.py against pandas reference formulas.

Runs only where pandas and numpy are installed (CI has neither, so it is skipped there). Wilder RSI is the textbook formula that open-source
libraries (ta, pandas-ta, TA-Lib) implement: seed with the simple mean of the first `n` changes, then smooth with alpha = 1/n.
"""
import random
import unittest

from consumer import oversold_panel as op

try:
    import numpy as np
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


def closes(n=700, seed=11):
    rnd = random.Random(seed)
    px, out = 100.0, []
    for _ in range(n):
        px *= 1 + rnd.gauss(0.0003, 0.02)
        out.append(px)
    return out


@unittest.skipIf(pd is None, "pandas/numpy not installed")
class CrossCheck(unittest.TestCase):
    def test_sma_matches_pandas_rolling_mean(self):
        c = closes()
        for n in (5, 25, 200):
            ref = pd.Series(c).rolling(n).mean().tolist()
            mine = op.sma(c, n)
            for a, b in zip(mine, ref):
                if b != b:  # NaN in pandas
                    self.assertIsNone(a)
                else:
                    self.assertAlmostEqual(a, b, places=8)

    def test_rsi2_matches_wilder_rsi(self):
        c = closes()
        s = pd.Series(c)
        d = s.diff()
        gain, loss = d.clip(lower=0), (-d).clip(lower=0)
        n = 2
        # Wilder seed: simple mean of the first n changes (positions 1..n), then recursive smoothing with alpha = 1/n
        ag, al = [np.nan] * len(c), [np.nan] * len(c)
        ag[n], al[n] = gain.iloc[1:n + 1].mean(), loss.iloc[1:n + 1].mean()
        for i in range(n + 1, len(c)):
            ag[i] = (ag[i - 1] * (n - 1) + gain.iloc[i]) / n
            al[i] = (al[i - 1] * (n - 1) + loss.iloc[i]) / n
        ref = [100.0 if (l == 0 and g == g) else (100 - 100 / (1 + g / l) if l == l and l != 0 else np.nan) for g, l in zip(ag, al)]
        mine = op.rsi2(c)
        compared = 0
        for a, b in zip(mine, ref):
            if a is None or b != b:
                continue
            self.assertAlmostEqual(a, b, places=6)
            compared += 1
        self.assertGreater(compared, 600)

    def test_zdev_matches_a_pandas_rolling_definition(self):
        # z = dev / sample std (ddof=1) of dev over the previous 250 values, dev = close/SMA25 - 1; no mean is subtracted (frozen S-042 definition)
        c = closes()
        s25 = op.sma(c, 25)
        mine = op.zdev_series(c, s25)
        dev = pd.Series([(x / m - 1.0) if m else np.nan for x, m in zip(c, s25)])
        std = dev.shift(1).rolling(250).std(ddof=1)
        ref = (dev / std).tolist()
        checked = 0
        for a, b in zip(mine, ref):
            if a is None or b != b:
                continue
            checked += 1
            self.assertAlmostEqual(a, b, places=4)
        self.assertGreater(checked, 300)


if __name__ == "__main__":
    unittest.main()
