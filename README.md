# ayush_bsm — SPX / ES BSM Options Analytics Terminal

[![Live Demo](https://img.shields.io/badge/demo-GitHub%20Pages-0A7B3E)](https://Ayushkumarsingh09.github.io/ayush_bsm/) [![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://www.python.org/)

Production-grade **Black–Scholes–Merton** and **Black-76** options analytics
for SPX (cash) and ES (futures) style European options.

**Live demo:** [https://Ayushkumarsingh09.github.io/ayush_bsm/](https://Ayushkumarsingh09.github.io/ayush_bsm/)

> First load downloads a WebAssembly Python runtime (~40 MB). Allow up to a
> minute; later loads are cached. All calculations run locally in your browser.

---

## Disclaimer

All outputs are **theoretical model values** (or market-implied fair values
when priced from market IVs). They are **not** guarantees of traded prices,
execution, profitability, or future market paths. Real markets include
volatility smile dynamics, jumps, discrete dividends, stochastic rates,
liquidity and bid/ask spreads that a single-factor BSM/Black model does not
fully capture. This is an analytics tool — not trading advice.

---

## Features

| Area | Capability |
|---|---|
| Pricing | BSM (equity index) and **Black-76** (futures, `q = r`) with auto-detect for ES |
| Inputs | Manual · CSV/Excel · BSM workbooks · **Live spot + live option bid/ask** |
| Live data | Yahoo chart (query1/query2) + Stooq failover · crumb-auth option chains for **^SPX / SPY** |
| Chain | Strike grid or **live/file market IV smile** (per-strike) |
| Premiums | Explicit **BSM Premium** columns (never confused with market quotes) |
| Greeks | Delta, Vega, Theta, Rho, Gamma, Vanna, Volga, Charm, Speed, Zomma, Color, Ultima |
| Market | Market vs Model · mispricing vs **real mids** · IV round-trip |
| Vol surface | Multi-expiry **IV surface**, smile chart, 25Δ RR / fly / ATM skew |
| Risk | Scenario shocks (spot / vol / rate / time) |
| Viz | Plotly premiums, Greeks, heatmap, smile, 3D surface |
| Export | CSV/Excel with inputs, conventions and model metadata |
| Quality | **745+** automated tests including finite-difference Greek validation |

---

## Live demo & local run

### Hosted (GitHub Pages + stlite)

Open **[https://Ayushkumarsingh09.github.io/ayush_bsm/](https://Ayushkumarsingh09.github.io/ayush_bsm/)** — the Streamlit app runs in-browser via
[stlite](https://github.com/whitphx/stlite) / Pyodide. No server required.

### Local

```bash
git clone https://github.com/Ayushkumarsingh09/ayush_bsm.git
cd ayush_bsm
pip install -r requirements.txt
streamlit run app.py
```

Requires **Python 3.10+**.

### CLI (Excel / CSV pipeline)

```bash
python scripts/run_excel_file.py "BSM inputs.xlsx"
python scripts/run_excel_file.py "sample_data/BSM_chain_inputs.xlsx"
```

---

## Mathematical model

### Black–Scholes–Merton (SPX cash, continuous dividend yield `q`)

```text
d1 = [ln(S/K) + (r − q + σ²/2)T] / (σ√T)
d2 = d1 − σ√T

Call = S e^(−qT) N(d1) − K e^(−rT) N(d2)
Put  = K e^(−rT) N(−d2) − S e^(−qT) N(−d1)
```

Put-call parity: `C − P = S e^(−qT) − K e^(−rT)`.

### Black-76 (ES / futures options)

Equivalent to BSM with **`q = r`** so the forward equals the futures price.
For an ATM futures option, call and put premiums coincide (up to discounting
symmetry). The app auto-selects Black-76 when the contract looks like ES.

### Time to expiry

```text
T = remaining_seconds / (days_per_year × 86400)
```

Default **ACT/365** (ACT/360 available). File workbooks use the sheet’s
**Current date and time** as valuation `asof` for reproducible `T`.

### Edge regimes

| Regime | Prices | Greeks |
|---|---|---|
| `T ≤ 0` | Intrinsic | **N/A** |
| `σ ≈ 0` | Discounted forward intrinsic | **N/A** |
| Regular | Full BSM / Black-76 | Analytic |

---

## Greek display conventions

| Displayed value | Convention |
|---|---|
| Theta, Charm, Color | per **calendar day** (annual / 365) |
| Vega, Vanna, Zomma, Rho | per **+1 percentage point** (raw × 0.01) |
| Volga | per (1 vol pct pt)² (raw × 10⁻⁴) |
| Ultima | per (1 vol pct pt)³ (raw × 10⁻⁶) |
| Delta, Gamma, Speed | per 1 index point (unscaled) |

Single source of truth: `pricing/conventions.py`.

---

## Input formats

Percentages in the UI are entered as percents (`18.50` → σ = 0.185).

### Flat CSV / Excel

Required: `spot`, `volatility`, `risk_free_rate`, and `expiry` **or** `dte`.  
Optional: `dividend_yield` (else `q = 0` is assumed and flagged).

### BSM chain workbook (`BSM inputs.xlsx`)

Header block + strike table:

```text
Underlying spot     7500
Risk free Rate      0.0365

Contract | Current date | Expiry | Strike | Implied Volatility
ES Sep26 | ...          | ...    | 7500   | 0.1289
```

Auto-detected. Prices **every listed strike** with its own IV. Optional
`call_mid` / `put_mid` (or bid/ask) unlock numerical mispricing.

Samples: `sample_data/sample.csv`, `sample_data/BSM inputs.xlsx`,
`sample_data/BSM_chain_inputs.xlsx`.

### Live API

Fetches **live spot** (^GSPC / SPY / ES=F) via Yahoo chart with Stooq failover,
**^IRX** (with ^TNX fallback) for rates, and **VIX**. When enabled, also
fetches **real option bid/ask** for **^SPX** / **SPY** via Yahoo crumb session,
builds a **multi-expiry IV surface**, 25Δ skew metrics, and can price the chain
on the live market IV smile. ES uses live futures spot with an explicit SPX
options vol-surface proxy (labeled in the UI). Quotes are **never fabricated**.

---

## Project structure

```text
ayush_bsm/
├── app.py                      # Streamlit UI
├── index.html                  # GitHub Pages + stlite host
├── config/settings.py          # defaults and numerical guards
├── pricing/                    # BSM core, Greeks, conventions, Black-76 model
├── analytics/                  # strike chain, scenarios, vol surface / skew
├── data/                       # manual, CSV/Excel, workbook, live options, market quotes
├── validation/                 # human-readable validators
├── visualization/              # option chain styling + Plotly charts / surface
├── utils/                      # dates, export, formatting
├── tests/                      # 745+ tests (FD Greeks, workbook, live, surface)
├── sample_data/                # example CSV / Excel
└── scripts/run_excel_file.py   # CLI runner
```

---

## Testing

```bash
python -m pytest tests -q
```

Coverage includes reference prices, put-call parity grids, deep ITM/OTM
limits, expiry and zero-vol regimes, **finite-difference validation of every
Greek**, CSV/Excel aliases, BSM workbook layout, Black-76 selection, live
snapshot smoke tests (skipped if offline), and presentation/export labels.

---

## Market inclination (read carefully)

| Mode | Meaning |
|---|---|
| Market IVs in file / live smile | Premiums are **market-implied European fair values** under BSM/Black-76 |
| Live spot / IRX / VIX | Multi-source macro inputs |
| Live option bid/ask (^SPX/SPY) | Real mids → mispricing, smile, surface, 25Δ skew |
| Path prediction | **Not** what this model does — no crystal-ball claims |

---

## License & author

Built and maintained by **Ayush**. Repository:
[github.com/Ayushkumarsingh09/ayush_bsm](https://github.com/Ayushkumarsingh09/ayush_bsm).

**Live link:** [https://Ayushkumarsingh09.github.io/ayush_bsm/](https://Ayushkumarsingh09.github.io/ayush_bsm/)
