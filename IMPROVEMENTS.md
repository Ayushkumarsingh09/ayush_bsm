# SPX BSM Dashboard — Complete Improvement Audit

**Audit date:** 2026-08-14  
**Baseline:** Original production-grade SPX BSM Options Analytics specification (as restated in the master audit prompt) + current `README.md` product definition  
**Method:** Full source inspection, independent BSM re-derivation, put-call parity checks, finite-difference Greek validation via existing tests, adversarial edge probing  
**Code modified during audit phase:** none (inspection only)

---

## 1. Executive Summary

### What is already excellent

- Clean layered architecture: `data → validation → pricing → analytics → visualization → app`.
- BSM core formulas (d1/d2, call, put, continuous dividend) are **mathematically correct**.
- Explicit numerical regimes: `regular` / `zero_vol` / `expired` with honest **N/A** Greeks (no fake finite values).
- Full first-/second-/third-order Greek set with a single convention source (`pricing/conventions.py`) and registry-driven UI/export/docs.
- Strong automated test suite: **716 passed** (run 2026-08-14), including independent textbook cross-checks and finite-difference validation of every Greek.
- CSV/Excel alias detection, percentage heuristics with reported interpretations, dividend-assumed flagging, live provider that refuses to fabricate data.
- Display already labels theoretical values as **BSM Value** (not bare “Price”) in the option chain; charts titled “BSM Theoretical Premium”.

### What is incomplete

- Market option premiums (bid/ask/mid/last) are not ingested — Case A (pure BSM calculator) only. Implied-vol solver exists but is unwired to UI.
- Live market data is intentionally unconfigured.
- Volatility smile / surface providers exist as stubs; UI always uses constant σ.
- No scenario analysis, strategy builder, portfolio Greeks, or market-vs-model mispricing view.
- Export / display-frame / UI workflow tests are thin relative to pricing tests.
- No sticky strike column; full Greek set produces a 30-column table that is hard to scan.

### What is incorrect

- **Color formula string in `pricing/registry.py` has the wrong leading sign** (documents `−…` while implementation and FD tests correctly use calendar-time `+…` / `∂Γ/∂t`). Misleading in the Greek Guide UI.
- **ATM “nearest” uses Python `round()`**, which applies banker's rounding on exact midpoints (e.g. spot 6137.5 → ATM 6150). Equidistant ties are not documented or made deterministic by `|K−S|` + explicit tie-break.
- Put-side column order is **not a true mirror** of the call side (Status sits between BSM Value and Greeks).

### What is fragile

- Timezone: expiry times interpreted in **local clock**, while SPX settlement is America/New_York — documented in a help tip but easy to mis-set.
- CSV vol heuristic: values in `(1.0, 1.5]` stay as decimals (e.g. `1.2` → 120% vol) with no ambiguity warning.
- Strike grid can include **K ≤ 0** for small ATM / large wings → `log(S/K)` → **NaN premiums** (verified: ATM=50, default 28/side → 26 NaN call prices).
- Root `*.xlsx` gitignored; sample Excel under `sample_data/` is exempt.

### What is missing (relative to a production terminal)

- Explicit validation that generated strikes are strictly positive.
- Consistent premium naming across raw DataFrame (`call_price`), UI (`BSM Value`), charts (`Premium`), export (`bsm_call_value`).
- Calculation audit trail (model version, input snapshot hash, seed/timestamps already partial in export).
- Exchange calendar / holiday awareness; ET timezone support.
- Accessibility beyond dark theme defaults; light theme; denser terminal typography options.
- CI config in-repo (tests exist but no visible GitHub Actions workflow in this tree).

### What should be prioritized

1. **P0:** Prevent non-positive strikes / NaN chain rows; fix Color docs; unify premium labeling; harden ATM midpoint.
2. **P1:** Ambiguous % warnings; timezone guidance; put-column mirror; export/display tests; moneyness edge cases.
3. **P2:** Table usability (sticky/groups), UI polish, SPX multiplier disclosure, CI.
4. **P3:** Market premiums + IV, smile, scenarios, strategies — only after P0/P1.

### Scores

