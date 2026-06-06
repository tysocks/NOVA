/**
 * NOVA v3 client — SeriesCache, unified query, IndexedDB config store.
 */
(function (global) {
  "use strict";

  const CONFIG_IDB_NAME = "nova-config-db";
  const CONFIG_IDB_STORE = "configs";
  const CONFIG_IDB_KEY = "library";

  function parseTimeMs(time) {
    if (typeof time === "number" && Number.isFinite(time)) return time;
    const ms = Date.parse(time);
    return Number.isFinite(ms) ? ms : NaN;
  }

  function stampRows(rows, dbId) {
    return (rows || []).map((r) => {
      const row = { ...r, __dbId: dbId };
      if (!Number.isFinite(row.__ts)) row.__ts = parseTimeMs(row.time);
      return row;
    });
  }

  class SeriesCache {
    constructor() {
      this.overview = null;
      this.detail = new Map();
      this.meta = null;
      this.key = null;
    }

    static requestKey(body) {
      return JSON.stringify(body);
    }

    setOverview(rows, meta, requestBody) {
      this.overview = rows;
      this.meta = meta || null;
      this.key = SeriesCache.requestKey(requestBody);
      this.detail.clear();
    }

    setDetail(windowKey, rows) {
      this.detail.set(windowKey, rows);
    }

    getDetail(windowKey) {
      return this.detail.get(windowKey) || null;
    }

    restoreOverview() {
      return this.overview;
    }
  }

  async function openConfigDb() {
    if (!global.indexedDB) return null;
    return new Promise((resolve, reject) => {
      const req = global.indexedDB.open(CONFIG_IDB_NAME, 1);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve(req.result);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(CONFIG_IDB_STORE)) {
          db.createObjectStore(CONFIG_IDB_STORE);
        }
      };
    });
  }

  async function loadConfigLibrary() {
    try {
      const db = await openConfigDb();
      if (!db) return null;
      return new Promise((resolve, reject) => {
        const tx = db.transaction(CONFIG_IDB_STORE, "readonly");
        const store = tx.objectStore(CONFIG_IDB_STORE);
        const req = store.get(CONFIG_IDB_KEY);
        req.onsuccess = () => resolve(req.result ?? null);
        req.onerror = () => reject(req.error);
      });
    } catch {
      return null;
    }
  }

  async function saveConfigLibrary(data) {
    try {
      const db = await openConfigDb();
      if (!db) return false;
      return new Promise((resolve, reject) => {
        const tx = db.transaction(CONFIG_IDB_STORE, "readwrite");
        const store = tx.objectStore(CONFIG_IDB_STORE);
        const req = store.put(data, CONFIG_IDB_KEY);
        req.onsuccess = () => resolve(true);
        req.onerror = () => reject(req.error);
      });
    } catch {
      return false;
    }
  }

  async function querySeries(body, { format = "json", signal = null } = {}) {
    const url = `/api/v3/series/query?format=${encodeURIComponent(format)}`;
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal || undefined,
    });
    if (!r.ok) {
      let msg = `v3 query failed (${r.status})`;
      try {
        const e = await r.json();
        if (e.detail) msg = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
      } catch {}
      throw new Error(msg);
    }
    if (format === "json") {
      const payload = await r.json();
      return {
        rows: payload.rows || [],
        meta: payload.meta || null,
        headers: r.headers,
      };
    }
    const metaHeader = r.headers.get("x-nova-series-meta");
    return {
      buffer: await r.arrayBuffer(),
      meta: metaHeader ? JSON.parse(metaHeader) : null,
      headers: r.headers,
    };
  }

  const NovaV3 = {
    SeriesCache,
    stampRows,
    parseTimeMs,
    querySeries,
    loadConfigLibrary,
    saveConfigLibrary,
    CONFIG_IDB_KEY,
  };

  global.NovaV3 = NovaV3;
})(typeof window !== "undefined" ? window : globalThis);
