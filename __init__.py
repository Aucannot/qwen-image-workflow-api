import importlib.util
import logging
from pathlib import Path


def _load_workflow_api_module():
    module_path = Path(__file__).with_name("workflow_api.py")
    spec = importlib.util.spec_from_file_location("comfyui_workflow_api", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    _workflow_api = _load_workflow_api_module()
    _workflow_api.register_routes()
except Exception:
    logging.warning("Failed to register ComfyUI Workflow API routes", exc_info=True)


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
