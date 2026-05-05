# NOVA Performance & UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NOVA snappy for 100k–1M row datasets and apply an Ultra Minimal UI redesign.

**Architecture:** Backend gains LTTB per-series downsampling behind a `max_points` query param. Frontend pre-caches numeric timestamps at load time, replaces per-frame trace rebuilding with pre-computed typed arrays + `Plotly.restyle`, debounces state saves, and adds viewport-aware zoom re-fetch for Postgres sources in absolute mode. UI CSS is replaced wholesale with a pure-black minimalist scheme.

**Tech Stack:** Python / FastAPI / psycopg2 (backend), Vanilla JS / Plotly.js 2.35 scattergl (frontend), pytest (backend tests)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/timeseries.py` | Modify | Add `_lttb_series()`, update `get_timeseries()` |
| `backend/app/main.py` | Modify | Add `max_points` param to `/api/timeseries` and `/api/file/timeseries` |
| `backend/app/static/index.html` | Modify | All frontend changes |
| `backend/tests/__init__.py` | Create | Package marker |
| `backend/tests/test_lttb.py` | Create | LTTB unit tests |

---

## Task 1: LTTB algorithm — pure function + tests

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_lttb.py`
- Modify: `backend/app/services/timeseries.py`

- [ ] **Step 1: Create test package**

```bash
# Create backend/tests/__init__.py (empty file)
```
File content: *(empty)*

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_lttb.py`:

```python
from datetime import datetime, timezone

import pytest

from app.services.timeseries import _lttb_series
from app.models import TimeSeriesPoint


def _make_pts(values: list[float], channel: str = "ch") -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(
            test_run_id=1,
            test_run_code="run1",
            channel_name=channel,
            time=datetime(2024, 1, 1, 0, 0, i, tzinfo=timezone.utc),
            value=v,
        )
        for i, v in enumerate(values)
    ]


def test_lttb_identity_when_under_threshold():
    pts = _make_pts([float(i) for i in range(5)])
    result = _lttb_series(pts, 10)
    assert result == pts


def test_lttb_identity_when_equal_threshold():
    pts = _make_pts([float(i) for i in range(10)])
    result = _lttb_series(pts, 10)
    assert result == pts


def test_lttb_reduces_to_threshold():
    pts = _make_pts([float(i % 10) for i in range(100)])
    result = _lttb_series(pts, 10)
    assert len(result) == 10


def test_lttb_keeps_first_and_last():
    pts = _make_pts([float(i) for i in range(50)])
    result = _lttb_series(pts, 10)
    assert result[0] == pts[0]
    assert result[-1] == pts[-1]


def test_lttb_minimum_threshold_of_2():
    pts = _make_pts([float(i) for i in range(20)])
    result = _lttb_series(pts, 2)
    assert len(result) == 2
    assert result[0] == pts[0]
    assert result[-1] == pts[-1]


def test_lttb_empty_input():
    assert _lttb_series([], 10) == []


def test_lttb_single_point():
    pts = _make_pts([1.0])
    assert _lttb_series(pts, 10) == pts
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_lttb.py -v
```

Expected: `ImportError` — `_lttb_series` does not exist yet.

- [ ] **Step 4: Implement `_lttb_series` in `timeseries.py`**

Add this function at the top of `backend/app/services/timeseries.py`, after the imports:

```python
def _lttb_series(points: list, threshold: int) -> list:
    """Largest-Triangle-Three-Buckets downsampling for a single sorted series."""
    n = len(points)
    if n == 0 or threshold <= 0:
        return points
    if threshold >= n:
        return points

    sampled = [points[0]]
    bucket_size = (n - 2) / (threshold - 2) if threshold > 2 else float(n)
    a = 0

    for i in range(threshold - 2):
        avg_range_start = int((i + 1) * bucket_size) + 1
        avg_range_end = min(int((i + 2) * bucket_size) + 1, n)
        avg_count = avg_range_end - avg_range_start
        avg_x = (avg_range_start + avg_range_end - 1) / 2.0
        avg_y = sum(points[j].value for j in range(avg_range_start, avg_range_end)) / avg_count

        range_start = int(i * bucket_size) + 1
        range_end = min(int((i + 1) * bucket_size) + 1, n)

        ax = float(a)
        ay = points[a].value
        max_area = -1.0
        next_a = range_start

        for j in range(range_start, range_end):
            area = abs((ax - avg_x) * (points[j].value - ay) - (ax - j) * (avg_y - ay)) * 0.5
            if area > max_area:
                max_area = area
                next_a = j

        sampled.append(points[next_a])
        a = next_a

    sampled.append(points[-1])
    return sampled
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_lttb.py -v
```

Expected: all 7 tests `PASSED`.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/__init__.py backend/tests/test_lttb.py backend/app/services/timeseries.py
git commit -m "feat: add LTTB downsampling algorithm with tests"
```

---

## Task 2: Wire `max_points` through the API

**Files:**
- Modify: `backend/app/services/timeseries.py` — update `get_timeseries()`
- Modify: `backend/app/main.py` — add `max_points` param to both timeseries endpoints

- [ ] **Step 1: Update `get_timeseries` in `timeseries.py`**

Add `max_points: int | None = None` parameter and apply LTTB after the SQL fetch.

Replace the `get_timeseries` function signature and return block:

