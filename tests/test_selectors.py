from rubric_forecast.executor.selectors import selector_prefix


def test_selector_prefix_known() -> None:
    assert selector_prefix("mintWithSig") == "0xa63d55a7"
    assert selector_prefix("intakeReasoning") == "0x4ed1f275"
    assert selector_prefix("unknown") is None
