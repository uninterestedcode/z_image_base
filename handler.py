"""
RunPod Serverless Handler for Z-Image ComfyUI

This handler processes image generation requests using the Z-Image model
through ComfyUI's API on RunPod's queue-based serverless infrastructure.
"""

import runpod
import json
import os
import time
import requests
import base64
import copy
import random
import logging
import signal
import sys
from io import BytesIO
from typing import Dict, List, Optional, Any

# Unbuffered stdout - flush after every write to ensure logs appear immediately
class Unbuffered:
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def flush(self):
        self.stream.flush()

sys.stdout = Unbuffered(sys.stdout)
sys.stderr = Unbuffered(sys.stderr)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
COMFYUI_API_URL = os.getenv("COMFYUI_API_URL", "http://127.0.0.1:8188")
MAX_RETRIES = 3
POLL_INTERVAL = 1
JOB_TIMEOUT = 300
WORKFLOW_FILE = "/comfyui/example_workflow.json"

# Subgraph node ID for Z-Image
SUBGRAPH_NODE_ID = "9b9009e4-2d3d-445f-9be5-6063f465757e"
SUBGRAPH_NODE_INDEX = 76  # The node index in the workflow
SAVE_IMAGE_NODE_INDEX = 9  # SaveImage node index

# Load default workflow on startup
DEFAULT_WORKFLOW = None

def load_default_workflow() -> Dict:
    """Load the default workflow from the workflow file."""
    global DEFAULT_WORKFLOW
    try:
        with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
            DEFAULT_WORKFLOW = json.load(f)
        logger.info(f"Successfully loaded default workflow from {WORKFLOW_FILE}")
        return DEFAULT_WORKFLOW
    except FileNotFoundError:
        logger.error(f"Workflow file {WORKFLOW_FILE} not found")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse workflow file: {e}")
        raise


def queue_prompt(workflow: Dict) -> Optional[str]:
    """
    Submit a workflow to ComfyUI for execution.
    
    Args:
        workflow: ComfyUI workflow dictionary
        
    Returns:
        prompt_id: The ID of the queued prompt, or None on failure
    """
    url = f"{COMFYUI_API_URL}/prompt"
    payload = {"prompt": workflow}
    
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Submitting workflow to ComfyUI (attempt {attempt + 1}/{MAX_RETRIES})")
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            prompt_id = result.get("prompt_id")
            
            if prompt_id:
                logger.info(f"Workflow submitted successfully. Prompt ID: {prompt_id}")
                return prompt_id
            else:
                logger.error("No prompt_id in response")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error(f"Failed to submit workflow after {MAX_RETRIES} attempts")
                return None


def get_history(prompt_id: str) -> Optional[Dict]:
    """
    Get the execution history for a prompt.
    
    Args:
        prompt_id: The ID of the prompt
        
    Returns:
        Execution history dictionary, or None on failure
    """
    url = f"{COMFYUI_API_URL}/history/{prompt_id}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get history for prompt {prompt_id}: {e}")
        return None