| Dimension | Score (/100) | Notes |
|---|---:|---|
| Mathematical correctness | 92 | Core BSM + Greeks verified; Color docs wrong; strike edge gap |
| Pricing accuracy | 90 | Correct Case A; naming inconsistency |
| Greek accuracy | 93 | FD-validated; registry Color formula text wrong |
| Data correctness | 82 | Good CSV heuristics; vol ambiguity band; TZ fragility |
| Numerical stability | 88 | Strong regimes; fails open with NaN on K≤0 |
| Architecture | 90 | Clear separation; app.py still large (~500 LOC) |
| Testing | 88 | 716 pass; weak export/UI/strike-positivity coverage |
| Security | 85 | No secrets hardcoded; upload local-only; no XSS beyond Streamlit |
| Performance | 95 | Instant for 57 strikes; cached |
| UI/UX | 72 | Functional terminal; density/mirror/sticky gaps |
| Documentation | 85 | Strong README; registry Color formula error |
| Extensibility | 88 | Provider/vol/IV abstractions ready |
| **Overall quality** | **86 / 100** | |
| **Production readiness** | **~75%** | Solid calculator; not yet a market-linked terminal |

---

## 2. Current Architecture

```text
USER INPUT (Manual / CSV|Excel / Live stub)
        ↓
DataProvider → MarketInputs (decimal σ,r,q + expiry)
        ↓
validators.validate_market_inputs / validate_chain_config
        ↓
utils.dates.time_to_expiry (ACT/365 | ACT/360)
        ↓
analytics.chain.build_option_chain
        ├── detect_atm_strike / generate_strikes
        ├── VolatilityProvider.sigma(K,T)
        ├── pricing.bsm.compute_core → call/put prices
        └── pricing.greeks + higher_order_greeks (raw units)
        ↓
visualization.option_chain (display scaling + CALL|STRIKE|PUT)
visualization.charts (Plotly from same DataFrame)
utils.export (display units + metadata)
        ↓
app.py Streamlit orchestration (+ stlite index.html for Pages)
```

**Separation quality:** Good. Mathematics is not embedded in the UI. Greek registry drives docs/export. Minor leak: `app.py` owns a lot of layout; raw column name `call_price` still implies market price.

---

## 3. Existing Features

| Feature | Location | Status | Correctness | Tests | Known issues | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| Manual input | `app.py`, `data/manual.py` | COMPLETE | Good | Partial | Local TZ for expiry | Offer America/New_York preset | P1 |
| CSV upload | `data/csv_provider.py` | COMPLETE | Good | Strong | Vol ambiguity (1,1.5] | Warn on ambiguous band | P1 |
| Excel upload | same | COMPLETE | Good | Strong | First-sheet default | Already sheet-selectable | — |
| CSV validation / mapping | `detect_columns`, UI | COMPLETE | Good | Strong | — | Keep | — |
| ATM detection | `analytics/chain.py` | PARTIALLY COMPLETE | Midpoint fragile | Partial | Banker's round | Explicit nearest + tie-break | P0 |
| Strike generation | `analytics/chain.py` | PARTIALLY COMPLETE | Logic OK | Partial | Allows K≤0 | Validate positive strikes | P0 |
| Expiry / T | `utils/dates.py` | COMPLETE | ACT/365 OK | Strong | No holiday calendar | Document; optional NY calendar later | P2 |
| BSM call/put | `pricing/bsm.py` | COMPLETE | **Verified correct** | Strong | — | Keep | — |
| Greeks (1st) | `pricing/greeks.py` | COMPLETE | **FD-verified** | Strong | — | Keep | — |
| Higher-order Greeks | `pricing/higher_order_greeks.py` | COMPLETE | **FD-verified** | Strong | Color docs wrong | Fix registry formula text | P0 |
| Option chain UI | `visualization/option_chain.py` | PARTIALLY COMPLETE | Good | Weak | Column mirror; density | Mirror + sticky strike | P1 |
| Charts | `visualization/charts.py` | COMPLETE | Uses same DF | None | — | Add smoke tests | P2 |
| Heatmap | `charts.heatmap_chart` | COMPLETE | Normalized OK | None | — | Caption clarity | P2 |
| Export CSV/XLSX | `utils/export.py` | COMPLETE | Good metadata | None | — | Add unit tests | P1 |
| Model details | `app.py` | COMPLETE | Good | — | — | Add multiplier note | P2 |
| Error handling | validators + UI | COMPLETE | Good | Strong | Strike grid gap | Extend validators | P0 |
| Live data | `data/live_provider.py` | COMPLETE (refuses) | Correct refusal | Yes | Not wired | Real provider later | P3 |
| Implied vol | `pricing/implied_vol.py` | COMPLETE (engine) | Round-trip OK | Yes | Not in UI | Wire with market premiums | P3 |
| Premium column | display `BSM Value` | PARTIALLY COMPLETE | Values correct | Indirect | Naming inconsistency | Standardize labels | P0 |

