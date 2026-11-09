# Builds PFA_Guide.pdf from guide.html: inlines the screenshots (and the Heebo
# font, if fonts/Heebo.ttf exists) so headless Chrome can render it from anywhere.
# Usage: python build.py  ->  guide_build.html, then render it to PDF with
#   chrome --headless --no-pdf-header-footer --print-to-pdf=../PFA_Guide.pdf guide_build.html
import base64
import re
from pathlib import Path

here = Path(__file__).parent
src = (here / "guide.html").read_text(encoding="utf-8")
b64 = lambda p: base64.b64encode((here / p).read_bytes()).decode()

font = here / "fonts" / "Heebo.ttf"
if font.exists():
    src = src.replace('url("fonts/Heebo.ttf") format("truetype")',
                      f'url(data:font/ttf;base64,{b64("fonts/Heebo.ttf")}) format("truetype")')
src = re.sub(r'src="(img/[^"]+\.png)"',
             lambda m: f'src="data:image/png;base64,{b64(m.group(1))}"', src)
(here / "guide_build.html").write_text(src, encoding="utf-8")
print("wrote guide_build.html")
