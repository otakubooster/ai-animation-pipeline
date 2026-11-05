# Phase 0 test frames (make these in ChatGPT)

10 images: a first and a last frame for 5 shots, same character throughout. Do it all in **one ChatGPT chat** so the character stays consistent. Ask for **portrait 2:3** every time. The spike crops them to 480×832 (9:16).

**Message 1 (character):**
> Create an original 3D animated character in a Pixar-like feature-film style (not any existing character): a cheerful woman in her late 20s with short curly auburn hair, big expressive green eyes, freckles, wearing a mustard-yellow knit sweater and dark-blue jeans. Soft studio lighting, warm colour palette, clean background. Full-body front view, portrait 2:3.

Then one message per frame. Always start with *"Same character, same outfit, same 3D style and lighting, portrait 2:3."*

| File | Prompt (after the prefix) |
|---|---|
| c1_first.png | Medium shot in a cosy sunlit kitchen, she holds a steaming mug at chest height, looking down at it. |
| c1_last.png | Same kitchen and framing, slightly closer: she lifts the mug towards the viewer and smiles warmly. |
| c2_first.png | Close-up of her face against a soft pastel background, surprised expression, mouth open. |
| c2_last.png | Same close-up and background: she is laughing, eyes squeezed, head tilted back slightly. |
| c3_first.png | Wide shot on a sunny small-town street, she walks towards the viewer, far away. |
| c3_last.png | Same street and light, she is now much closer to the viewer, mid-stride, smiling. |
| c4_first.png | Medium shot on a living-room sofa, she looks at a smartphone in her hands. |
| c4_last.png | Same sofa and framing: she holds the phone up with the screen facing the viewer, grinning. |
| c5_first.png | Medium close-up in front of a plain light-blue wall, she looks into the camera, neutral friendly face. |
| c5_last.png | Same wall and framing: she waves at the viewer with her right hand, mouth open as if talking. |

Upload all 10 to the chat with Claude (or commit them here). Claude then copies `cases.example.json` to `cases.json`.
