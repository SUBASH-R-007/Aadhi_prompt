import torch
from diffusers import LTXPipeline

print("Downloading and loading LTX-Video model (this will download ~10GB of weights)...")
ltx_pipeline = LTXPipeline.from_pretrained(
    "Lightricks/LTX-Video",
    torch_dtype=torch.bfloat16,
)
print("Model downloaded successfully!")
