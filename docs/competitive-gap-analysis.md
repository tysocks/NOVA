# NOVA Competitive Gap Analysis

**Date:** 2026-08-06  
**Scope:** Compare current NOVA (`main` @ `2f3a5a9`) to Grafana and peer products for **visualize / manipulate / share** data.  
**Goal context:** Make NOVA the default option for people who want to visualize, manipulate, and share data—especially test and engineering telemetry.

---

## 1. Positioning (read this first)

NOVA today is a **desktop-first engineering telemetry workbench** (FastAPI + Plotly UI + PySide6), not a multi-tenant observability or BI platform.

| | NOVA | Grafana | NI DIAdem / Synnax-class | Tableau / Power BI |
|---|------|---------|--------------------------|--------------------|
| Primary job | Interactive test-channel analysis | Live ops dashboards + alerting | Deep test/measurement analysis + reporting | Business metrics & governed reporting |
| Data model | Channels + tests + ranges + units | Metrics/logs/traces + datasources | Measurement channels + metadata search | Tables, cubes, semantic models |
| Deployment | Local desktop (localhost) | Self-host / Cloud | Desktop (+ enterprise data servers) | Cloud / desktop clients |
| Sharing model | Local JSON libraries / configs | URL dashboards, folders, RBAC | Reports, scripts, data plugins | Published workbooks, embeds |

**Implication:** Competing with Grafana feature-for-feature is the wrong frame. Grafana wins on ops monitoring; DIAdem/Synnax win on mature test workflows; BI tools win on enterprise sharing. NOVA should win on **fast, unit-aware, channel-centric analysis of engineering files and catalogs**—then close the gaps that block it from being the *default* for visualize → manipulate → share.

Remote branches `origin/data_overhaul` and `origin/ui_improvements` are **ancestors of `main`** (no extra features vs current tree).

---

## 2. What NOVA already has (strengths)

### Visualize
- Multiplot workspace: tabs, split tiles, Time Scope / Parametric / Spectral (Welch PSD + spectrogram) / Summary / Legend
- Plot overlays (ruler, regions, lines/bands, shapes, text, datanotes)
- Explorer-style Sources/Channels selection; multi-Y by unit; t0 modes; zoom LOD refetch
- DuckDB + Parquet catalog for project data; CSV/Parquet/Arrow/HDF5/TDMS ingest

### Manipulate
- Rolling + formula calculated channels (server-side v3 engine by default)
- Unit Manager with preferred units and conversion formulas
- Masks (formula, rolling, range); range detection rules and range definition library
- Summary aggregates + Limits status (ok/warn/fail) — offline, not alerting
- Configuration Manager for reusable channels/calcs/aliases/masks

### Share (today = local reuse, not collaboration)
- Import/export of unit, config, database, and range-definition libraries (JSON)
- Sources workspace + UI state persistence (localStorage / server JSON files)
- Catalog results key/value store

### Performance foundation
- v3 columnar query (`POST /api/v3/series/query`), Arrow IPC, LOD planner, Parquet ingest, LTTB detail tier

These are real differentiators versus Grafana for **engineering file depth** (TDMS/H5, units, channel calcs, ranges). Grafana is weak here by design.

---

## 3. Gap matrix vs industry options

Priority legend: **P0** blocks “default tool” adoption · **P1** expected by peers · **P2** nice-to-have / later moat

### 3.1 Share (largest strategic gap)

| Capability | NOVA | Grafana | DIAdem / peers | Gap |
|------------|------|---------|----------------|-----|
| Share a view via URL / link | No | Yes (dashboards, snapshots, public links) | Report files / server publish | **P0** — no first-class share artifact |
| Auth, users, permissions | None | Org/teams/RBAC | License + often AD/SSO in enterprise | **P0** for multi-user |
| Export plot image (PNG/PDF) | Modebar off; no product export | Dashboard image / PDF reports | Report generator (core) | **P0** |
| Export series / table (CSV/Parquet) | No dedicated product path | Limited (Explore/CSV in places) | Strong data export | **P0** |
| Packaged session / project file | Scattered JSON + `.nova_sessions` | Dashboard JSON / provisioning | Project / TDM / report packs | **P0** — hard to hand a colleague “the analysis” |
| Embed / API for other apps | Local HTTP only, no auth | Strong embedding + HTTP API | Scripting / DataPlugins | **P1** |
| Comments / review on plots | No | Annotations (limited) | Report markup / notes | **P2** |
| Live multi-user collab | No | Concurrent viewers | Rare | **P2** |

