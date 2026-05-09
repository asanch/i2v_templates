# i2v_templates — working context

**Project:** AI photo-to-video pipeline for cinematic real-estate walkthroughs.
**Hackathon:** AutoHDR image-to-video track (24-hour budget). Submission must include a video and a public repo. Coding agent constraint: OpenAI Codex (not Claude) for actual code generation; Claude is used for planning, design, and writing modules that are reviewed/handed off.
**Author:** Aaron Sanchez · `aaron@meekmachina.com`
**Repo:** This directory. Pushed manually to GitHub by Aaron — Claude does not push.

---

## What we're building (one paragraph)

Given a *template* (JSON: ordered shot slots with image-edit prompts, camera-motion prompts, music, and cuts) and a *set of amateur real-estate photos*, produce a professional cinematic walkthrough video. The template encodes a creative's stylistic decisions; the apply pipeline runs each slot's image + video passes through fal.ai models and stitches the result. The hackathon scope is **one well-authored template (`cinematic-editorial-v1`) that works reliably across any test photoshoot**.

Key strategic call: prioritize **conservative, reliable output** over flashy motion. Real estate buyers don't want to see hallucinated rooms; they want to see the actual house, slightly more cinematically.

---

## Where we are right now

Aaron is mid-stream working on **the consistency / identity-preservation problem** — the central technical challenge. Generative video models (Kling, Seedance) can hallucinate when asked to animate a single 2D photo. Generative image models (Nano Banana) can also hallucinate (adding windows that don't exist, reshaping rooms) when given long stylization prompts.

The current focus: **multi-reference image and video passes** to anchor the architecture. Just implemented: `--references` flag for the image pass. Aaron is about to test it against three of his kitchen photos (different angles).

Active discovery from research: **Seedance 2.0 has a `reference-to-video` route that accepts up to 9 reference images** with `@Image1`...`@Image9` tags. This is the strategically right model for real estate because it natively supports multi-photo scene anchoring. We have not wired it into our video pass yet — that's the next step after image-pass validation.

---

## Architecture decisions made (and why)

### Template format

A template is portable JSON. Schema in `i2v/types.py` (Pydantic). Per-slot:
- `photo_requirement` — what kind of source photo this slot needs (scene_kind, intent, composition_hints, ideal_time_of_day). Used by the (future) classifier to match user uploads to slots.
- `image_pipeline` — array of one or more `ImagePass`es. Each pass declares its `source` (`input_photo` or `previous_pass`), the model id, prompt, parameters, and now `reference_photos` + `max_references`. Slot 06 is two-pass (editorial enhance → 85mm recompose); others are single-pass.
- `video_pass` — single image-to-video step (model id, prompt, duration, optional end-frame, extra_args). Camera motion is described here, NOT in the image_pass.
- `clip_post` — local trim/fade/speed.

Cross-slot dependencies (using one slot's output as another's input) are *not* implemented yet — slot 06 currently runs its own editorial enhance independently of slot 03's. Was sketched as a `source: "slot:03_kitchen_wide_truck.last"` mechanism, but deferred.

### Image pass

`i2v/image_pass.py`. Public API:
- `run_image_pass(input_image_path, pass_spec, output_dir, ...)` — single pass.
- `run_image_pipeline(slot, input_image_path, ...)` — full chain for a slot.

Both now accept `reference_photos_override` (list[str] | None). When set, references replace whatever's in the template for the duration of this run.

When a pass has references active, the runner appends an explicit **role-assignment suffix** (`_ROLE_ASSIGNMENT_SUFFIX` constant in `image_pass.py`) to the prompt. The suffix tells the model: "image 1 is the primary edit target; images 2..N are architecture references only; do not blend or transition between them; output remains in the viewpoint of image 1." Worded model-agnostically so it works for both Nano Banana and any future model.

The CLI override applies only to passes whose `source` is `input_photo`. Passes with `source: "previous_pass"` ignore CLI references because they consume the prior pass's output.

FLUX Kontext is currently single-image-only (the fal route we use takes one `image_url`); when references are passed and the model is Kontext, the runner warns and downgrades to single-image. Nano Banana takes a list and works natively.

### Video pass

`i2v/video_pass.py`. Currently single-reference (one start frame, optional end frame). Default model is Kling 2.6 Pro.

**Pending architectural change:** add Seedance 2.0 reference-to-video with multi-image conditioning. The model adapter will need to build the `@Image1`..`@ImageN` prompt tags and ship the reference list as part of the input. Schema change required: `VideoPass.reference_images: list[str]` similar to the image pass.

### Models registry

`i2v/models.py` — image models. Registered:
- `fal-ai/nano-banana/edit` (default, Gemini 2.5 Flash Image edit)
- `fal-ai/flux-pro/kontext` (single-image, A/B alternate)
- `fal-ai/flux-pro/kontext/max` (slower/pricier Kontext)
- `fal-ai/bytedance/seedream/v4/edit`
- `fal-ai/qwen-image-edit`

`i2v/video_models.py` — video models. Registered:
- `fal-ai/kling-video/v2.6/pro/image-to-video` (default)
- `fal-ai/kling-video/v2.6/standard/image-to-video`
- `fal-ai/kling-video/v3/pro/image-to-video`
- `fal-ai/veo3`
- `fal-ai/bytedance/seedance/v1/pro/image-to-video`

**Needs adding:** `bytedance/seedance-2.0/image-to-video` (with end_image_url), `bytedance/seedance-2.0/reference-to-video` (up to 9 refs), `bytedance/seedance-2.0/fast/reference-to-video`, `bytedance/seedance/v1.5/pro/image-to-video`.

### Front-end

`app/` — Next.js 16 + React 19 + Tailwind 4 scaffold matching Aaron's studio stack. Bare-bones: hello-world page with a `/api/health` ping. Deliberately no component library, no state mgmt, no real UI yet — UX decisions deferred until image+video quality is solid. Plan: **demo locally** (Next.js + FastAPI both on Aaron's laptop), not deployed. If we ever deploy, Vercel for `app/` + Fly.io for the FastAPI backend.

