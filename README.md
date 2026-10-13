# music-channel

Automated pipeline that generates original instrumental music, assembles long-form
mixes and Shorts, and publishes them to YouTube on a schedule -- running entirely
on free cloud compute (GitHub Actions + Kaggle), with a human approval step before
anything goes public.

## Status

**Scaffold stage (P0).** Core modules are stubbed and fail safe in `dry_run` mode
(the default). Nothing uploads, spends, or calls a paid/limited API until each
phase below is explicitly completed and signed off. See `docs/RUNBOOK.md` for
setup, the full build plan, and troubleshooting.

## How it works, briefly

1. A trend step reads generic genre/mood words (no names) to vary prompts.
2. Music is generated on free GPU credits, quality-checked, and pooled.
3. Accepted tracks are assembled into long mixes plus short highlight clips,
   with a cover, a functional on-screen layer, and a written session note.
4. A human (the channel owner) approves each upload before it goes public.
5. Weekly analytics feed back into which styles get made more often.

## Development

```bash
pip install -r requirements.txt
FORCE_DRY_RUN=true pytest -q
```

See `docs/RUNBOOK.md` for the one-time account setup this repo needs before any
phase past the scaffold can run for real.
