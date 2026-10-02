# GPU load for the live GPU-mode test: about two minutes of matmuls.
python - << 'PY'
import time
import torch
x = torch.randn(4096, 4096, device="cuda")
t = time.time()
while time.time() - t < 120:
    y = x @ x
torch.cuda.synchronize()
print("gpu load done", torch.cuda.get_device_name(0))
PY
