# CLAUDE.md — AI Animation-Video Pipeline (project brief)

You are the lead engineer. Build a fully automated AI animation-video pipeline end to end, phase by phase, in this repo. Follow this brief.

# GOAL
Turn a script into finished animation-style video ads at volume, with NO per-clip credits:
script (+ optional reference ad) → shot list → GPT Image 2 first + last frames → Wan 2.2 image-to-video on a rented RTX 5090 → frame interpolation + upscale → voiceover → captions → final MP4s in 9:16, 1:1 and 16:9.

# HARD CONSTRAINTS
- Video model = open weights only (Wan 2.2, Apache 2.0). Never use Kling, Runway or any credit-based video API.
- GPU = rented, pay-per-second, scale to zero when idle. Primary: **Vast.ai** RTX 5090 (Blackwell) — user decision 2026-11-05, replaces RunPod. Phase 0 runs on a plain Vast instance with auto-destroy; production worker targets Vast Serverless (min_load=0, cold_workers=0, positive inactivity_timeout) if 5090s are available there, else on-demand Vast instances started/destroyed per batch.
- The user's laptop does orchestration only (Dell XPS 15, RTX 3050 Ti 4 GB VRAM, 32 GB RAM). Never render video locally. Detect the OS and adapt commands.
- Cost guardrails (updated 2026-11-05, user: "lowest cost every time", no approvals): rent GPUs WITHOUT asking, always the cheapest option that works (cheapest Vast offer by est. total cost incl. bandwidth, 1 ElevenLabs take, batch ads into one GPU session). Hard safety caps stay: $3 per ad, $15 per day, instance self-destroys; log seconds + $ per clip and per ad.
- Secrets only in .env (OPENAI_API_KEY, VAST_API_KEY, ANTHROPIC_API_KEY), with .env in .gitignore and a .env.example committed.

