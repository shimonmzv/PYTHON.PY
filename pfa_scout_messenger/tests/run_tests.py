"""
בדיקות אוטומטיות ל-PFA Scout Messenger.

    python tests/run_tests.py [APP_DIR] [--no-browser]

APP_DIR — התיקייה שבה נמצא app.py (ברירת מחדל: תיקיית הפרויקט). ב-CI זו התיקייה
שחולצה מקובץ ה-ZIP, כך שנבדק בדיוק מה שהמשתמש מקבל.
--no-browser — דילוג על בדיקות Selenium (שדורשות Google Chrome).
"""

import io
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

HERE = Path(__file__).resolve().parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
APP_DIR = Path(args[0]).resolve() if args else HERE.parent
WITH_BROWSER = "--no-browser" not in sys.argv

sys.path.insert(0, str(APP_DIR))
import app  # noqa: E402
import openpyxl  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, func) -> None:
    try:
        func()
        results.append((name, True, ""))
        print(f"PASS  {name}")
    except Exception:
        results.append((name, False, traceback.format_exc()))
        print(f"FAIL  {name}\n{traceback.format_exc()}")


# ------------------------------------------------------------------ לוגיקה

def test_normalize_phone():
    cases = {
        "972501234567": "972501234567", 972501234567: "972501234567",
        "972501234567.0": "972501234567", "+972-50-123-4567": "972501234567",
        "050-1234567": "972501234567", "00972501234567": "972501234567",
        "447911123456": "447911123456",
        "12345": None, None: None, float("nan"): None, "abc": None,
    }
    for raw, expected in cases.items():
        got = app.normalize_phone(raw)
        assert got == expected, f"{raw!r}: got {got!r}, expected {expected!r}"


def test_template_is_empty_and_valid():
    template = APP_DIR / "scouts_template.xlsx"
    df, warnings = app.load_contacts(template)
    assert len(df) == 0 and warnings == [], (len(df), warnings)


def test_filled_template():
    wb = openpyxl.load_workbook(APP_DIR / "scouts_template.xlsx")
    ws = wb.active
    for row in (
        ["ישראל ישראלי", "0501234567"],
        ["דנה כהן", 972521234567],
        ["", "972531111111"],          # בלי שם — ידולג
        ["רון", "123"],                # מספר לא תקין — ידולג
        ["כפול", "+972 50-123-4567"],  # כפול — ידולג
    ):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    df, warnings = app.load_contacts(buf)
    assert df["Phone"].tolist() == ["972501234567", "972521234567"], df.to_dict("records")
    assert len(warnings) == 3, warnings


def test_missing_columns():
    wb = openpyxl.Workbook()
    wb.active.append(["שם", "טלפון"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    try:
        app.load_contacts(buf)
    except ValueError:
        return
    raise AssertionError("expected ValueError for wrong headers")


def test_build_message_and_url():
    random.seed(7)
    msg = app.build_message("{name}, בדיקה & 100%\nשורה 2 {name}", "דני")
    assert msg.split(" ", 1)[0] in app.GREETINGS
    assert msg.count("דני") == 2
    url = app.SEND_URL_TEMPLATE.format(phone="972501234567", text=quote(msg, safe=""))
    assert parse_qs(urlparse(url).query)["text"][0] == msg


# ------------------------------------------------------------------ דפדפן

def run_browser_tests():
    fake_page = (HERE / "fake_whatsapp.html").as_uri()
    app.SEND_URL_TEMPLATE = fake_page + "?phone={phone}&text={text}"
    app.POST_SEND_PAUSE_SECONDS = 0
    app.CHROME_PROFILE_DIR = Path(tempfile.mkdtemp(prefix="pfa_profile_"))

    driver = None

    def launch():
        nonlocal driver
        driver = app.create_driver()  # בדיוק כמו באפליקציה: Chrome גלוי + Selenium Manager

    check("browser: Chrome starts via create_driver()", launch)
    if driver is None:
        return
    try:
        for phone, expected in (
            ("972501111111", "כפתור שליחה"),  # לחיצה על הכפתור עובדת
            ("972502222222", "Enter"),        # הלחיצה נבלעת — שליחה ב-Enter (התקלה שדווחה)
            ("972503333333", "כפתור שליחה"),  # שם אייקון חדש + aria-label
            ("972505555555", "כפתור שליחה"),  # לחיצה מוקדמת נבלעת
        ):
            def scenario(phone=phone, expected=expected):
                msg = app.build_message("{name}, בדיקה & 100%\nשורה 2", "דני")
                method = app.send_message(driver, phone, msg, timeout=15)
                sent = driver.execute_script("return window.sent")
                assert method == expected, f"method {method!r}, expected {expected!r}"
                assert sent == [msg], f"sent {sent!r}"
            check(f"browser: send flow, mode {phone[-1]}", scenario)

        def nothing_sends():
            try:
                app.send_message(driver, "972504444444", "x", timeout=15)
            except app.MessageNotSentError:
                assert driver.execute_script("return window.sent") == []
                return
            raise AssertionError("expected MessageNotSentError")
        check("browser: message typed but never sent -> MessageNotSentError", nothing_sends)

        def invalid_number():
            start = time.time()
            try:
                app.send_message(driver, "972500000000", "x", timeout=5)
            except app.TimeoutException:
                assert time.time() - start < 15
                return
            raise AssertionError("expected TimeoutException")
        check("browser: invalid number -> TimeoutException", invalid_number)
    finally:
        driver.quit()


def main() -> int:
    print(f"app.py from: {APP_DIR}")
    print(f"Python {sys.version.split()[0]} on {sys.platform}\n")
    for name, func in (
        ("normalize_phone", test_normalize_phone),
        ("template is empty and valid", test_template_is_empty_and_valid),
        ("filled template loads, bad rows skipped", test_filled_template),
        ("wrong headers raise ValueError", test_missing_columns),
        ("build_message + URL encoding", test_build_message_and_url),
    ):
        check(name, func)
    if WITH_BROWSER:
        run_browser_tests()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
