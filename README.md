# i2v_templates — photo-to-video pipeline

Apply a creative template (image prompts + camera-motion prompts + music + cut timing) to a set of amateur real-estate photos and produce a professional walkthrough video. The template is portable JSON; the runtime is a thin loop over fal.ai image and video models.

This repo holds Phase 1 — **the image layer only**. Given an input photo and a slot from a template, run the slot's `image_pipeline` (a chain of one or two image passes) and write each pass to `outputs/`. This is "shot zero" — prove the template format produces convincing output before scaling to the full video pipeline.

---

## Status

- ✅ Template schema with multi-pass image pipeline (`templates/cinematic-editorial-v1.json`)
- ✅ Provider-agnostic `i2v/image_pass.py` callable as a library or CLI
- ✅ `scripts/run_slot.py` CLI — runs the full image pipeline for one slot
- ⬜ Video pass (image-to-video via fal) — next phase
- ⬜ Concat + music mux via ffmpeg — next phase
- ⬜ Photo classifier (matches uploaded photos to template slots) — next phase
- ⬜ Web UI — next phase

---

## Setup

```bash
# 1. Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure fal key
cp .env.example .env
# edit .env and set FAL_KEY

# 3. Install DepthFlow CLI (isolated, NOT in this venv)
#    DepthFlow is the local backend for parallax video passes. We only need
#    the CLI; it's invoked via subprocess. Keep it out of our venv to avoid
#    pulling torch + imgui-bundle + glfw into our deps.
brew install pipx           # if not already installed
pipx ensurepath             # ensures ~/.local/bin (or similar) is on PATH
pipx install depthflow
depthflow --help            # confirm CLI is reachable
```

If pipx fails (or DepthFlow's pip install hits a transitive packaging issue
like the imgui-bundle 1.6.3 path-escape bug), the fallback is a portable
executable: download for macOS from
https://github.com/BrokenSource/DepthFlow/releases and put it on PATH.

---

## Run shot zero

```bash
# 1. Drop your kitchen photo into inputs/
cp ~/path/to/your/kitchen.jpg inputs/aaron-kitchen.jpg

# 2. Run the kitchen-wide slot's image pipeline (single pass)
python -m scripts.run_slot \
    --template templates/cinematic-editorial-v1.json \
    --slot 03_kitchen_wide_truck \
    --input inputs/aaron-kitchen.jpg

# 3. Run the kitchen-detail slot's image pipeline (two-pass chain)
python -m scripts.run_slot \
    --template templates/cinematic-editorial-v1.json \
    --slot 06_kitchen_detail_85mm \
    --input inputs/aaron-kitchen.jpg
```

Outputs land in `outputs/<timestamp>_<slot_id>/`:

```
outputs/2026-05-09T16-30-12_03_kitchen_wide_truck/
  metadata.json
  pass_01_editorial_enhance.png    # the only pass, also the final
outputs/2026-05-09T16-32-08_06_kitchen_detail_85mm/
  metadata.json
  pass_01_editorial_enhance.png    # cinematic editorial wide
  pass_02_recompose_detail.png     # 85mm close-up — the final image to feed to video pass
```

---

## Try a different image model

```bash
# Override the model defined in the template, just for this run
python -m scripts.run_slot \
    --template templates/cinematic-editorial-v1.json \
    --slot 03_kitchen_wide_truck \
    --input inputs/aaron-kitchen.jpg \
    --override-model fal-ai/flux-pro/kontext

# See all known model presets
python -m scripts.list_models
```

---

## Use as a library (pipeline-callable)

```python
from i2v.image_pass import run_image_pass
from i2v.types import ImagePass

result = run_image_pass(
    input_image_path="inputs/aaron-kitchen.jpg",
    pass_spec=ImagePass(
        model="fal-ai/nano-banana/edit",
        prompt="Transform this photo into a cinematic editorial image with harsh directional shadows...",
        parameters={"aspect_ratio": "16:9", "output_format": "png"},
    ),
    output_dir="outputs/quick-test",
    pass_index=1,
    pass_label="editorial_enhance",
)
print(result.output_path)
```

---

## Project layout

```
i2v_templates/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── i2v/                       # Library — pipeline-callable modules
│   ├── __init__.py
│   ├── types.py               # Pydantic types matching template.schema
│   ├── models.py              # Image-model registry + adapters
│   ├── fal_client.py          # Thin fal wrapper with upload + retry
│   └── image_pass.py          # Core: run_image_pass + run_image_pipeline
├── scripts/                   # CLI entrypoints
│   ├── run_slot.py            # Apply a slot's image pipeline to a photo
│   └── list_models.py         # Print known model presets
├── templates/
│   └── cinematic-editorial-v1.json
├── inputs/                    # Drop test photos here (gitignored)
└── outputs/                   # Generated images (gitignored)
```

---

## Design principles

1. **Templates are portable JSON.** All creative decisions (prompts, model choices, durations, music) live in the template. The runtime is dumb plumbing.
2. **Multi-pass chains supported.** Some shots (detail, recompose) need two image passes. The template's `image_pipeline` is an ordered array; each pass declares its `source` (the original photo, or the prior pass's output).
3. **Provider abstraction is thin.** A small per-model adapter normalises input/output shapes; the rest is a single `fal_client.subscribe` call. Swapping models is a one-line change.
4. **Library + CLI in one.** Every module exposes a Python API the eventual web pipeline will import, plus a `python -m` entrypoint for terminal/Codex testing.
5. **Outputs are auditable.** Every run writes `metadata.json` next to its images — the exact prompt, model, params, and run id used. So when a test produces a great frame, it can be reproduced.
