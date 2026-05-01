"""Cloud adapter for VLM requests.

This module is the only place in the current system allowed to wrap
the legacy VLM demo utility.
"""

from __future__ import annotations


class VLMCloudAdapter:
    """Adapter that encapsulates the legacy demo VLM invocation."""

    def ask(self, messages: list) -> str | None:
        """Forward messages to the legacy VLM API wrapper."""
        from vlm.providers.vlm_utils import ask_visual_model

        return ask_visual_model(messages=messages)
