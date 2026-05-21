# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from typing import Any

from motor.common.logger import get_logger

logger = get_logger(__name__)


class BaseConfigResolver:
    """Base resolver — not instantiated directly. Use the ConfigResolver() factory.

    Reads from both model_config (legacy) and engine_config (new).
    Priority: engine_config > model_config.
    When both define the same parameter with different values, a warning is logged.
    """

    _MAPPING: dict[str, str] = {}
    _warned_conflict_keys: set[str] = set()

    def __init__(self, engine_section: dict[str, Any]):
        self._model_cfg: dict[str, Any] = engine_section.get("model_config") or {}
        self._engine_cfg: dict[str, Any] = engine_section.get("engine_config") or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _warn_conflict(self, key, engine_val, model_val, model_source="model_config"):
        if engine_val is not None and model_val is not None and engine_val != model_val:
            if key not in BaseConfigResolver._warned_conflict_keys:
                logger.warning(
                    "Config conflict for '%s': engine_config=%s, %s=%s. Using engine_config.",
                    key, engine_val, model_source, model_val,
                )
                BaseConfigResolver._warned_conflict_keys.add(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a resolved value. Checks engine_config first, falls back to model_config."""
        engine_key = self._MAPPING.get(key, key)
        engine_val = self._engine_cfg.get(engine_key)
        model_val = self._model_cfg.get(key)

        self._warn_conflict(key, engine_val, model_val)

        if engine_val is not None:
            return engine_val
        if model_val is not None:
            return model_val
        return default

    def get_model_name(self, default: str = "") -> str:
        return self.get("model_name", default)

    def get_model_path(self, default: str = "") -> str:
        return self.get("model_path", default)

    def get_npu_mem_utils(self, default: float = 0.9) -> float:
        return self.get("npu_mem_utils", default)

    def get_parallel_config(self) -> dict[str, Any]:
        """Get resolved parallel configuration as a dict.

        Resolution order:
        1. Adapter-provided engine-specific keys via _resolve_engine_parallel_keys().
        2. model_config.parallel_config (legacy fallback).
        Warns when the same key exists in both sources with different values.
        """
        result: dict[str, Any] = {}
        result.update(self._resolve_engine_parallel_keys())

        legacy_parallel: dict[str, Any] = self._model_cfg.get("parallel_config") or {}
        for key, val in legacy_parallel.items():
            if key in result:
                self._warn_conflict(key, result[key], val, "model_config.parallel_config")
            else:
                result[key] = val

        # Always inject computed values, silently overriding user-supplied ones.
        result["local_world_size"] = self._compute_local_world_size(result)
        result["world_size"] = self._compute_world_size(result)

        return result

    def _compute_local_world_size(self, config: dict[str, Any]) -> int:
        """Compute local_world_size = pcp * tp * pp.

        Override in subclasses for engine-specific local-world-size semantics
        (e.g. when different engines calculate per-endpoint device count
        differently).
        """
        pcp = config.get("pcp_size", 1)
        tp = config.get("tp_size", 1)
        pp = config.get("pp_size", 1)
        return pcp * tp * pp

    def _compute_world_size(self, config: dict[str, Any]) -> int:
        """Compute world_size = dp * local_world_size = dp * pcp * tp * pp."""
        dp = config.get("dp_size", 1)
        return dp * self._compute_local_world_size(config)

    def _resolve_engine_parallel_keys(self) -> dict[str, Any]:
        """Override in adapters to map engine-native keys to Motor-internal keys.

        Returns a dict of {motor_internal_key: value} resolved from engine_config.
        """
        return {}

    def has_model_config(self) -> bool:
        """Check if model_config block exists (for deprecation detection)."""
        return bool(self._model_cfg)

    @property
    def model_config(self) -> dict[str, Any]:
        """Raw model_config dict (read-only, for backward compatibility)."""
        return self._model_cfg

    @property
    def engine_config(self) -> dict[str, Any]:
        """Raw engine_config dict."""
        return self._engine_cfg


# ------------------------------------------------------------------
# Engine-specific adapters
# ------------------------------------------------------------------

class VLLMConfigResolver(BaseConfigResolver):
    """Adapter: maps internal keys to vLLM-native engine_config keys."""

    _MAPPING = {
        "model_name": "served_model_name",
        "model_path": "model",
        "npu_mem_utils": "gpu_memory_utilization",
    }

    def _resolve_engine_parallel_keys(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        ec = self._engine_cfg

        if "data_parallel_size" in ec:
            result["dp_size"] = ec["data_parallel_size"]
        if "tensor_parallel_size" in ec:
            result["tp_size"] = ec["tensor_parallel_size"]
        if "pipeline_parallel_size" in ec:
            result["pp_size"] = ec["pipeline_parallel_size"]
        if "prefill_context_parallel_size" in ec:
            result["pcp_size"] = ec["prefill_context_parallel_size"]
        if "data_parallel_rpc_port" in ec:
            result["dp_rpc_port"] = ec["data_parallel_rpc_port"]
        if "enable_expert_parallel" in ec:
            result["enable_ep"] = ec["enable_expert_parallel"]
        if "cp_kv_cache_interleave_size" in ec:
            result["cp_kv_cache_interleave_size"] = ec["cp_kv_cache_interleave_size"]

        return result


class SGLangConfigResolver(BaseConfigResolver):
    """Adapter: maps internal keys to SGLang-native engine_config keys."""

    _MAPPING = {
        "model_name": "served-model-name",
        "model_path": "model-path",
        "npu_mem_utils": "mem-fraction-static",
    }

    def _resolve_engine_parallel_keys(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        ec = self._engine_cfg

        if "dp-size" in ec:
            result["dp_size"] = ec["dp-size"]
        if "tp-size" in ec:
            result["tp_size"] = ec["tp-size"]
        if "pp-size" in ec:
            result["pp_size"] = ec["pp-size"]

        cp_size = ec.get("context-parallel-size")
        cp_enabled = ec.get("enable-prefill-context-parallel", False)
        if cp_size and cp_enabled:
            result["pcp_size"] = cp_size

        return result


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

def ConfigResolver(
    engine_section: dict[str, Any],
    engine_type: str | None = None,
) -> BaseConfigResolver:
    """Factory: create the appropriate engine-specific config resolver.

    *engine_type* is normally read from the section; pass it explicitly only
    when the section dict doesn't carry ``engine_type`` itself.
    """
    if engine_type is None:
        engine_type = engine_section.get("engine_type")
    if not engine_type:
        logger.warning("engine_type not specified, defaulting to vllm")
        engine_type = "vllm"
    if engine_type == "sglang":
        return SGLangConfigResolver(engine_section)
    if engine_type != "vllm":
        logger.warning("unknown engine_type '%s', falling back to vllm", engine_type)
    return VLLMConfigResolver(engine_section)
