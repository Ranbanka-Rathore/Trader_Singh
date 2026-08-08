"""The research loop: register a hypothesis, screen it, walk it forward, kill it.

RESEARCH_CHARTER.md says what is allowed to count as evidence. This package is
the part that enforces it, so that discipline survives a bad week rather than
depending on remembering to be careful.

    python -m research.loop register --id ... --arena ... --claim ... --kill ...
    python -m research.loop run <id> [<id> ...]
    python -m research.loop list | show <id> | throughput

The metric for this package is throughput — hypotheses closed per week — not
returns. A loop that kills five bad ideas in an afternoon is working.
"""
