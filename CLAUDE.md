# CLAUDE.md — AI Animation-Video Pipeline (project brief)

You are the lead engineer. Build a fully automated AI animation-video pipeline end to end, phase by phase, in this repo. Follow this brief.

# GOAL
Turn a script into finished animation-style video ads at volume, with NO per-clip credits:
script → shot list → GPT Image 2 first + last frames → Wan 2.2 image-to-video on a rented RTX 5090 → frame interpolation + upscale → voiceover → captions → final MP4s in 9:16, 1:1 and 16:9.

# HARD CONSTRAINTS
- Video model = open weights only (Wan 2.2, Apache 2.0). Never use Kling, Runway or any credit-based video API.
- GPU = rented, pay-per-second, scale to zero when idle. Primary: RunPod Serverless on RTX 5090 (Blackwell). Verify 5090 availability on Serverless first; fallback = RunPod Pods or Vast.ai with auto-shutdown.
- The user's laptop does orchestration only (Dell XPS 15, RTX 3050 Ti 4 GB VRAM, 32 GB RAM). Never render video locally. Detect the OS and adapt commands.
- Cost guardrails: ask the user before creating any paid GPU resource; every job has a max-spend setting; pods auto-terminate; log seconds + $ per clip.
- Secrets only in .env (OPENAI_API_KEY, RUNPOD_API_KEY, ANTHROPIC_API_KEY), with .env in .gitignore and a .env.example committed.

# ARCHITECTURE
1. Planner: LLM turns a script into shots.json (schema below).
2. Frame generator: OpenAI Images API, model gpt-image-2. Generate a character sheet first, then the first + last frame of every shot using that sheet as a reference for consistency. Generate at the target aspect ratio natively, don't crop. Check the current OpenAI docs for the right endpoint and parameters.
3. Render worker: Docker image deployed as a RunPod Serverless endpoint, stateless. Input: first frame, optional last frame, motion prompt, resolution, frame count, seed. Output: MP4 URL plus timing stats.
   - Engine A (preferred): lightx2v/LightWan2.2-A14B (NVFP4, 4 steps, sparse attention) via the LightX2V framework. Needs Blackwell. Base runtime image: lightx2v/lightx2v:26052801-cu130-5090. Vendor benchmark on one 5090: I2V ~10.7 s at 480p and ~26.7 s at 720p per 81-frame clip.
   - Engine B (fallback): ComfyUI + Wan 2.2 I2V A14B fp8 (Comfy-Org repackaged) + lightx2v 4-step LoRAs, using the WanFirstLastFrameToVideo node, run headless through the ComfyUI API.
   - Put weights on a network volume or bake them into the image. Never download weights on cold start.
4. Post-processing, on the same GPU worker: RIFE interpolation (16 → 24/30 fps), then Real-ESRGAN upscale to 1080p.
5. Voiceover: pluggable TTS interface. Default Kokoro (local, free). For languages Kokoro doesn't cover (check German and Dutch), use Piper or another open TTS. Languages: EN, DE, IT, FR, NL.
6. Assembler: ffmpeg. Concat clips, lay VO per shot, optional music bed from a local folder, burned-in captions timed to the VO, exports in 9:16, 1:1 and 16:9.
7. Orchestrator: Python 3.11+ CLI (Typer), SQLite job queue, parallel render submissions, retry with a new seed on failure, resumable jobs, one output folder per job with a cost/timing report.

# shots.json SCHEMA (enforce with pydantic)
```json
{
  "job_id": "brand-angle-v1",
  "style": "3D cartoon, soft studio lighting",
  "aspect_ratio": "9:16",
  "language": "de",
  "character_refs": ["refs/hero_sheet.png"],
  "shots": [
    {
      "id": "s01",
      "duration_s": 5,
      "first_frame_prompt": "...",
      "last_frame_prompt": "...",
      "motion_prompt": "camera slowly pushes in, character turns her head and smiles",
      "vo_line": "...",
      "resolution": "480p",
      "seed": null
    }
  ]
}
```
Rules for the planner:
- Motion prompts describe movement and camera only. Never re-describe what's in the frame.
- First and last frames of a shot must share the same character, outfit, setting and lighting.
- Frame counts must fit Wan's 4n+1 rule (81 frames = ~5 s at 16 fps).
- Default to 480p renders plus upscale.

# BUILD PHASES
Stop at the end of every phase, show the user the results and wait for approval before continuing.

Phase 0 – Spike (do first):
- Rent one 5090 (ask the user first).
- Run Engine A I2V end to end.
- Find out whether Engine A supports first + last frame conditioning.
- Run the same 5 test frames through Engine B.
- Measure real seconds per clip including load and decode, plus $ per clip.
- Deliver a short comparison (speed, cost, quality notes, sample MP4s) and recommend one engine.

Phase 1 – Render worker:
- Serverless handler, Dockerfile, deploy script, health check.
- Post-processing built in.
- A local CLI command that renders one clip from an image and a prompt.

Phase 2 – Frame generator:
- Character sheet, first + last frames per shot.
- Cache results; never regenerate frames that already exist.

Phase 3 – Orchestrator:
- Queue, parallelism setting, retry policy, resume, cost report.
- Command: `pipeline run shots.json`.

Phase 4 – Voice + assembly:
- TTS per shot, ffmpeg timeline, captions, three aspect-ratio exports.
- Demo: a finished 30–60 s video from a real 6–12 shot job.

Phase 5 – Planner:
- `pipeline plan script.txt --style ... --lang ...` produces shots.json, which the user can edit before running.

Phase 6 – Auto-QA (optional):
- Flag clips with heavy flicker or character drift and auto re-render them with a new seed.

Phase 7 – Style LoRAs (optional):
- Support loading Wan 2.2 style LoRAs per job.
- Document how to train one on the user's brand style on a rented GPU.

# ENGINEERING RULES
- Measure, don't assume. Every render logs seconds, GPU type and cost.
- Render worker stays stateless; all state lives in the local SQLite queue.
- Unit tests with mocked APIs for every component; every phase ends with a runnable demo on a real 3-shot job.
- Clean module structure: /planner, /frames, /worker, /orchestrator, /voice, /assembly, /qa, /cli.
- README with setup, costs, and one-command usage.
- Design the CLI so it can later be exposed as an API module of the user's Marketing OS (keep core logic separate from the CLI layer).

# OPEN QUESTIONS (asked before Phase 0)
1. OpenAI API key with gpt-image-2 access?
2. Which animation styles first (2D flat, 3D Pixar-style, stickman, anime)?
3. RunPod account ready, or plan for Vast.ai?

# PROGRESS LOG
- Phase 0: not started (awaiting answers to open questions).
