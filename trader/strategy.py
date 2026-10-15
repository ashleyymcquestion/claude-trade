"""Trend + time-series momentum ensemble with volatility targeting.

Long-only (Alpaca cannot short crypto). Each asset gets a score in [0, 1]:
  - trend:    half from price > slow SMA, half from fast SMA > slow SMA
  - momentum: fraction of lookback windows with a positive return
Score = average of the two. Weights are score / volatility (risk parity),
scaled so the whole portfolio targets `target_vol`, then capped by risk limits.
"""

import numpy as np
import pandas as pd

from .config import Config

DAYS_PER_YEAR = 365  # prices are on a 7-day calendar


def compute_scores(prices: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    fast = prices.rolling(cfg.sma_fast).mean()
    slow = prices.rolling(cfg.sma_slow).mean()
    trend = 0.5 * (prices > slow).astype(float) + 0.5 * (fast > slow).astype(float)
    trend = trend.where(slow.notna())

    mom = [(prices.pct_change(lb, fill_method=None) > 0).astype(float)
           .where(prices.shift(lb).notna()) for lb in cfg.momentum_lookbacks]
    momentum = sum(mom) / len(mom)

    return 0.5 * trend + 0.5 * momentum


def _apply_caps(w: pd.Series, cfg: Config) -> pd.Series:
    w = w.clip(lower=0.0, upper=cfg.max_weight)
    crypto = [s for s in w.index if cfg.is_crypto(s)]
    crypto_sum = w[crypto].sum()
    if crypto_sum > cfg.max_crypto:
        w[crypto] *= cfg.max_crypto / crypto_sum
    gross = w.sum()
    if gross > cfg.max_gross:
        w *= cfg.max_gross / gross
    return w


def target_weights(prices: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Target portfolio weights for every date (row) using data up to that date."""
    rets = prices.pct_change(fill_method=None)
    scores = compute_scores(prices, cfg)
    vol = rets.rolling(cfg.vol_lookback).std() * np.sqrt(DAYS_PER_YEAR)
    cov = rets.rolling(cfg.vol_lookback).cov()

    out = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for date in prices.index:
        s, v = scores.loc[date], vol.loc[date]
        ok = s.notna() & v.notna() & (v > 0)
        if not ok.any():
            continue
        inv_vol = 1.0 / v[ok]
        base = (s[ok] * inv_vol) / inv_vol.sum()
        if base.sum() <= 0:
            continue
        c = cov.loc[date].loc[base.index, base.index].fillna(0.0).to_numpy()
        port_vol = float(np.sqrt(max(base.to_numpy() @ c @ base.to_numpy(), 0.0) * DAYS_PER_YEAR))
        scaled = base * (cfg.target_vol / port_vol) if port_vol > 0 else base
        out.loc[date, scaled.index] = _apply_caps(scaled.copy(), cfg)
    return out


def latest_target(prices: pd.DataFrame, cfg: Config) -> pd.Series:
    # Only the tail matters for the latest row; trims the per-date loop.
    tail = prices.iloc[-(cfg.warmup_days() + 5):]
    return target_weights(tail, cfg).iloc[-1]