---

## 4. Original Specification vs Actual Implementation

| Requirement | Original Specification | Current Implementation | Status | Evidence/File | Problem | Recommendation |
|---|---|---|---|---|---|---|
| Manual inputs S,σ,r,q,expiry | Required | Implemented | COMPLETE | `app.py`, `data/manual.py` | — | — |
| CSV upload + validation | Required | CSV + Excel + mapping UI | BETTER THAN SPECIFICATION | `csv_provider.py` | — | Keep |
| ATM = closest strike | Required | `round(S/interval)*interval` | PARTIALLY COMPLETE | `analytics/chain.py:53` | Midpoint banker's round | Use argmin \|K−S\| |
| 28 each side, interval 25 → 57 | Required | Defaults + tests | COMPLETE | `settings.py`, `test_data.py` | — | — |
| BSM European with q | Required | Correct formulas + regimes | COMPLETE | `pricing/bsm.py` | — | — |
| Call/Put theoretical premium visible | Required | UI: `BSM Value`; raw: `call_price` | PARTIALLY COMPLETE | `option_chain.py:42-43` | Ambiguous internal names | Rename display to `BSM Premium` |
| Market premium fields | Spec Case B/C | None (Case A) | MISSING (by design) | README Limitations | No market chain | Add when data exists; never fabricate |
| First-order Greeks | Required | Implemented + FD tests | COMPLETE | `greeks.py`, `test_greeks.py` | — | — |
| Higher-order Greeks | Required | All listed + FD tests | COMPLETE | `higher_order_greeks.py` | Color doc sign | Fix registry string |
| Option chain CALL\|STRIKE\|PUT | Required | Implemented | COMPLETE | `option_chain.py` | Put not mirrored | Fix column order |
| Moneyness ITM/ATM/OTM | Required | Grid-ATM labeled ATM | PARTIALLY COMPLETE | `_moneyness_status` | ATM ≠ S=K always | Document; optional S≈K flag |
| Charts for premium + Greeks | Required | Plotly + heatmap | COMPLETE | `charts.py` | No chart unit tests | Add smoke tests |
| Export | Required | CSV + Excel w/ metadata | COMPLETE | `export.py` | Untested | Add tests |
| Model assumptions UI | Required | Model Details + Guide | COMPLETE | `app.py` | Color formula wrong in guide | Fix registry |
| ACT/365 time | Required | ACT/365 + ACT/360 | BETTER THAN SPECIFICATION | `dates.py` | Local TZ | ET option |
| Edge: T→0, σ→0 | Required | Intrinsic + N/A Greeks | COMPLETE | `bsm.py`, tests | — | — |
| Finite-difference validation | Required | Comprehensive | COMPLETE | `test_greeks.py`, `test_higher_order_greeks.py` | — | — |
| Put-call parity | Required | Engine + UI max residual | COMPLETE | `parity_error`, `app.py` | — | — |
| No fabricated live data | Required | Live raises | COMPLETE | `live_provider.py` | — | — |
| % input as percent | Required | Manual /100; CSV heuristic | COMPLETE | `app.py`, `csv_provider.py` | Ambiguity band | Warn |
| Professional terminal UX | Required | Dark Streamlit terminal | PARTIALLY COMPLETE | `app.py`, theme | Density, sticky, a11y | P2 polish |
| Tests | Required | 716 passing | COMPLETE | `tests/` | Gaps noted | Fill P0/P1 gaps |

---

## 5. Mathematical Audit

### Independent verification (executed 2026-08-14)

Reference point: `S=6137`, `K=6125`, `T=30/365`, `r=0.0525`, `q=0.0135`, `σ=0.185`.

Independent textbook implementation vs engine:

| Quantity | Engine | Independent | Diff |
|---|---:|---:|---:|
| Call | 145.75567650534413 | 145.75567650534413 | **0.0** |
| Put | 114.18859468259143 | 114.18859468259143 | **0.0** |
| Parity residual | 0.0 | — | Pass |

Formulas confirmed:

```text
d1 = [ln(S/K) + (r−q+σ²/2)T] / (σ√T)
d2 = d1 − σ√T
C  = S e^(−qT) N(d1) − K e^(−rT) N(d2)
P  = K e^(−rT) N(−d2) − S e^(−qT) N(−d1)
```

