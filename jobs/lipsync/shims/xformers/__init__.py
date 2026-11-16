"""Shim: InfiniteTalk imports xformers only for plain attention on one GPU; PyTorch SDPA does the same."""
from . import ops  # noqa: F401
