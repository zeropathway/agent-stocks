# Autonomous Trading Agent

A 24/7 autonomous trading agent powered by Claude that researches markets, analyzes equities and crypto, executes trades through Alpaca, and journals every decision.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys:

```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ANTHROPIC_API_KEY=your_anthropic_key
```

## Paper vs Live

The agent always starts in **paper mode**. To switch to live trading:

1. Open `config.yaml`
2. Change `trading.live: false` → `trading.live: true`
3. Update `.env` `ALPACA_BASE_URL` to `https://api.alpaca.markets`

No code changes needed — `broker.py` reads the config flag at runtime.

## Running the Routines

### Pre-market (08:00 ET) — Research + Gappers
```bash
python routines/premarket.py
```

### Mid-day (12:30 ET) — Technical Re-checks + Position Management
```bash
python routines/midday.py
```

### End of Day (15:50 ET) — P&L, Journal, Context Update
```bash
python routines/eod.py
```

## Running Tests

```bash
pytest tests/ -v
```

Tests hit the **paper** Alpaca endpoint directly — no mocks, real network calls.

## Smoke Test (Hello World Order)

```bash
python broker.py
```

Places and immediately cancels a 1-share order on a cheap ticker to verify end-to-end connectivity.

## Directory Layout

```
.
├── .env                    # API keys (never commit)
├── config.yaml             # Risk parameters + paper/live flag
├── broker.py               # Alpaca wrapper — all order flow goes here
├── requirements.txt
├── README.md
├── PLAN.md                 # Phases 2–7 checklist
├── routines/
│   ├── premarket.py        # 08:00 ET
│   ├── midday.py           # 12:30 ET
│   └── eod.py              # 15:50 ET
├── data/
│   └── cache/              # Parquet cached market data
├── journal/
│   ├── lessons.md          # Rolling lessons the agent edits
│   └── trades/             # One .md file per trade
├── context/
│   └── market_context.md   # Agent's evolving market worldview
└── tests/
    └── test_broker.py
```

## Hard Risk Rules (enforced in code, not prompts)

| Rule | Value |
|------|-------|
| Paper only | Until `config.yaml` `live: true` |
| Max risk per trade | 1% of equity (ATR-sized) |
| Max open positions | 5 |
| Max sector exposure | 20% gross |
| Daily loss limit | -3% halts new entries |
| Leverage | Disabled |
| Options | Disabled |
| Shorting | Disabled (v1) |
