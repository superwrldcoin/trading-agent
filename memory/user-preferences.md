# User Preferences

Only preferences the user has stated directly. Do not infer. Unfilled fields mean "unknown, ask."

## Watchlist (added: 2026-10-04)
- XAU/USD (spot gold)
- MSFT (Microsoft)
- SI (silver)
- BCH/USDT (Bitcoin Cash)

## Timeframe (added: 2026-10-04)
- Primary: **4H**. Structure, zones, and invalidation are judged on 4H candle closes.

## Analysis method (added: 2026-10-04)
- Structure is read off higher-timeframe liquidity reference levels:
  - PDH / PDL: previous day high / low
  - PWH / PWL: previous week high / low
  - PMthH / PMthL: previous month high / low
- Look for sweeps and retests of those levels, demand/supply shelves, and range or base structure.
- Invalidation = a clean 4H close beyond the structural level, not a wick.

## Report format: "4H Execution Matrix" (added: 2026-10-04)
One block per instrument:

```
<INSTRUMENT> (<name>)
Market Structure: <one-line read of 4H structure>
Current Price: <fetched price, with as-of time>
Execution Zone: <low> - <high> (<which reference level / confluence>)
Structural Invalidation (Stop): <level> (<4H close condition>)
Target 1 (De-Risk NN%): <level> (<reference level>)
Target 2 (Expansion NN%): <level> (<reference level>)
Target 3 (Runner NN%): <level> (<reference level>)   # optional
```

- Scale-out split used so far: 40 / 40 / 20 for three targets, 50 / 50 for two.
- Every level must tie to a named reference (PDH, PWL, etc.) computed from fetched data.

## Not yet specified
- Risk tolerance / max position size: _TBD_
- Minimum reward:risk to show a setup: _TBD_
- Report length / detail beyond the matrix: _TBD_