### Deployment plan

- **Demo path:** local-only. Two terminals: `make api-dev` (FastAPI on :8000) + `make app-dev` (Next.js on :3000). No Docker, no DNS, no fly.toml needed for hackathon submission.
- **If deployed:** Vercel + Fly. Aaron's studio uses this exact pattern; muscle memory.
- **Skipped:** Apify (wrong tool category), Modal (new stack risk), pure Vercel + TS port (would require porting ~600 lines of Python).

### Repo layout

```
i2v_templates/
├── pyproject.toml          # Python package
├── Makefile                # make api-dev, app-dev, app-install
├── .env.example
├── .gitignore              # Python + Node + media
├── README.md
├── CONTEXT.md              # this file
├── HACKATHON_PLAN_V?.md    # earlier strategic plans (older context)
├── i2v/                    # Python library — pipeline-callable
│   ├── __init__.py
│   ├── types.py            # Pydantic types (Template, Slot, ImagePass, VideoPass, ...)
│   ├── models.py           # Image-model registry + adapters
│   ├── video_models.py     # Video-model registry + adapters
│   ├── fal_client.py       # Thin wrapper around fal-client
│   ├── image_pass.py       # run_image_pass + run_image_pipeline
│   └── video_pass.py       # run_video_pass
├── scripts/                # CLI entry points
│   ├── run_slot.py         # apply slot pipeline (image + optional video)
│   ├── run_video.py        # video pass on existing image
│   ├── list_models.py
│   └── list_video_models.py
├── templates/
│   └── cinematic-editorial-v1.json   # the only template (3 slots)
├── app/                    # Next.js front-end (hello world)
│   ├── package.json        # Next.js 16, React 19, Tailwind 4
│   ├── tsconfig.json
│   ├── next.config.ts
│   ├── postcss.config.mjs
│   ├── .env.local.example  # NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
│   ├── app/                # Next.js App Router
│   │   ├── layout.tsx
│   │   ├── page.tsx        # home + health check
│   │   ├── globals.css
│   │   ├── _components/HealthCheck.tsx
│   │   └── api/health/route.ts
│   └── README.md
├── inputs/                 # gitignored — drop test photos here
├── outputs/                # gitignored — generated images and clips
└── .venv/                  # gitignored — Python venv
```

---

## Test photos in inputs/

13 files staged. Aaron has identified that **`IMG_5293.JPG`, `IMG_5316.JPG`, `IMG_5290.JPG`** are all the same kitchen from different angles (the LOVE/HOPE/FAITH wall art kitchen with the dining/living visible past the archway).

Other photos: IMG_5273, 5283, 5284, 5288, 5304, 5306, 5331, plus three GetMedia-N.jpg files (likely from MLS / AutoHDR test set). Scene mapping for these is not yet documented — Aaron will likely sort them as we test other slots.

---

## What's done

