"""`_apply_erd_theme` 的直接單元測試——覆蓋 C1(掃描範圍限制在 `<script>` 區塊內、遮罩排除
字串/註解裡的假呼叫)與 M4(引數旁的註解含撇號不再讓呼叫被誤判成「括號不平衡」)。

刻意獨立成檔(不放 test_html_guard.py):這裡直接呼叫私有的 `_apply_erd_theme`,隔離掉整條
`check_dashboard_html` pipeline(Level 1/2 語法與 sandbox 檢查)的干擾,才能精準測試改寫本身
的掃描範圍與括號配對邏輯，而不用另外滿足其他規則(tooltip、__ERD_RESULTS__ 讀取等)。
"""

from app.engine.html_guard.theme_rewrite import _UNBALANCED_CALL_ERROR, _apply_erd_theme

# -- C1:掃描範圍限制在 <script> 區塊內 + 遮罩排除字串/註解裡的假呼叫 ------------------


def test_real_call_string_literal_and_body_text_all_three_cases_diffed() -> None:
    """同一份文件裡放齊三種情境:HTML body 可見文字裡的 `echarts.init(`、JS 字串字面值裡的
    `echarts.init(`、真正的呼叫——只有最後一種該被改寫,前兩種必須逐字不動。"""
    html = (
        "<html><body>"
        "<p>使用 echarts.init(el) 建立圖表</p>"
        "<script>"
        "const hint = 'call echarts.init(el) to create a chart';"
        "const chart = echarts.init(document.getElementById('chart'));"
        "</script>"
        "</body></html>"
    )

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert errors == []
    # HTML body 可見文字逐字不動 -- 不在 <script> 區塊內,根本不進掃描範圍。
    assert "<p>使用 echarts.init(el) 建立圖表</p>" in rewritten
    # JS 字串字面值裡的假呼叫逐字不動 -- 遮罩把它排除在呼叫點搜尋之外。
    assert "'call echarts.init(el) to create a chart'" in rewritten
    # 真正的呼叫被改寫,補上 'erd' 主題。
    assert "echarts.init(document.getElementById('chart'), 'erd')" in rewritten
    # 除了改寫的那一段,其餘字元逐字相同(用 diff 驗證只有真呼叫那段變了)。
    assert rewritten.replace(
        "echarts.init(document.getElementById('chart'), 'erd')", "PLACEHOLDER"
    ) == html.replace("echarts.init(document.getElementById('chart'))", "PLACEHOLDER")


def test_call_in_html_body_text_outside_script_is_never_touched() -> None:
    html = "<html><body><p>執行 echarts.init(el) 之後 chart 才會出現</p></body></html>"

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert rewritten == html
    assert errors == []


def test_call_inside_js_string_literal_is_never_touched() -> None:
    html = "<html><script>const hint = 'echarts.init(el)';</script></html>"

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert rewritten == html
    assert errors == []


def test_call_inside_js_block_comment_is_never_touched() -> None:
    html = "<html><script>/* echarts.init(el) */ const x = 1;</script></html>"

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert rewritten == html
    assert errors == []


def test_external_src_script_is_skipped_even_with_echarts_init_text() -> None:
    """`src=` 外部 script 一律跳過(不是這份 HTML 自己寫的 JS),即使內文剛好含
    `echarts.init(` 字樣也不掃描 -- 外部檔案的內容本來就不在 `extract_inline_script_spans`
    回傳的範圍內。"""
    html = '<html><script src="/vendor/echarts.min.js">echarts.init(fake)</script></html>'

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert rewritten == html
    assert errors == []


# -- M4:引數旁的註解含撇號不再讓呼叫被誤判成「括號不平衡」 --------------------------


def test_comment_with_apostrophe_after_call_still_gets_erd_theme() -> None:
    """M4 repro:`_find_matching_close_paren` 現在只在遮罩過的文字上掃描,`/* don't reuse */`
    這種註解裡的撇號在遮罩階段就已被清空,不會再讓括號配對誤判成不平衡、讓改寫被靜默跳過。
    """
    html = (
        "<html><script>"
        "echarts.init(document.getElementById('chart') /* don't reuse */);"
        "</script></html>"
    )

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert errors == []
    assert "echarts.init(document.getElementById('chart') /* don't reuse */, 'erd')" in rewritten


def test_backtick_template_literal_argument_still_gets_erd_theme() -> None:
    """`_find_matching_close_paren` 現在也不需要自己認 backtick -- 遮罩已經把 template
    literal 內文清空,對只認 `'`/`\"` 的舊版來說原本就是另一個潛在的誤判來源。"""
    html = "<html><script>echarts.init(document.getElementById(`chart-${id}`));</script></html>"

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert errors == []
    assert "echarts.init(document.getElementById(`chart-${id}`), 'erd')" in rewritten


# -- 畸形呼叫(真的括號不平衡)現在會記一條 error,不再靜默跳過 -----------------------


def test_genuinely_unbalanced_call_records_error_instead_of_silently_skipping() -> None:
    """真的括號不平衡(不是被字串/註解誤判)時,舊版原樣保留、零 error -- `ok` 因此可能仍是
    True,但主題其實沒套上。現在必須留下一條 error,讓呼叫端看得到「這裡沒套成功」。"""
    html = "<html><script>echarts.init(document.getElementById('chart');</script></html>"

    errors: list[str] = []
    rewritten = _apply_erd_theme(html, errors)

    assert errors == [_UNBALANCED_CALL_ERROR]
    # 畸形呼叫原樣保留,不強行改寫。
    assert "echarts.init(document.getElementById('chart');" in rewritten
