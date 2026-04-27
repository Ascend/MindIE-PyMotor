# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

"""SSE stream handling, token ID cache, and client-facing response stripping for recompute."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import msgspec
from fastapi import HTTPException, status

from motor.coordinator.models.constants import OpenAIField
from motor.coordinator.router.adapters.completion_to_chat import (
    adapt_completion_stream_chunk_to_chat,
    is_completion_like_stream_chunk,
)

from .common import (
    RECOMPUTE_BLOCKED_BY_POLICY_KEY,
    _recompute_log,
    extract_content_from_choice,
)


def _compact_json_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to compact UTF-8 JSON bytes (hot path: SSE chunk re-encode).

    Prefer :func:`msgspec.json.encode` over :func:`json.dumps` for lower CPU;
    fall back if the value is not encodable (exotic types).
    """
    try:
        return msgspec.json.encode(obj)
    except Exception:
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def parse_stream_chunk_json(chunk: bytes, logger: Any | None = None) -> dict | None:
    """Parse one SSE/data line to JSON; return None if not JSON object."""
    try:
        chunk_str = chunk.decode("utf-8").strip()
    except UnicodeDecodeError:
        if logger is not None:
            logger.debug("Skipping chunk: %s", chunk)
        return None

    if not chunk_str:
        return None

    if chunk_str.startswith("data: "):
        chunk_str = chunk_str[len("data: "):]

    try:
        return json.loads(chunk_str)
    except json.JSONDecodeError:
        if logger is not None:
            logger.debug("Skipping chunk str: %s", chunk_str)
        return None


def update_token_id_cache(request_info: dict, chunk_json: dict) -> None:
    """Accumulate ``return_token_ids`` response fields into ``request_info`` (mutates in place).

    - Root ``prompt_token_ids``: set ``cached_prompt_token_ids`` once (first non-null list).
    - ``choices[0].prompt_token_ids`` (Completion stream): promoted when root is absent.
    - ``choices[0].token_ids``: extend ``cached_output_token_ids`` when a list.
    """
    pti = chunk_json.get(OpenAIField.PROMPT_TOKEN_IDS)
    if pti is None:
        choices = chunk_json.get(OpenAIField.CHOICES) or []
        if choices and isinstance(choices[0], dict):
            pti = choices[0].get(OpenAIField.PROMPT_TOKEN_IDS)
    if (
        isinstance(pti, (list, tuple))
        and request_info.get("cached_prompt_token_ids") is None
    ):
        request_info["cached_prompt_token_ids"] = list(pti)

    choices = chunk_json.get(OpenAIField.CHOICES) or []
    if not choices:
        return
    c0 = choices[0]
    token_ids = c0.get(OpenAIField.TOKEN_IDS)
    if isinstance(token_ids, list):
        request_info.setdefault("cached_output_token_ids", []).extend(token_ids)


def fill_recompute_kv_from_token_cache(
    request_info: dict,
    logger: Any | None = None,
) -> None:
    """Set ``recompute_kv_transfer`` from ``cached_*`` for ``prepare_retry_request``.

    Raises:
        HTTPException: 502 if prompt/output cache cannot form a valid token retry.
    """
    log = logger if logger is not None else _recompute_log
    prompt = request_info.get("cached_prompt_token_ids")
    output = request_info.get("cached_output_token_ids") or []
    if not isinstance(prompt, list) or not prompt:
        log.error(
            "Recompute aborted: missing cached prompt_token_ids (enable return_token_ids on decode)."
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Recompute requires cached prompt_token_ids from stream or response; "
                "enable return_token_ids on decode requests."
            ),
        )
    if not isinstance(output, list):
        log.error(
            "Recompute aborted: cached_output_token_ids is not a list (token cache corrupt)."
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Recompute token cache corrupted: cached_output_token_ids is not a list.",
        )
    all_ids = list(prompt)
    all_ids.extend(output)
    request_info["recompute_kv_transfer"] = {
        "all_token_ids": all_ids,
        OpenAIField.PROMPT_TOKEN_IDS: list(prompt),
    }
    log.debug(
        "Recompute KV from token cache: prompt_len=%d output_len=%d all_len=%d",
        len(prompt),
        len(output),
        len(all_ids),
    )


def update_completion_tokens(request_info: dict, chunk_json: dict) -> None:
    """Update completion_tokens from stream chunk or non-stream usage.

    Stream: increments **once per SSE/JSON chunk** (not vLLM token count when
    ``stream_interval > 1``); do not use for ``prepare_retry_request`` budgeting.
    """
    usage = chunk_json.get(OpenAIField.USAGE, {})
    if request_info["stream_flag"]:
        request_info[OpenAIField.COMPLETION_TOKENS] += 1
    else:
        request_info[OpenAIField.COMPLETION_TOKENS] += usage.get(
            OpenAIField.COMPLETION_TOKENS, 0
        )


def strip_openai_token_id_fields_for_client(
    obj: dict,
    *,
    client_return_token_ids: bool = False,
) -> None:
    """Remove ``return_token_ids``-related fields before JSON is sent to the client (mutates ``obj``).

    When ``client_return_token_ids`` is ``True`` the token-id fields are kept
    (the client explicitly asked for them); only ``stop_reason`` normalisation
    is still applied unconditionally.
    """
    if not client_return_token_ids:
        obj.pop(OpenAIField.PROMPT_TOKEN_IDS, None)
    for ch in obj.get(OpenAIField.CHOICES) or []:
        if isinstance(ch, dict):
            if not client_return_token_ids:
                ch.pop(OpenAIField.TOKEN_IDS, None)
                ch.pop(OpenAIField.PROMPT_TOKEN_IDS, None)
            if ch.get("stop_reason") == "recomputed":
                ch["stop_reason"] = "stop"


