# NOVA Implementation Refinement Plan

**Status:** Plan only — no product code in this change  
**Date:** 2026-08-10  
**Goal:** Close the share / accessibility / feature / performance gaps that block NOVA from being the default visualize → manipulate → share tool.

Related: [competitive-gap-analysis.md](./competitive-gap-analysis.md)

---

## Pillar 1 — Share

| Item | Today | Planned work |
|------|-------|--------------|
| Export PNG of page | Missing | Capture visible plot workspace → PNG download (`File → Export`, shortcut) |
| Copy view to clipboard | Missing | Active Plotly tile → PNG blob → clipboard (download fallback) |
| Export PDF | Missing | Active view and/or page → single-page PDF download |
| Manager import/export | Units / Configs / Range defs only | Wire **Database** + **Limits** managers to the same JSON Import/Export pattern |

**Surfaces:** File menu Export actions, plot context menu, plot-tab menu, shortcuts.

---

## Pillar 2 — Accessibility / workflow

| Item | Today | Planned work |
|------|-------|--------------|
| Shortcuts / keybinds | Plot, Plot All, Delete, Preferences | Add Export PNG/PDF, Copy view, Copy/Paste channels, Reset zoom, Stitch ranges, Link X/Y |
| Ctrl / Shift zoom | Wheel = XY; Shift+drag = pan X | **Ctrl+wheel** → X-only zoom; **Shift+wheel** → Y-only zoom; keep Shift+drag pan |
| Copy/paste channels | Missing | In-memory channel clipboard; paste into active view channel set |
| More right-click menus | Partial | Export/share on plot; Copy/Paste on channels; Stitch on ranges; Link axes on plot tabs; Import/Export on DB & Limits |

---

## Pillar 3 — Function / Features

| Item | Today | Planned work |
|------|-------|--------------|
| More plot types | time / parametric / spectral / summary / legend | Prefer **engineering time-series views** below — not Grafana Bar/Histogram/Stat |
| Range stitch | Align Ranges = t0 sync only | **Stitch Ranges tip-to-tail**: place selected ranges sequentially on shared X for comparison |
| Link axes between views | Per-tile locks only | `axisLink.x` / `axisLink.y` on plots; propagate relayout across linked tiles |

### View brainstorm (engineering time series — replace Grafana-style panels)

Bar / Histogram / Stat are deferred as better suited to BI/ops aggregation apps. NOVA should prioritize views that help engineers **see structure in large multi-channel, multi-run waveforms**.

#### Productized direction (confirmed)

1. **Stacked Strips (scope rack)** — **priority 1**  
   One channel (or unit group) per horizontal strip, shared X, independent Y. Best way to scan dozens of channels without overplotting. Natural extension of multiplot + linked X.

2. **Event-aligned overlay (+ optional envelope)** — **priority 2**  
   Distinct from today’s **Align Ranges** (which sets per-source `t0` for the whole source). This view should:
   - Accept **as many selected ranges as practical**, each from a **distinct source**
   - Snap every range’s start to a shared event origin on X (ignition, valve open, range From, etc.)
   - Overlay the selected channel(s) for those range windows on one axes set
   - Optionally draw a min/mean/max (or percentile) **envelope** across the overlaid population  
   Goal: compare many tests/events in one frame without manually aligning whole sources first. Scale target: N ranges from N sources (practical UI/perf caps TBD; design for large N, not “a few”).

3. **Grid Scope (small multiples)** — **priority 3**  
   Each **selected source** is one tile in a growing grid. Same selected channel(s) drawn in every tile (source-local series). Layout grows with source count, e.g.:

   | Sources | Grid |
   |---------|------|
   | 1 | 1×1 |
   | 2 | 1×2 |
   | 3 | 1×3 |
   | 4 | 2×2 |
   | 5 | 2×3 (one cell empty) |
   | 6 | 2×3 |
   | 7 | 3×3 (two empty) |
   | … | next rectangle that fits N (row-major fill; unused cells blank) |

   Linked X (and optionally Y) across tiles by default so zoom/pan compares runs side-by-side.

4. **Channel × time heatmap** — **low priority (keep in plan)**  
   Rows = channels (or runs), columns = time, color = value (or normalized). Useful when line plots collapse under density; ship after Strips / Event overlay / Grid Scope.

#### Supporting / later views

5. **Difference / residual scope**  
   Plot `A − B` (or A vs reference run) in time.

6. **Persistence / density plot** — see detailed description below  
7. **Cursor / readout table** — see detailed description below  
8. **Cross-correlation / lag view**  
9. **Coherence / transfer (freq-domain pair)**  
10. **Phase portrait / Campbell / pass-fail ribbon** (niche)

#### Persistence / density plot (detail)

**Problem:** Overlaying 50–500 runs as opaque lines turns into solid ink; you cannot see the common path vs rare outliers.