```python
def get_timeseries(
    test_run_ids: list[int],
    channel_names: list[str],
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = None,
    max_points: int | None = None,
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> list[TimeSeriesPoint]:
    if not test_run_ids:
        return []
    if not channel_names:
        return []

    query_limit = max(1, min(limit or settings.default_limit, 5000000))
    sql = """
    SELECT
      sr.test_run_id,
      tr.run_code AS test_run_code,
      c.channel_name,
      c.unit,
      sr.time,
      sr.value
    FROM sensor_readings sr
    INNER JOIN channels c ON c.id = sr.channel_id
    INNER JOIN test_runs tr ON tr.id = sr.test_run_id
    WHERE sr.test_run_id = ANY(%s)
      AND c.channel_name = ANY(%s)
      AND (%s::timestamptz IS NULL OR sr.time >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR sr.time <= %s::timestamptz)
    ORDER BY sr.time ASC
    LIMIT %s
    """
    params = (test_run_ids, channel_names, start_time, start_time, end_time, end_time, query_limit)

    with get_conn(
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    ) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows: Sequence[dict] = cur.fetchall()

    points = [TimeSeriesPoint(**row) for row in rows]

    if max_points and max_points > 0 and len(points) > max_points:
        points = _downsample_timeseries(points, max_points)

    return points
```

Then add the `_downsample_timeseries` helper just below `_lttb_series`:

```python
def _downsample_timeseries(points: list[TimeSeriesPoint], max_points: int) -> list[TimeSeriesPoint]:
    """Group by (test_run_id, channel_name) and apply LTTB per series."""
    from collections import defaultdict
    series: dict[tuple, list[TimeSeriesPoint]] = defaultdict(list)
    for pt in points:
        series[(pt.test_run_id, pt.channel_name)].append(pt)

    result: list[TimeSeriesPoint] = []
    for pts in series.values():
        result.extend(_lttb_series(pts, max_points))
    return result
```

- [ ] **Step 2: Add `max_points` to `/api/timeseries` in `main.py`**

Replace the `timeseries` endpoint:

```python
@app.get("/api/timeseries", response_model=list[TimeSeriesPoint])
def timeseries(
    test_run_ids: list[int] = Query(..., description="One or more test_run_id values."),
    channel_names: list[str] = Query(..., description="One or more channel names."),
    start_time: str | None = Query(default=None, description="ISO timestamp inclusive lower bound."),
    end_time: str | None = Query(default=None, description="ISO timestamp inclusive upper bound."),
    limit: int | None = Query(default=None, ge=1, le=5000000),
    max_points: int | None = Query(default=None, ge=2, le=5000000, description="Max points per series (LTTB). Omit for full resolution."),
    db_name: str | None = Query(default=None, description="Optional database override."),
    db_host: str | None = Query(default=None),
    db_port: int | None = Query(default=None),
    db_user: str | None = Query(default=None),
    db_password: str | None = Query(default=None),
    db_sslmode: str | None = Query(default=None),
) -> list[TimeSeriesPoint]:
    return get_timeseries(
        test_run_ids=test_run_ids,
        channel_names=channel_names,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        max_points=max_points,
        db_name=db_name,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_sslmode=db_sslmode,
    )
```

- [ ] **Step 3: Verify LTTB tests still pass**

```bash
cd backend && python -m pytest tests/test_lttb.py -v
```

Expected: all 7 tests `PASSED`.

- [ ] **Step 4: Smoke-test the server starts**

```bash
cd backend && python -m uvicorn app.main:app --port 8765 &
sleep 2 && curl -s "http://localhost:8765/health" && kill %1
```