def wait_for_completion(prompt_id: str, timeout: int = JOB_TIMEOUT) -> Dict[str, Any]:
    """
    Wait for a prompt to complete execution.
    
    Args:
        prompt_id: The ID of the prompt
        timeout: Maximum time to wait in seconds
        
    Returns:
        Result dictionary with status and data
    """
    start_time = time.time()
    
    logger.info(f"Waiting for prompt {prompt_id} to complete (timeout: {timeout}s)")
    
    while time.time() - start_time < timeout:
        history = get_history(prompt_id)
        
        if history is None:
            logger.warning(f"Failed to get history for prompt {prompt_id}, retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        
        if prompt_id in history:
            prompt_data = history[prompt_id]
            status = prompt_data.get("status", {})
            
            if status.get("completed", False):
                logger.info(f"Prompt {prompt_id} completed successfully")
                return {
                    "status": "completed",
                    "data": prompt_data
                }
            elif status.get("str") == "execution error":
                error_msg = status.get("exception", "Unknown error")
                logger.error(f"Prompt {prompt_id} failed with error: {error_msg}")
                return {
                    "status": "error",
                    "error": error_msg
                }
        
        time.sleep(POLL_INTERVAL)
    
    logger.error(f"Prompt {prompt_id} timed out after {timeout}s")
    return {
        "status": "timeout",
        "error": f"Execution timed out after {timeout} seconds"
    }


def get_image(filename: str, subfolder: str = "", folder_type: str = "output") -> Optional[bytes]:
    """
    Retrieve an image from ComfyUI.
    
    Args:
        filename: Name of the image file
        subfolder: Subfolder path (default: "")
        folder_type: Type of folder (default: "output")
        
    Returns:
        Image bytes, or None on failure
    """
    url = f"{COMFYUI_API_URL}/view"
    params = {
        "filename": filename,
        "subfolder": subfolder,
        "type": folder_type
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to retrieve image {filename}: {e}")
        return None


def extract_images(result: Dict) -> List[Dict[str, Any]]:
    """
    Extract images from ComfyUI execution result.
    
    Args:
        result: Result dictionary from wait_for_completion
        
    Returns:
        List of image dictionaries with base64 data
    """
    images = []
    
    if result.get("status") != "completed":
        logger.warning(f"Cannot extract images from non-completed result: {result.get('status')}")
        return images
    
    try:
        data = result.get("data", {})
        outputs = data.get("outputs", {})
        
        # Find SaveImage node outputs
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img_info in node_output["images"]:
                    filename = img_info.get("filename")
                    subfolder = img_info.get("subfolder", "")
                    folder_type = img_info.get("type", "output")
                    
                    if filename:
                        logger.info(f"Retrieving image: {filename}")
                        image_bytes = get_image(filename, subfolder, folder_type)
                        
                        if image_bytes:
                            # Convert to base64
                            base64_data = base64.b64encode(image_bytes).decode('utf-8')
                            
                            images.append({
                                "filename": filename,
                                "subfolder": subfolder,
                                "type": folder_type,
                                "data": base64_data
                            })
                            logger.info(f"Successfully encoded image {filename} to base64")
                        else:
                            logger.error(f"Failed to retrieve image {filename}")
        
        logger.info(f"Extracted {len(images)} image(s) from result")
        return images
        
    except Exception as e:
        logger.error(f"Error extracting images: {e}")
        return images


def apply_workflow_overrides(workflow: Dict, overrides: Dict) -> Dict:
    """
    Apply parameter overrides to the workflow.
    
    Args:
        workflow: Original workflow dictionary
        overrides: Dictionary of parameters to override
        
    Returns:
        Modified workflow with overrides applied
    """
    # Create a deep copy to avoid modifying the original
    workflow = copy.deepcopy(workflow)
    
    # Find the subgraph node
    subgraph_node = None
    for node in workflow.get("nodes", []):
        if node.get("id") == SUBGRAPH_NODE_INDEX:
            subgraph_node = node
            break
    
    if not subgraph_node:
        logger.warning(f"Subgraph node {SUBGRAPH_NODE_INDEX} not found in workflow")
        return workflow
    
    widgets_values = subgraph_node.get("widgets_values", [])
    
    # Map override keys to widget indices
    # Based on the workflow structure:
    # [0]: text (prompt)
    # [1]: width
    # [2]: height
    # [3]: steps
    # [4]: cfg
    # [5]: seed
    # [6]: control_after_generate
    # [7]: unet_name
    # [8]: clip_name
    # [9]: vae_name
    
    override_map = {
        "prompt": 0,
        "width": 1,
        "height": 2,
        "steps": 3,
        "cfg": 4,
        "seed": 5,
        "unet_name": 7,
        "clip_name": 8,
        "vae_name": 9
    }
    
    # Apply overrides
    for key, value in overrides.items():
        if key in override_map:
            index = override_map[key]
            if index < len(widgets_values):
                old_value = widgets_values[index]
                widgets_values[index] = value
                logger.info(f"Override applied: {key} = {value} (was: {old_value})")
            else:
                logger.warning(f"Widget index {index} for {key} out of range")
        else:
            logger.warning(f"Unknown override parameter: {key}")
    
    # Handle seed generation
    if "seed" in overrides and overrides["seed"] is None:
        # Generate random seed if seed is None
        widgets_values[5] = random.randint(0, 2**32 - 1)
        logger.info(f"Generated random seed: {widgets_values[5]}")
    
    # Ensure control_after_generate is set to "randomize" for seed randomization
    if len(widgets_values) > 6:
        widgets_values[6] = "randomize"
    
    subgraph_node["widgets_values"] = widgets_values
    
    return workflow


def validate_input(event: Dict) -> tuple[bool, Optional[str]]:
    """
    Validate the input event.
    
    Args:
        event: RunPod event dictionary
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(event, dict):
        return False, "Event must be a dictionary"
    
    input_data = event.get("input", {})
    
    if not isinstance(input_data, dict):
        return False, "Event input must be a dictionary"
    
    # Validate overrides if provided
    overrides = input_data.get("overrides", {})
    if overrides:
        if not isinstance(overrides, dict):
            return False, "Overrides must be a dictionary"
        
        # Validate numeric parameters
        for param in ["width", "height", "steps", "cfg", "seed"]:
            if param in overrides and overrides[param] is not None:
                if not isinstance(overrides[param], (int, float)):
                    return False, f"Parameter '{param}' must be a number"
        
        # Validate prompt
        if "prompt" in overrides and overrides["prompt"] is not None:
            if not isinstance(overrides["prompt"], str):
                return False, "Parameter 'prompt' must be a string"
    
    return True, None


def handler(event: Dict) -> Dict:
    """
    Main RunPod serverless handler.
    
    Args:
        event: RunPod event dictionary containing:
            - input.workflow: ComfyUI workflow JSON (optional, uses default if not provided)
            - input.overrides: Dict to override workflow parameters (optional)
        
    Returns:
        RunPod queue format response:
        {
            "output": {
                "images": [...],
                "prompt_id": "...",
                "execution_time": ...
            },
            "error": None,
            "status": "COMPLETED"
        }
    """
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("New request received")
    logger.info(f"Event: {json.dumps(event, indent=2)}")
    
    # Validate input
    is_valid, error_msg = validate_input(event)
    if not is_valid:
        logger.error(f"Input validation failed: {error_msg}")
        return {
            "output": None,
            "error": f"Validation error: {error_msg}",
            "status": "FAILED"
        }
    
    input_data = event.get("input", {})
    
    # Get workflow (use provided or default)
    workflow = input_data.get("workflow")
    if workflow:
        logger.info("Using provided workflow from input")
    else:
        if DEFAULT_WORKFLOW is None:
            load_default_workflow()
        workflow = copy.deepcopy(DEFAULT_WORKFLOW)
        logger.info("Using default workflow")
    
    # Apply overrides
    overrides = input_data.get("overrides", {})
    if overrides:
        logger.info(f"Applying overrides: {overrides}")
        workflow = apply_workflow_overrides(workflow, overrides)
    
    # Submit workflow to ComfyUI
    prompt_id = queue_prompt(workflow)
    if not prompt_id:
        logger.error("Failed to submit workflow to ComfyUI")
        return {
            "output": None,
            "error": "Failed to submit workflow to ComfyUI",
            "status": "FAILED"
        }
    
    # Wait for completion
    result = wait_for_completion(prompt_id)
    
    if result["status"] == "timeout":
        return {
            "output": None,
            "error": result["error"],
            "status": "FAILED"
        }
    
    if result["status"] == "error":
        return {
            "output": None,
            "error": result["error"],
            "status": "FAILED"
        }
    
    # Extract images
    images = extract_images(result)
    
    execution_time = time.time() - start_time
    logger.info(f"Request completed in {execution_time:.2f}s")
    logger.info(f"Generated {len(images)} image(s)")
    
    return {
        "output": {
            "images": images,
            "prompt_id": prompt_id,
            "execution_time": round(execution_time, 2)
        },
        "error": None,
        "status": "COMPLETED"
    }


def handle_shutdown(signum, frame):
    """Handle graceful shutdown signals."""
    logger.info(f"Received shutdown signal {signum}, gracefully terminating...")
    sys.exit(0)


# Startup code - executed when module is imported (required for RunPod handler detection)
print("=== HANDLER.PY LOADING ===")
print("Starting RunPod serverless handler for Z-Image ComfyUI...")
print(f"ComfyUI API URL: {COMFYUI_API_URL}")
print(f"Job timeout: {JOB_TIMEOUT}s")
print(f"Poll interval: {POLL_INTERVAL}s")
print(f"Working directory: {os.getcwd()}")
print(f"Python version: {sys.version}")

# Load default workflow on startup
print("=== LOADING DEFAULT WORKFLOW ===")
try:
    load_default_workflow()
    print("Default workflow loaded successfully")
except Exception as e:
    print(f"Warning: Failed to load default workflow: {e}")
    print("Handler will require workflow in each request")

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# Start RunPod serverless handler
# This call must be at module level (not inside if __name__ == "__main__")
# for RunPod's GitHub scanner to detect it
print("=== STARTING RUNPOD SERVERLESS ===")
print("About to call runpod.serverless.start()")
try:
    runpod.serverless.start({"handler": handler})
    print("runpod.serverless.start() returned (should not happen in serverless mode)")
except Exception as e:
    print(f"Error starting RunPod serverless: {e}")
    import traceback
    traceback.print_exc()
