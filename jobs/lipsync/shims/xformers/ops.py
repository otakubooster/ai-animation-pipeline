import torch


class _Fmha:
    class attn_bias:
        class BlockDiagonalMask:
            @staticmethod
            def from_seqlens(*a, **k):
                raise NotImplementedError("sequence parallel is not supported in this shim (single GPU only)")


fmha = _Fmha()


def memory_efficient_attention(q, k, v, attn_bias=None, op=None, **_):
    """xformers layout is [B, M, H, K]; SDPA wants [B, H, M, K]."""
    if attn_bias is not None:
        raise NotImplementedError("attn_bias not supported in this shim")
    out = torch.nn.functional.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2))
    return out.transpose(1, 2)
