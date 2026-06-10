import asyncio
import copy
import json
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode


COMFY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW_PATH = COMFY_ROOT / "qwen-image-unet-empty.json"
Z_IMAGE_WORKFLOW_PATH = COMFY_ROOT / "Z-Image文生图基础工作流_api.json"

KSAMPLER_NODE = "3"
POSITIVE_NODE = "6"
NEGATIVE_NODE = "7"
QWEN_IMAGE_LATENT_NODE = "58"
Z_IMAGE_LATENT_NODE = "13"

MAX_SEED = 2**64
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


class WorkflowApiError(Exception):
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


def load_workflow_template(path=DEFAULT_WORKFLOW_PATH):
    try:
        with Path(path).open("r", encoding="utf-8") as workflow_file:
            return json.load(workflow_file)
    except FileNotFoundError as exc:
        raise WorkflowApiError(500, f"workflow template not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowApiError(500, f"workflow template is invalid JSON: {path}", str(exc)) from exc


def apply_qwen_image_parameters(workflow, payload):
    return apply_workflow_parameters(workflow, payload, QWEN_IMAGE_LATENT_NODE)


def apply_z_image_parameters(workflow, payload):
    return apply_workflow_parameters(workflow, payload, Z_IMAGE_LATENT_NODE)


def apply_workflow_parameters(workflow, payload, latent_node):
    patched = copy.deepcopy(workflow)

    if "positive_prompt" in payload:
        _set_text(patched, POSITIVE_NODE, "positive_prompt", payload["positive_prompt"])
    if "negative_prompt" in payload:
        _set_text(patched, NEGATIVE_NODE, "negative_prompt", payload["negative_prompt"])

    seed = _parse_seed(payload.get("seed", None))
    _set_input(patched, KSAMPLER_NODE, "seed", seed)

    if "steps" in payload:
        _set_input(patched, KSAMPLER_NODE, "steps", _parse_int(payload["steps"], "steps", minimum=1))
    if "cfg" in payload:
        _set_input(patched, KSAMPLER_NODE, "cfg", _parse_float(payload["cfg"], "cfg", minimum=0.0))
    if "width" in payload:
        _set_input(patched, latent_node, "width", _parse_int(payload["width"], "width", minimum=1))
    if "height" in payload:
        _set_input(patched, latent_node, "height", _parse_int(payload["height"], "height", minimum=1))
    if "batch_size" in payload:
        _set_input(patched, latent_node, "batch_size", _parse_int(payload["batch_size"], "batch_size", minimum=1))

    return patched, seed


def build_image_results(history_entry, base_url):
    images = []
    outputs = history_entry.get("outputs", {})
    for output in outputs.values():
        for image in output.get("images", []):
            filename = image.get("filename", "")
            image_type = image.get("type", "output")
            subfolder = image.get("subfolder", "")
            query = urlencode(
                [
                    ("filename", filename),
                    ("type", image_type),
                    ("subfolder", subfolder),
                ]
            )
            images.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": image_type,
                    "url": f"{base_url.rstrip('/')}/view?{query}",
                }
            )
    return images


async def handle_qwen_image_run(request):
    return await _handle_workflow_run(request, DEFAULT_WORKFLOW_PATH, apply_qwen_image_parameters)


async def handle_z_image_run(request):
    return await _handle_workflow_run(request, Z_IMAGE_WORKFLOW_PATH, apply_z_image_parameters)


async def _handle_workflow_run(request, workflow_path, apply_parameters):
    from aiohttp import web
    from server import PromptServer

    try:
        payload = await _read_json_object(request)
        timeout_seconds = _parse_float(
            payload.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            "timeout",
            minimum=1.0,
        )
        workflow = load_workflow_template(workflow_path)
        workflow, seed = apply_parameters(workflow, payload)
        prompt_id = _parse_prompt_id(payload.get("prompt_id"))

        server = PromptServer.instance
        await _submit_prompt(server, workflow, prompt_id, payload.get("client_id"))
        history_entry = await _wait_for_history(server, prompt_id, timeout_seconds)

        status = history_entry.get("status") or {}
        response = {
            "prompt_id": prompt_id,
            "seed": seed,
            "status": status,
            "images": build_image_results(history_entry, _request_base_url(request)),
        }
        if status.get("status_str") == "error" or status.get("completed") is False:
            return web.json_response(response, status=500)
        return web.json_response(response)
    except WorkflowApiError as exc:
        body = {"error": exc.message}
        if exc.details is not None:
            body["details"] = exc.details
        return web.json_response(body, status=exc.status)


