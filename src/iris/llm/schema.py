"""The LLM layer's data shapes: what goes into a prompt, what comes back.

Kept apart from the client for the reason every schema module is: the decision
shapes are what the tests pin, the drafts are what a human reviews, and neither
should need an HTTP client imported to be constructed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LLMDecision",
    "PluginDraft",
]


class PluginDraft(BaseModel):
    """A rule document the model proposes, as YAML text.

    ``yaml`` is the *text*, not parsed structure: parsing it here would mean the
    schema accepts half of the engine's grammar, and the grammar has one
    authority already (``iris.rules.engine``). The draft is validated by the
    engine's own loader when it is installed -- until then it is text a person
    reads, which is exactly what a draft is.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    stage: str
    description: str
    yaml: str


class LLMDecision(BaseModel):
    """What one model round trip decided, as a closed vocabulary.

    ``extra="forbid"`` for the reason every response model here gives: the
    default drops unknown fields silently, and a decision carrying a key nobody
    declared would arrive complete, validate clean, and do nothing.

    The action vocabulary is deliberately three values. The guardian's other
    recovery actions (watchdog, resource, diagnostic, web diagnosis) each take
    parameters derived from the serial-log pattern counts -- but a round trip
    only reaches the model when those counts produced no recommendation, so a
    model-nominated parameter set would be a *second* place that derives the
    same parameters from less evidence. What remains is exactly what runs
    without them: the container restart (the one repair that reaches a running
    guest), the plugin draft (which takes effect on the next run, after a
    person accepts it), and no action at all.
    """

    model_config = ConfigDict(extra="forbid")

    diagnosis: str
    action: Literal["NONE", "WEB_SERVER_RESTART", "DRAFT_PLUGIN"]
    plugin_draft: PluginDraft | None = None
    #: 0.0-1.0, the model's own claim. Displayed next to the decision, never
    #: acted on: a confidence number nobody reads is decoration, and one a
    #: workflow gates on is a second opinion pretending to be evidence.
    confidence: float = Field(ge=0.0, le=1.0)
    #: What to check after the action, in the model's own words. Advisory.
    verify_plan: list[str] = Field(default_factory=list)