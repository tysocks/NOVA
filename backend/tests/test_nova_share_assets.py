"""Smoke tests for NovaShare helpers (PDF/PNG utilities)."""
from __future__ import annotations

from pathlib import Path


def test_nova_share_js_exists_and_exports_api():
    root = Path(__file__).resolve().parents[1]
    path = root / "app" / "static" / "js" / "nova-share.js"
    text = path.read_text(encoding="utf-8")
    assert "NovaShare" in text
    for name in (
        "exportPlotPng",
        "copyPlotPng",
        "exportPlotPdf",
        "exportWorkspacePng",
        "exportWorkspacePdf",
        "jpegToPdfBlob",
    ):
        assert name in text


def test_implementation_plan_documents_four_pillars():
    root = Path(__file__).resolve().parents[2]
    plan = (root / "docs" / "implementation-refinement-plan.md").read_text(encoding="utf-8")
    for pillar in ("Share", "Accessibility", "Function", "Performance"):
        assert pillar in plan
