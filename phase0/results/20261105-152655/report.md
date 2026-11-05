# Phase 0 spike report (2026-11-05T16:36:12Z)

Instance price: $0.578/h. Wall time so far: 0.2 min ≈ $0.00.

| Engine | Variant | Task | OK/fail | Load s | Median s/clip | Cold 1st clip s | $/clip |
|---|---|---|---|---|---|---|---|
| A | nvfp4 | flf2v | 0/1 | 82.16 | None | None | None |
| A | nvfp4 | i2v | 5/0 | 82.16 | 12.219999999999999 | 61.66 | 0.002 |

## Downloads

- Wan-AI/Wan2.2-I2V-A14B / base (VAE, T5, tokenizer, configs): ? GB in 196.0 s
- lightx2v/LightWan2.2-A14B / Wan2.2-I2V-A14B_NVFP4_Sparse_high.safetensors: 8.38 GB in 288.9 s
- lightx2v/LightWan2.2-A14B / Wan2.2-I2V-A14B_NVFP4_Sparse_low.safetensors: 8.38 GB in 744.0 s
- Wan-AI/Wan2.2-I2V-A14B / base (VAE, T5, tokenizer, configs): ? GB in 2.9 s
- lightx2v/LightWan2.2-A14B / Wan2.2-I2V-A14B_NVFP4_Sparse_high.safetensors: 8.38 GB in 0.4 s
- lightx2v/LightWan2.2-A14B / Wan2.2-I2V-A14B_NVFP4_Sparse_low.safetensors: 8.38 GB in 0.3 s

## Errors

- nvfp4 flf2v: ```t_info = self.runner.prepare_request(request_data)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/LightX2V/lightx2v/models/runners/wan/wan_runner.py", line 101, in prepare_request
    input_info = super().prepare_request(request_data)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/LightX2V/lightx2v/models/runners/base_runner.py", line 136, in prepare_request
    raise ValueError(f"Task {task!r} is not supported by this runner; expected one of: {task_names}")
ValueError: Task 'flf2v' is not supported by this runner; expected one of: i2v
```
