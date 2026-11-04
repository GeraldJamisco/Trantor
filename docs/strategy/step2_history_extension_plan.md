# Step 2 — Historical Data Extension (prepared, not started)

- **Status:** prepared 2026-11-04. Owner decision: first observe live shadow/paper
  operation for a few days, then decide.
- **Why:** every historical split has fewer than 30 closed trades (Phase E: 43
  trades; Phase G: 23), so no strategy question can be answered from the current
  2024-01 → 2026-10 M15 sample.

## 1. What the broker provides today (read-only probe, 2026-11-04)

| Timeframe | Earliest available | Note |
|---|---|---|
| D1 | 2010-01-04 | already used for EMA warm-up |
| H4 | 2010-01-04 | 25,940 bars |
| M15 | **2022-08-11** | yearly counts: 2022 9,151 · 2023 23,561 · 2024 23,736 |

The M15 boundary matches the terminal's **Max bars in chart = 100,000** exactly
(about 100k M15 bars back from today). The broker's server may hold more.

**Owner action (optional, terminal setting):** MT5 → Tools → Options → Charts →
Max bars in chart → *Unlimited*, restart the terminal, then re-run the probe. Trantor
does not change terminal settings itself.

## 2. Acquisition (when approved)

Use the existing tool. Each run writes a new immutable directory; existing
datasets are never touched.

```powershell
python tools\mt5_acquire_xauusd_p.py --timeframe 15m --start 2022-08-11 --include-costs
python tools\mt5_acquire_xauusd_p.py --timeframe 4h  --start 2022-01-01 --include-costs
python tools\mt5_acquire_xauusd_p.py --timeframe 1d  --start 2010-01-01 --include-costs
python tools\mt5_acquire_xauusd_p.py --audit research_data\xauusd_p\runs\<run>
```

Then a new `DatasetSpec` identity with checksums. The Phase E default identity
stays as the reference baseline.

## 3. Research protocol for strategy variants (to pre-register before any run)

1. Time conversion with the inferred broker rule (server = New York + 7 h), as in Phase G.
2. Chronological splits fixed in advance, for example development 2022-08 → 2024-06,
   validation 2024-07 → 2025-12. The 2026 data and all live shadow/paper data
   serve as the forward test. The 2026 holdout has already been viewed and must
   not be used for selection.
3. At most 2–3 variants, each with its own identity and output directory, each
   addressing the documented weakness (entry quality, initial-stop losses):
   - zone rejection confirmation before entry (close back inside the zone);
   - stop-buffer sensitivity relative to H4 ATR;
   - news-aware scoring once the calendar is owner-confirmed (enables the high tier).
4. Risk ceilings unchanged (1% / 0.5%, 3% daily, 3 entries, 2 positions).
5. Adoption only with explicit owner approval. "No variant shows an edge" is an
   acceptable, reportable outcome.
