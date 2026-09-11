"""把目前 process 環境變數合併寫進 `one-local.properties`(deepagent 預設讀的設定檔),
給只有 env vars 可用的機器(如 CI/容器)一鍵補一份, 讓 `run-deepagent.sh`/`generate.sh`
之類的腳本原樣執行, 不用改用法。

Key 清單的權威來源是 `app.config.Settings`(與 one.properties 範本同一份權威)。合併規則:
env 覆寫, 既有檔案裡 env 沒設的 key 原樣保留, 不在 Settings 裡的既有 key 也保留(附在檔尾)。
只印 key 名稱, NEVER 印值(裡面可能是 secrets)。

用法:
    uv run python scripts/env_to_properties.py              # 寫進 one-local.properties
    uv run python scripts/env_to_properties.py --dry-run    # 只印會寫哪些 key, 不寫檔
    uv run python scripts/env_to_properties.py --out other.properties
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# 以腳本方式執行時 sys.path[0] 是 scripts/ 而不是 cwd, 要自己把 service root 加進去才 import 得到 app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings, _parse_properties

HEADER_COMMENT_LINES = (
    "# 由 scripts/env_to_properties.py 從目前 process 環境變數產生, 每次執行覆寫, 不要手動編輯.",
    "# 本檔已在 .gitignore, 不會進版控.",
)

UNKNOWN_KEYS_COMMENT = "# 下列 key 不在目前的 Settings 定義中, 原樣保留."


def _default_out_path() -> Path:
    return Path(__file__).resolve().parent.parent / "one-local.properties"


def collect_env_values(environment: Mapping[str, str], keys: Sequence[str]) -> dict[str, str]:
    """只挑 Settings 認得的 key, 且值非空字串的; 保留 keys 給的順序."""
    collected: dict[str, str] = {}
    for key in keys:
        value = environment.get(key)
        if value:
            collected[key] = value
    return collected


def merge_properties(
    existing: Mapping[str, str], from_env: Mapping[str, str], ordered_keys: Sequence[str]
) -> tuple[dict[str, str], list[str]]:
    """按 ordered_keys 順序合併: env 有就用 env, 否則用既有值; 只收兩邊都有值的 key。
    回傳 (merged, unknown_keys) —— unknown_keys 是既有檔案裡不在 ordered_keys 的 key,
    順序照既有檔案原樣。"""
    merged: dict[str, str] = {}
    for key in ordered_keys:
        if key in from_env:
            merged[key] = from_env[key]
        elif key in existing:
            merged[key] = existing[key]
    unknown_keys = [key for key in existing if key not in ordered_keys]
    return merged, unknown_keys


def _guard_single_line(key: str, value: str) -> None:
    if "\n" in value:
        raise ValueError(f"value for {key} contains a newline; properties file is one line per key")


def render_properties(
    merged: Mapping[str, str],
    unknown_keys: Sequence[str],
    existing_unknown_values: Mapping[str, str],
) -> str:
    """組出完整檔案內容: header 註解 + merged(依傳入順序) + 未知 key(附註解, 附在檔尾)。"""
    lines = list(HEADER_COMMENT_LINES)
    for key, value in merged.items():
        _guard_single_line(key, value)
        lines.append(f"{key}={value}")
    if unknown_keys:
        lines.append(UNKNOWN_KEYS_COMMENT)
        for key in unknown_keys:
            value = existing_unknown_values[key]
            _guard_single_line(key, value)
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把目前環境變數合併寫進 one-local.properties(給只有 env vars 的機器用)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="輸出路徑(預設 deepagent-service/one-local.properties, 相對於本腳本位置解析, 不是 cwd)",
    )
    parser.add_argument("--dry-run", action="store_true", help="只印出會寫哪些 key, 不真的寫檔")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    out_path = args.out if args.out is not None else _default_out_path()

    ordered_keys = list(Settings.model_fields.keys())
    from_env = collect_env_values(os.environ, ordered_keys)
    existing = _parse_properties(out_path) if out_path.exists() else {}

    merged, unknown_keys = merge_properties(existing, from_env, ordered_keys)
    existing_unknown_values = {key: existing[key] for key in unknown_keys}
    rendered = render_properties(merged, unknown_keys, existing_unknown_values)

    env_names = [key for key in ordered_keys if key in from_env]
    kept_names = [
        key for key in ordered_keys if key in existing and key not in from_env
    ] + unknown_keys

    if env_names:
        print("從 env 寫入: " + ", ".join(env_names))
    else:
        print("no matching env vars")
    if kept_names:
        print("保留既有檔案: " + ", ".join(kept_names))

    if args.dry_run:
        print(f"--dry-run: 未寫入 {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"已寫入 {out_path}")


if __name__ == "__main__":
    main()
