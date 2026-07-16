# Nexus Trading Bot

MT5 trading automation with FVG/structure scanning, War Room decisioning, live risk controls, trade management, and a real-time dashboard.

The bot does not trade on a timer. It trades only when signal quality, execution gates, risk checks, lockouts, kill switches, spread safety, and broker checks pass.

## Core Behavior

- Dashboard runs at `http://127.0.0.1:5000`.
- Scanner runs in fast early-entry mode by default, about every 3 seconds.
- M5 data is still used for market structure.
- One active trade per symbol is the default, while multiple qualified symbols can trade at the same time.
- A symbol enters cooldown after its position closes.
- Cooldown can be bypassed only by exceptional A-grade fresh-structure setups.
- The bot dynamically weights execution methods and will choose the strongest validated path: regular setup, early entry, scalp, or ICT order block when enabled.
- Trade management is staged: partial TP, then trailing SL/TP, then reverse-profit protection.
- `XAU` and `JPY` symbols use wider exit-management profiles.

## Quick Start

Windows:

```powershell
run.bat
```

Fallback:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Linux/Mac:

```bash
chmod +x run.sh
./run.sh
```

Fallback:

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

## Required Setup

Start MT5 and log in to the account you want the bot to trade. The engine attaches to the currently logged-in local MT5 terminal account and logs the detected account mode, login, server, leverage, balance, and equity on startup.

`MT5_ACCOUNT`, `MT5_PASSWORD`, and `MT5_SERVER` are optional notes only. They are not required for execution.

Core trading setup:

```env
TRADING_SYMBOLS=EURUSD,GBPUSD,USDJPY,XAUUSD
EXECUTION_SYMBOLS=EURUSD,GBPUSD,USDJPY,XAUUSD
TRADE_VOLUME=0.001
POSITION_SIZING_MODE=fixed
FEATURE_DYNAMIC_ACCOUNT_PROFILE=true
FEATURE_SMALL_ACCOUNT_MODE=false
SMALL_ACCOUNT_EQUITY_THRESHOLD=25
SMALL_ACCOUNT_TRADE_VOLUME=0.001
SMALL_ACCOUNT_MAX_AUTO_MIN_LOT=0.01
SMALL_ACCOUNT_MAX_EXPOSURE_PERCENT=0.01
SMALL_ACCOUNT_MAX_ACTIVE_TRADES=1
SMALL_ACCOUNT_ALLOW_METALS=false
SMALL_ACCOUNT_ALLOW_CRYPTO=false
SMALL_ACCOUNT_ALLOW_STOCKS=false
SMALL_ACCOUNT_DISABLE_NEWS_LADDER=true
SMALL_ACCOUNT_DISABLE_PENDING_ORDERS=true
MAX_EXPOSURE_PERCENT=0.05
DAILY_PROFIT_CAP=0.02
MAX_DAILY_LOSSES=100
MAX_CONSECUTIVE_LOSSES=30
```

The bot uses fixed lots only. `TRADE_VOLUME=0.001` is allowed by the bot, but the broker symbol must also support that minimum and volume step.
If the broker minimum is higher, `FEATURE_BROKER_MIN_LOT_FALLBACK=true` lets the engine use the broker minimum only up to `MAX_AUTO_MIN_LOT=0.01`; symbols that require larger lots stay blocked.

For very small accounts, use a cent/nano account if possible. A standard `0.01` lot can be too large for proper risk control.

```env
TRADE_VOLUME=0.001
FEATURE_BROKER_MIN_LOT_FALLBACK=true
MAX_AUTO_MIN_LOT=0.01
POSITION_SIZING_MODE=fixed
MAX_EXPOSURE_PERCENT=0.01
MAX_TRADES_PER_SYMBOL=1
MAX_ACTIVE_TRADES_TOTAL=10
MAX_DAILY_LOSSES=100
MAX_CONSECUTIVE_LOSSES=30
FEATURE_NEWS_LADDER=false
```

`MAX_DAILY_LOSSES` and `MAX_CONSECUTIVE_LOSSES` are count-based brakes. High values such as `100` and `30` prevent one or two losing results from stopping new trades. Set either value to `0` only when you intentionally want to disable that specific count brake; daily loss cap and exposure checks still remain separate safeguards.

`FEATURE_SMALL_ACCOUNT_MODE=true` adds a dynamic safety overlay when account equity is at or below `SMALL_ACCOUNT_EQUITY_THRESHOLD`. It does not replace the strategy; it tightens live execution by forcing small fixed lots, lower exposure, fewer active trades, optional metal/crypto/stock blocking, and no news ladder or pending orders when configured.

## Scan Timing

Fast early-entry mode:

