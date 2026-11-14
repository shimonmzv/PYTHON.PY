"""
בונה את חבילת ההפצה ל-Windows: dist/PFA_Scout_Messenger.zip

החבילה כוללת את uv.exe (מנהל Python של Astral), כך שהמשתמש לא צריך להתקין Python:
בהפעלה הראשונה START_PFA.bat מוריד את Python ואת הספריות לתוך תיקיית התוכנה.

הרצה:
    python build_release.py

הפלט באנגלית בלבד: קונסולת Windows (cp1252) לא יודעת להציג עברית.
"""

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# גרסת uv שנארזת בחבילה (נבדקה ב-CI על Windows)
UV_VERSION = "0.13.8"

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
ZIP_PATH = DIST / "PFA_Scout_Messenger.zip"

# קבצים שנכנסים לשורש החבילה
FILES = [
    "PFA_Guide.pdf",
    "START_PFA.bat",
    "scouts_template.xlsx",
    "app.py",
    "requirements.lock",
]


def fetch_uv_windows(work: Path) -> dict[str, bytes]:
    """מוריד מ-PyPI את ה-wheel של uv ל-Windows ומחלץ ממנו את uv.exe ואת קבצי הרישיון."""
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", f"uv=={UV_VERSION}",
            "--no-deps", "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", "3.13", "-d", str(work), "--quiet",
        ],
        check=True,
    )
    wheel = next(work.glob("uv-*.whl"))
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if name.endswith("/scripts/uv.exe"):
                found["bin/uv.exe"] = zf.read(name)
            elif "/licenses/" in name and base.startswith("LICENSE"):
                found[f"bin/uv-{base}"] = zf.read(name)
    if "bin/uv.exe" not in found:
        raise SystemExit("uv.exe not found in the wheel")
    return found


def main() -> None:
    missing = [f for f in FILES if not (HERE / f).exists()]
    if missing:
        raise SystemExit(f"missing files: {missing}")

    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        uv_files = fetch_uv_windows(Path(tmp))

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in FILES:
            zf.write(HERE / name, arcname=name)
        for arcname, data in sorted(uv_files.items()):
            zf.writestr(arcname, data)

    size_mb = ZIP_PATH.stat().st_size / 1_000_000
    print(f"built: {ZIP_PATH} ({size_mb:.1f} MB)")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        for info in zf.infolist():
            print(f"  {info.filename:28} {info.file_size:>10,}")


if __name__ == "__main__":
    main()