**Verdict:** Against the stated goal (“visualize, manipulate **and share**”), sharing is the weakest pillar. Peers treat share as a product surface; NOVA treats it as file copy of libraries.

### 3.2 Visualize

| Capability | NOVA | Grafana | DIAdem / PlotJuggler / peers | Gap |
|------------|------|---------|------------------------------|-----|
| Time-series multi-channel | Strong | Strong | Strong | — |
| Engineering views (XY parametric, PSD, spectrogram) | Present | Weak / plugins | Strong | Keep investing |
| Panel variety (stat, gauge, bar, table, heatmap, geo, logs) | Narrow set | Very broad | Analysis-focused panels | **P1** for “anyone” claim; **P2** if niche stays test telemetry |
| Dashboard variables / templating | No | Core | Scripted layouts | **P1** |
| Live / streaming refresh | Trimmed / absent | Core | Acquisition-linked tools | **P1** if competing with Grafana live; else defer |
| Logs / traces / APM | No | Core Grafana stack | Usually no | Out of scope unless pivoting to observability |
| Map / GIS | No | Geomap | Niche | **P2** |
| Video / CAN / bus sync | No | No | DIAdem strength | **P1** for automotive/aero labs |
| 200+ file formats | ~5 families | Datasource plugins | DIAdem / DataPlugins | **P1** expand formats gradually |

### 3.3 Manipulate

| Capability | NOVA | Grafana | DIAdem | Gap |
|------------|------|---------|--------|-----|
| Channel formulas / rolling | Strong (server) | Transformations (limited) | 100+ analysis fns | **P1** broaden analysis library |
| Units & engineering conversions | Strong | Weak | Strong | Differentiator — protect |
| Ranges / event windows | Solid MVP | Annotations / alert windows | Strong | Finish ingestion-rules UI (**P0** stub today) |
| Query languages (PromQL, SQL UI, etc.) | Fixed query API | Many | Scripting (VBS/Python) | **P1** optional SQL/script surface |
| Batch / automate across many tests | Partial (API + rules backend) | Alerting + reporting | Script automation | **P0** for lab throughput |
| Masks / aliases / configs | Strong local | Limited | Strong | Differentiator |
| Limits → alerts / notifications | Offline Summary only | Full alerting stack | Report checks / scripts | **P1** (email/Slack/webhook) if ops-adjacent |

### 3.4 Data connectivity & ops

| Capability | NOVA | Grafana | Peers | Gap |
|------------|------|---------|-------|-----|
| Datasource ecosystem | Files + DuckDB catalog; Postgres opt-in | 100+ plugins | Many formats / historians | **P0–P1** connectors (Influx, Timescale, S3, Synnax, MQTT…) |
| Credential security | Plaintext JSON library | Secrets + SSO | Often OS/enterprise vault | **P0** OS keychain / secret store |
| Multi-machine deployment | Localhost desktop | Designed for servers | Desktop + enterprise | **P1** if “default for anyone” includes teams |
| HA / multi-org tenancy | No | Yes (Enterprise) | Varies | **P2** unless SaaS |
| Plugin marketplace | No | Yes | DataPlugins | **P2** |
| OpenAPI / docs for integrators | Docs disabled | Strong | Varies | **P1** |

### 3.5 Product polish vs “default tool”

| Capability | Status in NOVA | Gap |
|------------|----------------|-----|
| Ingestion rules UI | Placeholder “Coming soon” | **P0** — backend range rules exist; UI missing |
| Cross-platform install | Windows-oriented launchers (`.bat`/`.vbs`/shortcuts) | **P0** macOS/Linux packaging if audience is broader |
| Onboarding / empty states / sample projects | Examples exist; weak guided tour | **P1** |
| Monolithic UI (`index.html` ~24k lines) | Hard to extend/test | **P1** modular frontend (already in v3 plan, largely undone) |
| Plot export & clipboard | Missing | **P0** |
| Versioned project / git-friendly session | Partial JSON dumps | **P1** single `.nova` project package |

---

## 4. Competitor-specific takeaways