```env
SCAN_ON_NEW_CANDLE=false
SCAN_TIMEFRAME_MINUTES=5
SCAN_INTERVAL_SECONDS=3
ENGINE_LOOP_SLEEP_SECONDS=3
DUPLICATE_SIGNAL_COOLDOWN_SECONDS=300
```

Calmer candle-close mode:

```env
SCAN_ON_NEW_CANDLE=true
SCAN_TIMEFRAME_MINUTES=5
```

## Entry Gates

The bot can create candidates from FVG or broader structure archetypes:

- Sweep Reversal
- Structure Continuation
- Order Block Mitigation
- FVG Momentum
- Scalp Retest

Core thresholds:

```env
ANALYTIC_TIMEFRAMES=M1,M5,M15,H1,H4
FEATURE_EARLY_ENTRY=true
EARLY_ENTRY_MIN_SCORE=0.50
EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.58

FEATURE_WAR_ROOM=true
ANALYTIC_WEIGHT=0.6
PREDICTIVE_WEIGHT=0.4
CONVICTION_THRESHOLD=0.20
EXECUTION_CONVICTION_THRESHOLD=0.35
EXECUTION_SETUP_SCORE_THRESHOLD=0.50
EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.58
MIN_TRADE_READINESS_SCORE=0.62
MARKET_EXECUTION_SCORE_THRESHOLD=0.45
MARKET_EXECUTION_CONVICTION_THRESHOLD=0.35
FEATURE_ICT_MODE=false
ICT_MIN_SETUP_SCORE=0.60
ICT_MIN_CONFLUENCE=0.60
```

ICT order block mode is an optional path that can allow order-block-aligned setups when score, confluence, and session strength all validate. It still requires spread/drift checks and professional gate safety.

The analytic engine scores liquidity, volume, FVG quality, order-block context, and multi-timeframe alignment. `ANALYTIC_TIMEFRAMES` controls which timelines feed that alignment score. By default, M1/M5 help timing, M15 anchors local structure, and H1/H4 check higher-timeframe bias before the War Room receives the analytic score.

Professional execution gate:

```env
FEATURE_PROFESSIONAL_EXECUTION_GATE=true
MIN_EXECUTION_GRADE=B
ALLOW_C_GRADE_SCALPS=false
MIN_PROFESSIONAL_SETUP_SCORE=0.62
MIN_PROFESSIONAL_CONVICTION=0.30
MIN_SESSION_SCORE_FOR_TRADE=0.40
MIN_SESSION_SCORE_FOR_SCALP=0.55
BLOCK_CONTEXT_WATCH_TRADES=true
```

Context-only setups stay watch-only. Executable setups need real structure such as liquidity sweep, MSS/BOS, displacement, aligned order block/FVG, or a confirmed false-move reversal. The professional gate uses the strongest available conviction source from the ensemble, signal, scalp profile, or setup score, so a strong structure setup is not blocked just because one conviction field is conservative.

Scalps need hard structure before execution: MSS/BOS, displacement, OB/FVG reaction, or confirmed false-move reclaim. A liquidity sweep by itself is treated as watchlist context, not enough to open a scalp.

Execution is staged:

```text
DISCOVERED: interesting setup for dashboard/watchlist.
QUALIFIED: archetype has the required proof, such as sweep, MSS/BOS, displacement, OB/FVG, false move, or aligned post-news retest.
EXECUTABLE: qualified setup also passes readiness from setup quality, spread/drift, expected R, session, and structure.
```

## Trade Horizon Execution

Scalp, intraday, and swing setups are not executed with the same method:

```env
SCALP_EXECUTION_CONVICTION_THRESHOLD=0.28
SCALP_EXECUTION_SETUP_SCORE_THRESHOLD=0.52
SCALP_EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.55
SCALP_REQUIRE_HARD_STRUCTURE=true
SCALP_REQUIRE_HTF=false

INTRADAY_EXECUTION_CONVICTION_THRESHOLD=0.35
INTRADAY_EXECUTION_SETUP_SCORE_THRESHOLD=0.50
INTRADAY_EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.58
INTRADAY_REQUIRE_HARD_STRUCTURE=false
INTRADAY_REQUIRE_HTF=false

SWING_EXECUTION_CONVICTION_THRESHOLD=0.42
SWING_EXECUTION_SETUP_SCORE_THRESHOLD=0.68
SWING_EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.66
SWING_REQUIRE_HARD_STRUCTURE=true
SWING_REQUIRE_HTF=true
MIN_EXPECTED_R_SWING=1.5
TAKE_PROFIT_R_MULTIPLIER_SWING=2.5
```

