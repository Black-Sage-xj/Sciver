"""Judge contract and repair prompts expose the validator's exact literals."""
import json

import pytest

from theory_master.contracts import role_contract
from theory_master.prompt_stages import build_contract_repair_prompt


def test_judge_prompt_schema_has_only_literal_enum_values():
    contract = role_contract("judge", phase="terminal_assessment")
    assert contract.prompt_schema["verdict"].split("|") == [
        "supported", "rejected", "milestone_advanced", "open", "null",
    ]
    projection = contract.prompt_projection({})
    assert '"open"' in projection
    assert "open for terminal" not in projection


@pytest.mark.parametrize("verdict", ["supported", "rejected", "milestone_advanced", "open"])
def test_terminal_literals_keep_their_existing_judge_authority(verdict):
    contract = role_contract("judge", phase="terminal_assessment")
    contract.validate({"verdict": verdict, "rationale": "A bounded scientific conclusion."}, {})


@pytest.mark.parametrize("verdict", ["open for terminal", "open for terminal;", "terminal_open", "open_for_terminal"])
def test_invalid_enum_repair_names_the_exact_supported_literals(verdict):
    contract = role_contract("judge", phase="terminal_assessment")
    with pytest.raises(ValueError) as raised:
        contract.validate({"verdict": verdict, "rationale": "A bounded scientific conclusion."}, {})
    message = str(raised.value)
    assert '"open"' in message
    assert "JSON null" in message
    prompt = build_contract_repair_prompt(
        "terminal_assessment", [{"path": "/verdict", "message": message}],
        contract.prompt_projection({}), {}, repair_attempt=1, repair_limit=3,
    )
    assert "open for terminal" not in prompt
    assert json.dumps("open") in prompt