### Grafana
- **Wins:** live dashboards, alerting, datasource plugins, RBAC, URL sharing, embedding, ops scale.
- **Loses to NOVA:** TDMS/H5 depth, unit-centric engineering UX, channel calcs, range/mask workflows, offline desktop file analysis.
- **Do not copy blindly:** PromQL, Loki/Tempo, alertmanager — only if NOVA pivots to observability.
- **Do copy the product mechanics:** shareable named views, permissions model (even single-user “link + password” first), image/PDF export, variables for reusable layouts.

### NI DIAdem (and similar test desks)
- **Wins:** format breadth, sync of heterogeneous signals, analysis function library, automated reporting, metadata search over large archives.
- **NOVA path:** expand formats + batch automation + report/export; keep modern UX and columnar engine as the wedge.

### Synnax-class / PlotJuggler
- Synnax-style catalog/manager UX is already influencing NOVA (Databases/Unit managers, channel editors).
- PlotJuggler: fast XY/time plotting and plugins; weaker durable analysis libraries — NOVA can outcompete on catalog + calcs + units if sharing/export catch up.

### Tableau / Power BI
- **Wins:** governed sharing, semantic models, executive dashboards.
- Overlap is low for high-rate telemetry; steal **publish + permissions + scheduled report** patterns, not BI chart vocabulary.

---

## 5. Recommended gap closure order (toward “default”)

### Phase A — Make “share” real (unblocks the goal statement)
1. **Session/project package** — one portable artifact (sources refs, channels, calcs, masks, ranges, plot layout, appearance).
2. **Export** — PNG/SVG of active plot(s); CSV/Parquet of visible series; optional PDF one-pager from Summary + plots.
3. **Share link MVP** — even local “copy shareable JSON / open in NOVA” before cloud; then optional authenticated upload.
4. **Credential hardening** — OS keychain for DB passwords.

### Phase B — Finish engineering workflows already half-built
1. Ship **Ingestion rules** UI (placeholder today) wired to existing range-rule APIs.
2. Batch apply configs/calcs across many tests; scriptable headless query CLI.
3. Broaden analysis functions (FFT peaks, rise time, integrals) to approach DIAdem expectations without matching all 100+.

### Phase C — Become the default for a wider audience
1. More connectors (Timescale/Postgres always-on UX, object storage, common historians) with a plugin-shaped interface.
2. Lightweight multi-user: users, read-only shared projects, folder permissions.
3. Cross-platform installers; optional server mode beyond `127.0.0.1`.
4. Modularize frontend; re-enable OpenAPI for integrators.

### Explicit non-goals (for now)
- Full Grafana observability stack (logs/traces/APM)
- Competing on PromQL ecosystem size
- Real-time multi-user collaborative cursors

---

## 6. Scorecard (current vs “default tool”)

Rough 1–5 scores for the stated triad:

| Pillar | NOVA today | Grafana | DIAdem-class | Target for “default” |
|--------|------------|---------|--------------|----------------------|
| Visualize | 3.5 | 4.5 | 4.5 | 4.5 |
| Manipulate | 4.0 | 2.5 | 5.0 | 4.5 |
| Share | **1.5** | 4.5 | 3.5 | 4.0 |

**Bottom line:** NOVA is already competitive on **manipulate** for channel telemetry and credible on **visualize** for engineering views. It is far behind on **share**. Closing share/export/project-packaging—and finishing ingestion automation—is the highest-leverage path to becoming the default, without trying to become Grafana.

Concrete next-pass scope (Share, Accessibility, Features, Performance): [implementation-refinement-plan.md](./implementation-refinement-plan.md).

---

## 7. Code / product anchors

| Area | Location |
|------|----------|
| Primary UI | `backend/app/static/index.html` (~24k lines) |
| v3 client | `backend/app/static/js/nova-v3.js` |
| Query engine | `backend/app/engine/` |
| API surface | `backend/app/main.py` |
| Desktop shell | `backend/desktop_app.py` |
| Formats | `docs/data-file-formats.md` |
| Perf plan (partial frontend debt) | `docs/NOVA-v3-performance-implementation-plan.md` |
| Ingestion UI stub | `index.html` Ingestion toolbar — “Coming soon” |
| Postgres gated off by default | `config.enable_postgres` (`NOVA_ENABLE_POSTGRES`) |
