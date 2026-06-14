# comfy-workflow-api

ComfyUI custom node plugin that exposes blocking HTTP APIs for fixed workflow
templates. The API patches common generation parameters, queues the workflow,
waits for execution to finish, and returns ComfyUI `/view` download URLs.

## Endpoints

The plugin currently registers two synchronous endpoints:

```text
POST /workflow-api/qwen-image/run
POST /workflow-api/z-image/run
```

ComfyUI also mirrors custom routes under `/api`, so these usually work too:

```text
POST /api/workflow-api/qwen-image/run
POST /api/workflow-api/z-image/run
```

## Workflow Templates

The plugin reads workflow JSON files bundled inside this plugin:

```text
custom_nodes/comfy-workflow-api/workflows/qwen-image-unet-empty.json
custom_nodes/comfy-workflow-api/workflows/z-image-txt2img.json
```

Restart ComfyUI after installing or changing this plugin. Custom API routes are
registered at startup.

## Request Body

All fields are optional unless your business layer decides otherwise. Missing
fields keep the workflow default, except `seed`, which is randomized when it is
not provided.

```json
{
  "positive_prompt": "Young Chinese woman in red Hanfu",
  "negative_prompt": "low quality, blurry",
  "seed": 123456,
  "steps": 40,
  "cfg": 4,
  "width": 1024,
  "height": 1024,
  "batch_size": 1,
  "timeout": 600
}
```

Parameter mapping:

| Field | Qwen Image Node | Z-Image Node |
| --- | --- | --- |
| `positive_prompt` | `6.inputs.text` | `6.inputs.text` |
| `negative_prompt` | `7.inputs.text` | `7.inputs.text` |
| `seed` | `3.inputs.seed` | `3.inputs.seed` |
| `steps` | `3.inputs.steps` | `3.inputs.steps` |
| `cfg` | `3.inputs.cfg` | `3.inputs.cfg` |
| `width` | `58.inputs.width` | `13.inputs.width` |
| `height` | `58.inputs.height` | `13.inputs.height` |
| `batch_size` | `58.inputs.batch_size` | `13.inputs.batch_size` |

## Examples

Qwen Image:

```bash
curl -X POST http://127.0.0.1:8192/workflow-api/qwen-image/run \
  -H 'Content-Type: application/json' \
  -d '{
    "positive_prompt": "一个中国美女拿着马克笔微笑",
    "negative_prompt": "low quality, blurry",
    "steps": 20,
    "cfg": 2.5,
    "width": 1328,
    "height": 1328,
    "batch_size": 1
  }'
```

Z-Image:

```bash
curl -X POST http://127.0.0.1:8192/workflow-api/z-image/run \
  -H 'Content-Type: application/json' \
  -d '{
    "positive_prompt": "Young Chinese woman in red Hanfu, intricate embroidery",
    "negative_prompt": "low quality, blurry",
    "steps": 40,
    "cfg": 4,
    "width": 1024,
    "height": 1024,
    "batch_size": 1
  }'
```

## Response

The endpoint blocks until the workflow finishes or `timeout` is reached.

```json
{
  "prompt_id": "7f6e8e9a-5d18-4a2b-a4ed-4dcb5e7a1d9b",
  "seed": 123456,
  "status": {
    "status_str": "success",
    "completed": true,
    "messages": []
  },
  "images": [
    {
      "filename": "ComfyUI_00001_.png",
      "subfolder": "",
      "type": "output",
      "url": "http://127.0.0.1:8192/view?filename=ComfyUI_00001_.png&type=output&subfolder="
    }
  ]
}
```

Download the first generated image:

```bash
curl -L "http://127.0.0.1:8192/view?filename=ComfyUI_00001_.png&type=output&subfolder=" -o result.png
```

If you call ComfyUI from another machine, use the server IP or domain in the API
URL. The plugin builds image URLs from the request host.

## Troubleshooting

Check that ComfyUI is reachable:

```bash
curl -i http://127.0.0.1:8192/system_stats
```

Check API status and timing:

```bash
curl -i -sS -w '\nHTTP=%{http_code} time=%{time_total}\n' \
  -X POST http://127.0.0.1:8192/workflow-api/z-image/run \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Common cases:

- `404`: restart ComfyUI, or check that this plugin directory is under `custom_nodes/`.
- `400`: the workflow failed validation; inspect the JSON response details.
- No output while the command is running: this is normal for the blocking API.
- Empty `images`: check that the workflow has a `SaveImage` output node.
