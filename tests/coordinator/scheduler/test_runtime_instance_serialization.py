# -*- coding: utf-8 -*-
"""Verify behavior of _instance_to_dict when instance is None.

Conclusion: `if instance else {}` is NECESSARY.
- Without it: None.model_dump() raises AttributeError.
- Call site refresh_instances receives list[Instance] from HTTP; malformed JSON could yield [inst, None].
- get_available_instances uses dict.values() which never yields None, but guard is defensive.
"""

import pytest

from motor.common.resources.instance import Instance
from motor.coordinator.scheduler.runtime.scheduler_client import _instance_to_dict
from motor.coordinator.scheduler.runtime.scheduler_server import _instance_to_dict as _instance_to_dict_server


def test_instance_model_dump_with_none_raises():
    """Without guard: None.model_dump() raises AttributeError."""
    instance = None
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'model_dump'"):
        instance.model_dump(mode="json")


def test_instance_to_dict_with_guard_returns_empty():
    """With guard: if instance else {} returns {} for None."""
    instance = None
    result = instance.model_dump(mode="json") if instance else {}
    assert result == {}


def test_instance_to_dict_func_handles_none():
    """_instance_to_dict(None) returns {} (guard is necessary)."""
    assert _instance_to_dict(None) == {}
    assert _instance_to_dict_server(None) == {}


def test_instance_to_dict_func_with_valid_instance():
    """_instance_to_dict(Instance) returns dict."""
    instance = Instance(
        job_name="test-job",
        model_name="test-model",
        id=1,
        role="prefill",
    )
    result = _instance_to_dict(instance)
    assert isinstance(result, dict)
    assert result["id"] == 1
    assert result["role"] == "prefill"


def test_list_comprehension_with_none_without_guard_would_fail():
    """Simulate refresh_instances: list with None would crash without guard."""
    instances = [
        Instance(job_name="a", model_name="m", id=1, role="prefill"),
        None,  # malformed data from HTTP could produce this
        Instance(job_name="b", model_name="m", id=2, role="decode"),
    ]
    # With guard (current _instance_to_dict): succeeds, None -> {}
    result = [_instance_to_dict(inst) for inst in instances]
    assert len(result) == 3
    assert result[0]["id"] == 1
    assert result[1] == {}  # None became {}
    assert result[2]["id"] == 2
