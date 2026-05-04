# NOVA Performance & UI Redesign

**Date:** 2026-05-04  
**Status:** Approved

## Summary

Two intertwined goals: make NOVA extremely snappy for 100k–1M row datasets, and give it a clean ultra-minimal look. The primary bottlenecks are (1) full raw-data transfer on every load, (2) per-frame trace reconstruction destroying real-time animation, (3) redundant `new Date()` in every hot loop, and (4) synchronous `saveState` on every click. UI direction: pure black/near-black, dividers not borders, no border-radius, text-first hierarchy.

---

## Backend Changes

### 1. LTTB Downsampling in `timeseries.py`

Add a `max_points: int | None` parameter to `get_timeseries`. When set, apply the Largest-Triangle-Three-Buckets algorithm per (test_run_id, channel_name) series before returning. The algorithm preserves visual shape with a 100× reduction in point count.

- Default: `None` (no downsampling — existing behaviour preserved)
- Frontend passes `max_points=10000` on initial Chooch loads
- Frontend passes no `max_points` (or a large value) on zoom-in refetches
- LTTB runs in Python after the SQL fetch, before serialization
- Pure Python implementation (~40 lines), no new dependencies

### 2. `max_points` Query Param in `main.py`

Add `max_points: int | None = Query(default=None, ge=2, le=5000000)` to the `/api/timeseries` endpoint and thread it through to `get_timeseries`.

---

## Frontend Changes

### 3. Pre-cache Timestamps at Load Time

When rows arrive from the API, stamp each row with `__ts = Date.parse(r.time)` once. Every downstream function (`applyFreq`, `plotRows`, sort comparisons in `applyMasks`, `computeCalculatedRows`, `buildRealtimeRows`) reads `r.__ts` instead of calling `new Date(r.time)`. Eliminates thousands of Date allocations per Chooch.

### 4. Viewport-Aware Zoom Refetch

After a successful Chooch, store the full time span (`overviewStartMs`, `overviewEndMs`) and the overview rows.

Hook `plotly_relayout` on the plot element (fired on every zoom/pan). When the visible x-range covers less than 20% of the total loaded time span:

1. Debounce 300ms (cancel if another zoom fires before debounce expires)
2. Convert the Plotly x-range to absolute ISO timestamps — in `first_index` / `t0_relative` modes the x-axis is in seconds; add those seconds back to the stored `overviewStartMs` anchor to recover the absolute window
3. Re-fetch `/api/timeseries` for the visible `start_time`/`end_time` with no `max_points` limit (Postgres sources only — file sources load all data upfront and have no time-range filtering)
4. Merge result into `latestRows`, re-call `plotRows()`
5. Show a subtle "Loading detail..." status during fetch

When the user zooms back out past the 20% threshold, restore `latestRows` from the cached overview data (no re-fetch needed).

Track a `zoomState = "overview" | "detail"` flag to avoid redundant fetches.

### 5. Real-Time Trace Pre-computation (0.5 fps → ~30 fps)

**Root cause:** `plotRows()` re-parses dates, re-sorts, and re-builds full Plotly trace objects every animation frame. At 500k rows this takes ~2s per frame.

**Fix:** When `startRealtime()` is called, pre-compute a `realtimeTraces` array once:

```
realtimeTraces = [{
  traceIdx,           // index in the Plotly figure
  xFull,             // Float64Array of __rt values (pre-sorted)
  yFull,             // Float64Array of values (same order)
  rtMaskArr,         // Float64Array — same as xFull, used for binary search mask
}]
```

Each animation tick:
1. Binary search `xFull` for `realtimeCurrentMs` → index `lo`
2. Call `Plotly.restyle("plot", { x: [xFull.subarray(0, lo)], y: [yFull.subarray(0, lo)] }, [traceIdx])` per trace
3. No full trace rebuild, no date parsing, no sorting

Pre-computation is triggered once on `startRealtime()` and invalidated when `latestRows` changes. The pre-computation itself runs via `nextPaint()` yields to stay non-blocking.

`Plotly.restyle` with typed array subarray views is zero-copy and roughly 200× faster than `Plotly.react` with rebuilt objects.

### 6. Debounced `saveState`

Wrap `saveState()` in a 150ms debounce. Clicking through a channel list currently fires synchronous localStorage serialization on every click. With debounce, rapid selections batch into one write.

### 7. Ultra Minimal UI Redesign

**Color palette:**
- UI background: `#0a0a0a` (near-black, slightly warmer than pure black)
- Plot background: `#000000` (pure black)
- Border/divider: `#1c1c1c` (single-pixel dividers, no box borders)
- Primary text: `#d4d4d4`
- Secondary text: `#404040`
- Active/selected: `#d4d4d4` with left accent line `#555`
- Accent (load button, active tabs): `#e2e2e2` text on `#1a1a1a` bg with `#555` border — monochrome, no color accent
- Error text: `#a87070`
- Status text: `#707070`

**Typography:**
- Labels: `0.62rem`, `letter-spacing: 0.08em`, `text-transform: uppercase`, color `#404040`
- Item primary: `0.78rem`, color `#d4d4d4`
- Item meta: `0.62rem`, color `#404040`
- All `border-radius: 0` globally

**Sidebar panels:**
- No box borders around panels — section headers use a bottom divider `1px solid #1c1c1c`
- Items separated by `1px solid #0f0f0f` dividers (nearly invisible, just enough for scanability)
- Selected item: `background: #141414`, thin `2px left border #555`
- No `.active` background flash — selection indicated purely by left border + slight bg lift
- Collapse/expand buttons replaced by clicking the section header itself

**Buttons:**
- Global `border-radius: 0`
- Load ("Chooch") button: full-width, `background: #141414`, `border: 1px solid #333`, uppercase label `LOAD`
- Add (+) and collapse buttons: smaller, `border: 1px solid #1c1c1c`
- Modal action buttons: right-aligned, outlined style

**Modals:**
- Background: `#0d0d0d`
- Border: `1px solid #222`
- No `border-radius`
- Header divider instead of padding gap

**Tabs:**
- Active tab: `background: #1a1a1a`, bottom border removed, top `1px solid #555` accent
- Inactive: `background: #0a0a0a`, `color: #404040`

**Scrollbars:** remain hidden (existing behaviour)

**Status indicator:** moves to a fixed bottom-left bar (`position: fixed; bottom: 0; left: 0`) — always visible, doesn't push sidebar content

---

## What Is Not Changing

- Plotly.js version and `scattergl` trace type (already GPU-accelerated — correct choice)
- All existing functionality: masks, calculated channels, multi-tab plots, animation export, config save/load, appearance modals
- Backend SQL queries, DB schema, connection pooling
- File source (CSV/TDMS) paths — they get the same `max_points` treatment

---

## Performance Impact (Expected)

| Scenario | Before | After |
|---|---|---|
| Chooch 500k rows, 5 channels | ~8s load | ~0.8s load (10k pts/ch) |
| Zoomed detail refetch | n/a | ~0.5s |
| Real-time animation | ~0.5 fps | ~30 fps |
| UI click (select channel) | ~50ms | ~2ms (debounced save) |
| `plotRows` on 500k rows | ~2s | ~80ms (pre-cached __ts) |