Scalps need fast, hard structure such as MSS/BOS, displacement, OB/FVG reaction, or confirmed false-move reclaim. Intraday trades use balanced structure and session quality. Swing trades need stronger score/conviction and higher-timeframe alignment before execution.

## Instrument Profiles

The engine separates execution assumptions by asset class before placing a trade:

```env
FEATURE_INSTRUMENT_PROFILES=true

MIN_PROFIT_PIPS_FOREX=1.5
MAX_ENTRY_DRIFT_PIPS_FOREX=6
MAX_SPREAD_PIPS_FOREX=2.5

MIN_PROFIT_PIPS_METAL=20
MAX_ENTRY_DRIFT_PIPS_METAL=50
MAX_SPREAD_PIPS_METAL=35

MIN_PROFIT_PIPS_STOCK=20
MAX_ENTRY_DRIFT_PIPS_STOCK=10
MAX_SPREAD_PIPS_STOCK=5
MIN_SETUP_SCORE_STOCK=0.70
MIN_CONVICTION_STOCK=0.45
MIN_SESSION_SCORE_STOCK=0.60
MIN_EXECUTION_GRADE_STOCK=A
BLOCK_STOCK_SCALPS=true
```

Forex stays tight and spread-sensitive. Metals get wider drift/spread tolerance and wider trade management. Stocks are stricter by default because CFD spreads, sessions, gaps, and fills behave differently from forex pairs.

## Lockout And Cooldown

```env
SIGNAL_LOCKOUT_ENABLED=true
MAX_TRADES_PER_SYMBOL=1
MAX_ACTIVE_TRADES_TOTAL=10
TRADE_COOLDOWN_MINUTES=3

FEATURE_COOLDOWN_OVERRIDE=true
COOLDOWN_OVERRIDE_MIN_GRADE=A
COOLDOWN_OVERRIDE_MIN_SCORE=0.78
COOLDOWN_OVERRIDE_MIN_CONVICTION=0.45
COOLDOWN_OVERRIDE_REQUIRE_SPREAD_SAFE=true
COOLDOWN_OVERRIDE_REQUIRE_NEW_STRUCTURE=true
NO_REVENGE_COOLDOWN_SECONDS=3600
FEATURE_REVERSAL_SHOCK_GUARD=true
REVERSAL_SHOCK_COOLDOWN_MINUTES=30
REVERSAL_SHOCK_XAU_COOLDOWN_MINUTES=60
FEATURE_OPPOSING_SIGNAL_PROFIT_EXIT=true
OPPOSING_SIGNAL_MIN_R=0.20
OPPOSING_SIGNAL_MIN_SCORE=0.58
```

Cooldown override can bypass only the post-close symbol cooldown. It does not bypass active-trade limits, kill switches, exposure checks, spread safety, price drift checks, professional gate, news/trap gates, or broker/funds checks.

The reversal shock guard blocks same-direction re-entry after a max-adverse exit or losing close. This prevents the bot from repeatedly buying or selling into a fresh reversal. `XAU` symbols use the longer shock cooldown because one failed gold entry can dominate a small account's daily P&L.

Opposing-signal profit exit is a defensive close rule. If a symbol already has an open trade, the scanner sees a qualified opposite setup, and the open position is at least `OPPOSING_SIGNAL_MIN_R` in profit, the bot closes the active trade and lets cooldown/shock guard take over. It does not instantly flip into the new direction.

## News And False-Move Guard

```env
FEATURE_FALSE_MOVE_DETECTION=true
FEATURE_NEWS_MODE=true
NEWS_BLOCK_UNSAFE=true
NEWS_RISK_MULTIPLIER=0.35
NEWS_ALLOW_RETEST_FOLLOW=true
FEATURE_NEWS_LADDER=true
NEWS_LADDER_MAX_ADDONS=2
NEWS_LADDER_MIN_R=0.55
NEWS_LADDER_VOLUME_PCT=0.35
NEWS_LADDER_COOLDOWN_SECONDS=180
```

Unsafe news spikes are blocked. Post-news follow trades are allowed only after confirmation, manageable spread, and reduced risk. News ladder add-ons do not consume another `MAX_TRADES_PER_SYMBOL` slot.

## Trade Management

