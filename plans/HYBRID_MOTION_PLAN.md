# Hybrid motion plan — structure as rule, generative as exception

**Status:** Plan only. DepthFlow backend has shipped (slot 03 / 01 / 06 now use it). The two layers below (masked moving elements, video-to-video style polish) are *next-up* if DepthFlow alone produces output we'd put in a demo. If DepthFlow already wins, layers 2 and 3 are stretch — the demo can ship without them.

The premise: real estate buyers care about whether the architecture is the actual house. DepthFlow guarantees that by reprojecting real pixels through depth. Generative video is then reserved for two narrowly-scoped enhancements that don't require touching the architecture.

---

## Layer 1 — DepthFlow (shipped)

Done. The default video backend for every slot. Architecture-locked by construction. See `i2v/depth_video.py` and the new `local/depthflow/*` entries in `i2v/video_models.py`.

The slots all default to a DepthFlow preset:
- `01_hero_exterior_dolly_in` → `local/depthflow/slow_dolly_in`
- `03_kitchen_wide_truck` → `local/depthflow/slow_truck`
- `06_kitchen_detail_85mm` → `local/depthflow/static_with_light_drift`

---

## Layer 2 — Masked moving elements (not implemented)

**Goal:** Add small, locally-scoped moving elements to a parallax video without ever animating the architecture. Examples:

- Water running in a kitchen sink
- Light shifting across a countertop as if time-lapsed
- A curtain billowing near an open window
- Steam rising from a coffee cup
- Fire flickering in a fireplace

**Architecture:**

```
DepthFlow output (architecture locked)
       │
       ▼
[Frame extraction] ────► extract every Nth frame as PNG
       │
       ▼
[Mask generation] ────► SAM2 prompted with target region (e.g. "sink area")
       │                 OR a manually-drawn mask saved as a binary PNG
       ▼
[Masked v2v inpaint] ──► fal video model (e.g. Kling, Runway) with mask + prompt:
       │                  "Within the masked region only: water flowing from
       │                   the faucet into the sink. Outside the mask:
       │                   pixel-identical to the input."
       ▼
[Composite] ──────────► ffmpeg overlay/blend to combine the original parallax
                         video with the inpainted region. Mask edges feathered.
```

**Why this works:**
- DepthFlow's output is the floor — pixel-perfect architecture.
- The mask explicitly tells the inpaint model "only modify here."
- Video inpainting endpoints exist on fal (and Runway, Pika) where the mask defines the modify region.
- Compositing in ffmpeg is deterministic — no further drift.

**Concrete next steps when we implement:**

1. Add `i2v/masked_motion.py` with:
   - `extract_frames(video_path, fps) -> list[Path]`
   - `generate_mask_via_sam2(frames[], prompt) -> Path` (or `manual_mask_path`)
   - `inpaint_masked_motion(frames, mask, prompt, model_id) -> video_path`
   - `composite(original_video, masked_video, mask, output) -> video_path`
2. Extend `VideoPass` schema with optional `masked_motion: MaskedMotion | None`:
   ```python
   class MaskedMotion(BaseModel):
       region_prompt: str       # for SAM2 — "the kitchen sink area"
       motion_prompt: str       # for the inpaint — "water flowing from faucet"
       model: str               # video inpaint model id
       feather_px: int = 8
   ```
3. Pick the inpaint model. Candidates:
   - `fal-ai/runway/inpaint-video` (if exists)
   - `fal-ai/kling/inpaint` (check)
   - Or extract frames + use FLUX inpaint per frame + reassemble (slower but proven)
4. Test on slot 03 first — kitchen sink with running water as the proof of concept.

**Risk:** the inpainted region's lighting/shadows have to match the surrounding parallax frames, or it'll look stuck-on. Feathering helps; matching the source's white balance in the prompt helps; iteration may be needed.

**Estimated effort:** 4–6 hours. Defer until DepthFlow alone is validated as the right base.

---

## Layer 3 — Video-to-video style polish (not implemented)

**Goal:** Apply a cinematic look (color grading, atmosphere, lens character, film grain) to the parallax video output without changing geometry.

**Why we want it:** DepthFlow output looks *technically correct* — same colors, same exposure as the source photo. It does NOT look like a Hollywood-graded film. For the editorial-cinematic style our template targets, we want the parallax video to come out feeling like it was shot on Alexa with a vintage lens and graded. That's a stylistic transformation that doesn't need to invent geometry.

**Architecture options, in order of preference:**

### Option A — LUT applied per-frame (deterministic, no AI)

```
DepthFlow video → ffmpeg with LUT3D filter → output video
```

ffmpeg has a built-in `lut3d` filter that takes a `.cube` file and applies it per frame. Same look as the still-image LUT pass, applied to video. Zero hallucination risk. ~1-2 minutes for a 6-second clip.

This is the right default. It's basically "take the LUT we'd apply to the still and apply it to every frame." Boring and bulletproof.

ffmpeg invocation:
```bash
ffmpeg -i parallax.mp4 -vf "lut3d=cinematic-editorial-v1.cube" -c:v libx264 -crf 18 graded.mp4
```

### Option B — Generative video-to-video at low strength (AI, bounded)

Run the DepthFlow output through a generative video model in img2img / vid2vid mode at very low denoise strength (0.15–0.25). The model can shift colors, add grain, soften lens characteristics, but cannot meaningfully alter geometry at that strength.

fal options:
- `fal-ai/flux-general/image-to-image` — currently for stills only, but the workflow is: extract frames → run through FLUX img2img at strength 0.15 → reassemble. Slower and more expensive than LUT but produces a more "AI cinematic" look that some creatives prefer.
- True video-to-video models (LTX, Wan 2.x via ComfyUI-on-fal) — research, may not be reliable.

**Recommended:** ship Option A (LUT). Option B is a quality experiment for v2.

**Estimated effort:** 30–60 min for Option A. Option B is hours and uncertain.

---

## Composition order

When all three layers are present, the slot's pipeline becomes:

```
source photo
   │
   ▼
[image_pipeline] (Nano Banana — recompose for slot 06; otherwise no AI image pass)
   │
   ▼
[DepthFlow video_pass] (Layer 1)
   │
   ▼
[Masked motion overlay] (Layer 2, if declared)
   │
   ▼
[LUT grade per-frame] (Layer 3)
   │
   ▼
final clip
```

The template format already accommodates Layers 1 + 3 cleanly. Layer 2 needs a small schema addition (the `MaskedMotion` block on `VideoPass`).

---

## When to add each layer

| Layer | Trigger to implement |
|---|---|
| 1 — DepthFlow | DONE |
| 2 — Masked motion | Demo would be more impressive with running water / flickering fire / shifting light. *AND* DepthFlow alone is solid. If DepthFlow looks bad, fix that first. |
| 3 — LUT grade | When DepthFlow output looks technically correct but not "cinematic enough." Probably first thing after validating Layer 1. |

---

## Decision log for this plan

- **Why DepthFlow over Immersity AI / LeiaPix:** open-source, local, free, scriptable, no watermark, works offline. Same algorithm class.
- **Why Layer 2 (masked) over generative video for moving elements:** hard mask = hard architectural guarantee. Even a great inpaint model can drift outside a soft constraint; it can't drift outside a binary mask.
- **Why ffmpeg LUT (Layer 3 Option A) over generative styling:** deterministic, fast, free, looks identical to professional color grading. Generative styling is a "cinematic-but-different-each-time" outcome; LUT is the same look every time, which is what a template promises.
- **Why we're not generating motion on the parallax output:** any generative pass over the full frame can shift geometry. Layer 2 lives in a mask precisely to avoid that.