def encode_stream_chunk_bytes(original_chunk: bytes, chunk_json: dict) -> bytes:
    """Re-serialize one SSE ``data:`` line or a raw JSON line after in-place edits to ``chunk_json``."""
    raw = original_chunk.decode("utf-8", errors="replace").strip()
    payload = _compact_json_bytes(chunk_json)
    if raw.startswith("data: "):
        line_b = b"data: " + payload
    else:
        line_b = payload
    if original_chunk.endswith(b"\r\n\r\n"):
        suffix = b"\r\n\r\n"
    elif original_chunk.endswith(b"\n\n"):
        suffix = b"\n\n"
    elif original_chunk.endswith(b"\r\n"):
        suffix = b"\r\n"
    elif original_chunk.endswith(b"\n"):
        suffix = b"\n"
    else:
        suffix = b""
    return line_b + suffix


def strip_stream_chunk_bytes_for_client(
    chunk: bytes,
    *,
    client_return_token_ids: bool = False,
) -> bytes:
    """Strip token id fields from one stream chunk (SSE or raw JSON line)."""
    chunk_json = parse_stream_chunk_json(chunk, logger=None)
    if chunk_json is None:
        try:
            text = chunk.decode("utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if "[DONE]" in text:
            return chunk
        return b""
    strip_openai_token_id_fields_for_client(chunk_json, client_return_token_ids=client_return_token_ids)
    return encode_stream_chunk_bytes(chunk, chunk_json)


def strip_nonstream_response_body_for_client(
    body: dict,
    *,
    client_return_token_ids: bool = False,
) -> None:
    """Strip token id fields from a non-streaming OpenAI-style JSON body (mutates ``body``)."""
    strip_openai_token_id_fields_for_client(body, client_return_token_ids=client_return_token_ids)


def _should_adapt_completion_stream_to_chat(
    entry_api: str | None,
    req_id: str | None,
    chunk_json: dict[str, Any],
) -> bool:
    """True when Chat ingress should coerce Completion-shaped stream chunks."""
    if not entry_api:
        return False
    if "chat" not in entry_api:
        return False
    if not req_id:
        return False
    return is_completion_like_stream_chunk(chunk_json)


def process_stream_chunk(
    chunk: bytes,
    request_info: dict,
    _req_data: dict,
    *,
    retry_count: int,
    logger: Any | None = None,
    nonstream_retry_patch: Callable[[dict, dict], None] | None = None,
    entry_api: str | None = None,
    stream_adapter_state: dict[str, Any] | None = None,
    req_id: str | None = None,
    recompute_enabled: bool = True,
    prompt_tokens_details: dict | None = None
) -> bytes | None:
    """Process one decode stream chunk.

    Returns:
        ``None`` if recompute is signaled (do not yield chunk to client).
        Otherwise bytes to forward (may differ from input for non-stream retry shaping).

    ``nonstream_retry_patch`` (choice, request_info): mutates choice for non-stream retry
    (e.g. accumulate ``total_generated_token`` on the router, then set message/text).

    When ``recompute_enabled`` is ``False``, skips ``update_token_id_cache`` (saves work
    when ``return_token_ids`` is off). On ``recomputed``, sets
    ``RECOMPUTE_BLOCKED_BY_POLICY_KEY`` on ``request_info`` and returns ``None`` without
    ``fill_recompute_kv_from_token_cache``.
    """
    chunk_json = parse_stream_chunk_json(chunk, logger)
    if chunk_json is None:
        try:
            text = chunk.decode("utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if "[DONE]" in text:
            return chunk
        if logger is not None:
            logger.debug("Dropping non-JSON decode stream chunk (Coordinator safety)")
        return b""

    if prompt_tokens_details:
        if chunk_json.get(OpenAIField.USAGE, {}):
            chunk_json[OpenAIField.USAGE]["prompt_tokens_details"] = prompt_tokens_details

    if recompute_enabled:
        update_token_id_cache(request_info, chunk_json)

    sta = stream_adapter_state if stream_adapter_state is not None else {}
    # Chat ingress must see chat.completion.chunk; adapt when engine emits
    # Completion stream chunks (e.g. text_completion / choices[].text), including
    # first decode when return_token_ids or stack behavior yields Completion shape.
    if _should_adapt_completion_stream_to_chat(entry_api, req_id, chunk_json):
        adapt_completion_stream_chunk_to_chat(
            chunk_json,
            req_id=req_id,
            stream_state=sta,
        )

    _crti = request_info.get("client_return_token_ids", False)

    choices = chunk_json.get(OpenAIField.CHOICES, [])
    if not choices:
        strip_openai_token_id_fields_for_client(chunk_json, client_return_token_ids=_crti)
        return encode_stream_chunk_bytes(chunk, chunk_json)

    choice0 = choices[0]
    request_info["generated_token"] += extract_content_from_choice(choice0)
    update_completion_tokens(request_info, chunk_json)

    if choice0.get("stop_reason") == "recomputed":
        if not recompute_enabled:
            request_info[RECOMPUTE_BLOCKED_BY_POLICY_KEY] = True
            return None
        fill_recompute_kv_from_token_cache(request_info, logger=logger)
        return None

    if retry_count > 0 and not request_info["stream_flag"]:
        if nonstream_retry_patch is not None:
            nonstream_retry_patch(choice0, request_info)
        strip_openai_token_id_fields_for_client(chunk_json, client_return_token_ids=_crti)
        return _compact_json_bytes(chunk_json)

    strip_openai_token_id_fields_for_client(chunk_json, client_return_token_ids=_crti)
    return encode_stream_chunk_bytes(chunk, chunk_json)
