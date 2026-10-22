# Freqtrade (crypto strategy backtesting)

[Freqtrade](https://github.com/freqtrade/freqtrade) is an open-source crypto trading bot with a strong
backtester and strategy optimizer. Here it is for **research only**: backtesting crypto strategies on
exchange price history. It does not trade on Robinhood (it supports exchanges like Kraken, Coinbase and
Binance through ccxt), so the hourly Robinhood bots don't use it.

Install (its own virtual environment, so its packages can't clash with the bots' pinned versions):

```bash
python3 -m venv ~/freqtrade-venv && ~/freqtrade-venv/bin/pip install -r requirements-freqtrade.txt
```

This folder (named freqtrade_user so it never shadows the freqtrade package) is its `user_data` directory (`--userdir freqtrade_user`). Getting price history needs network access
to an exchange's public API, for example `api.kraken.com` or `api.exchange.coinbase.com`; in the cloud
environment add those under Allowed domains first. Then, for example:

```bash
~/freqtrade-venv/bin/freqtrade download-data --userdir freqtrade_user --exchange kraken \
    --pairs BTC/USD ETH/USD SOL/USD --timeframes 1h 1d --days 730
~/freqtrade-venv/bin/freqtrade backtesting --userdir freqtrade_user --strategy <Name> --timeframe 1h
```

Downloaded data and backtest results stay out of git (see .gitignore); strategies in `strategies/` are kept.