- ✅ Template schema with multi-pass image pipeline (deferred: cross-slot output dependency)
- ✅ Cinematic-editorial-v1 template, 3 slots: hero exterior, kitchen wide, kitchen detail (2-pass)
- ✅ `i2v/` Python package: types, image-model registry, video-model registry, fal client, image_pass, video_pass
- ✅ CLIs: `run_slot`, `run_video`, `list_models`, `list_video_models`
- ✅ Multi-reference image pass (just shipped — `--references` CLI flag, role-assignment prompt suffix)
- ✅ Conservative video prompt rewrites (eliminated the lightning-bolt failure mode)
- ✅ Next.js scaffold under `app/` (hello world, ready to grow)
- ✅ Local dev workflow: `make app-dev`, `make api-dev`
- ✅ All Python files compile clean; Pydantic schema validates the template

---

## What's in flight / next

### Immediate (next 30–60 min)

1. **Test multi-reference image pass on the kitchen.** Aaron runs:
   ```bash
   python -m scripts.run_slot \
     --template templates/cinematic-editorial-v1.json \
     --slot 03_kitchen_wide_truck \
     --input inputs/IMG_5293.JPG \
     --references inputs/IMG_5316.JPG,inputs/IMG_5290.JPG
   ```
   Compare to the single-image baseline run (no `--references`). Look for: (a) architecture preservation — windows/cabinets stay where they are in IMG_5293; (b) no leakage from the reference angles into the edit; (c) editorial style still as strong as the single-image version.

2. **Decide based on output:**
   - If multi-ref preserves architecture and the style is still good → ship it. Move to video.
   - If style weakens but architecture preserved → adjust the role-assignment suffix to weight style more.
   - If architecture still drifts → multi-ref Nano Banana isn't the answer; pivot to LUT-only color grading (deterministic, no AI on the still).

### Near-term (next 2–4 hours)

3. **Add Seedance 2.0 to the video model registry.** Three new entries:
   - `bytedance/seedance-2.0/image-to-video` (with `end_image_url`)
   - `bytedance/seedance-2.0/reference-to-video` (up to 9 reference images)
   - `bytedance/seedance-2.0/fast/reference-to-video`
   Build adapters that handle the `@Image1`..`@ImageN` prompt-tag convention.

4. **Add multi-reference to the video pass.** `VideoPass.reference_images: list[str]`. The video runner builds prompts with explicit role assignment: "@Image1 is the first frame; @Image2..N are architecture references; do not transition between them."

5. **Test end-to-end on the kitchen.** Run image pass with multi-ref, then video pass with Seedance reference-to-video using the same multi-ref set. Validate that motion is real AND architecture is preserved.

### Mid-term (if time)

6. **LUT grading module** (`i2v/grade.py`) using `pillow-lut` library. Replaces or supplements the AI editorial enhance for color/tone with a deterministic look-up-table transform. Pixel-perfect identity preservation.

7. **Add the remaining slots** (entry, living wide, primary bed, outdoor evening, hero exterior pull-back) using AutoHDR's prompt kit. We deferred adding these until the per-slot quality is solid.

8. **FastAPI wrapper** (`api/main.py`) exposing the i2v functions as HTTP endpoints. Currently stubbed in the Makefile (`make api-dev`) but not yet written. ~80 lines.

9. **Wizard / upload UI** in the Next.js app once we know what the flow looks like.

### Deferred / probably never

- Synthesizing alternate angles via AI for users with only one photo. Risky — references would be the model's own hallucinations. Revisit only if real-multi-ref turns out to be insufficient.
- Photogrammetry / NeRF / Gaussian Splat. Multi-week build. Right answer for a real product, wrong for 24 hours.
- Depth-conditioned video. No mature fal endpoint accepts depth.
- Story-driven storyboarding (the original feature ambition, scrapped at "let's simplify").
- Wizard-style guided iPhone capture. Replaced by upload-first demo flow.

---

## Open decisions

- **Will we keep AI image edits at all, or move to LUT-only?** TBD until multi-ref test result. AI edit is genuinely valuable for the slot 06 recompose (different framing). For non-recompose slots, LUT may be enough.
- **Seedance reference-to-video vs. Kling tail-image trick.** Both are conservative-motion strategies. Seedance reference-to-video is the more architecturally sound — wants to be the default. Kling tail-image is a fallback if Seedance gives unexpected results.
- **Optimal number of references.** Research says 3–5; we're testing with 3 (primary + 2 refs). Will iterate.
- **Single template vs. two.** Decision: ship one well-authored template (`cinematic-editorial-v1`) that works reliably. Don't split focus.

---

## Important files to read first when picking up

In rough priority order:

