"""chevron 0.14.0, a Mustache renderer by Noah Morrison, MIT license (see LICENSE).

Vendored from https://files.pythonhosted.org/packages/source/c/chevron/chevron-0.14.0.tar.gz
(the sha256 of the archive is in VENDORED). Only renderer.py and tokenizer.py are kept,
unchanged. agentmap uses `render` only.
"""

from .renderer import render
from .tokenizer import ChevronError

__all__ = ["render", "ChevronError"]
