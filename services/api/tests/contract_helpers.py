from pathlib import Path
import re


CONTRACT_PATH = Path("contracts/http/api.yaml")
RESOURCE_SPEC_PATH = Path("spec/resource_spec.yaml")


def get_contract_operation_block(operation_id: str) -> str:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    pattern = rf"  - operation_id: {re.escape(operation_id)}\n(.*?)(?=\n  - operation_id: |\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"Operation block not found: {operation_id}")
    return match.group(0)


def get_resource_block(resource_id: str) -> str:
    text = RESOURCE_SPEC_PATH.read_text(encoding="utf-8")
    pattern = rf"  - id: {re.escape(resource_id)}\n(.*?)(?=\n  - id: |\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"Resource block not found: {resource_id}")
    return match.group(0)
