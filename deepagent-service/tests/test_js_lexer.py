"""lexer 狀態機的特徵測試——釘住搬遷前的既有行為，供 html_guard 拆 package 時當安全網。

刻意獨立成檔（不放 test_html_guard.py）：這些測到的是私有函式，拆 package 後 import
路徑會變，隔離在此可讓 test_html_guard.py 的頂層 import 完全不動。
"""

from app.engine.html_guard.js_lexer import (
    extract_inline_scripts_with_lines,
    find_script_end,
    mask_strings_and_comments,
)


def test_find_script_end_block_comment_containing_close_tag_is_not_a_terminator() -> None:
    html = "var a=1; /* fake </script> here */ var b=2;</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_unterminated_block_comment_returns_full_length() -> None:
    html = "var a=1; /* never closed </script>"

    assert find_script_end(html, 0) == len(html)


def test_mask_strings_and_comments_blanks_block_comment_body_keeping_delimiters() -> None:
    text = "const a = 1; /* xyz */ const b = 2;"

    masked = mask_strings_and_comments(text)

    assert masked == "const a = 1; /*     */ const b = 2;"
    assert len(masked) == len(text)


def test_mask_strings_and_comments_blanks_template_literal_body() -> None:
    text = "const s = `hello {x}`; const c = 3;"

    masked = mask_strings_and_comments(text)

    assert masked == "const s = `         `; const c = 3;"
    assert len(masked) == len(text)


# -- C3: regex literal 狀態機 -------------------------------------------------------------


def test_find_script_end_regex_literal_containing_quote_does_not_open_fake_string() -> None:
    """C3 repro:`name.replace(/'/g, '')` 裡 regex 內的撇號過去會被誤判成開了一個永不閉合
    的字串,吃掉真正的 `</script>` 終止符。加了 regex literal 狀態之後,規律的 `'` 只是
    regex 內文的一部分,不影響狀態機找到真正的終止符。"""
    html = "const clean = name.replace(/'/g, '');</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_regex_literal_containing_double_quote_does_not_open_fake_string() -> None:
    html = 'const clean = name.replace(/"/g, "");</script>tail'

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_division_is_not_swallowed_as_regex() -> None:
    """相反方向:`a / b / c` 是連續兩次除法,不能被誤判成 regex literal 開頭(把後面的除法
    運算子當成 regex 的收尾 `/`,吃掉中間內容)。"""
    html = "const result = a / b / c;</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_regex_after_return_keyword_is_recognized() -> None:
    """`return` 以識別字字元結尾,但語法上一定接表達式——regex context 的例外關鍵字清單要
    覆蓋到它,否則 `_is_regex_context` 的預設「識別字結尾→除法」會誤判。"""
    html = "function test() { return /'/.test(x); }</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_closing_html_tags_are_not_misread_as_regex_start() -> None:
    """`<`/`>` 刻意排除在「期待表達式」之外——這兩個函式也被套用在整份 HTML(含標籤)上,
    `</div>`、`</p>` 這類收尾標籤裡的 `/` 前一個字元就是 `<`,不能被判成 regex 開頭,否則
    regex 狀態會一路吃掉後面的標籤與程式碼,重現一次「後面全部消失」。"""
    html = "<div>content</div><script>const x = 1;</script>tail"

    end_index = find_script_end(html, html.index("<script>") + len("<script>"))

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_regex_after_arrow_function_is_recognized() -> None:
    """`=>` 收尾的 `>` 落在 `)]<>` 排除規則裡,但箭頭函式後面一定接表達式——`>` 前一個字元是
    `=` 時要判成 regex context,不能落入「`>`→除法」的預設分支,否則 regex 內的 `'` 會被誤判
    開了一個永不閉合的字串,吃掉真正的 `</script>` 終止符。"""
    html = "const clean = s => /'/.test(s);</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_mask_strings_and_comments_blanks_regex_literal_body_keeping_delimiters() -> None:
    text = "const clean = name.replace(/'/g, '');"

    masked = mask_strings_and_comments(text)

    assert masked == "const clean = name.replace(/ /g, '');"  # '' 內文為空,沒東西可遮罩。
    assert len(masked) == len(text)


def test_mask_strings_and_comments_division_is_not_blanked_as_regex() -> None:
    text = "const result = a / b / c;"

    masked = mask_strings_and_comments(text)

    assert masked == text  # 純除法,沒有字串/註解/regex 可遮罩,原文不動。


def test_find_script_end_regex_character_class_containing_slash_is_not_a_terminator() -> None:
    """regex literal 內的 character class(`[...]`)可以合法包含 `/` 與 `'`,不算 regex 的收尾
    `/`——沒有 character-class 追蹤的話,`[/']` 裡的第一個 `/` 會被誤判成收尾,剩下的 `']` 落回
    NORMAL 狀態,`'` 開了一個永不閉合的字串,吃掉真正的 `</script>`。"""
    html = "const pattern = /[/']/.test(x);</script>tail"

    end_index = find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_mask_strings_and_comments_blanks_regex_body_across_character_class() -> None:
    text = "const pattern = /[/']/.test(x);"

    masked = mask_strings_and_comments(text)

    assert masked == "const pattern = /[  ]/.test(x);"  # class 內文(含 '/')整段被遮罩。
    assert len(masked) == len(text)


def test_extract_inline_scripts_regex_with_quote_does_not_hide_later_script_blocks() -> None:
    """C3 的 blast radius:修復前,第一個 block 裡的 regex 撇號會讓 `find_script_end` 回傳
    `len(html)`,`search_from` 被推到檔尾,第二個 `<script>` block 從此對 guard 不存在。"""
    html = (
        "<html><body>"
        "<script>const clean = name.replace(/'/g, '');</script>"
        "<script>const second = 2;</script>"
        "</body></html>"
    )

    scripts = extract_inline_scripts_with_lines(html)

    assert len(scripts) == 2
    assert "clean" in scripts[0][0]
    assert "second" in scripts[1][0]