Signs, discounts, dividend treatment, and regimes match the continuous-yield BSM model.

### Put-call parity

Suite covers ITM/ATM/OTM-style strike grids, short/long T, varied q. UI surfaces `max |parity_error|`. **Pass** under tests (`abs(err) < 1e-9`).

### Expiry / zero-vol

- `T ≤ EPS_TIME` → intrinsic; Greeks NaN. **Correct.**
- `σ ≤ EPS_SIGMA` → discounted forward intrinsic; Greeks NaN. **Correct.**

---

## 6. Premium / Pricing Audit

### Case determination: **Case A — Pure BSM Calculator**

No bid/ask/mid/last fields exist in `MarketInputs` or CSV aliases. Live provider refuses. Therefore premium **must** be theoretical BSM value only. Fabricating market premiums would be incorrect.

### Current representation

| Layer | Name | Verdict |
|---|---|---|
| Engine | `call_price` / `put_price` | Accurate values; **ambiguous names** |
| Chain DF | `call_price` / `put_price` | Same |
| UI table | `BSM Value` | Clear enough; prefer `BSM Premium` |
| KPI | `ATM BSM Call` / `ATM BSM Put` | Clear |
| Chart | “BSM Theoretical Premium vs Strike” | Clear |
| Export | `bsm_call_value` / `bsm_put_value` | Clear |

### Verdict

Premium is **not missing from the UI**. Values are **correct** (independently verified). Remaining issue is **consistent, unambiguous naming** and documenting Case A vs future Case C. Do **not** add fabricated market columns.

### Required labeling standard (to implement)

```text
CALL: BSM Premium | Greeks...
STRIKE
PUT:  BSM Premium | Greeks...
```

Caption: “BSM Premium = Black–Scholes–Merton theoretical value (not a market quote).”

---

## 7. Greeks Audit

| Greek | Definition | Implementation | Units (raw→display) | Sign | FD tests | Correctness |
|---|---|---|---|---|---|---|
| Delta | ∂V/∂S | `greeks.py` | unscaled | Correct | Yes | PASS |
| Gamma | ∂²V/∂S² | `greeks.py` | unscaled | Correct | Yes | PASS |
| Vega | ∂V/∂σ | `greeks.py` | ×0.01 per 1% | Correct | Yes | PASS |
| Theta | ∂V/∂t | `greeks.py` | /365 per day | Calendar t | Yes | PASS |
| Rho | ∂V/∂r | `greeks.py` | ×0.01 per 1% | Correct | Yes | PASS |
| Vanna | ∂²V/∂S∂σ | `hog.py` | ×0.01 | Correct | Yes | PASS |
| Volga | ∂²V/∂σ² | `hog.py` | ×1e-4 | Correct | Yes | PASS |
| Charm | ∂Δ/∂t | `hog.py` | /365 | Calendar t | Yes | PASS |
| Speed | ∂³V/∂S³ | `hog.py` | unscaled | Correct | Yes | PASS |
| Zomma | ∂Γ/∂σ | `hog.py` | ×0.01 | Correct | Yes | PASS |
| Color | ∂Γ/∂t | `hog.py` | /365 | Calendar t; **docs wrong** | Yes | Code PASS / Docs FAIL |
| Ultima | ∂³V/∂σ³ | `hog.py` | ×1e-6 | Correct | Yes | PASS |

**Convention mixing:** Not found in code paths. Display scaling is centralized. Good.

---

## 8. Numerical Stability Audit

| Case | Behavior | Verdict |
|---|---|---|
| T→0 | Intrinsic; Greeks N/A | PASS |
| σ→0 | Forward intrinsic; Greeks N/A | PASS |
| Deep ITM/OTM | Bounded, finite | PASS (tested) |
| Huge σ (4.9) | Finite, ≤ spot bound | PASS |
| K≤0 | **NaN prices** | **FAIL — must block** |
| S≤0 | Validator rejects | PASS at input boundary |
| Extreme K (1e7) | Finite prices; Greeks not ±inf | PASS |

---

## 9. Data Accuracy Audit

| Input | Source | Units | Risk |
|---|---|---|---|
| Spot | Manual/CSV | Index points | Low |
| Volatility | Manual % / CSV heuristic | Decimal internally | Medium: `1.2` stays 120% |
| Rate / q | Same | Decimal | Medium: threshold at 1.0 |
| Expiry | Timestamp / DTE | Local TZ | Medium vs ET settlement |
| Premium | Model only | Index points (not ×100 $) | Disclose multiplier |

