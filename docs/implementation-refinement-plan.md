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

#### Highest fit with NOVA today

1. **Stacked strip chart (scope rack)**  
   One channel (or unit group) per horizontal strip, shared X, independent Y. Best way to scan dozens of channels without overplotting. Natural extension of multiplot + linked X.

2. **Channel × time heatmap (raster / waterfall of channels)**  
   Rows = channels (or runs), columns = time, color = value (or normalized). Surfaces which sensors move when, across large sets that a line plot cannot show.

3. **Run / range overlay with envelope**  
   Many aligned tests or ranges as faint traces + min/mean/max (or percentile) band. Answers “is this run typical?” — builds on Align / Stitch Ranges.

4. **Event-aligned montage**  
   Snap many runs to a range start (ignition, valve open, t0) and overlay or stack. Core test-analysis workflow; leverages existing ranges + t0 modes.

5. **Difference / residual scope**  
   Plot `A − B` (or A vs reference run) in time. Spot drift between builds, sensors, or back-to-back tests without leaving Time Scope mentally.

#### Strong next-tier views

6. **Persistence / density plot**  
   Overplot many runs as a 2D density (time × value). Reveals common paths and outliers when N is large.

7. **Small multiples (run grid)**  
   Same channel(s) tiled per test/run with linked axes. Faster than flipping sources one at a time.

8. **Cross-correlation / lag view**  
   Corr(A, B) vs lag, or lag-aligned overlay. Useful for transport delay, sensor sync, and causality checks.

9. **Coherence / transfer (freq-domain pair)**  
   Beyond single-channel PSD: coherence and approximate transfer between two channels. Complements Spectral.

10. **Cursor / readout table view**  
    Multi-cursor values across many channels at linked X positions (and deltas). Analysis companion to Time Scope rather than a chart type alone.

#### Niche but high value for test labs

11. **Phase portrait (state space)**  
    Classic parametric with trail/velocity coloring; useful for cycles, hysteresis, limit cycles (already adjacent to Parametric).

12. **Campbell / waterfall (RPM or condition vs spectrum)**  
    Stack spectra vs operating point when speed/condition metadata exists — bridge to rotating machinery.

13. **Mask / pass-fail ribbon**  
    Time axis with limit bands and per-range pass/fail coloring; ties Summary Limits into a visual timeline.

#### Explicit non-goals for new views

- Bar / gauge / KPI stat tiles (Grafana dashboard language)  
- Business histograms of categorical aggregates  
- Geo / logs / APM panels  

#### Recommended first three to productize

| Priority | View | Why |
|----------|------|-----|
| 1 | Stacked strip chart | Highest leverage for “large sets of channels” |
| 2 | Event-aligned overlay + envelope | Highest leverage for “large sets of runs/ranges” |
| 3 | Channel × time heatmap | Unique density view when line plots collapse |

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
3. **Features** — linked axes; range stitch; then engineering views (strip → event overlay/envelope → channel heatmap)  
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
5. At least one new engineering view ships from the recommended trio (strip / event overlay+envelope / channel heatmap).  
6. Stitch Ranges places ≥2 selected ranges tip-to-tail and replots.  
7. Linking X (or Y) on two tiles keeps ranges in sync while zooming either.  
8. Opening menus and resizing tiles feels snappier with less flicker.
