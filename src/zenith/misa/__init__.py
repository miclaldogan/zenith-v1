"""Misa: the user-facing assistant on Zenith (offline, deterministic MVP).

Layers: intents (language detection + intent parsing) → retrieval (evidence-grounded facts from
the kernel's services) → render (TR/EN templates with provenance) → repl (presentation and
conversation state). No model provider is involved; every reply is marked deterministic.
"""
