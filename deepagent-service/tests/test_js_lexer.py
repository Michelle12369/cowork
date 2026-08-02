"""lexer 狀態機的特徵測試——釘住搬遷前的既有行為，供 html_guard 拆 package 時當安全網。

刻意獨立成檔（不放 test_html_guard.py）：這些測到的是私有函式，拆 package 後 import
路徑會變，隔離在此可讓 test_html_guard.py 的頂層 import 完全不動。
"""

from app.engine.html_guard.js_lexer import find_script_end, mask_strings_and_comments


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
