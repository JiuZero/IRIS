"""LLM-assisted attribution for the guardian's long tail.

The layer is three pieces, and the split is the design:

* :mod:`iris.llm.client` is one OpenAI-compatible round trip -- no conversation,
  no tool loop;
* :mod:`iris.llm.diagnose` tries the deterministic rule engine first and reaches
  the model only for the signals the pattern counts cannot name;
* :mod:`iris.llm.drafts` is where a model-authored rule waits for a person, and
  the only path by which it becomes an installed plugin goes through the same
  validation chain a browser upload takes.

Nothing here runs automatically and nothing here executes anything: the model's
decision is a proposal, the side effects are explicit endpoints, and an
unconfigured endpoint disables the whole layer rather than failing every
diagnosis on it.
"""

from iris.llm.schema import LLMDecision, PluginDraft

__all__ = [
    "LLMDecision",
    "PluginDraft",
]