---

## 10. Option Chain Audit

**Present:** Strike, BSM values, Greeks (toggleable groups), moneyness S/K, ITM/ATM/OTM, ATM highlight, ITM shading, filters, decimals, export, parity caption.

**Gaps:**

- No sticky Strike column (Streamlit limitation / not configured).
- Put Status placement breaks visual mirror.
- Full Greek mode = 30 columns — usable only with group toggles (defaults help).
- Internal `call_price` naming.

---

## 11. UI/UX Audit

**Trader-seconds test:** KPIs + ATM BSM Call/Put + chain → yes for core workflow.

**Student-project tells to remove:**

- Inconsistent premium vocabulary.
- Non-mirrored put columns.
- Weak timezone/settlement guidance.
- No “index points vs $ multiplier” disclosure.
- Greek Guide Color formula contradiction vs implementation.

**Positives:** Dark terminal theme, disclaimer, parity badge, calculate-snapshot consistency, N/A honesty.

---

## 12. Visualization Audit

Charts consume the same analytics DataFrame and `scaled_greek` — **no duplicate math**. Premium chart correctly plots theoretical values. Heatmap normalization is disclosed in title. Missing: automated chart smoke tests; optional dual-axis premium chart.

---

## 13. Testing Audit

**Ran:** `python -m pytest tests -q` → **716 passed** in ~49s (2026-08-14).

| Area | Coverage |
|---|---|
| Pricing / parity / limits | Excellent |
| FD Greeks all orders | Excellent |
| Edge T/σ | Strong |
| CSV/Excel/validators/IV | Strong |
| Strike count / ATM nearest | Partial |
| Export / display frame | **Missing** |
| Non-positive strike grid | **Missing** |
| UI workflows | **Missing** (acceptable for Streamlit) |
| Chart consistency | **Missing** |

---

## 14. Security Audit

| Check | Finding |
|---|---|
| Secrets in repo | None found; `.env.example` placeholders only |
| `.gitignore` | Ignores `.env`, `secrets.toml` |
| Live API | Refuses without config |
| File upload | Parsed via pandas; local Streamlit trust model |
| Logs | No secrets logged |
| Dependency pins | Lower bounds only (supply-chain drift risk) |

No invented CVEs. Recommend pinning versions in production deploys (P2).

---

## 15. Performance Audit

57-strike chain is instantaneous. `@st.cache_data` on `compute_chain`. Vectorized NumPy. No issues for current scope. Cap at 500 strikes/side is sensible.

---

## 16. Architecture Audit

**Strengths:** Provider ABC, volatility ABC, convention registry, regime masks, UI orchestration separated from math.

**Issues worth fixing (not style refactors):**

- Premium column naming inconsistency across layers.
- Strike positivity not validated at config boundary.
- `app.py` size — acceptable; no urgent split required.

---

## 17. Missing Features

Valuable additions (only if they earn their complexity):

| Feature | Why | Priority |
|---|---|---|
| Market mid + BSM + mispricing % | Real trading usefulness | P3 (needs data) |
| Wire implied vol to UI | Completes Case C | P3 |
| Spot/IV/time scenario grid | Risk desk staple | P3 |
| Multi-leg payoff | Strategy education | P3 |
| Smile via `PerStrikeVolatility` UI | Uses existing abstraction | P3 |
| Portfolio / net Greeks | Position aggregation | P3 |
| America/New_York expiry helper | SPX correctness | P1 |
| Positive-strike validation | Correctness | P0 |

---

## 18. What the Original Specification Missed

1. **Strike grid positivity invariant** — mathematical domain of `ln(S/K)`.
2. **Explicit ATM midpoint tie-break policy**.
3. **SPX contract multiplier ($100)** disclosure for P&L interpretation.
4. **Settlement timezone (ET)** as first-class input, not a help tooltip.
5. **Calculation audit trail** (engine version, git SHA, input hash) in every export.
6. **Ambiguous percentage band** handling policy for CSV.
7. **CI + dependency pinning** for reproducible builds.
8. **Chart/export regression tests** as part of “mathematical correctness” definition.
9. **Graceful degradation** when Greek groups make the table unusable (column presets).
10. **Model versioning** string surfaced in UI/export for reproducibility.

---

## 19. Recommended Improvements

### P0 — Critical

