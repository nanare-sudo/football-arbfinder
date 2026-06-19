# Methodology

This document goes deeper than the [README](../README.md) for readers who want to
audit the validation logic. It explains the maths, the metric, and — most
importantly — the layered defences against fooling ourselves.

> **One-line thesis.** The maths of betting markets is trivial; the edge, if any,
> lives in *data quality and disciplined validation*. This project is built to
> *disprove* its own candidate edges as aggressively as it searches for them.

## Contents
- [1. The question](#1-the-question)
- [2. No-arbitrage background (and why it is trivial)](#2-no-arbitrage-background-and-why-it-is-trivial)
- [3. Fair probabilities: devigging and the sharp anchor](#3-fair-probabilities-devigging-and-the-sharp-anchor)
- [4. Closing Line Value (CLV): the primary metric](#4-closing-line-value-clv-the-primary-metric)
- [5. The validation hierarchy](#5-the-validation-hierarchy)
- [6. Multiple-testing / selection bias](#6-multiple-testing--selection-bias)
- [7. Exact meaning of each verdict](#7-exact-meaning-of-each-verdict)
- [8. Known statistical limitations](#8-known-statistical-limitations)

---

## 1. The question

Is there an **executable, out-of-sample-stable edge** in football match-odds
markets — i.e. can a price from a single, realistically reachable book
systematically beat the *fair* (margin-removed) closing line of the sharpest book
in the market?

Betting markets are an unusually clean laboratory for market-efficiency questions:
each event resolves to a known ground truth within days, the "asset" expires, and
a near-consensus sharp price (Pinnacle's closing line) is observable. That makes
them a good sandbox for the *methodology* a quant actually cares about —
devigging, leakage-free validation, walk-forward analysis, confidence intervals —
without the data-snooping ambiguity of open-ended financial series.

## 2. No-arbitrage background (and why it is trivial)

A decimal odd `o` implies probability `1/o`. For a complete market the bookmaker's
**overround** is `Σ 1/oᵢ`. If the *best* available `1/oᵢ` across books sum to
`< 1`, a risk-free **Dutch book** (arbitrage) exists: stake proportionally and you
profit on every outcome.

```
margin = Σ_outcomes  1/best_odds(outcome)
arbitrage  ⇔  margin < 1
profit_pct = (1/margin − 1) × 100
```

This is implemented in `arbitrage.py` in ~30 lines and is, deliberately, the least
interesting part of the repo: it is a *mathematical fact*, identical in every
project, with **no overfitting risk**. Pure arbitrage signals therefore bypass the
validation hierarchy (`requires_validation = False` → always `confirmed` if a real
arb exists). Everything hard is upstream: identifying the same event across books
(`normalize.py`: alias → canonical → fuzzy, with identity = *teams AND kickoff*),
parsing messy data defensively, and — for *predictive* strategies — proving the
signal is not noise.

## 3. Fair probabilities: devigging and the sharp anchor

A value strategy needs an estimate of the **true** probability to compare a price
against. `fair_probability.py` exposes a swappable `FairProbabilityModel`:

- **Devigging** a single book: take `1/o` per outcome and renormalise to sum 1.
  This removes the bookmaker's margin and yields that book's implied *fair* view.
- **`ConsensusDevigModel`**: devig every *complete* book and average. An early
  version of this project used it as the fair anchor — and it was **unmasked as an
  artifact** (its season-by-season ROI flipped sign, a classic overfitting tell).
- **`PinnacleAnchorModel`** (the anchor used here): Pinnacle is the sharpest widely
  available book; its devigged line is a far better fair-value estimate than an
  average that includes soft books.

**Avoiding circularity is explicit.** When judging a price from book *X*, *X* (and
any aggregate that contains it) is excluded from the consensus via leave-one-out;
if too few independent books remain, the model returns `None` (no signal) rather
than comparing a price against itself.

**Open vs. close, and why it matters.** We anchor selection on the Pinnacle
*opening* line and measure CLV against the Pinnacle *closing* line. Anchoring on
the close and then measuring closing-line value against that same close would be
circular (CLV ≈ 0 by construction).

## 4. Closing Line Value (CLV): the primary metric

For a bet taken at odds `q` on an outcome whose fair closing probability is
`p_close`:

```
fair_close_odds = 1 / p_close            # p_close = devig(Pinnacle close)
CLV%            = (q / fair_close_odds − 1) × 100  =  (q · p_close − 1) × 100
```

**Why CLV is the leading indicator** (and the primary metric here):

1. **It needs no match results.** It compares two prices, so it is available
   immediately and is far less noisy than realised PnL (which is dominated by the
   variance of who actually won).
2. **It is the discipline sharps use.** Consistently beating the closing line is
   the standard evidence of skill; the closing line is the market's best final
   estimate, so beating it ⇒ you bought value the market later agreed with.
3. **It cross-checks PnL.** Negative/zero CLV alongside positive in-sample PnL is a
   tell that the PnL was *luck*, not edge.

**Devig the benchmark — this is a deliberate, *harder* choice.** The raw closing
price is margin-*shortened*; beating the raw close is easier than beating the fair
(no-vig) close. Measuring against the devigged close removes a systematic bias
that would flatter every bet. On EPL this matters: it is part of why the honest
EPL result collapses to "no edge".

**Moderate-odds focus (2.0–4.0).** The bet odds are restricted to `[2.0, 4.0]`.
On EPL, of 49 outcomes that beat the sharp open by ≥2%, **46 sat at odds > 4.0**
(longshots). The longshot bias is a well-known trap (favourite-longshot bias and
fat-tailed variance); excluding it is honest risk control, and the per-league
`odds_filter_diag` block reports exactly how many edge-hits were dropped as
favourites vs. longshots (no silent truncation).

## 5. The validation hierarchy

The core of the project. Each layer is designed to *kill* a candidate that does
not deserve to survive, while a three-tier verdict avoids throwing away genuine
edges prematurely.

### 5.1 Three-tier judgment (`validation.judge`)

Following López de Prado (*Advances in Financial Machine Learning*): test enough
variants and one will look good in-sample by chance. But a too-harsh discount also
discards real edges. So there is **no hard guillotine** — instead:

| Verdict | Meaning | Action |
|---|---|---|
| `confirmed` | in-sample signal **and** robust out-of-sample | keep |
| `parked` | promising but too little evidence (thin OOS, or OOS weak) | gather more data, do **not** discard |
| `rejected` | no signal even in-sample | drop |

Only `rejected` is terminal. `parked` is the safety net against the "we discard
good things too fast" critique.

### 5.2 Multiple-testing deflation

`validation.deflate` reduces an in-sample edge by a term that grows with
`log(n_trials)` (and the dispersion of trials). It is intentionally **mild** (log,
not linear) and is **informative only** — it never single-handedly rejects a
strategy. On the real data the deflated in-sample edge (`n_trials = 17`, the number
of leagues scanned) for I2 was ≈ +1.07%, which *matched the realised single-season
OOS* almost exactly — a satisfying internal consistency check.

### 5.3 Bankroll simulation with ruin

PnL is reported only under a **binding €100 bankroll** (`diagnostics.simulate`):
flat 1% stakes and fractional (¼) Kelly, capped, chronological. Capital is the
hard constraint — you cannot stake more than you hold, and hitting zero is **ruin**
(the run stops, the bet index/date is recorded). This kills the "+€2061 on infinite
capital" illusion that a naïve flat-€100-per-bet backtest produces.

### 5.4 Stress checks

- **Price haircut**: shave every taken price by 0–3% to model that the soft price
  may not be reachable; report how fast ROI decays.
- **Concentration**: ROI by season, by book, and by odds bucket, computed as a
  **ruin-independent flat-unit** metric (so a post-ruin season is not falsely shown
  as −100%). A genuine edge should not come from a single bucket or a single
  season.

### 5.5 Single holdout → walk-forward + pooling

- **Single holdout** (`oos.run`): train on 2020/21–2023/24, test on 2024/25.
  `n_samples` for the gate is the number of OOS bets *with* a CLV observation; a
  tiny holdout **must** `park`, never `confirm` (`min_samples ≥ 30`).
- **Walk-forward** (`oos.run_walkforward`): expanding-window folds
  (`min_train ≥ 3`): e.g. Fold A trains 2020/21–2022/23 → tests 2023/24; Fold B
  trains 2020/21–2023/24 → tests 2024/25. The OOS bets of **all** folds are
  **pooled** into one larger sample, and the verdict is judged on the pooled OOS.
  Reported per league: each fold's stats, the pooled stats, **consistency** (how
  many folds stayed positive), and a **95% confidence interval**.

### 5.6 Confidence interval and `statistically_secured`

For the pooled OOS CLV values:

```
SE      = sample_std / √n          (sample std, ddof = 1)
CI₉₅    = mean ± 1.96 · SE         (normal approximation)
ci_excludes_zero = (lo > 0) or (hi < 0)
statistically_secured = (verdict == confirmed) AND ci_excludes_zero AND mean > 0
```

A league is only called **secured** if it is `confirmed` *and* its pooled 95% CI
lies entirely above zero. This is what demoted I2 (see results): single-holdout
`confirmed`, but pooled CI `[−0.46, +3.64]` **includes 0** → not secured.

## 6. Multiple-testing / selection bias

The candidate leagues (EC, I2, F2; SC3 flagged uncertain) were *selected* because
they looked best across **17** scanned leagues. That selection is itself a
multiple-comparison: `n_trials = 17` is passed into the deflation, and the
walk-forward is the real defence — a candidate picked for an in-sample peak has to
re-earn its keep on data it was not selected on.

## 7. Exact meaning of each verdict

- **`rejected`** — in-sample mean CLV ≤ 0. No signal to begin with.
- **`parked`** — in-sample positive, but either the OOS sample is below
  `min_samples`, or the OOS mean CLV is ≤ `min_out_of_sample` (0.5%), or data is
  missing. Explicitly *not* a rejection.
- **`confirmed`** — in-sample positive **and** pooled OOS mean CLV >
  `min_out_of_sample` **and** pooled `n ≥ min_samples`.
- **`statistically_secured`** — `confirmed` **and** the pooled 95% CI excludes 0.
  The strongest claim the project will make — and even this is hedged (§8).
- **`uncertain`** — an orthogonal flag carried for SC3 (n at the floor, an
  outlier-driven bucket); reported but never treated as a clean candidate.

## 8. Known statistical limitations

Stated up front because they bound every conclusion:

1. **The CI is optimistic.** It is a normal approximation that treats every bet as
   an independent observation. Real bets are **correlated** (same match, same
   match-day, market-wide line moves), so the true interval is **wider**. The one
   "secured" league (EC) has a CI lower bound of only **+0.25%** — under realistic
   correlation that could cross zero. The output says this explicitly and marks
   such cases `KNAPP abgesichert` (barely secured). A **block/cluster bootstrap**
   (resampling by match-day) is the right next step (see README → Next steps).
2. **Costs and limits are not modelled.** No commission/tax, and crucially no
   bookmaker **stake limits** — and the books that offer the most generous outlier
   prices limit winning accounts first. A +1–2% gross CLV is *thin* relative to
   these frictions.
3. **One holdout window is not a proof.** Even a clean walk-forward over a few
   seasons is a **signal**, not a guarantee of future profit.
4. **Thin Pinnacle coverage in lower leagues** weakens the anchor itself — the
   benchmark is noisier exactly where the apparent edge is largest.

These limitations are the point: a method that survives being told all of this and
still produces a single, marginally-positive, honestly-hedged candidate is doing
its job.
