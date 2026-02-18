# clean base image containing only comfyui, comfy-cli and comfyui-manager
FROM runpod/worker-comfyui:5.5.1-base

# install custom nodes into comfyui (first node with --mode remote to fetch updated cache)
# No registry-verified custom nodes found.
# The following custom nodes were listed under unknown_registry but have no aux_id (GitHub repo) and could not be resolved:
# - MarkdownNote (could not resolve installation source)
# - MarkdownNote (could not resolve installation source)
# - MarkdownNote (could not resolve installation source)

# download models into comfyui
RUN comfy model download --url https://huggingface.co/Comfy-Org/z_image/blob/main/split_files/diffusion_models/z_image_bf16.safetensors --relative-path models/diffusion_models --filename z_image_bf16.safetensors
RUN comfy model download --url https://huggingface.co/Comfy-Org/z_image_turbo/blob/main/split_files/text_encoders/qwen_3_4b.safetensors --relative-path models/text_encoders --filename qwen_3_4b.safetensors
RUN comfy model download --url https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors --relative-path models/vae --filename ae.safetensors

# copy all input data (like images or videos) into comfyui (uncomment and adjust if needed)
# COPY input/ /comfyui/input/

# copy handler and workflow files
COPY handler.py /handler.py
COPY example_workflow.json /comfyui/example_workflow.json

# Start ComfyUI server in the background and then start the RunPod serverless handler
CMD ["sh", "-c", "python -m comfyui.server --listen 0.0.0.0 --port 8188 & python /handler.py"]
