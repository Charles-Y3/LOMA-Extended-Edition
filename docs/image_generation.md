# Image generation quality and configuration

LOMA uses Diffusers with SD 1.5 (default `Lykon/dreamshaper-8`) or SDXL checkpoints from settings.

## Fast vs full-quality diffusion

| Setting | Typical behavior |
|--------|-------------------|
| `LOMA_IMAGE_QUALITY=fast` | 6 steps, LCM on (SD 1.5) |
| `LOMA_IMAGE_QUALITY=balanced` (default) | 8 steps, LCM on |
| `LOMA_IMAGE_QUALITY=quality` | 16 steps, **LCM still on** unless disabled |

LCM (Latent Consistency) is tuned for roughly 4–8 steps. Raising step count alone often does **not** fix complex multi-subject scenes (e.g. predator + prey + motion).

### Recommended for complex scenes

Use full scheduler steps without LCM:

```bash
set LOMA_IMAGE_USE_LCM=0
set LOMA_IMAGE_STEPS=24
set LOMA_IMAGE_GUIDANCE=7.5
```

Or set `LOMA_IMAGE_QUALITY=quality` **and** `LOMA_IMAGE_USE_LCM=0`.

### SDXL for multi-subject compositions

Pick an SDXL model in Profile Manager / `LOMA_IMAGE_MODEL`. SDXL paths use 1024×1024, ~30 steps, guidance ~7.5, and **no LCM** automatically.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LOMA_IMAGE_MODEL` | Hugging Face repo or local checkpoint |
| `LOMA_IMAGE_USE_LCM` | `1` (default) or `0` to disable LCM LoRA |
| `LOMA_IMAGE_STEPS` | Override step count |
| `LOMA_IMAGE_GUIDANCE` | CFG scale |
| `LOMA_IMAGE_QUALITY` | `fast` / `balanced` / `quality` presets |
| `LOMA_IMAGE_WIDTH` / `LOMA_IMAGE_HEIGHT` | SD 1.5 resolution (default 512) |
| `LOMA_IMAGE_DISABLE_DIFFUSERS` | `1` to skip GPU pipeline (fallback file only) |

## Preview vs Download

After `image_generation` runs, the PNG on disk is the source of truth. **Download** and **Save** copy that file (or convert to JPG) when Preview is unchanged—they do not re-run diffusion from the caption markdown.

If you edit the diffusion prompt in Preview, `preview_dirty` is set and export may synthesize again using `last_image_diffusion_prompt` or the edited `**Prompt:**` line.

## Prompt tips

- Name every subject you need visible (lion **and** rabbits).
- Put action first: `lion chasing rabbits in savanna, golden hour`.
- SD 1.5 CLIP sees ~77 tokens; long style lists can drop secondary subjects.
