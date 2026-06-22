"""Foundational facts — the fact-first cascade over the model
(foundational_facts_design.md).

reliability.py is the gate: a sensor must EARN bypass ('fact') status; failing it
is demoted to a model feature/hint, not discarded. The resolver (cascade:
override → gating facts → masked ML → rules → unknown) builds on this.
"""