Expected: `{"ok":true,"app":"NOVA"}`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/timeseries.py backend/app/main.py
git commit -m "feat: add max_points LTTB downsampling to timeseries API"
```

---

## Task 3: Pre-cache `__ts` timestamps on the frontend

**Files:**
- Modify: `backend/app/static/index.html`

The goal is to stamp every loaded row with `__ts = Date.parse(r.time)` once at load time, then replace all `new Date(r.time)` / `Date.parse(r.time)` calls in hot paths with `r.__ts`.

- [ ] **Step 1: Stamp `__ts` in `loadTimeseries` after `finalRows` is assembled**

Find the line in `loadTimeseries`:
```js
latestRows=finalRows;
```

Replace with:
```js
finalRows.forEach(r => { r.__ts = Date.parse(r.time); });
latestRows=finalRows;
```

- [ ] **Step 2: Fix `applyFreq` — remove `new Date()` in hot loop**

Find in `applyFreq`:
```js
const ts=new Date(p.time).getTime(),k=Math.floor(ts/bucket)*bucket;
```

Replace with:
```js
const ts=p.__ts,k=Math.floor(ts/bucket)*bucket;
```

- [ ] **Step 3: Fix `plotRows` sort — remove `new Date()` comparator**

Find in `plotRows` (inside the `xAxis==="time"` branch):
```js
const down=applyFreq(pts,chId).sort((a,b)=>new Date(a.time)-new Date(b.time));
```

Replace with:
```js
const down=applyFreq(pts,chId).sort((a,b)=>a.__ts-b.__ts);
```

- [ ] **Step 4: Fix `plotRows` x-axis relative time mapping**

Find (inside `plotRows`, the relative-modes branch of x mapping):
```js
return Number.isFinite(base)?(new Date(p.time).getTime()-base)/1000:p.time;
```

Replace with:
```js
return Number.isFinite(base)?(p.__ts-base)/1000:p.time;
```

- [ ] **Step 5: Fix `buildRealtimeRows` — remove `Date.parse`**

Find in `buildRealtimeRows`:
```js
latestRows.forEach((r)=>{ const key=`${r.__dbId}|${r.test_run_id}`; const ts=Date.parse(String(r.time));
```

Replace with:
```js
latestRows.forEach((r)=>{ const key=`${r.__dbId}|${r.test_run_id}`; const ts=r.__ts;
```

Then find the second timestamp parse in the same `.map()` call:
```js
const ts=Date.parse(String(r.time)); let rt=ts/1000;
```

Replace with:
```js
const ts=r.__ts; let rt=ts/1000;
```

- [ ] **Step 6: Fix `applyMasks` — remove `Date.parse` in t0 computation**

Find in `applyMasks` (the t0ByTest loop):
```js
rows.forEach((r)=>{
  const key=`${r.__dbId}|${r.test_run_id}`;
  const ts=Date.parse(String(r.time));
```

Replace with:
```js
rows.forEach((r)=>{
  const key=`${r.__dbId}|${r.test_run_id}`;
  const ts=r.__ts;
```

Find the rolling mask sort in `applyMasks`:
```js
series.sort((a,b)=>new Date(a.time)-new Date(b.time));
```

Replace with:
```js
series.sort((a,b)=>a.__ts-b.__ts);
```

Find the formula mask group sort in `applyMasks`:
```js
const sorted=keys.slice().sort((a,b)=>new Date(groups.get(a).sample.time)-new Date(groups.get(b).sample.time));
```

Replace with:
```js
const sorted=keys.slice().sort((a,b)=>groups.get(a).sample.__ts-groups.get(b).sample.__ts);
```

Find the formula mask `currMs` in `applyMasks`:
```js
const currMs=Date.parse(String(g.sample.time));
```

Replace with:
```js
const currMs=g.sample.__ts;
```

Also fix the time-mask comparison in `applyMasks` (absolute mode):
```js
const ts=Date.parse(String(g.sample.time));
```

Replace with:
```js
const ts=g.sample.__ts;
```

- [ ] **Step 7: Fix `computeCalculatedRows` — remove `new Date()` sorts and currMs**

Find in `computeCalculatedRows` (rolling calc sort):
```js
rows.sort((a,b)=>new Date(a.time)-new Date(b.time));
```

Replace with:
```js
rows.sort((a,b)=>a.__ts-b.__ts);
```

Find in `computeCalculatedRows` (formula calc, `currMs`):
```js
const currMs=new Date(t).getTime();
```

`t` is a string key from the `byTime` map. Replace by reading `__ts` from the slot's sample row:
```js
const currMs=slot.sample.__ts;
```

Also stamp `__ts` on computed rows so they work in subsequent operations. Find where `computeCalculatedRows` pushes output rows:
```js
out.push({ test_run_id: rows[i].test_run_id, test_run_code: rows[i].test_run_code, channel_name: calc.name, unit: calc.unit || null, time: rows[i].time, value: Number(v), __dbId: calc.dbId });
```

Replace with:
```js
out.push({ test_run_id: rows[i].test_run_id, test_run_code: rows[i].test_run_code, channel_name: calc.name, unit: calc.unit || null, time: rows[i].time, value: Number(v), __dbId: calc.dbId, __ts: rows[i].__ts });
```

And the formula calc push (uses `slot.sample`):
```js
out.push({ test_run_id: slot.sample.test_run_id, test_run_code: slot.sample.test_run_code, channel_name: calc.name, unit: calc.unit || null, time: t, value: Number(v), __dbId: calc.dbId });
```

Replace with:
```js
out.push({ test_run_id: slot.sample.test_run_id, test_run_code: slot.sample.test_run_code, channel_name: calc.name, unit: calc.unit || null, time: t, value: Number(v), __dbId: calc.dbId, __ts: slot.sample.__ts });
```

- [ ] **Step 8: Manual verification**

Start NOVA, load a dataset, click Chooch. Open browser DevTools → Console. Run:
```js
latestRows[0].__ts  // should be a finite number (epoch ms)
```
Expected: a number like `1706000000000`, not `undefined` or `NaN`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/static/index.html
git commit -m "perf: pre-cache __ts timestamps on load, eliminate new Date() in hot loops"
```

---

## Task 4: Debounce `saveState`

**Files:**
- Modify: `backend/app/static/index.html`

- [ ] **Step 1: Add debounce utility**

Find the line:
```js
function clearCatalogCache(){ catalogCache.clear(); }
```

Add immediately before it:
```js
function debounce(fn, ms){ let t; return (...args)=>{ clearTimeout(t); t=setTimeout(()=>fn(...args), ms); }; }
```

- [ ] **Step 2: Wrap `saveState` calls with debounced variant**

Find the existing `saveState` function declaration:
```js
function saveState() { localStorage.setItem(STORAGE_KEY,
```

Add a debounced wrapper immediately after the closing `}` of `saveState`:
```js
const saveStateDeferred = debounce(saveState, 150);
```

- [ ] **Step 3: Replace hot-path `saveState()` calls with `saveStateDeferred()`**

In `renderList`, find the `row.onclick` handler — it ends with:
```js
renderAll(); saveState();});
```

Replace with:
```js
renderAll(); saveStateDeferred();});
```

In `deleteFocused`, find the final call:
```js
renderAll(); saveState(); }
```

Replace with:
```js
renderAll(); saveStateDeferred(); }
```

In `setChannelFrequency`, find:
```js
if(latestRows.length) plotRows(latestRows); renderAll(); saveState(); }
```

Replace with:
```js
if(latestRows.length) plotRows(latestRows); renderAll(); saveStateDeferred(); }
```

Keep `saveState()` (immediate) in: `loadTimeseries` (end of Chooch), `file_loadConfig` handler, `addPlot`, `switchPlot`, `closePlot`, `duplicatePlot` — these are one-shot operations where immediate persistence is correct.

- [ ] **Step 4: Manual verification**

Open DevTools → Performance tab. Record a 2-second clip while rapidly clicking 5 channels in the channels list. Verify that localStorage writes are batched (you'll see far fewer `setItem` calls than clicks in the timeline).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/index.html
git commit -m "perf: debounce saveState to eliminate per-click localStorage writes"
```

---

## Task 5: Real-time pre-computation (0.5 fps → ~30 fps)

**Files:**
- Modify: `backend/app/static/index.html`

**Approach:** Pre-build x/y arrays for all traces once when `startRealtime()` is called. Each animation tick does a single binary search + `Plotly.restyle` with array slices — no trace reconstruction, no date parsing.

- [ ] **Step 1: Add state variables for pre-computed traces**

Find:
```js
let realtimeRows = [];
```

Add immediately after:
```js
let realtimePrecomputed = null; // [{xFull, yFull, rtFull, traceIdx}] | null
```

- [ ] **Step 2: Add binary search helper**

Find the `debounce` function added in Task 4. Add immediately after it:
```js
function bsearch(arr, target){ let lo=0, hi=arr.length; while(lo<hi){ const mid=(lo+hi)>>1; if(arr[mid]<=target) lo=mid+1; else hi=mid; } return lo; }
```

- [ ] **Step 3: Add `precomputeRealtimeTraces` function**

Find the `startRealtime` function. Add this new function immediately before it:

```js
function precomputeRealtimeTraces(){
  realtimePrecomputed=null;
  if(!realtimeRows.length) return;
  const t0Mode=byId("t0Mode").value;
  const xAxisVal=byId("xAxis").value;
  if(xAxisVal!=="time") return; // only time-axis mode supported

  const grouped=new Map();
  realtimeRows.forEach((r)=>{
    const key=`${r.test_run_code||r.test_run_id} | ${r.channel_name}`;
    if(!grouped.has(key)) grouped.set(key,[]);
    grouped.get(key).push(r);
  });

  const t0FirstSample=new Map(), t0Meta=new Map();
  if(t0Mode==="first_index"||t0Mode==="t0_relative"){
    realtimeRows.forEach((r)=>{
      const k=`${r.__dbId}|${r.test_run_id}`;
      if(!t0FirstSample.has(k)||r.__ts<t0FirstSample.get(k)) t0FirstSample.set(k,r.__ts);
    });
  }
  if(t0Mode==="t0_relative"){
    testsData.forEach((t)=>{ const ts=Date.parse(String(t.t0_utc||"")); if(Number.isFinite(ts)) t0Meta.set(`${t.__dbId}|${t.test_run_id}`,ts); });
  }

  const result=[];
  let traceIdx=0;
  grouped.forEach((pts,_name)=>{
    const chId=`${pts[0].__dbId}|${pts[0].channel_name}`;
    const down=applyFreq(pts,chId).sort((a,b)=>a.__ts-b.__ts);
    const n=down.length;
    const xIsNumeric=(t0Mode!=="absolute");
    const xFull=xIsNumeric ? new Float64Array(n) : new Array(n);
    const yFull=new Float64Array(n);
    const rtFull=new Float64Array(n);
    down.forEach((p,i)=>{
      yFull[i]=p.value;
      rtFull[i]=p.__rt;
      if(t0Mode==="absolute"){
        xFull[i]=p.time;
      } else {
        const k=`${p.__dbId}|${p.test_run_id}`;
        const base=t0Mode==="t0_relative"?t0Meta.get(k):t0FirstSample.get(k);
        xFull[i]=Number.isFinite(base)?(p.__ts-base)/1000:p.__ts/1000;
      }
    });
    result.push({xFull,yFull,rtFull,xIsNumeric,traceIdx});
    traceIdx++;
  });
  realtimePrecomputed=result;
}
```

- [ ] **Step 4: Call `precomputeRealtimeTraces` inside `startRealtime` after rows are built**

Find in `startRealtime`:
```js
if(!realtimeRows.length){
  realtimeRows=buildRealtimeRows();
  if(!realtimeRows.length){ setStatus("No valid timestamps for real-time traces.", true); return; }
  realtimeMinMs=realtimeRows[0].__rt;
  realtimeMaxMs=realtimeRows[realtimeRows.length-1].__rt;
  realtimeCurrentMs=realtimeMinMs;
}
```

Replace with:
```js
if(!realtimeRows.length){
  realtimeRows=buildRealtimeRows();
  if(!realtimeRows.length){ setStatus("No valid timestamps for real-time traces.", true); return; }
  realtimeMinMs=realtimeRows[0].__rt;
  realtimeMaxMs=realtimeRows[realtimeRows.length-1].__rt;
  realtimeCurrentMs=realtimeMinMs;
  precomputeRealtimeTraces();
}
```

Also invalidate precomputed data when `latestRows` changes. Find in `loadTimeseries` (after `stopRealtime()`):
```js
realtimeRows=[];
stopRealtime();
realtimeCurrentMs=null;
```

Replace with:
```js
realtimeRows=[];
realtimePrecomputed=null;
stopRealtime();
realtimeCurrentMs=null;
```

- [ ] **Step 5: Replace per-frame `plotRows` with `Plotly.restyle` in the tick**

Find inside `startRealtime`, the tick's draw block:
```js
if((now-realtimeLastDrawMs)>=realtimeFrameStepMs || hitEnd){
  const frameRows=realtimeFrameRows();
  if(previewVisible){ renderRows(frameRows); }
  try{ plotRows(frameRows); }catch(e){ setStatus(`Plot render warning: ${e}`, true); }
  try{ if(typeof opts.onDraw==="function") opts.onDraw(frameRows); }catch{}
  updateRealtimeStatus();
  realtimeLastDrawMs=now;
}
```

Replace with:
```js
if((now-realtimeLastDrawMs)>=realtimeFrameStepMs || hitEnd){
  if(realtimePrecomputed&&realtimePrecomputed.length){
    // Fast path: restyle with pre-computed slices
    const xUpdates=[], yUpdates=[], indices=[];
    realtimePrecomputed.forEach((t)=>{
      const lo=bsearch(t.rtFull,realtimeCurrentMs);
      xUpdates.push(t.xIsNumeric ? Array.from(t.xFull.subarray(0,lo)) : t.xFull.slice(0,lo));
      yUpdates.push(Array.from(t.yFull.subarray(0,lo)));
      indices.push(t.traceIdx);
    });
    try{ Plotly.restyle("plot",{x:xUpdates,y:yUpdates},indices); }catch(e){ setStatus(`Plot render warning: ${e}`,true); }
    if(previewVisible){ renderRows(realtimeFrameRows()); }
  } else {
    // Fallback: full rebuild
    const frameRows=realtimeFrameRows();
    if(previewVisible){ renderRows(frameRows); }
    try{ plotRows(frameRows); }catch(e){ setStatus(`Plot render warning: ${e}`,true); }
    try{ if(typeof opts.onDraw==="function") opts.onDraw(frameRows); }catch{}
  }
  updateRealtimeStatus();
  realtimeLastDrawMs=now;
}
```

- [ ] **Step 6: Manual verification**

Load a dataset with 2+ channels. Open Edit → Real Time Traces → Play. Verify the animation plays at visibly smooth frame rate (should feel like 20-30fps vs the previous ~0.5fps).

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/index.html
git commit -m "perf: pre-compute realtime traces as typed arrays, use Plotly.restyle per frame"
```

---

## Task 6: Frontend — pass `max_points` on Chooch + viewport zoom re-fetch

**Files:**
- Modify: `backend/app/static/index.html`

- [ ] **Step 1: Add overview state variables**

Find:
```js
let realtimePrecomputed = null;
```

Add immediately after:
```js
let overviewRows = [];
let overviewEpochStartMs = null;
let overviewEpochEndMs = null;
let zoomState = "overview"; // "overview" | "detail"
let zoomRefetchTimer = null;
```

- [ ] **Step 2: Pass `max_points=10000` on the Chooch fetch**

In `loadTimeseries`, find the Postgres timeseries fetch:
```js
p.append("limit","5000000");
p.append("db_name",db.name);
```

Replace with:
```js
p.append("limit","5000000");
p.append("max_points","10000");
p.append("db_name",db.name);
```

- [ ] **Step 3: Save overview snapshot after each Chooch**

In `loadTimeseries`, find the block:
```js
finalRows.forEach(r => { r.__ts = Date.parse(r.time); });
latestRows=finalRows;
realtimeRows=[];
realtimePrecomputed=null;
stopRealtime();
realtimeCurrentMs=null;
```

Replace with:
```js
finalRows.forEach(r => { r.__ts = Date.parse(r.time); });
latestRows=finalRows;
overviewRows=finalRows;
zoomState="overview";
let _minMs=Infinity, _maxMs=-Infinity;
finalRows.forEach(r=>{ if(r.__ts<_minMs)_minMs=r.__ts; if(r.__ts>_maxMs)_maxMs=r.__ts; });
overviewEpochStartMs=_minMs===Infinity?null:_minMs;
overviewEpochEndMs=_maxMs===-Infinity?null:_maxMs;
realtimeRows=[];
realtimePrecomputed=null;
stopRealtime();
realtimeCurrentMs=null;
```

- [ ] **Step 4: Add `bindZoomRefetch` function**

Find the `initSidebarResize` function. Add this new function immediately before it:

```js
function bindZoomRefetch(){
  const plotEl=byId("plot");
  if(!plotEl||typeof plotEl.on!=="function") return;
  plotEl.on("plotly_relayout",(ev)=>{
    if(!overviewRows.length||!overviewEpochStartMs||overviewEpochStartMs===overviewEpochEndMs) return;
    if(byId("t0Mode").value!=="absolute") return;

    const autorange=ev["xaxis.autorange"];
    if(autorange===true){
      if(zoomState==="detail"){
        zoomState="overview";
        latestRows=overviewRows;
        plotRows(latestRows);
        setStatus(`Overview: ${latestRows.length} rows.`);
      }
      return;
    }

    const x0=ev["xaxis.range[0]"], x1=ev["xaxis.range[1]"];
    if(!x0||!x1) return;

    const visStart=new Date(x0).getTime(), visEnd=new Date(x1).getTime();
    const visSpan=visEnd-visStart;
    const totalSpan=overviewEpochEndMs-overviewEpochStartMs;
    if(totalSpan<=0) return;

    const fraction=visSpan/totalSpan;

    if(fraction<0.2 && zoomState!=="detail"){
      clearTimeout(zoomRefetchTimer);
      zoomRefetchTimer=setTimeout(()=>refetchZoomedDetail(new Date(visStart).toISOString(), new Date(visEnd).toISOString()), 300);
    } else if(fraction>=0.2 && zoomState==="detail"){
      zoomState="overview";
      latestRows=overviewRows;
      plotRows(latestRows);
      setStatus(`Overview: ${latestRows.length} rows.`);
    }
  });
}

async function refetchZoomedDetail(startTime, endTime){
  setStatus("Loading detail...");
  const rowsAll=[];
  for(const dbId of selectedDatabaseIds){
    const db=state.databases.find((d)=>d.id===dbId), src=db?state.sources.find((s)=>s.id===db.sourceId):null;
    if(!db||!src||src.type!=="postgres") continue;
    const tests=[...selectedTestIds].filter((t)=>t.startsWith(`${dbId}|`)).map((t)=>t.split("|")[1]);
    const selForDb=[...selectedChannelIds].filter((c)=>c.startsWith(`${dbId}|`)).map((c)=>c.split("|")[1]);
    const calcForDb=(state.calculatedChannels||[]).filter((c)=>c.dbId===dbId&&selForDb.includes(c.name));
    const channelSet=new Set(selForDb.filter((c)=>!calcForDb.some((x)=>x.name===c)));
    calcForDb.forEach((c)=>(c.channels||[]).forEach((id)=>channelSet.add(id.split("|")[1])));
    if(!tests.length||!channelSet.size) continue;
    const p=new URLSearchParams();
    tests.forEach((id)=>p.append("test_run_ids",id));
    [...channelSet].forEach((ch)=>p.append("channel_names",ch));
    p.append("limit","5000000");
    p.append("db_name",db.name);
    p.append("start_time",startTime);
    p.append("end_time",endTime);
    sourceConnQuery(src).split("&").forEach((kv)=>{ if(kv){ const [k,v]=kv.split("="); p.append(k,decodeURIComponent(v)); }});
    try{
      const rows=await getJson(`/api/timeseries?${p.toString()}`);
      rows.forEach((r)=>{ r.__dbId=dbId; r.__ts=Date.parse(r.time); rowsAll.push(r); });
    }catch(e){ setStatus(`Detail fetch failed: ${e}`,true); return; }
  }
  const calcRows=await computeCalculatedRows(rowsAll);
  const selNonCalc=rowsAll.filter((r)=>selectedChannelIds.has(`${r.__dbId}|${r.channel_name}`));
  const finalRows=applyMasks([...selNonCalc,...calcRows]);
  finalRows.forEach(r=>{ if(!r.__ts) r.__ts=Date.parse(r.time); });
  zoomState="detail";
  latestRows=finalRows;
  realtimeRows=[]; realtimePrecomputed=null;
  plotRows(finalRows);
  setStatus(`Detail: ${finalRows.length} rows in window.`);
}
```

- [ ] **Step 5: Call `bindZoomRefetch` after first Chooch render**

In `loadTimeseries`, find the final block (after `plotRows(finalRows)`):
```js
await nextPaint();
await loadMetadataForSelection();
saveState();
setStatus(`Loaded ${finalRows.length} rows after masks.`);
```

Replace with:
```js
await nextPaint();
if(!plotHandlersBound){ bindZoomRefetch(); plotHandlersBound=true; }
await loadMetadataForSelection();
saveState();
setStatus(`Loaded ${finalRows.length} rows after masks.`);
```

- [ ] **Step 6: Manual verification**

1. Connect to a Postgres source with >50k rows. Click Chooch. Open DevTools Network tab.
2. Verify the initial fetch includes `max_points=10000` in the query string.
3. In the plot, zoom in to a narrow time window (< 20% of total). After ~300ms, verify a new `/api/timeseries` request fires with `start_time` and `end_time` params and no `max_points`.
4. Zoom back out to full view. Verify the overview data is restored without a new fetch.

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/index.html
git commit -m "feat: viewport-aware zoom refetch with LTTB overview, full-res detail on zoom-in"
```

---

## Task 7: Ultra Minimal UI redesign

**Files:**
- Modify: `backend/app/static/index.html`

- [ ] **Step 1: Replace the entire `<style>` block**

Find the opening `<style>` tag and locate its closing `</style>`. Replace the entire CSS block with:

```css
<style>
  :root { color-scheme:dark; --nova-ui-bg:#0a0a0a; --nova-view-bg:#000000; --nova-accent:#333; --nova-text:#d4d4d4; --nova-text-dim:#404040; --nova-divider:#1c1c1c; --nova-hover:#111111; --nova-active:#141414; --nova-line:#555; }
  *{ box-sizing:border-box; }
  body{ margin:0; font-family:"MiSans","Segoe UI","Microsoft YaHei UI",Arial,sans-serif; font-size:13px; background:var(--nova-ui-bg); color:var(--nova-text); height:100vh; overflow:hidden; }
  /* Menubar */
  .menubar{ display:flex; align-items:center; gap:4px; padding:3px 6px; border-bottom:1px solid var(--nova-divider); background:var(--nova-ui-bg); user-select:none; }
  .menuItem{ position:relative; padding:5px 9px; border:1px solid transparent; cursor:pointer; font-size:0.74rem; }
  .menuItem:hover{ background:var(--nova-hover); border-color:var(--nova-divider); }
  .menuDropdown{ position:absolute; top:calc(100% + 1px); left:0; min-width:220px; display:none; z-index:120; background:var(--nova-ui-bg); border:1px solid #222; box-shadow:0 8px 20px rgba(0,0,0,0.7); }
  .menuDropdown.show{ display:block; }
  .menuAction{ padding:7px 10px; border-bottom:1px solid var(--nova-divider); font-size:0.8rem; cursor:pointer; color:var(--nova-text); }
  .menuAction:last-child{ border-bottom:none; }
  .menuAction:hover{ background:var(--nova-hover); }
  .menuSep{ height:1px; background:var(--nova-divider); margin:2px 0; }
  /* Layout */
  .app{ display:flex; height:calc(100vh - 26px); width:100vw; }
  .sidebar{ width:252px; min-width:5vw; max-width:704px; background:var(--nova-ui-bg); border-right:1px solid var(--nova-divider); overflow-y:auto; flex:0 0 auto; font-size:0.8rem; display:flex; flex-direction:column; }
  .sidebarResizer{ width:4px; margin-left:-2px; margin-right:-2px; cursor:col-resize; background:transparent; z-index:5; }
  .main{ flex:1 1 auto; background:var(--nova-view-bg); overflow:hidden; display:flex; flex-direction:column; min-width:0; }
  /* Panels */
  .panel{ background:transparent; border:none; }
  .panelHeader{ display:flex; justify-content:space-between; align-items:center; background:var(--nova-ui-bg); border-bottom:1px solid var(--nova-divider); padding:5px 6px; margin:0; cursor:pointer; }
  .panelHeader:hover{ background:var(--nova-hover); }
  .panelHeader h3{ margin:0; font-size:0.60rem; color:var(--nova-text-dim); letter-spacing:0.09em; text-transform:uppercase; font-weight:500; }
  .headerBtns{ display:flex; gap:3px; }
  .addBtn,.collapseBtn{ width:14px; height:14px; border-radius:0; border:1px solid var(--nova-divider); background:transparent; color:var(--nova-text-dim); padding:0; font-weight:600; cursor:pointer; font-size:0.60rem; line-height:1; }
  .addBtn:hover,.collapseBtn:hover{ border-color:var(--nova-line); color:var(--nova-text); }
  /* Lists */
  .list{ border:none; background:var(--nova-ui-bg); overflow-y:auto; max-height:136px; }
  .item{ padding:4px 6px 4px 8px; border-bottom:1px solid #0f0f0f; cursor:pointer; border-left:2px solid transparent; }
  .item:last-child{ border-bottom:none; }
  .item.active{ background:var(--nova-active); border-left-color:var(--nova-line); }
  .item:hover:not(.active){ background:var(--nova-hover); }
  .item.focused{ outline:none; }
  .itemPrimary{ font-size:0.72rem; color:var(--nova-text); line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .itemMeta{ font-size:0.60rem; color:var(--nova-text-dim); margin-top:1px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .empty{ padding:8px; color:var(--nova-text-dim); font-size:0.70rem; }
  /* Form elements */
  label{ display:block; margin:5px 0 2px 0; font-size:0.60rem; color:var(--nova-text-dim); letter-spacing:0.06em; text-transform:uppercase; }
  input,select,button{ width:100%; border:1px solid var(--nova-divider); border-radius:0; background:#0d0d0d; color:var(--nova-text); padding:5px; font-size:0.72rem; outline:none; }
  input:focus,select:focus{ border-color:var(--nova-line); }
  button{ cursor:pointer; background:#111; border:1px solid #2a2a2a; }
  button:hover{ background:#1a1a1a; border-color:#444; }
  button:active{ background:#222; }
  /* Plot tabs */
  .plotTabs{ display:flex; align-items:stretch; gap:0; border-bottom:1px solid var(--nova-divider); background:var(--nova-ui-bg); min-height:26px; flex:0 0 auto; overflow-x:auto; }
  .plotTab{ width:auto; min-width:82px; max-width:195px; background:var(--nova-ui-bg); border:none; border-right:1px solid var(--nova-divider); border-top:2px solid transparent; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; flex:0 0 auto; padding:4px 8px; font-size:0.68rem; color:var(--nova-text-dim); }
  .plotTab.active{ background:#111; border-top-color:var(--nova-line); color:var(--nova-text); }
  .plotTab:hover:not(.active){ background:var(--nova-hover); color:var(--nova-text); }
  .plotAdd{ width:auto; min-width:24px; max-width:24px; padding:0; font-weight:900; flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; border:none; border-radius:0; background:var(--nova-ui-bg); color:var(--nova-text-dim); }
  .plotAdd:hover{ color:var(--nova-text); background:var(--nova-hover); }
  #plot{ width:100%; flex:1 1 auto; min-height:0; }
  /* Button overrides */
  .menuItem{ width:auto; }
  .plotTab,.plotAdd{ width:auto !important; }
  .menubar button{ width:auto; border:none; background:transparent; }
  .menubar button:hover{ background:var(--nova-hover); }
  /* Scrollbars hidden */
  .sidebar,.list,.modalList,.tableWrap{ scrollbar-width:none; }
  .sidebar::-webkit-scrollbar,.list::-webkit-scrollbar,.modalList::-webkit-scrollbar,.tableWrap::-webkit-scrollbar{ width:0; height:0; }
  /* Status bar */
  #statusBar{ position:fixed; bottom:0; left:0; right:0; height:20px; background:var(--nova-ui-bg); border-top:1px solid var(--nova-divider); padding:2px 8px; font-size:0.65rem; color:#555; z-index:50; display:flex; align-items:center; pointer-events:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #statusBar.error{ color:#a87070; }
  /* Drawer */
  .drawer{ position:fixed; left:0; right:0; bottom:20px; background:#0d0d0d; border-top:1px solid #222; max-height:44vh; padding:8px; display:none; z-index:40; }
  .drawer.show{ display:block; }
  .tableWrap{ overflow:auto; max-height:34vh; }
  table{ width:100%; border-collapse:collapse; font-size:0.74rem; }
  th,td{ border-bottom:1px solid var(--nova-divider); padding:4px 6px; text-align:left; white-space:nowrap; }
  th{ color:var(--nova-text-dim); font-weight:500; font-size:0.60rem; letter-spacing:0.06em; text-transform:uppercase; }
  /* Modal */
  .modalBackdrop{ position:fixed; inset:0; background:rgba(0,0,0,0.78); display:none; z-index:60; align-items:center; justify-content:center; }
  .modalBackdrop.show{ display:flex; }
  .modal{ width:min(760px,92vw); max-height:82vh; overflow:auto; background:#0d0d0d; border:1px solid #222; border-radius:0; padding:0; }
  .modalHeader{ display:flex; justify-content:space-between; align-items:center; padding:8px 10px; border-bottom:1px solid #222; }
  .modalTitle{ margin:0; font-size:0.85rem; color:var(--nova-text); }
  .modalClose{ width:auto; min-width:24px; padding:3px 7px; }
  .modalGrid{ display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:10px; }
  .modalActions{ padding:8px 10px; display:flex; gap:8px; justify-content:flex-end; border-top:1px solid var(--nova-divider); margin:0; }
  .modalActions button{ width:auto; min-width:100px; }
  .modalList{ border:1px solid var(--nova-divider); border-radius:0; background:#0a0a0a; max-height:48vh; overflow-y:auto; }
  .selectRow{ padding:7px 8px 7px 10px; border-bottom:1px solid var(--nova-divider); cursor:pointer; border-left:2px solid transparent; }
  .selectRow:last-child{ border-bottom:none; }
  .selectRow.active{ background:var(--nova-active); border-left-color:var(--nova-line); }
  .selectRow:hover:not(.active){ background:var(--nova-hover); }
  .timeRangeRow{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .timeRangeRow label{ margin-top:0; }
  /* Menus */
  .contextMenu,.miniMenu{ position:fixed; z-index:80; display:none; background:#0d0d0d; border:1px solid #222; border-radius:0; min-width:180px; }
  .contextItem,.miniMenuItem{ padding:7px 10px; cursor:pointer; font-size:0.78rem; border-bottom:1px solid var(--nova-divider); color:var(--nova-text); }
  .contextItem:last-child,.miniMenuItem:last-child{ border-bottom:none; }
  .contextItem:hover,.miniMenuItem:hover{ background:var(--nova-hover); }
  /* Realtime panel */
  #realtimePanel{ background:#0d0d0d; border:1px solid #222; }
  .realtimeBtn{ width:26px; height:26px; min-width:26px; border-radius:0; padding:0; font-size:0.72rem; line-height:1; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--nova-divider); background:transparent; color:var(--nova-text); }
  .realtimeBtn:hover{ background:var(--nova-hover); border-color:var(--nova-line); }
  .realtimeBtn.active{ border-color:var(--nova-line); background:var(--nova-active); }
  #realtimeCloseBtn{ border-radius:0; }
  /* Load dock */
  .choochDock{ margin-top:auto; padding:8px; border-top:1px solid var(--nova-divider); }
  .choochDock button{ border-radius:0; margin:0; min-height:30px; letter-spacing:0.09em; text-transform:uppercase; font-size:0.65rem; font-weight:600; background:#111; border-color:#2a2a2a; }
  .choochDock button:hover{ background:#1a1a1a; border-color:#555; }
  /* Region section */
  #regionBody{ max-height:none !important; padding:6px 8px; }
</style>
```

- [ ] **Step 2: Replace the old `<p id="status">` in the sidebar with the fixed status bar**

Find in the sidebar HTML:
```html
          <p id="status" class="status"></p>
```

Remove that line entirely.

Find the closing `</body>` tag and add the status bar just before it:
```html
  <div id="statusBar"></div>
</body>
```

- [ ] **Step 3: Update `setStatus` to write to `#statusBar`**

Find:
```js
function setStatus(text, isError = false) { const e = byId("status"); e.textContent = text; e.className = isError ? "status error" : "status"; }
```

Replace with:
```js
function setStatus(text, isError = false) { const e = byId("statusBar"); if(!e) return; e.textContent = text; e.className = isError ? "error" : ""; }
```

- [ ] **Step 4: Update `applyAppearance` to sync CSS variables**

Find:
```js
function applyAppearance(){
  document.documentElement.style.setProperty("--nova-ui-bg", appearance.uiBg);
  document.documentElement.style.setProperty("--nova-view-bg", appearance.viewBg);
  document.documentElement.style.setProperty("--nova-accent", appearance.accent);
  document.body.style.background = appearance.uiBg;
  if(latestRows.length) plotRows(latestRows);
}
```

Replace with:
```js
function applyAppearance(){
  document.documentElement.style.setProperty("--nova-ui-bg", appearance.uiBg);
  document.documentElement.style.setProperty("--nova-view-bg", appearance.viewBg);
  document.documentElement.style.setProperty("--nova-accent", appearance.accent||"#333");
  document.body.style.background = appearance.uiBg;
  if(latestRows.length) plotRows(latestRows);
}
```

- [ ] **Step 5: Update the Load button label**

Find:
```html
      <div class="choochDock"><button id="loadBtn">Chooch</button></div>
```

Replace with:
```html
      <div class="choochDock"><button id="loadBtn">Load</button></div>
```

- [ ] **Step 6: Manual verification**

Open NOVA in the browser. Verify:
- Background is near-black (#0a0a0a)
- Panel headers show uppercase dim labels, no box borders
- Selected items show left accent line, no blue background
- Active plot tab has top border line, not filled background
- Status bar appears at bottom of screen
- Load button is uppercase, monochrome
- All elements have square corners (no border-radius)

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/index.html
git commit -m "design: Ultra Minimal UI — pure black, dividers, left-border selection, fixed status bar"
```

---

## Final verification checklist

- [ ] `python -m pytest backend/tests/ -v` — all tests pass
- [ ] Server starts: `cd backend && uvicorn app.main:app`
- [ ] Load data: click Load, verify status shows row count
- [ ] Chooch with Postgres source: DevTools Network shows `max_points=10000`
- [ ] Zoom in to <20% window: detail refetch fires, shows higher point density
- [ ] Zoom back out: overview data restores without fetch
- [ ] Real-time animation: play is smooth (~20-30 fps)
- [ ] `latestRows[0].__ts` in console is a finite number
- [ ] UI matches Ultra Minimal spec: no border-radius, dividers not borders, left-line selection
