"""`check_dashboard_html` 的結果型別與最底層的結構/體積檢查。"""

from dataclasses import dataclass, field

HTML_MAX_BYTES = 2_000_000


@dataclass
class GuardReport:
    """`check_dashboard_html` 的檢查結果。`unconditional_errors` 是 `errors` 的子集,只收
    「無論 `ERD_GUARD_BLOCKING` 是否關閉都不能出貨」的違規(見 `ChatTurn.finalize()`)——
    script src 白名單(唯一的遠端腳本邊界)與截斷偵測(缺 `</html>`、`finish_reason=="length"`)。"""

    ok: bool
    errors: list[str] = field(default_factory=list)
    unconditional_errors: list[str] = field(default_factory=list)
    html: str = ""


def check_structure(html: str, errors: list[str], unconditional_errors: list[str]) -> None:
    if not html or "<div" not in html:
        errors.append(
            "dashboard.html content is incomplete: missing HTML content or at least one <div> element."
        )
    # 每次 dashboard 修改都是單次完整 write_file(見 agent/prompts.py 的 SYSTEM_PROMPT),真實 dashboard
    # 量到 62855 bytes(約 18K tokens),對比模型輸出 budget 約 24K tokens——輸出在收尾前被
    # 腰斬是活生生的風險,而且腰斬點若剛好落在最後一個 </script> 之後,前面所有檢查都測不出
    # 異狀。要求 </html> 收尾標籤是最低成本的截斷偵測。
    if html and "</html>" not in html:
        error = (
            "dashboard.html content is incomplete: missing the closing </html> tag -- the "
            "output was likely truncated mid-generation. Please write the ENTIRE dashboard.html "
            "again in one write_file call, all the way through the closing </html> tag."
        )
        errors.append(error)
        # 截斷的文件 MUST 永不出貨——`ERD_GUARD_BLOCKING=false` 只讓其他規則變成建議性。
        unconditional_errors.append(error)


def check_size(html: str, errors: list[str]) -> None:
    byte_length = len(html.encode("utf-8"))
    if byte_length > HTML_MAX_BYTES:
        errors.append(
            f"dashboard.html is too large: {byte_length} bytes, exceeding the {HTML_MAX_BYTES} byte limit. "
            "Please trim the content (e.g. remove redundant comments, embedded data, or duplicate style definitions)."
        )
