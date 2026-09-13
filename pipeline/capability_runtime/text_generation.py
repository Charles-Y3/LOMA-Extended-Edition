# -*- coding: utf-8 -*-

"""Text generation via role pipeline (capabilities that stream to chat/artifact)."""

from __future__ import annotations



from typing import Any, Callable



from pipeline.capability_runtime.role_pipeline import RolePipelineConfig, run_role_pipeline

from pipeline.roles.registry import resolve_roles





def run_capability_text_generation(

    *,

    capability_id: str,

    execution_mode: str,

    base_system_instruction: str,

    profile: dict,

    model: str,

    user_query: str,

    user_message: str,

    sink: Any,

    is_cancelled: Callable[[], bool],

    stream_final: bool = True,

    has_context: bool = False,

) -> str:

    role_ids = [

        r.id

        for r in resolve_roles(user_query, capability_id, has_context=has_context)

    ]

    messages = [

        {"role": "system", "content": base_system_instruction},

        {"role": "user", "content": user_message},

    ]

    cfg = RolePipelineConfig(

        mode=execution_mode,

        base_system_instruction=base_system_instruction,

        profile=profile,

        model=model,

        user_query=user_query,

        role_ids=role_ids,

        messages=messages,

        sink=sink,

        is_cancelled=is_cancelled,

        stream_final=stream_final,

        capability_id=capability_id,

    )

    return run_role_pipeline(cfg)

