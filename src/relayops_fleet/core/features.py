"""Deterministic feature computation. NO LLM CALLS IN THIS MODULE.

Everything the segment agent reasons over is computed here first: days
lapsed and its bucket, VIP status (80th-percentile spend WITHIN the clinic),
visit count, lifetime spend.

The model never does arithmetic. It receives finished numbers and is told
they are authoritative — the pattern proven in relayops-agentic-cine, where
`build_plan()` runs before the strategist agent speaks.

See CLAUDE.md F-7.
"""
from __future__ import annotations

# TODO(F-7): implement. Port the feature half of relayops-prod
# `src/relayops/pipeline/segment_agent.py:86` (`compute_features`); leave the
# LangGraph `decide()`/`build_graph()` half behind — that is what ADK replaces.
