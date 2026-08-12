"""API datasource 目錄——v1 兩支 mock API 的靜態定義與參數驗證。base-url 走 Settings
(API_MOCK_BASE_URL),registry 只存路徑段。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ApiParameter:
    name: str
    type: str  # string | number | date | enum
    required: bool
    multi: bool
    prompt: str  # 反問使用者時的問題文案素材
    options: tuple[str, ...] | None = None  # 有值→QUESTION 直接列選項
    options_source: str | None = None  # v2 動態候選值預留,v1 恆 None


@dataclass(frozen=True)
class ApiDefinition:
    id: str
    alias: str  # 掛進 session 後的表名,慣例帶 api_ 前綴(與上傳檔 alias 同空間)
    name: str
    endpoint_path: str
    method: str
    parameters: tuple[ApiParameter, ...]
    # 可判別 union 保留字:json-array(v1 唯一實作)| sql | custom(未實作,巢狀回應路線預留)
    response_format: str = "json-array"
    max_rows: int = 5000


API_REGISTRY: dict[str, ApiDefinition] = {
    "mock_orders": ApiDefinition(
        id="mock_orders",
        alias="api_orders",
        name="訂單查詢 API",
        endpoint_path="/orders",
        method="GET",
        parameters=(
            ApiParameter(
                name="date_range",
                type="enum",
                required=True,
                multi=False,
                prompt="要查詢的日期區間",
                options=("7d", "30d", "90d"),
            ),
            ApiParameter(
                name="machines",
                type="enum",
                required=True,
                multi=True,
                prompt="要查詢的機台",
                options=("M1", "M2", "M3", "M4"),
            ),
        ),
    ),
    "mock_machines": ApiDefinition(
        id="mock_machines",
        alias="api_machines",
        name="機台清單 API",
        endpoint_path="/machines",
        method="GET",
        parameters=(
            ApiParameter(
                name="site",
                type="string",
                required=True,
                multi=False,
                prompt="要查詢的廠區代碼",
            ),
        ),
    ),
}


def _validate_scalar(parameter: ApiParameter, value: object) -> list[str]:
    if parameter.type == "enum":
        if not isinstance(value, str) or (
            parameter.options is not None and value not in parameter.options
        ):
            allowed = ", ".join(parameter.options or ())
            return [f"parameter {parameter.name!r}: {value!r} not in allowed options ({allowed})"]
        return []
    if parameter.type == "string":
        if not isinstance(value, str):
            return [f"parameter {parameter.name!r} must be a string, got {value!r}"]
        return []
    if parameter.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"parameter {parameter.name!r} must be a number, got {value!r}"]
        return []
    if parameter.type == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            return [f"parameter {parameter.name!r} must be an ISO date (YYYY-MM-DD), got {value!r}"]
        return []
    return [f"parameter {parameter.name!r} has unknown type {parameter.type!r}"]


def validate_params(definition: ApiDefinition, params: dict) -> list[str]:
    """回傳錯誤訊息清單,空 list＝合法。驗:未知參數、必填缺漏、multi 形狀、型別、enum 值域。"""
    errors: list[str] = []
    known_names = {parameter.name for parameter in definition.parameters}
    errors.extend(f"unknown parameter {name!r}" for name in params if name not in known_names)
    for parameter in definition.parameters:
        if parameter.name not in params:
            if parameter.required:
                errors.append(f"missing required parameter {parameter.name!r}")
            continue
        value = params[parameter.name]
        if parameter.multi:
            if not isinstance(value, list):
                errors.append(f"parameter {parameter.name!r} must be a list (multi-select)")
                continue
            scalar_values = value
        else:
            if isinstance(value, list):
                errors.append(f"parameter {parameter.name!r} must be a single value, not a list")
                continue
            scalar_values = [value]
        for scalar_value in scalar_values:
            errors.extend(_validate_scalar(parameter, scalar_value))
    return errors
