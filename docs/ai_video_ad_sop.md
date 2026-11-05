# **AI Video Ad Creation SOP**

## From Script → Starting Frame → Animated Video Clip (Scene by Scene)

## **Overview**

This SOP turns a written ad script into a series of AI-generated video clips by breaking the script into scenes, generating a **Text-to-Image starting frame** for each scene, then writing an **Image-to-Video animation prompt** to bring each frame to life. The final clips are stitched together into a complete ad.

**Tools Required:**

- Claude (script development + prompt generation)

- Text-to-Image model (e.g., Nanobanana Pro, Midjourney, Flux)

- Image-to-Video model (e.g., Kling, Runway, Pika, Minimax)

- Video editor for final assembly (CapCut, Premiere, DaVinci)

## **Phase 1: Script Development**

Before generating any visuals, you need a locked script. This is non-negotiable — prompts built from a loose script produce unusable footage.

### **Step 1: Define Your Inputs**

Paste the following into Claude and fill in your brand specifics:

You are my expert direct response copywriter specializing in \[NICHE\].

My product: \[PRODUCT NAME + WHAT IT DOES\]

My target customer: \[GENDER, AGE RANGE, CORE PROBLEM\]

My unique mechanism of the problem: \[WHY THE PROBLEM EXISTS — THE ROOT CAUSE THEY DON'T KNOW ABOUT\]

My unique mechanism of the solution: \[HOW YOUR PRODUCT SPECIFICALLY SOLVES IT AT THAT ROOT CAUSE LEVEL\]

My traffic source: \[COLD FACEBOOK ADS / WARM RETARGETING / EMAIL / ETC.\]

My target video length: \[15 SEC / 30 SEC / 45 SEC / 60 SEC+\]

### **Step 2: Feed Claude a Winning Competitor Script**

Find a high-performing ad in any niche (doesn't need to be your niche) that uses a structure you want to model. Paste the full script into Claude and ask:

Please analyze this ad script through a direct response copywriting lens.

Tell me exactly why it performs well — break down the hook, story structure,

objection handling, mechanism delivery, proof elements, and CTA.

### **Step 3: Rewrite for Your Brand**

Once Claude has analyzed the competitor structure, ask:

Now rewrite this script for my \[PRODUCT\] targeting \[AVATAR\],

closely modeling the structure and hook format of the original.

Target length: roughly \[X\] seconds.

**Key rule:** If the output is too long, tell Claude the target word count. A 45-second conversational ad is roughly 110-120 words. A 15-second hook is roughly 30-40 words.

### **Step 4: Lock the Final Script**

Review the script. Edit until you're satisfied. Then break it into individual scenes — each scene is one visual setting or one speaker segment that will become its own clip.

**Example scene breakdown:**

| **Scene \#** | **Visual Setting**             | **Script Lines**                      | **Target Duration** |
|--------------|--------------------------------|---------------------------------------|---------------------|
| 1            | Melting woman in office        | "If you wake up feeling like this..." | 10-12 sec           |
| 2            | Animated NAD+ molecule         | "When you age, your body loses..."    | 6-8 sec             |
| 3            | Woman with product (lifestyle) | "That's where NMN comes in..."        | 5-7 sec             |
| 4            | Product scoop into water       | "One scoop every morning..."          | 6-8 sec             |
| 5            | Woman CTA (candid angle)       | "Run, don't walk..."                  | 5-6 sec             |

## **Phase 2: Text-to-Image Starting Frame Prompts**

For each scene, you generate a static image that becomes the first frame the Image-to-Video model animates from. The quality and specificity of this image determines everything downstream.

### **Prompt Template**

Use this structure for every Text-to-Image prompt. Paste it into Claude along with your scene description:

I need a Text-to-Image prompt for Scene \[#\] of my ad.

Scene description: \[DESCRIBE WHAT'S HAPPENING IN THIS SCENE\]

Script line being delivered: "\[PASTE THE EXACT SCRIPT LINE\]"

Visual style: \[PHOTOREALISTIC / CARTOON / MEDICAL ILLUSTRATION / UGC CANDID / ETC.\]

Mood/tone: \[UNSETTLING / WARM / CLINICAL / ENERGETIC / ETC.\]

Requirements:

\- Describe the subject in detail (age, clothing, expression, posture, what they're holding)

\- Describe the setting in detail (location, lighting, background elements)

\- Specify camera angle and lens (e.g., "shot on 85mm lens, shallow depth of field")

\- Specify what should NOT be in the frame

\- If there is a product in frame, describe the label text EXACTLY as it appears

on the real product (brand name, sub-text, bullet points, weight, all of it)

\- If there is overlay text on the image, write it out exactly and specify font

style, placement, and color

Do NOT include any production notes or editorial commentary.

Output the prompt only.

### **Critical Rules for Text-to-Image Prompts**

1.  **Product label accuracy:** If your product appears in frame, spell out every single line of text on the label inside the prompt. AI image generators butcher text — the more specific you are, the closer you get. Example:  
    >   
    >   
    > The canister is dark forest green with a matte finish. The label reads exactly as follows: small text at the top reads "simply nootropics" in lowercase. Below that in large bold uppercase letters reads "AGELESS NMN". Below that in smaller text reads "Anti-ageing with increased NAD+ levels". Below that three bullet points reading "Cellular Longevity\*" then "Supports Metabolism\*" then "Daily Energy\*". At the bottom "100 G \| 3.5 OZ" and "DIETARY SUPPLEMENT".

2.  **Camera and lens specs sell realism.** Always include: lens focal length, depth of field, lighting type. Example: "Shot on 85mm lens, shallow depth of field, warm natural morning light from the right, no dramatic color grading."

3.  **Describe what's NOT happening.** If the action (like pouring powder) should only happen in the video, explicitly say "No powder in motion, no pouring" in the image prompt. The image is a frozen moment BEFORE the action starts.

4.  **Reference images help.** If you have a product photo or a specific look you want, upload it alongside your prompt request and tell Claude: "Use this image as reference for the product/person/setting."

5.  **Iterate aggressively.** Your first generated image will rarely be perfect. Common fixes to request:

    - "Remove \[element\] from the frame"

    - "Change the camera angle to \[lower/higher/wider\]"

    - "The phone/product/text shouldn't be visible, adjust"

    - "Make it more \[candid/unsettling/clinical/etc.\]"

## **Phase 3: Image-to-Video Animation Prompts**

Once you have your approved starting frame image, you write the prompt that tells the video model how to animate it.

### **Prompt Template**

****I have my approved starting frame image for Scene \[#\].

Script line to be delivered in this clip: "\[EXACT SCRIPT LINE\]"

Target duration: \[X-X\] seconds

Speaker: \[WHO IS TALKING — on-screen character / voiceover / animated character\]

Requirements:

\- Specify what happens the INSTANT the clip begins (no pause? immediate speech? hold for 2 seconds?)

\- Describe all movement in the clip (what moves, how fast, in what direction)

\- Describe what stays STILL (critical for product label legibility)

\- Specify the delivery style of the dialogue (flat/energetic/warm/urgent/robotic)

\- Describe any sound design (ambient sounds, drips, impacts, silence)

\- Describe any visual events timed to specific words in the script

\- If there's a dramatic event (impact, explosion, transition), specify EXACTLY

which word triggers it and what happens

\- State the total target runtime

\- End with a hard cut instruction (cut to black / cut on final word / hold for one beat then cut)

Do NOT include music direction unless you specifically want scored footage.

Output the prompt only.

### **Critical Rules for Image-to-Video Prompts**

1.  **Timing is everything.** Specify whether speech starts immediately or after a hold. If you want zero pause, say: "The moment the clip begins she is already speaking." If you want a beat, say: "Hold completely still for 2 seconds, then..."

2.  **Anchor visual events to specific words.** Don't say "at some point a wrecking ball hits her." Say: "The instant the word 'video' leaves her mouth, a wrecking ball swings in from the right." The video model and your editor need precise timing cues.

3.  **Describe continuous background action separately from foreground action.** Example: "Wax drips continuously throughout the entire clip \[background\]. She speaks directly to camera without acknowledging it \[foreground\]."

4.  **Minimize movement for product shots.** If your product label needs to be legible, explicitly state: "Minimal movement throughout — only natural subtle breathing motion. Canister label faces directly forward and remains still and legible throughout."

5.  **Specify delivery style precisely.** "She speaks" is useless. "She speaks directly to camera, flat and unbothered, delivering the entire line in one continuous unbroken breath with no pauses" gives the model and your talent direction something to work with.

6.  **One clip = one continuous camera angle.** Don't ask for camera cuts inside a single Image-to-Video prompt. Each prompt produces one continuous shot. Cuts happen in your editor.

7.  **Sound design sells the uncanny.** Silence is a choice. "No music. Just the faint sound of wax dripping onto linoleum" is more unsettling than any score. Specify ambient sound or its absence.

## **Phase 4: Assembly Checklist**

After all scenes are generated:

- \[ \] Review each clip individually for quality, lip sync, and visual consistency

- \[ \] Regenerate any clips where the product label is illegible

- \[ \] Regenerate any clips where movement is too extreme or distracting

- \[ \] Import all clips into your video editor in scene order

- \[ \] Trim each clip to remove any AI artifacts at the start/end (first and last 0.5 sec are often unstable)

- \[ \] Add hard cuts between scenes (no transitions unless intentional)

- \[ \] Add text overlays if not baked into the generated footage

- \[ \] Add end card with product info, guarantee, and CTA

- \[ \] Export at platform-native specs (1080x1920 for Stories/Reels, 1080x1080 for feed)

- \[ \] Watch the full assembled ad 3x before publishing — check pacing, read the text aloud against the video timing

## **Quick-Reference: Prompt Type Cheat Sheet**

| **You Need...**                   | **Ask Claude For...**         | **Key Phrase**                                                                    |
|-----------------------------------|-------------------------------|-----------------------------------------------------------------------------------|
| A static first frame for a scene  | Text-to-Image prompt          | "Create a text-to-image prompt for this scene..."                                 |
| To animate that frame into a clip | Image-to-Video prompt         | "I have my image, now create an image-to-video prompt for this script line..."    |
| A jarring scroll-stop concept     | Creative concept brainstorm   | "Come up with 5 scroll-stopper image concepts that are visually jarring..."       |
| To fix a generated image          | Revised Text-to-Image prompt  | "Adjust the prompt — remove X, change Y, keep everything else the same"           |
| To fix movement/pacing in video   | Revised Image-to-Video prompt | "She should speak immediately with no pauses, adjust the prompt..."               |
| Product label accuracy            | Detailed label description    | "Go into exact detail about the text on the product so there are no misspellings" |

## **Example Workflow (Complete)**

**Scene 1 — Scroll-Stop Hook**

1.  Brainstormed 5 jarring visual concepts → selected "melting woman in office"

2.  Wrote Text-to-Image prompt with full scene detail (office, fluorescent light, wax melting, coffee mug, 85mm lens)

3.  Generated image → reviewed → requested adjustments (no legs visible, adjusted framing)

4.  Wrote Image-to-Video prompt: immediate speech, continuous wax dripping, wrecking ball on final word, 12-14 sec runtime

5.  Reviewed → adjusted pacing (removed pauses, continuous delivery, wrecking ball triggers on "watching")

**Scene 2 — Mechanism Explainer**

1.  Wrote Text-to-Image prompt for cartoon NAD+ molecule with face, glowing amber, dark background

2.  Generated image → iterated (removed left/right comparison, simplified to single character)

3.  Wrote Image-to-Video prompt: molecule speaks to camera, background molecules pop and vanish on "NAD+", continuous speech, 6-8 sec

**Scene 3 — Product Introduction**

1.  Used existing product photo as reference image

2.  Wrote Image-to-Video prompt: minimal movement, label must stay legible, one finger tap on "NMN comes in", 5-7 sec

**Scene 4 — Product Demo**

1.  Wrote Text-to-Image prompt: open canister, scoop in powder, glass of water beside it, NO pouring action in image

2.  Wrote Image-to-Video prompt: hand enters frame, scoops powder, pours into water, water turns light blue, voiceover delivers line, 6-8 sec

**Scene 5 — CTA**

1.  Wrote Text-to-Image prompt: candid low counter-level angle, woman looking down at camera, product on counter, no phone visible

2.  Wrote Image-to-Video prompt: most energetic delivery, points downward on "tapping the link", holds smile, 5-6 sec
