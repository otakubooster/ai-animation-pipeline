---
name: make-ad
description: Turn zips of first frames uploaded in the chat (one zip = one ad) into finished animated video ads with ElevenLabs voiceover, fully unattended and at the lowest cost. Use when the user uploads frame zips and says "go", "make the ad(s)", or similar.
---

# Make an ad, end to end, without asking

The user wants this to run unattended. Do not ask for approval for routine choices (voice, take, GPU offer,
motion prompts, small QA fixes). Always pick the cheapest option that works. Stop and report only on a real
blocker (missing input, a cap reached, an access error). Follow CLAUDE.md for rules and the team SOP.

## Caps (hard, never exceed)
- $3 GPU spend per ad, $15 per day across ads (`jobs/ledger.json`, sum today's `gpu_usd`). Over the day cap: stop, report.
- GPU instances always run with `--max-usd` and self-destroy; never leave one running.

## 0. Inputs (the user only uploads zips in the chat and says go)
Each uploaded zip is one ad. Uploads land in `/root/.claude/uploads/<session>/`. Slug = zip name, lowercased,
spaces -> dashes (e.g. `uk-cat-pov-v2`). Never ask the user to use GitHub.

## 1. Intake (per zip)
```bash
python3 pipeline/stage_frames.py "<upload.zip>" <slug>      # frames -> branch frames-store/frames/<slug>/Shot_NN.jpg
```
It prints the frames, the unzipped folder and any script file inside the zip (.md/.txt/.docx/.pdf).
- Script: use the one in the zip, else one pasted in the chat. If there is none, write the VO yourself from the
  frames following the SOP (one line per shot, ~2.5 words/s) and the brand/product visible in the frames.
- Look at every frame (contact sheet) before writing anything; write prompts from what is actually in the frame.
- In job.json use `"frames_git": {"branch": "frames-store", "path": "frames/<slug>"}` and frame names `Shot_NN.jpg`.

## 2. Shot list -> jobs/<slug>/job.json
Copy the shape of `jobs/meow-mouth-uk/job.json`: `job_id`, `render` (720x1280, 16 fps, interpolate 32),
`banner` (offer text + first shot, if the script has an offer),
`captions` {`high_on_shots`: shots whose product/label sits in the lower third}, `shots` [{id, frame, vo_line,
motion_prompt, seed}].
Motion prompts (SOP): movement and camera only, what stays still (product labels: "stays completely still with
its label facing the camera"), no re-description of the frame, one continuous shot. Prefer gentle motion;
avoid jumps/standing up (subjects leave the frame).

## 3. Voiceover (ElevenLabs connector, 1 take)
1. `creative_create_flow`, then `creative_list_voices` (language/accent/gender/age from the script) and pick the
   best fit yourself. Reuse the previous ad's voice for the same brand unless the script asks otherwise.
2. `creative_generate_speech` with `generations_count: 1`, `model_id: eleven_v4`, the whole script in one take,
   light emotion tags only where the script turns ([sad], [hopeful], [excited]).
3. Poll `creative_get_flow_run_status`, download the mp3 with curl to `jobs/<slug>/vo/take.mp3` (gitignored).
4. `creative_add_flow_asset_node` (the generation) -> `creative_transcribe_audio` (connect_from it) -> download
   `words_download_url` to `jobs/<slug>/vo/words.json`.
5. `python pipeline/fit_to_vo.py jobs/<slug>/job.json jobs/<slug>/vo/words.json` (sizes every clip to the real VO).

## 4. Render (cheapest 5090, unattended, ALL new jobs in ONE GPU session)
Do steps 1-3 for every new job first, then render them together: the ~20 min GPU setup (image + model
download) is paid once per batch, and the model loads once.
```bash
git add jobs/*/job.json && git commit -m "batch: shot lists" && git push origin main
N=<number of jobs>
python3 phase0/launch.py create --best --script jobs/run_job.sh --job jobs/<a> jobs/<b> ... \
  --max-usd <min(3*N, 15 - spent today)> --max-hours <0.75 + 0.25*N> --yes
python3 -u phase0/launch.py watch <instance> --every 60 > <scratch>/watch.log 2>&1   # run_in_background
```
One 5090 renders ~29 short shots in ~13 min, so 10 ads of ~30 shots take ~2.5 h and ~$1.50 GPU in total.
Record Vast credit before and after (`GET /api/v0/users/current/` -> `credit`); split the batch cost across ads
by render seconds. The box pushes to branch `job-results` every 10 clips and a "<job_id> ready" commit as each
job finishes interpolation (`jobs/<job_id>/results/<run>/READY`), then destroys itself after the last one.
Start QA + edit of each job as soon as its READY push lands, while the GPU renders the next jobs.

## 5. Automatic QA
```bash
git fetch origin job-results && git archive FETCH_HEAD jobs/<job_id>/results/<run> | tar -x -C <scratch>
python pipeline/qa_sheet.py <results>/clips <scratch>/qa
```
Look at every sheet. For each shot: subject leaves the frame, face/label warps, character changes, frozen
clip. Fix cheapest first: set `clip_start` (seconds) to use a clean part of the clip; only if no clean part
exists, queue that shot for a re-render with a calmer prompt and a new seed (batch re-renders).

## 6. Edit + deliver
```bash
python assembly/edit_ad.py jobs/<slug> --clips <results> --vo jobs/<slug>/vo/take.mp3 --words jobs/<slug>/vo/words.json --out <scratch>/final --name <Brand>_<slug>
```
Check a few frames of the 9:16 export (captions not over labels, banner present). Send the 9:16 with
SendUserFile (`display: render`) and the 1:1 and 16:9 (`display: attach`), with a short cost line.

## 7. Close out
Write `jobs/<slug>/DONE.json` {outputs, gpu_usd, elevenlabs_credits, render stats, QA fixes} and append the ad
to `jobs/ledger.json` {date, slug, gpu_usd, elevenlabs_credits}. Commit and push. If the push returns 403,
keep the commit and say so in the summary (fix: reconnect GitHub at https://claude.ai/connect-github).
Finish with a 3-line summary: what was made, total cost, anything that needs the user.
