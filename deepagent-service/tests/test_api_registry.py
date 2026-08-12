"""app/engine/api_registry.py 的靜態 registry 內容與 validate_params 規則。"""

from app.engine.api_registry import API_REGISTRY, ApiDefinition, ApiParameter, validate_params


def test_registry_contains_two_mock_apis_with_api_prefixed_aliases():
    assert set(API_REGISTRY) == {"mock_orders", "mock_machines"}
    for definition in API_REGISTRY.values():
        assert definition.alias.startswith("api_")
        assert definition.response_format == "json-array"


def test_validate_params_valid_orders_params_returns_empty():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "30d", "machines": ["M1", "M3"]})
    assert errors == []


def test_validate_params_missing_required_reports_name():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "30d"})
    assert any("machines" in message for message in errors)


def test_validate_params_enum_out_of_range_rejected():
    definition = API_REGISTRY["mock_orders"]
    errors = validate_params(definition, {"date_range": "365d", "machines": ["M1"]})
    assert any("date_range" in message for message in errors)


def test_validate_params_multi_requires_list_and_single_rejects_list():
    definition = API_REGISTRY["mock_orders"]
    assert any(
        "machines" in message
        for message in validate_params(definition, {"date_range": "7d", "machines": "M1"})
    )
    assert any(
        "date_range" in message
        for message in validate_params(definition, {"date_range": ["7d"], "machines": ["M1"]})
    )


def test_validate_params_unknown_parameter_rejected():
    definition = API_REGISTRY["mock_machines"]
    errors = validate_params(definition, {"site": "TP", "bogus": 1})
    assert any("bogus" in message for message in errors)


def _single_parameter_definition(parameter: ApiParameter) -> ApiDefinition:
    return ApiDefinition(
        id="test_definition",
        alias="api_test",
        name="測試用 API",
        endpoint_path="/test",
        method="GET",
        parameters=(parameter,),
    )


def test_validate_params_number_type_accepts_int_and_float_rejects_bool():
    definition = _single_parameter_definition(
        ApiParameter(
            name="quantity", type="number", required=True, multi=False, prompt="要查詢的數量"
        )
    )
    assert validate_params(definition, {"quantity": 3}) == []
    assert validate_params(definition, {"quantity": 3.5}) == []
    errors = validate_params(definition, {"quantity": True})
    assert any("quantity" in message for message in errors)


def test_validate_params_date_type_validates_iso_format():
    definition = _single_parameter_definition(
        ApiParameter(name="as_of", type="date", required=True, multi=False, prompt="要查詢的日期")
    )
    assert validate_params(definition, {"as_of": "2026-08-12"}) == []
    errors = validate_params(definition, {"as_of": "12-08-2026"})
    assert any("as_of" in message for message in errors)


def test_validate_params_string_type_rejects_non_string():
    definition = _single_parameter_definition(
        ApiParameter(
            name="site", type="string", required=True, multi=False, prompt="要查詢的廠區代碼"
        )
    )
    assert validate_params(definition, {"site": "TP"}) == []
    errors = validate_params(definition, {"site": 123})
    assert any("site" in message for message in errors)