1. `CONTEXT.md` (this file)
2. `templates/cinematic-editorial-v1.json` — the template, source of truth for slots/prompts
3. `i2v/types.py` — Pydantic schema
4. `i2v/image_pass.py` — most recently modified; look for `_ROLE_ASSIGNMENT_SUFFIX` and `reference_photos` handling
5. `i2v/video_pass.py` — needs Seedance 2.0 work
6. `i2v/video_models.py` — needs Seedance 2.0 entries
7. `scripts/run_slot.py` — CLI; just got `--references` flag
8. `app/app/page.tsx` — Next.js hello world (scaffold only)
9. Older planning docs: `HACKATHON_PLAN.md`, `hackathon/SHOT_ZERO_VALIDATION.md` if they're around (they were earlier strategic plans; treat as historical context)

---

## How to resume on another laptop

### Step 1 — get the code

```bash
# On laptop A (current): commit and push
cd /Users/aaronsanchez/Workspace/Pipelines/i2v_templates
git add -A
git commit -m "wip: multi-reference image pass + context dump"
git push origin <your-branch>

# On laptop B: clone fresh
cd ~/Workspace/Pipelines       # or wherever
git clone <repo-url> i2v_templates
cd i2v_templates
```

### Step 2 — set up Python and Node

```bash
# Python
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

# Node (under app/)
cd app
pnpm install                   # or npm install
cd ..
```

### Step 3 — restore secrets

`.env` (root, for Python) — `cp .env.example .env` and fill in:
- `FAL_KEY` — from fal.ai dashboard
- `GEMINI_API_KEY` — only if calling Google directly (not needed if all routing through fal)

`app/.env.local` (optional) — `cp app/.env.local.example app/.env.local` and set `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000`.

These files are gitignored on purpose — copy them manually between machines (1Password, secure note, whatever) or regenerate the keys.

### Step 4 — bring this context into Claude

Open Claude on laptop B. Drop this file (`CONTEXT.md`) into the conversation as the first message, with something like:

> "Picking up from a previous session. Here's the current state of the project. Read CONTEXT.md and let's continue. Latest action was: just shipped multi-reference image pass with `--references` flag. About to test against the kitchen photos before moving to Seedance 2.0 reference-to-video."

Claude will load the project state and you can keep going. The cleaner the CONTEXT.md, the faster Claude is back up to speed. Update CONTEXT.md as decisions are made — it's a living doc, not a snapshot.

### What does NOT transfer automatically

- The Cowork session itself (conversation history). Cowork sessions are local to the laptop. The new laptop starts a fresh session.
- The .venv (Python venv binaries are platform-specific). Recreate via `python3 -m venv .venv`.
- The `node_modules/` and `.next/` (large, gitignored). Reinstall via `pnpm install`.
- The `inputs/` and `outputs/` (gitignored). Re-upload test photos. The 13 photos currently in `inputs/` aren't in git — Aaron will need to copy them manually if continuing tests.
- Local secrets (`.env`, `.env.local`).
- Anything in `outputs/` from previous runs.

### Tips

- Keep CONTEXT.md updated each time we make a meaningful architecture decision. It's the durable record. Conversation history is ephemeral.
- For long-running runs (image+video pass), the `metadata.json` next to each output is the audit trail. Lets you reproduce a successful generation exactly.
- If returning after a few hours, `git log --oneline | head` shows recent commits. The commit message I'd use for the multi-reference work is something like `feat(image_pass): multi-reference architecture anchoring with --references flag`.

---

## Hackathon constraints to remember

- **24 hours total.** Don't pursue rabbit holes (depth-conditioned video, NeRF, photogrammetry).
- **Submission needs:** a video of the demo, a public GitHub repo, a description.
- **Demo is local.** Recording from Aaron's laptop running both servers.
- **Codex (not Claude) writes code in production**, but Claude does the design and authors modules in this session — Codex picks up where this leaves off when needed.
- **No Vercel deploy needed** unless time at the end allows.
- **The judging criteria** for the AutoHDR track: (1) systems engineering, (2) creative problem solving, (3) consistent professional output. Optimize for (3) demoed convincingly. Architecture story is a bonus, not the headline.

---

## Where credit / blame lives

- AutoHDR's prompt kit is the source of every editorial-image prompt in `cinematic-editorial-v1.json`. Used verbatim where they apply.
- The video-prompt rewrites (constrained, light-led) are ours, after observing Kling's failure modes (lightning bolts, geometric morphing).
- The role-assignment suffix in `image_pass.py` is ours, derived from Seedance reference-to-video research.
- The architecture (template = portable JSON, image_pipeline = chain of typed passes, multi-reference for architecture anchoring) is co-evolved between Aaron and Claude through this session.
