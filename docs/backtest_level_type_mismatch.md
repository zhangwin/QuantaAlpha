# Backtest Level Type Mismatch Bug

## Bug Description

Backtest in combined mode (custom + Qlib factors) failed with:

```
ValueError: Level type mismatch
```

when calling `qlib_backtest()`. The error originated from `pred.index.levels[0]` containing string values (stock codes like `'SH600000'`) mixed with `pd.Timestamp` values, causing Qlib's internal type validation to fail.

## Root Cause

### 1. MultiIndex Level Names Lost During `pd.concat`

Custom factors loaded from PKL/H5 cache had correctly named MultiIndex (`['datetime', 'instrument']` with `datetime64[ns]` dtype). Qlib `D.features()` also returned correctly named index. However:

- `pd.concat([computed_factors, qlib_factors], axis=1)` **drops all level names to `None`** if any individual factor's index has differing names. The resulting `features_df` had `[None, None]` index names with `object, object` dtypes.

### 2. `_normalize_multiindex` Could Not Convert Level Values

The original `_normalize_multiindex` only renamed levels by position, assigning all `object`-dtype unnamed levels to `'instrument'`:

```python
# Old code (simplified):
if name is None:
    if is_datetime64: new_names[i] = 'datetime'
    else: new_names[i] = 'instrument'
```

This produced `['instrument', 'instrument']` — both levels named `'instrument'`. Downstream code calling `get_level_values('datetime')` raised `KeyError`.

### 3. Level Cache Contamination

`features_df.index.levels[0]` (the unique values cache for level 0) contained **both** `Timestamp` values and stock code strings (`'SH600000'`). This happened because some tuples in the 11M-row DataFrame had level 0 values that were stock codes instead of dates.

Attempting `pd.to_datetime(df.index.levels[0])` failed with `"Unknown datetime string format, unable to parse: SH600000"`, so `set_levels()` was silently skipped.

### 4. Merge Fallback Produced Wrong-Type Index

When intersection failed (pre-fix), the merge fallback:

```python
merged = pd.merge(feat_reset, label_reset, on=[dt_col, inst_col], how='inner')
merged = merged.set_index([dt_col, inst_col])
merged.index.names = ['datetime', 'instrument']
```

`dt_col` was `feat_reset.columns[0]` (both columns named `'instrument'`), producing wrong pairing — string dates from features matched against instrument codes from labels in a subset of rows.

### 5. Stale `.pyc` Cache

After fixing `_normalize_multiindex`, the old `__pycache__/runner.cpython-310.pyc` had a timestamp of `1970-01-01` (epoch), meaning the fix code was **never executed** — Python was still loading the old bytecode.

## Changes Made

### Files Modified

| File | Changes |
|------|---------|
| `quantaalpha/backtest/runner.py` | `_normalize_multiindex()`: date detection via sampling first 100 values; try-except for `pd.to_datetime` per level; fallback swap level logic. Pred index tuple-by-tuple conversion. LAST RESORT roundtrip before `qlib_backtest()`. |
| `quantaalpha/core/experiment.py` | macOS symlink support in `link_all_files_in_folder_to_workspace`. |
| `quantaalpha/factors/coder/factor.py` | `source_data_path` resolution: corrected `workspace_path.parent.parent.parent` → `Path(__file__).parent.parent.parent.parent`. |
| `quantaalpha/factors/runner.py` | `project_root` parent levels: 5-level → 3-level traversal. |
| `quantaalpha/factors/data_template/generate.py` | Added `__name__ == '__main__'` guard + minor fixes. |
| `frontend-v2/backend/app.py` | Symlink handling for directories (`os.readlink()` crash on `EINVAL`). |
| `frontend-v2/src/pages/BacktestPage.tsx` | Backtest task persistence via localStorage (`quantaalpha_backtest_task_id`, `quantaalpha_backtest_task_data`, `quantaalpha_backtest_logs`). Loading spinner for restore. |
| `frontend-v2/src/pages/MiningDashboardPage.tsx` | Loading spinner for restored tasks. |
| `frontend-v2/src/context/TaskContext.tsx` | Unified WebSocket restore + polling for both mining and backtest; health-check before clearing stale cache. |
| `frontend-v2/src/App.tsx` | Initial page routing from localStorage savedTaskId; auto-switch effects. |
| `frontend-v2/src/services/api.ts` | `connectMiningWs`, `getMiningStatus`, `getBacktestStatus` helpers. |

### Key Fixes in `_normalize_multiindex`

```python
# For each unnamed object-dtype level, sample first 100 values:
sample = level_vals[:min(100, len(level_vals))]
try:
    parsed = pd.to_datetime(sample, errors='raise')
    if parsed.notna().sum() > len(parsed) * 0.5:
        is_date = True  # Level is datetime
except:
    is_date = False  # Level is instrument
```

- If datetime: set name to `'datetime'`, try `pd.to_datetime(df.index.levels[i])` (may fail on contaminated cache).
- If instrument: set name to `'instrument'`. If level 0 is instrument (shouldn't be), try swapping with other level.

### LAST RESORT Conversion (before `qlib_backtest()`)

```python
_frame = pred.index.to_frame(index=False)
for _col in _frame.columns:
    _sample = _frame[_col].dropna().iloc[:50]
    if len(_sample) > 0:
        try:
            _test = pd.to_datetime(_sample, errors='raise')
            if _test.notna().sum() > len(_test) * 0.5:
                _frame[_col] = pd.to_datetime(_frame[_col], errors='coerce')
        except:
            pass
pred = pd.Series(pred.values, index=pd.MultiIndex.from_frame(_frame))
```

Rebuilds pred index from scratch via DataFrame roundtrip — the most reliable fallback.

## Results

After fixes, backtest completed successfully:

| Metric | Value |
|--------|-------|
| IC | 0.0430 |
| ICIR | 0.3757 |
| Rank IC | 0.0412 |
| Rank ICIR | 0.3675 |
| Annualized Return | 0.0222 |
| Max Drawdown | -0.0908 |
| Information Ratio | 0.3459 |
| Calmar Ratio | 0.2450 |
| Total Time | 131.5s |

## Remaining Issues

- `df.index.levels[0]` still contains mixed stock codes + Timestamps in level cache (the level values cache is contaminated during earlier data processing). The LAST RESORT conversion works around this, but the root cause in the level cache is not fixed.
- If a new factor (not in PKL cache) is computed from expression, its index may have unnamed levels, triggering the same issue.
