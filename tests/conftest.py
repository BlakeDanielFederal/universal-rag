"""Test-suite defaults.

The production default reranker is a cross-encoder (sentence-transformers), which
would download a ~568MB model and run torch inference on every retrieval. Force the
no-op backend for the suite so tests stay fast and hermetic; the dedicated
cross-encoder test opts back in explicitly.
"""

import os

os.environ.setdefault("RERANK_BACKEND", "none")
