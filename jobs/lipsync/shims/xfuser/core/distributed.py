"""Shim: single-GPU runs never use sequence parallelism."""
def get_sequence_parallel_rank():
    return 0


def get_sequence_parallel_world_size():
    return 1


def get_sp_group():
    raise NotImplementedError("no sequence parallel group (single GPU)")


def initialize_model_parallel(*a, **k):
    raise NotImplementedError


def init_distributed_environment(*a, **k):
    raise NotImplementedError