# ARCHITECTURE
0. Reference analyzer (/reference, added 2026-11-05): the user supplies whole finished animation ads from OTHER brands as inspiration. Runs on the laptop, no GPU. Steps: shot-cut detection (PySceneDetect) → keyframes per shot → local speech-to-text (faster-whisper) for VO timing → measured palette/brightness/motion intensity → Claude vision turns it all into `reference_profile.json`:
   - look: rendering style, palette, lighting, character proportions (as words, not images)
   - structure: per-shot duration, shot type, purpose (hook / problem / product / CTA), cut rhythm
   - camera + motion: per-shot camera move and motion intensity
   - VO + captions: words per second, tone, caption placement/style, music energy
   The planner fills that template with the user's own script, characters and brand.
   Guardrail (others' ads): copy style, pacing, structure and camera language only. Never reuse their characters, logos, products, exact scenes or VO text; never feed their frames into gpt-image-2 as image references; never train a LoRA on their footage.
1. Planner: LLM turns a script (+ optional reference_profile.json) into shots.json (schema below).
2. Frame generator: OpenAI Images API, model gpt-image-2. Generate a character sheet first, then the first + last frame of every shot using that sheet as a reference for consistency. Generate at the target aspect ratio natively, don't crop. Check the current OpenAI docs for the right endpoint and parameters.
3. Render worker: Docker image deployed on Vast.ai (Serverless endpoint or per-batch instance), stateless. Input: first frame, optional last frame, motion prompt, resolution, frame count, seed. Output: MP4 URL plus timing stats.
   - Engine A (preferred): lightx2v/LightWan2.2-A14B (NVFP4, 4 steps, sparse attention) via the LightX2V framework. Needs Blackwell. Base runtime image: lightx2v/lightx2v:26052801-cu130-5090. Vendor benchmark on one 5090: I2V ~10.7 s at 480p and ~26.7 s at 720p per 81-frame clip.
   - Engine B (fallback): ComfyUI + Wan 2.2 I2V A14B fp8 (Comfy-Org repackaged) + lightx2v 4-step LoRAs, using the WanFirstLastFrameToVideo node, run headless through the ComfyUI API.
   - Bake weights into the image or a Vast volume on the host. Never download weights on cold start.
4. Post-processing, on the same GPU worker: RIFE interpolation (16 → 24/30 fps), then Real-ESRGAN upscale to 1080p.
5. Voiceover: pluggable TTS interface. Default Kokoro (local, free). For languages Kokoro doesn't cover (check German and Dutch), use Piper or another open TTS. Languages: EN, DE, IT, FR, NL.
6. Assembler: ffmpeg. Concat clips, lay VO per shot, optional music bed from a local folder, burned-in captions timed to the VO, exports in 9:16, 1:1 and 16:9.
7. Orchestrator: Python 3.11+ CLI (Typer), SQLite job queue, parallel render submissions, retry with a new seed on failure, resumable jobs, one output folder per job with a cost/timing report.


# TEAM SOP (docs/ai_video_ad_sop.md, received 2026-11-05) — the pipeline automates this process
The user's team makes ads with a 4-phase manual SOP (Claude + text-to-image + Kling/Runway + CapCut). The pipeline must reproduce its steps and quality rules, swapping in gpt-image-2 and Wan 2.2.
- Script phase: brand inputs (niche, product, avatar, problem mechanism, solution mechanism, traffic source, length) → analyse a winning competitor script (hook, story, objections, mechanism, proof, CTA) → rewrite for the brand modelling that structure → lock → break into scenes. Word budget ≈ 2.5 words/s (45 s ≈ 110-120 words, 15 s ≈ 30-40 words).
- A scene = one visual setting or one speaker segment = one continuous camera angle; cuts only between scenes. Scenes run 5-14 s, so scenes longer than one Wan clip (81 frames ≈ 5 s) must be chained: last frame of clip n = first frame of clip n+1.
- Start-frame prompt rules: subject + setting detail, camera/lens/DoF/lighting, explicit "NOT in frame" list, product label text spelled out line by line, overlay text exact (font, placement, colour), frame = frozen moment BEFORE the action.
- Motion prompt rules: what happens at t=0 (speaking immediately vs hold), all movement with speed/direction, what stays STILL (product labels), delivery style, ambient sound / silence, visual events anchored to specific words, background vs foreground action separated, target runtime, end instruction (hard cut / hold a beat).
- Assembly rules: trim first/last 0.5 s of each clip, hard cuts, text overlays, end card (product, guarantee, CTA), 1080x1920 and 1080x1080 (+16:9), human review gates for frames and clips before publishing.
- Gaps vs Kling-style models that the pipeline must cover itself: Wan has no native audio or speech (TTS + lip-sync for on-screen speakers + SFX/ambient layer); word-anchored events need VO word timestamps; product labels warp in motion (keep product shots near-static and/or composite the real label back in post).

# shots.json SCHEMA (enforce with pydantic)
```json
{
  "job_id": "brand-angle-v1",
  "style": "3D cartoon, soft studio lighting",
  "aspect_ratio": "9:16",
  "language": "de",
  "character_refs": ["refs/hero_sheet.png"],
  "reference_profile": "refs/videos/competitor_ad1.profile.json",
  "shots": [
    {
      "id": "s01",
      "duration_s": 5,
      "first_frame_prompt": "...",
      "last_frame_prompt": "...",
      "motion_prompt": "camera slowly pushes in, character turns her head and smiles",
      "vo_line": "...",
      "resolution": "480p",
      "seed": null,
      "ref_shot": 3
    }
  ]
}
```
Rules for the planner:
- Motion prompts describe movement and camera only. Never re-describe what's in the frame.
- First and last frames of a shot must share the same character, outfit, setting and lighting.
- Frame counts must fit Wan's 4n+1 rule (81 frames = ~5 s at 16 fps).
- Default to 480p renders plus upscale.
- When a reference_profile is given, match its shot count, durations, shot purposes and camera moves; `ref_shot` (optional) points to the reference shot each shot is modelled on.

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

Phase 5 – Reference analyzer + Planner:
- `pipeline analyze ref.mp4` → reference_profile.json plus a contact sheet of the detected shots, for the user to check.
- `pipeline plan script.txt --style ... --lang ... [--ref ref.profile.json]` produces shots.json, which the user can edit before running.

Phase 6 – Auto-QA (optional):
- Flag clips with heavy flicker or character drift and auto re-render them with a new seed.

Phase 7 – Style LoRAs (optional):
- Support loading Wan 2.2 style LoRAs per job.
- Document how to train one on the user's brand style on a rented GPU.

# ENGINEERING RULES
- Measure, don't assume. Every render logs seconds, GPU type and cost.
- Render worker stays stateless; all state lives in the local SQLite queue.
- Unit tests with mocked APIs for every component; every phase ends with a runnable demo on a real 3-shot job.
- Clean module structure: /reference, /planner, /frames, /worker, /orchestrator, /voice, /assembly, /qa, /cli.
- README with setup, costs, and one-command usage.
- Design the CLI so it can later be exposed as an API module of the user's Marketing OS (keep core logic separate from the CLI layer).

# DECISIONS (answers to pre-Phase-0 questions, 2026-11-05)
1. OpenAI: user has a ChatGPT plan only, NO API key yet. ChatGPT subscriptions don't include API access; the API is billed separately at platform.openai.com. Phase 0 test frames can be made in ChatGPT and uploaded. Phase 2 needs a real API key.
2. Styles: 3D Pixar-style first.
3. GPU host: Vast.ai (account not yet funded). RunPod dropped.
4. Reference videos: user will provide whole animation ads from other brands as inspiration (see Architecture step 0 and its guardrail).

# FINDINGS
- LightX2V (ModelTC/LightX2V, checked 2026-11-05) has task `flf2v` for `wan2.2_moe` (scripts/wan22/run_wan22_moe_flf2v.sh, configs/wan22/wan_moe_flf2v.json: 40 steps, CFG on, no NVFP4). The NVFP4 4-step distilled config exists only for i2v (configs/wan22/extreme/wan_moe_i2v_distill_nvfp4_sparse_attn.json). Untested: NVFP4 distill weights + task flf2v. Phase 0 must test this on the GPU.
- Kokoro: official model card confirms only EN/ZH reliably; German is community/partial, Dutch not supported. Plan Piper (or another open TTS) for DE and NL; check IT/FR quality.
- Vast Serverless supports scale-to-zero; cold start documented at 3-5 min.
- LightX2V also ships an official 4-step distilled (bf16, not NVFP4) Wan 2.2 FLF2V path: scripts/wan22/inference/distill/run_wan22_distill_moe_flf2v.sh + configs/wan22/wan_distill_moe_flf2v.json (weights: lightx2v/Wan2.2-Distill-Models). One LightX2VPipeline can serve both tasks (support_tasks=["i2v","flf2v"], generate(task=...)).
- Lip-sync options in LightX2V: Wan 2.2 S2V (40 steps + CFG, slow), SekoTalk and InfiniteTalk (4-step distilled, SekoTalk has 5090 configs). Not tested yet.
- Vast hidden cost: download bandwidth ranges $0-53/TB between hosts; always rank offers by total est. cost (launch.py offers does).
- Cloud session limits: outbound SSH blocked, huggingface.co and platform.openai.com not reachable from this session. The GPU box downloads models itself and pushes results to the `phase0-results` branch with a GitHub token stored as a Vast account env var (GH_TOKEN).

# AUTOMATION (user decision 2026-11-05)
- Chat-driven (user decision 2026-11-05, replaces the 2-hourly scheduled task, now disabled): the user uploads frame zips in the chat (one zip = one ad) and says go; Claude follows .claude/skills/make-ad/SKILL.md end to end. The user never touches GitHub. No per-step approvals; only stop for a real blocker.
- Voiceover = the ElevenLabs connector in the Claude session (not an API key), 1 take, eleven_v4. ALWAYS ElevenLabs (user 2026-11-06: Kokoro voices are not acceptable; never substitute a free TTS). If credits run out, stop and ask the user to top up. Scribe word timings cost 0 credits only for audio ElevenLabs generated itself.
- Intake: pipeline/stage_frames.py puts the zip's frames on branch frames-store (frames/<slug>/Shot_NN.jpg) for the GPU box. Script from the zip or chat, else Claude writes it. Output: finished MP4s sent in the chat + jobs/<slug>/DONE.json with costs. Several zips = one batch on one GPU.

# PROGRESS LOG
- Phase 0: spike code written (phase0/: launch.py, onstart.sh, run_spike.sh, engine_a.py, engine_b.py, fetch_models.py, report.py; tests/test_phase0.py passing). Vast funded ($10 credit), key saved as a network secret for console.vast.ai. Waiting on: GH_TOKEN in Vast account env vars, 10 test frames (phase0/frames/README.md), user go-ahead to rent.
- Phase 0 measured (2026-11-05, Vast RTX 5090 $0.58/h): Engine A NVFP4 I2V 480p 81 frames = 12.2 s/clip warm ($0.002), model load 82 s, first clip 62 s. FLF2V not tested properly (pipeline was created for i2v only — code bug, not a model limit). Engine B not run yet.
- First real ad delivered: Meow Mouth UK v1 (jobs/meow-mouth-uk), 29 shots, user-made gpt-image-2 frames, 720p 49-121 frames per shot: 29/29 ok, median 25.4 s/clip, 13 min render, $0.13 render, $0.32 total GPU incl. image pull + model download (30 min instance). VO = ElevenLabs (voice AK - British Posh Well-Spoken Old Man, eleven_v4, ~2,950 credits for 2 takes) + Scribe word timings. Edit (assembly/edit_ad.py) runs in the cloud session: ~7.5 min for 3 formats. Shot 28 QA fix: clip_start 3.5 s (cat left frame).
- User decisions 2026-11-05: voiceover = ElevenLabs (not Kokoro, never TTS on the GPU); GPU only animates; user supplies first frames (gpt-image-2 via ChatGPT) and later a reference ad; Claude decides and executes without asking for routine choices.
- Batch 1 delivered 2026-11-06 (5 ads from one 515 MB zip via GitHub release batch-1): dental-treats, suffering-for-years, hiding-from-mum, tooth-pov (two voices), theo-clay-story (4:37, subtitles). 217 clips on one Vast 5090 in 98 min, 0 failures, GPU $1.07 total; ElevenLabs 9,688 credits (~$1.76); ~$2.83 for all 5. 30 of 225 uploaded frames were truncated PNGs: 22 auto-repaired (pipeline/repair_frames.py), 8 not rendered (previous shot continues; re-export needed: dental 5, hiding 4, tooth 35, theo 2/9/35/77). QA fixes: hiding s027 clip_end (lid lift), tooth-pov no overlay banner (frames carry badges), Theo subtitles made bolder. Per-ad costs in jobs/*/DONE.json and jobs/ledger.json.
- Batch 2 (2026-11-06, 12 ads, 474 clips, one Vast 5090 run 20261106-135223): ElevenLabs quota ran out after 8 of 16 parts; 6 ads first voiced with Kokoro, user rejected them, re-voiced with ElevenLabs after top-up (gum = Beatrice, female, matches the frame). Off-story frames fixed in the edit with clip_from/fill_from (My Own Cat's Heart: dog/other-cat/other-label shots; Dropping My Food: lone photo shots s009, s014). 3 Mistakes gets a hook title card (job.hook_card).
- Batch 2 delivered 2026-11-06: 12 ads, 474 clips, 0 render failures, all with music, 3 formats each. Cost $5.92 total ($4.38 Vast GPU incl. ~$1.0 lost on slow/failed hosts, $1.54 ElevenLabs = 8,451 credits; 6 ads Kokoro = $0). Per-ad costs in jobs/*/DONE.json + jobs/ledger.json.
- Lessons (2026-11-06): (1) Vast hosts advertise speeds they don't deliver to HF: launch.py now filters >=600 Mbps down, >=1 GB/s disk, >=99% reliability, skips Nepal, and watch destroys a box still loading after 15 min. (2) Disk must be 120 GB: ~40 GB image + 29 GB weights + clips; 80 GB filled and Vast stopped the box mid-batch. (3) push_results.sh and the editor fetch use blobless sparse clones of job-results (full fetches took 10+ min per box and filled the session disk). (4) If a box ends before 32 fps interpolation finishes, the editor falls back to the 16 fps clip (empty files skipped); interpolation can also be run locally (4 CPUs: ~10 s/clip). Vast credit left: $3.51.

