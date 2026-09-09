"""requirements.txt 是給 internal 環境的安裝來源,由 uv.lock 匯出。漂移時預設環境走
uv sync --frozen 完全無感,只有 internal 會裝到舊依賴——故在預設環境的測試就攔下。"""

import shutil
import subprocess
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = SERVICE_ROOT / "requirements.txt"

EXPORT_COMMAND = [
    "uv",
    "export",
    "--no-dev",
    "--no-hashes",
    "--no-emit-project",
    "--format",
    "requirements-txt",
]


def _package_lines(text: str) -> list[str]:
    """只留套件行。註解含當初的 export 指令字串,會因 -o 參數不同而有差異,與依賴內容無關。"""
    return [line for line in text.splitlines() if line and not line.lstrip().startswith("#")]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv 不在 PATH")
def test_requirements_txt_matches_uv_lock() -> None:
    exported = subprocess.run(
        EXPORT_COMMAND, cwd=SERVICE_ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert _package_lines(exported) == _package_lines(
        REQUIREMENTS_PATH.read_text(encoding="utf-8")
    ), (
        "requirements.txt 與 uv.lock 不同步，internal 環境會裝到舊依賴。重新匯出："
        "uv export --no-dev --no-hashes --format requirements-txt -o requirements.txt"
    )
