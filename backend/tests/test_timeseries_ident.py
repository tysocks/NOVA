import pytest

from app.services.timeseries import _safe_ident, _test_table_ident


def test_safe_ident_accepts_simple_and_qualified_names():
    assert _safe_ident("test_runs") == "test_runs"
    assert _safe_ident("public.ptf_runs") == "public.ptf_runs"


def test_safe_ident_rejects_invalid_names():
    with pytest.raises(ValueError):
        _safe_ident("bad-name")
    with pytest.raises(ValueError):
        _safe_ident("a.b.c")


def test_test_table_ident_defaults_to_test_runs():
    ident = _test_table_ident(None)
    assert "test_runs" in ident.strings