1. Reject / repair strike grids with `K ≤ 0`; never show NaN premiums from invalid K.
2. Unify premium labels to **BSM Premium** (UI/charts/KPI caption) and document Case A.
3. Fix Color formula text in `GREEK_REGISTRY` to match calendar-time implementation.
4. Replace ATM `round()` with explicit nearest-strike + documented tie-break (prefer lower strike when equidistant).

### P1 — High

5. Warn on CSV volatility in ambiguous `(1.0, VOL_PERCENT_THRESHOLD]` band.
6. Mirror put columns; put Status outermost.
7. Add tests: strike positivity, ATM midpoint, export frame, display labels.
8. Stronger expiry timezone messaging / optional ET assumption note in Model Details.
9. Rename raw analytics columns to `call_bsm_premium` / `put_bsm_premium` (or keep aliases) for clarity.

### P2 — Medium

10. Sticky/emphasized strike column styling; “compact Greeks” preset.
11. SPX $100 multiplier disclosure.
12. Chart smoke tests; pin dependencies; add CI workflow.
13. Model version string in export metadata.

### P3 — Enhancement

14. Market premium columns when CSV provides them (bid/ask/mid) + mispricing.
15. UI for IV solve + smile via `PerStrikeVolatility`.
16. Scenario / strategy / portfolio modules.

---

## 20. P0/P1/P2/P3 Roadmap

| ID | Item | Priority | Difficulty | Impact | Risk | Dependencies |
|---|---|---|---|---|---|---|
| R1 | Positive strike validation | P0 | Low | High | Low | validators, chain, tests |
| R2 | Premium naming consistency | P0 | Low | High | Low | option_chain, charts, app, export |
| R3 | Fix Color registry formula | P0 | Low | Medium | Low | registry only |
| R4 | ATM nearest + tie-break | P0 | Low | Medium | Low | chain, tests |
| R5 | CSV vol ambiguity warning | P1 | Low | Medium | Low | csv_provider |
| R6 | Put column mirror | P1 | Low | Medium | Low | option_chain |
| R7 | Tests for R1–R6, export | P1 | Medium | High | Low | tests |
| R8 | TZ / multiplier disclosures | P1–P2 | Low | Medium | Low | app, README |
| R9 | Table UX presets | P2 | Medium | Medium | Low | app, option_chain |
| R10 | Market vs model (Case C) | P3 | High | High | Med | data model change |

---

## 21. Implementation Plan

```text
PHASE 1 — Mathematical / data correctness (P0)
  • Strike positivity validation
  • ATM nearest + tie-break
  • Color registry formula fix
  • Premium labeling consistency (BSM Premium)

PHASE 2 — Pricing presentation (P0/P1)
  • Display/export/chart/KPI vocabulary
  • Put-side column mirror
  • Case A caption under chain

PHASE 3 — Greek documentation (P0)
  • Registry Color string aligned with ∂Γ/∂t

PHASE 4 — Data correctness (P1)
  • CSV ambiguous vol warning
  • Timezone + multiplier disclosures

PHASE 5 — Testing (P1)
  • New tests for strikes, ATM ties, export, display labels
  • Re-run full suite + independent BSM spot-check

PHASE 6 — Architecture (minimal)
  • Optional raw column rename with compatibility aliases if needed

PHASE 7 — UI/UX (P2, selective)
  • Compact greek preset; disclosures

PHASE 8 — Advanced analytics (P3) — defer unless time remains after P0–P2
```

---

## 22. Verification Strategy

After each phase:

1. `python -m pytest tests -q`
2. Independent BSM spot-check script (call/put/parity)
3. Adversarial: K-grid with small ATM, σ=0, T=0, midpoint spot, malformed CSV
4. Manual UI smoke: Calculate → chain shows **BSM Premium**, no NaNs on default SPX inputs

---

## 23. Production Readiness Score

| Gate | Status |
|---|---|
| Correct BSM prices | PASS |
| Correct Greeks (code) | PASS |
| Honest edge regimes | PASS |
| No fabricated market data | PASS |
| Premium unambiguous to end user | PARTIAL → fix in Phase 2 |
| Invalid strike grids blocked | FAIL → fix in Phase 1 |
| Docs match code | PARTIAL (Color) → fix |
| Market-linked terminal | NOT READY (by design) |
| **Overall production readiness** | **~75% as theoretical analytics tool; ~40% as market terminal** |

---

*End of audit document. Implementation follows the plan in §21 in priority order.*
