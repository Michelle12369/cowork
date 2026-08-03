"""跨規則共用的「回報筆數上限 + 摘要句」。修復 prompt 不能無限長——單一規則若每個違規都各出
一條訊息，量大時(見 M3:40 個 script src 違規 = 6670 字元、42 個非 erd 主題引數 = 5986 字元)
會把 repair prompt 灌爆，稀釋掉真正需要模型注意的訊號。句型統一比照 `console.py` 最早訂的
`_MAX_REPORTED_COLUMN_MISSES` 摘要寫法(`... and N more ... with the same root cause.`)，讓模型
在每一條被截斷的規則上看到一致的形狀。
"""


def cap_reported_messages(
    messages: list[str], max_reported: int, more_description: str
) -> list[str]:
    """`messages` 超過 `max_reported` 筆時只保留前 `max_reported` 筆,並加一句
    `... and N more {more_description}.` 摘要句取代其餘;未超過原樣回傳(新 list,不修改
    傳入的 `messages`)。呼叫端若同時需要完整、未截斷的清單(例如 `unconditional_errors` 要
    保留每一筆真實訊號),MUST 對原始 `messages` 另行處理——這個函式只管「要餵給模型看的那份」
    要多短。
    """
    if len(messages) <= max_reported:
        return list(messages)
    hidden_count = len(messages) - max_reported
    capped = messages[:max_reported]
    capped.append(f"... and {hidden_count} more {more_description}.")
    return capped
