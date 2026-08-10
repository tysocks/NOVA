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
| More plot types (Grafana niche) | time / parametric / spectral / summary / legend | Add **Bar**, **Histogram**, **Stat** |
| Range stitch | Align Ranges = t0 sync only | **Stitch Ranges tip-to-tail**: place selected ranges sequentially on shared X for comparison |
| Link axes between views | Per-tile locks only | `axisLink.x` / `axisLink.y` on plots; propagate relayout across linked tiles |

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
3. **Features** — Bar / Histogram / Stat; range stitch; linked axes  
4. **Performance** — menu snappiness, resize/overlay thrash, flicker reduction  

---

## Non-goals this pass

- Cloud URL sharing / auth / multi-user  
- Full Grafana panel plugin ecosystem  
- Ingestion-rules UI (backend exists; still deferred)  
- Series CSV / Parquet bulk export (follow-up)

---

## Acceptance checks

1. File → Export can PNG the workspace, copy the active view, and PDF the active view.  
2. Database Manager and Limits Manager support Import/Export via right-click (including empty list).  
3. Ctrl+wheel zooms X only; Shift+wheel zooms Y only on the plot area.  
4. Channels can be copied and pasted between views via shortcut and context menu.  
5. Bar / Histogram / Stat appear in View Type and render.  
6. Stitch Ranges places ≥2 selected ranges tip-to-tail and replots.  
7. Linking X (or Y) on two tiles keeps ranges in sync while zooming either.  
8. Opening menus and resizing tiles feels snappier with less flicker.
