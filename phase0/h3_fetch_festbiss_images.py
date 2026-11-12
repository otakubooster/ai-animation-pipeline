"""Extract the Festbiss product photos embedded in the landing page (German packaging) for the H3 spike.
  python phase0/h3_fetch_festbiss_images.py OUT_DIR   -> product_de.jpg (packshot), apply_first_frame_9x16.jpg"""
import base64, io, re, subprocess, sys
from pathlib import Path
from PIL import Image, ImageFilter

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
html = subprocess.run(["curl", "-sSL", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "https://artikel.festbiss.de/reasonsdental/"],
                      capture_output=True, check=True).stdout.decode("utf-8", "ignore")
imgs = [Image.open(io.BytesIO(base64.b64decode(m.group(2)))).convert("RGB")
        for m in re.finditer(r"data:image/(png|jpe?g|webp);base64,([A-Za-z0-9+/=]+)", html)]
print(len(imgs), [im.size for im in imgs])
imgs[2].save(out / "product_de.jpg", quality=92)                      # box + tube on the counter
a = imgs[1]                                                           # hands applying gel, 1200x1200
w = round(a.height * 9 / 16)
a.crop(((a.width - w) // 2, 0, (a.width - w) // 2 + w, a.height)).resize((576, 1024), Image.LANCZOS).save(out / "apply_first_frame_9x16.jpg", quality=92)
