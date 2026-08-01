from app.engine.theme import ERD_THEME_SCRIPT, inject_theme

EXPECTED_PALETTE = "'#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948'"


def test_theme_script_carries_exact_palette_order() -> None:
    assert EXPECTED_PALETTE in ERD_THEME_SCRIPT.replace(" ", "")


def test_theme_script_carries_id_marker() -> None:
    assert ERD_THEME_SCRIPT.startswith('<script id="erd-theme">')


def test_inject_theme_before_head_close() -> None:
    html = "<html><head></head><body></body></html>"
    injected = inject_theme(html)
    assert "registerTheme('erd'" in injected
    assert injected.index("registerTheme") < injected.index("</head>")


def test_inject_theme_is_idempotent() -> None:
    html = inject_theme("<html><head></head><body></body></html>")
    assert inject_theme(html) == html