**Idea:** Treat every sample from every selected run/range as a point in the (time, value) plane (after event alignment). Instead of drawing each polyline, accumulate a **2D histogram / density field**:

- X = relative time (or absolute)
- Y = engineering value (preferred unit)
- Color / brightness = how many samples (or runs) fell in that bin

**What you see:** A bright “ridge” where most tests travel; faint wings where outliers wander. Optional controls: percentile contours, log density, per-run ghost lines at low opacity.

**When it helps:** Campaign-level review (“where do burns usually sit?”), sensor noise envelopes, spotting one-off anomalies without picking a single reference run.

**Relation to Event-aligned overlay:** Overlay is best for modest N with identifiable traces; Persistence is for large N where individual traces are no longer useful.

#### Cursor / readout table (detail)

**Problem:** With many channels (or many Grid Scope tiles), reading values off the plot legend is slow. Engineers often need **numbers at one or more times**, plus deltas between cursors.

**Idea:** A companion view (or docked panel) that is primarily a **table**, driven by cursors on a linked time scope:

| Channel | Cursor A | Cursor B | Δ (B−A) | Unit |
|---------|----------|----------|---------|------|
| chamber_p | 12.41 | 18.02 | +5.61 | bar |
| thrust | 1.50e3 | 2.11e3 | +610 | N |
| … | … | … | … | … |

**Behavior:**
- Place Cursor A / B (and optionally more) on Time Scope, Stacked Strips, Grid Scope, or Event overlay
- Table lists effective selected channels (or one row per strip/tile channel)
- Values are interpolated at each cursor’s X; Δ columns update live as cursors drag
- Optional: show source/run columns when multiple sources are in play; copy table to clipboard

**Why it is a “view”:** It is not another chart type for exploring shape — it is the **numeric readout surface** for large channel sets, like a scope’s measurement bar but multi-channel and shareable with the plot layout.

**Relation to Summary:** Summary is aggregate-over-range (mean/min/max + limits). Cursor table is **instantaneous (or dual-point) readout** while scrubbing.

#### Explicit non-goals for new views

- Bar / gauge / KPI stat tiles (Grafana dashboard language)  
- Business histograms of categorical aggregates  
- Geo / logs / APM panels  

#### Recommended productization order

| Priority | View | Why |
|----------|------|-----|
| 1 | **Stacked Strips** | Large sets of channels |
| 2 | **Event-aligned overlay** (+ envelope) | Many ranges from distinct sources on one axes |
| 3 | **Grid Scope** | Many sources as a growing tile grid |
| Low | **Channel × time heatmap** | Keep planned; ship after the three above |

---

## Pillar 4 — Performance

| Item | Hotspot | Planned fix |
|------|---------|-------------|
| Menu / window delay | Tag-chip hover delay; long `suppressZoomRelayout` holds | Cut hover delay; shorten programmatic suppress windows |
| Resize / reposition jitter | ResizeObserver + mosaic rebuild + overlay sync | Debounce resize; avoid full purge on resize; sync overlays on active host only |
| UI flicker | `Plotly.purge` / `newPlot` paths; status toast thrash | Prefer restyle/relayout; reduce forced full rebuilds; contain CSS for plot hosts |

---

## Suggested implementation order

1. **Share MVP** — PNG page, clipboard view, PDF view; Database + Limits Import/Export  
2. **Accessibility** — shortcuts, Ctrl/Shift zoom, channel copy/paste, context menus  
3. **Features** — linked axes; range stitch; engineering views in order: **Stacked Strips → Event-aligned overlay → Grid Scope** (heatmap later)  
4. **Performance** — menu snappiness, resize/overlay thrash, flicker reduction  

---

## Non-goals this pass

- Cloud URL sharing / auth / multi-user  
- Grafana-style Bar / Histogram / Stat / gauge panels  
- Full Grafana panel plugin ecosystem  
- Ingestion-rules UI (backend exists; still deferred)  
- Series CSV / Parquet bulk export (follow-up)

---

## Acceptance checks

1. File → Export can PNG the workspace, copy the active view, and PDF the active view.  
2. Database Manager and Limits Manager support Import/Export via right-click (including empty list).  
3. Ctrl+wheel zooms X only; Shift+wheel zooms Y only on the plot area.  
4. Channels can be copied and pasted between views via shortcut and context menu.  
5. **Stacked Strips** renders selected channels as shared-X strips.  
6. **Event-aligned overlay** overlays many selected ranges from distinct sources on a shared event origin (optional envelope).  
7. **Grid Scope** tiles one cell per selected source with a growing grid (1×1, 1×2, 1×3, 2×2, 2×3 with empties, …).  
8. Stitch Ranges places ≥2 selected ranges tip-to-tail and replots.  
9. Linking X (or Y) on two tiles keeps ranges in sync while zooming either.  
10. Opening menus and resizing tiles feels snappier with less flicker.
