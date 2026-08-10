/**
 * NOVA share / export helpers (PNG, clipboard, PDF).
 * Loaded before the main UI script; exposes window.NovaShare.
 */
(function (global) {
  "use strict";

  function downloadBlob(blob, filename) {
    const a = document.createElement("a");
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  function dataUrlToBlob(dataUrl) {
    const parts = String(dataUrl || "").split(",");
    const meta = parts[0] || "";
    const data = parts[1] || "";
    const isBase64 = /;base64/i.test(meta);
    const mime = (meta.match(/^data:([^;]+)/) || [])[1] || "application/octet-stream";
    if (isBase64) {
      const bin = atob(data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      return new Blob([bytes], { type: mime });
    }
    return new Blob([decodeURIComponent(data)], { type: mime });
  }

  async function plotlyToDataUrl(gd, opts = {}) {
    if (!global.Plotly || !gd) throw new Error("No Plotly chart to export");
    const format = opts.format || "png";
    const width = Math.max(320, Math.round(opts.width || gd.clientWidth || gd._fullLayout?.width || 960));
    const height = Math.max(240, Math.round(opts.height || gd.clientHeight || gd._fullLayout?.height || 540));
    const scale = opts.scale || 2;
    return global.Plotly.toImage(gd, { format, width, height, scale });
  }

  async function copyImageDataUrl(dataUrl) {
    const blob = dataUrlToBlob(dataUrl);
    if (global.navigator?.clipboard?.write && global.ClipboardItem) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ [blob.type || "image/png"]: blob })]);
        return { ok: true, via: "clipboard" };
      } catch (err) {
        // Fall through to download.
        console.warn("[NOVA] clipboard write failed", err);
      }
    }
    downloadBlob(blob, optsFilename("nova_view", "png"));
    return { ok: true, via: "download" };
  }

  function optsFilename(stem, ext) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    return `${stem}_${stamp}.${ext}`;
  }

  function concatUint8(chunks) {
    let len = 0;
    chunks.forEach((c) => { len += c.length; });
    const out = new Uint8Array(len);
    let off = 0;
    chunks.forEach((c) => { out.set(c, off); off += c.length; });
    return out;
  }

  function encodeUtf8(str) {
    return new TextEncoder().encode(str);
  }

  /**
   * Minimal single-page PDF embedding a JPEG image (DeviceRGB / DCTDecode).
   */
  function jpegToPdfBlob(jpegBytes, imgW, imgH, pageW, pageH) {
    const objects = [];
    const add = (body) => {
      objects.push(body);
      return objects.length;
    };

    const catalogId = add(null);
    const pagesId = add(null);
    const pageId = add(null);
    const contentId = add(null);
    const imageId = add(null);

    const contentStream = `q\n${pageW} 0 0 ${pageH} 0 0 cm\n/Im0 Do\nQ\n`;
    const contentBytes = encodeUtf8(contentStream);

    objects[catalogId - 1] = encodeUtf8(`<< /Type /Catalog /Pages ${pagesId} 0 R >>`);
    objects[pagesId - 1] = encodeUtf8(`<< /Type /Pages /Kids [${pageId} 0 R] /Count 1 >>`);
    objects[pageId - 1] = encodeUtf8(
      `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${pageW} ${pageH}] ` +
      `/Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>`
    );
    objects[contentId - 1] = concatUint8([
      encodeUtf8(`<< /Length ${contentBytes.length} >>\nstream\n`),
      contentBytes,
      encodeUtf8("\nendstream"),
    ]);
    objects[imageId - 1] = concatUint8([
      encodeUtf8(
        `<< /Type /XObject /Subtype /Image /Width ${imgW} /Height ${imgH} ` +
        `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpegBytes.length} >>\nstream\n`
      ),
      jpegBytes,
      encodeUtf8("\nendstream"),
    ]);

    const header = encodeUtf8("%PDF-1.4\n");
    const parts = [header];
    const offsets = [0];
    let pos = header.length;
    for (let i = 0; i < objects.length; i += 1) {
      offsets.push(pos);
      const objHeader = encodeUtf8(`${i + 1} 0 obj\n`);
      const objFooter = encodeUtf8("\nendobj\n");
      parts.push(objHeader, objects[i], objFooter);
      pos += objHeader.length + objects[i].length + objFooter.length;
    }
    const xrefPos = pos;
    let xref = `xref\n0 ${objects.length + 1}\n`;
    xref += "0000000000 65535 f \n";
    for (let i = 1; i <= objects.length; i += 1) {
      xref += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
    }
    xref += `trailer\n<< /Size ${objects.length + 1} /Root ${catalogId} 0 R >>\nstartxref\n${xrefPos}\n%%EOF\n`;
    parts.push(encodeUtf8(xref));
    return new Blob([concatUint8(parts)], { type: "application/pdf" });
  }

  async function loadImageSize(dataUrl) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve({ width: img.naturalWidth || img.width, height: img.naturalHeight || img.height, img });
      img.onerror = () => reject(new Error("Failed to decode export image"));
      img.src = dataUrl;
    });
  }

  async function exportPlotPng(gd, filename) {
    const dataUrl = await plotlyToDataUrl(gd, { format: "png", scale: 2 });
    downloadBlob(dataUrlToBlob(dataUrl), filename || optsFilename("nova_view", "png"));
    return dataUrl;
  }

  async function copyPlotPng(gd) {
    const dataUrl = await plotlyToDataUrl(gd, { format: "png", scale: 2 });
    return copyImageDataUrl(dataUrl);
  }

  async function exportPlotPdf(gd, filename) {
    const dataUrl = await plotlyToDataUrl(gd, { format: "jpeg", scale: 2 });
    const { width, height } = await loadImageSize(dataUrl);
    const jpeg = new Uint8Array(await dataUrlToBlob(dataUrl).arrayBuffer());
    // Fit letter-ish page preserving aspect.
    const maxW = 792;
    const maxH = 612;
    const scale = Math.min(maxW / width, maxH / height, 1);
    const pageW = Math.max(1, Math.round(width * scale));
    const pageH = Math.max(1, Math.round(height * scale));
    const blob = jpegToPdfBlob(jpeg, width, height, pageW, pageH);
    downloadBlob(blob, filename || optsFilename("nova_view", "pdf"));
    return blob;
  }

  /**
   * Composite visible Plotly hosts inside a workspace element into one PNG.
   */
  async function exportWorkspacePng(workspaceEl, filename) {
    if (!workspaceEl) throw new Error("No plot workspace");
    const hosts = [...workspaceEl.querySelectorAll(".plotHost")].filter((h) => {
      const gd = h.data || h._fullLayout ? h : (h.querySelector?.(".js-plotly-plot") || h);
      return Boolean(gd && (gd.data || gd._fullLayout || global.Plotly));
    });
    if (!hosts.length) throw new Error("No visible plots to export");

    const wsRect = workspaceEl.getBoundingClientRect();
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(wsRect.width * scale));
    canvas.height = Math.max(1, Math.round(wsRect.height * scale));
    const ctx = canvas.getContext("2d");
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--nova-view-bg").trim() || "#191919";
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    for (const host of hosts) {
      const gd = (host.data || host._fullLayout) ? host : (host.querySelector(".js-plotly-plot") || host);
      if (!gd || !global.Plotly) continue;
      try {
        const dataUrl = await plotlyToDataUrl(gd, {
          format: "png",
          width: host.clientWidth || 640,
          height: host.clientHeight || 360,
          scale: 1,
        });
        const { img } = await loadImageSize(dataUrl);
        const rect = host.getBoundingClientRect();
        const x = (rect.left - wsRect.left) * scale;
        const y = (rect.top - wsRect.top) * scale;
        const w = rect.width * scale;
        const h = rect.height * scale;
        ctx.drawImage(img, x, y, w, h);
      } catch (err) {
        console.warn("[NOVA] workspace tile export failed", err);
      }
    }

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Workspace PNG encode failed");
    downloadBlob(blob, filename || optsFilename("nova_page", "png"));
    return blob;
  }

  async function exportWorkspacePdf(workspaceEl, filename) {
    // Reuse PNG composite → draw to JPEG via canvas → PDF.
    if (!workspaceEl) throw new Error("No plot workspace");
    const hosts = [...workspaceEl.querySelectorAll(".plotHost")];
    if (!hosts.length) throw new Error("No visible plots to export");
    const wsRect = workspaceEl.getBoundingClientRect();
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(wsRect.width * scale));
    canvas.height = Math.max(1, Math.round(wsRect.height * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (const host of hosts) {
      const gd = (host.data || host._fullLayout) ? host : (host.querySelector(".js-plotly-plot") || host);
      if (!gd || !global.Plotly) continue;
      try {
        const dataUrl = await plotlyToDataUrl(gd, {
          format: "jpeg",
          width: host.clientWidth || 640,
          height: host.clientHeight || 360,
          scale: 1,
        });
        const { img } = await loadImageSize(dataUrl);
        const rect = host.getBoundingClientRect();
        ctx.drawImage(
          img,
          (rect.left - wsRect.left) * scale,
          (rect.top - wsRect.top) * scale,
          rect.width * scale,
          rect.height * scale,
        );
      } catch (err) {
        console.warn("[NOVA] workspace PDF tile failed", err);
      }
    }
    const jpegUrl = canvas.toDataURL("image/jpeg", 0.92);
    const { width, height } = await loadImageSize(jpegUrl);
    const jpeg = new Uint8Array(await dataUrlToBlob(jpegUrl).arrayBuffer());
    const maxW = 792;
    const maxH = 612;
    const fit = Math.min(maxW / width, maxH / height, 1);
    const blob = jpegToPdfBlob(jpeg, width, height, Math.round(width * fit), Math.round(height * fit));
    downloadBlob(blob, filename || optsFilename("nova_page", "pdf"));
    return blob;
  }

  global.NovaShare = {
    downloadBlob,
    dataUrlToBlob,
    plotlyToDataUrl,
    exportPlotPng,
    copyPlotPng,
    exportPlotPdf,
    exportWorkspacePng,
    exportWorkspacePdf,
    optsFilename,
  };
})(window);