def register_routes():
    from server import PromptServer

    if getattr(register_routes, "_registered", False):
        return

    routes = PromptServer.instance.routes

    @routes.post("/workflow-api/qwen-image/run")
    async def qwen_image_run(request):
        return await handle_qwen_image_run(request)

    @routes.post("/workflow-api/z-image/run")
    async def z_image_run(request):
        return await handle_z_image_run(request)

    register_routes._registered = True


async def _read_json_object(request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise WorkflowApiError(400, "request body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise WorkflowApiError(400, "request body must be a JSON object")
    return payload


async def _submit_prompt(server, workflow, prompt_id, client_id=None):
    import execution

    json_data = {
        "prompt": workflow,
        "prompt_id": prompt_id,
    }
    json_data = server.trigger_on_prompt(json_data)
    prompt = json_data["prompt"]
    server.node_replace_manager.apply_replacements(prompt)

    valid = await execution.validate_prompt(prompt_id, prompt, None)
    if not valid[0]:
        raise WorkflowApiError(400, "prompt validation failed", {"error": valid[1], "node_errors": valid[3]})

    extra_data = {"create_time": int(time.time() * 1000)}
    if client_id is not None:
        extra_data["client_id"] = str(client_id)

    number = server.number
    server.number += 1
    server.prompt_queue.put((number, prompt_id, prompt, extra_data, valid[2], {}))


async def _wait_for_history(server, prompt_id, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while True:
        history = server.prompt_queue.get_history(prompt_id=prompt_id)
        if prompt_id in history:
            return history[prompt_id]
        if time.monotonic() >= deadline:
            raise WorkflowApiError(504, "workflow execution timed out", {"prompt_id": prompt_id})
        await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)


def _request_base_url(request):
    return f"{request.scheme}://{request.host}"


def _parse_prompt_id(value):
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str) or value.strip() == "":
        raise WorkflowApiError(400, "prompt_id must be a non-empty string")
    return value


def _set_text(workflow, node_id, field_name, value):
    if not isinstance(value, str):
        raise WorkflowApiError(400, f"{field_name} must be a string")
    _set_input(workflow, node_id, "text", value)


def _set_input(workflow, node_id, input_name, value):
    try:
        workflow[node_id]["inputs"][input_name] = value
    except KeyError as exc:
        raise WorkflowApiError(500, f"workflow template is missing node {node_id} input {input_name}") from exc


def _parse_seed(value):
    if value is None:
        return secrets.randbelow(MAX_SEED)
    seed = _parse_int(value, "seed", minimum=0)
    if seed >= MAX_SEED:
        raise WorkflowApiError(400, f"seed must be less than {MAX_SEED}")
    return seed


def _parse_int(value, name, minimum=None):
    if isinstance(value, bool):
        raise WorkflowApiError(400, f"{name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise WorkflowApiError(400, f"{name} must be an integer") from exc
    else:
        raise WorkflowApiError(400, f"{name} must be an integer")

    if minimum is not None and parsed < minimum:
        raise WorkflowApiError(400, f"{name} must be >= {minimum}")
    return parsed


def _parse_float(value, name, minimum=None):
    if isinstance(value, bool):
        raise WorkflowApiError(400, f"{name} must be a number")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError as exc:
            raise WorkflowApiError(400, f"{name} must be a number") from exc
    else:
        raise WorkflowApiError(400, f"{name} must be a number")

    if minimum is not None and parsed < minimum:
        raise WorkflowApiError(400, f"{name} must be >= {minimum}")
    return parsed