```env
TRAILING_STOP_TRIGGER_PCT=0.55
TRAILING_STOP_LOCK_PIPS=10
TRAILING_STOP_STEP_PCT=0.50
TRAILING_STOP_MIN_STEP_PIPS=5

FEATURE_TRAILING_TAKE_PROFIT=true
TRAILING_TP_TRIGGER_PCT=0.85
TRAILING_TP_EXTENSION_PCT=0.5
TRAILING_TP_COOLDOWN_SECONDS=300
FEATURE_PARTIAL_TP_EXTEND=true
PARTIAL_TP_EXTEND_PCT=0.5

FEATURE_PARTIAL_TAKE_PROFIT=true
PARTIAL_TP_TRIGGER_R=0.75
PARTIAL_TP_CLOSE_PCT=0.5
PARTIAL_TP_LOCK_PIPS=10

FEATURE_BREAKEVEN_PROTECTION=true
BREAKEVEN_TRIGGER_R=0.30
BREAKEVEN_LOCK_PIPS=0
FEATURE_FIRST_PROFIT_BREAKEVEN=true
FIRST_PROFIT_BREAKEVEN_TRIGGER_R=0.10
FIRST_PROFIT_BREAKEVEN_TRIGGER_R_SCALP=0.08
FEATURE_REVERSAL_BREAKEVEN_AT_ENTRY=true

FEATURE_MAX_ADVERSE_EXIT=true
MAX_ADVERSE_R=0.60

FEATURE_REVERSE_PROFIT_EXIT=true
REVERSE_PROFIT_MIN_R=1.20
REVERSE_PROFIT_GIVEBACK_PCT=0.45
REVERSE_PROFIT_CLOSE_PCT=0.5
REVERSE_AFTER_PARTIAL_LOCK_R=0.20

FEATURE_SYMBOL_PROFILES=true
FEATURE_INSTRUMENT_PROFILES=true
FEATURE_TRADE_HORIZON_PROFILES=true
HORIZON_PROFILE_MODE=exit_only
ENABLE_SCALP_PROFILE=true
ENABLE_INTRADAY_PROFILE=true
ENABLE_SWING_PROFILE=true
```

Exit rules are staged so a close action pauses further exit changes until the next position-management loop. Partial TP is the exception for runner targeting: after the partial close, the bot immediately protects the runner. It tries to move SL to entry plus `PARTIAL_TP_LOCK_PIPS`; if price has not moved far enough, it falls back to exact breakeven instead of forcing a tight stop. It also assigns a fresh TP for the remaining runner using `PARTIAL_TP_EXTEND_PCT`.

For stronger profitability, the bot favors a staged exit profile:
- Move the stop loss only after the trade reaches about 50–60% of the original TP distance.
- Lock the stop around 10 pips into profit when price has enough room; otherwise use exact breakeven.
- Take a partial TP around 0.75–0.85R and close 40–60% of the position.
- Let the remaining size run with trailing TP extension and reverse-profit exit if the trend continues.

Symbol and horizon profiles affect exit management only:

```text
DEFAULT: global values above.
JPY: wider trailing and later partial/reverse exits.
XAU: widest trailing and latest partial/reverse exits.

SCALP: faster protection, earlier partial TP, tighter max-adverse exit, no news ladder.
INTRADAY: balanced defaults.
SWING: later partial TP, wider trailing, later reverse exit, no news ladder.
```

For `XAUUSD`, keep trailing wider. Values near `0.50` or higher for `TRAILING_STOP_STEP_PCT` usually give the trade more room.

## Dashboard

Key dashboard panels:

- Open, Closed, Net, and Daily P&L
- Drawdown
- Bot Score / Readiness
- Trade Decision
- Confirmed / Missing blockers
- Strategy Breakdown
- Execution Safety
- Why No Trade?
- Global Radar
- Open Positions and active trade management state

The bot score is a readiness score, not a profitability guarantee. It measures connection, runtime, scan freshness, risk guardrails, management features, and current signal quality.

## API

Useful endpoints:

```text
GET  /api/bot/status
POST /api/bot/start
POST /api/bot/stop
GET  /api/positions
GET  /api/signals
GET  /api/logs
GET  /api/stats
GET  /api/config
POST /api/config
GET  /api/watchlist
GET  /api/pending-orders
POST /api/pending-orders/place
POST /api/panic-close
```

## Safety Notes

- Live trading depends on MT5 and broker execution rules.
- Keep `MAX_TRADES_PER_SYMBOL=1` while tuning.
- Use `MAX_ACTIVE_TRADES_TOTAL` to control how many qualified symbols can be active at once.
- Review logs after changing risk, conviction, cooldown, or exit settings.
- For small accounts, disable news ladder and avoid volatile symbols such as `XAUUSD`.

## Troubleshooting

- Dashboard stats show zeros: check MT5 connection and today's JSON log.
- Repeated signals: check `DUPLICATE_SIGNAL_COOLDOWN_SECONDS` and symbol lockout.
- No trades: inspect War Room, professional gate, spread, drift, cooldown, exposure, and kill-switch reasons.
- Trades too frequent: raise `TRADE_COOLDOWN_MINUTES`, `CONVICTION_THRESHOLD`, or professional gate thresholds.
