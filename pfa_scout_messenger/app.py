# -*- coding: utf-8 -*-
"""
PFA Scout Messenger
כלי מקומי לשליחת הודעות WhatsApp אישיות לסקאוטים מתוך קובץ אקסל,
ללא צורך בשמירת המספרים באנשי הקשר.

הרצה:
    streamlit run app.py
"""

import io
import os
import random
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

import pandas as pd
import streamlit as st
from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

# ============================================================
# הגדרות קבועות
# ============================================================

WHATSAPP_URL = "https://web.whatsapp.com"
SEND_URL_TEMPLATE = "https://web.whatsapp.com/send?phone={phone}&text={text}"

# כפתור השליחה. הביטוי הראשון הוא זה שנדרש באפיון; השאר מכסים גרסאות חדשות
# של WhatsApp Web שבהן שם האייקון שונה. אם WhatsApp ישנו את ה-DOM — מעדכנים כאן.
SEND_BUTTON_XPATH = (
    '//span[@data-icon="send"]'
    ' | //span[contains(@data-icon, "send")]'
    ' | //button[@aria-label="Send" or @aria-label="שליחה" or @aria-label="שלח"]'
)

# תיבת כתיבת ההודעה בצ'אט הפתוח (שם WhatsApp מזריק את הטקסט מה-URL)
COMPOSER_XPATH = (
    '//footer//div[@contenteditable="true"]'
    ' | //div[@id="main"]//div[@contenteditable="true"]'
)

# אלמנט שמופיע רק אחרי התחברות מוצלחת (רשימת הצ'אטים בצד)
LOGGED_IN_CSS = "#pane-side"

# פרופיל כרום קבוע — שומר את החיבור ל-WhatsApp, כך שסריקת QR נדרשת רק בפעם הראשונה.
# ב-Windows הוא נשמר בתיקייה המקומית של המשתמש (LOCALAPPDATA) ולא ליד התוכנה,
# כדי שלא יסונכרן ל-OneDrive ולא ישותף בטעות יחד עם תיקיית התוכנה.
_LOCAL_DATA = os.environ.get("LOCALAPPDATA")
CHROME_PROFILE_DIR = (
    Path(_LOCAL_DATA) / "PFA_Scout_Messenger" / "chrome_profile"
    if _LOCAL_DATA
    else Path(__file__).resolve().parent / "chrome_profile"
)

# ChromeDriver רשמי של Google (קובץ חתום), שמתאים לגרסת ה-Chrome המותקנת.
# לא משתמשים ב-Selenium Manager כי הקובץ שלו (selenium-manager.exe) לא חתום,
# ו-Smart App Control ב-Windows 11 חוסם אותו.
CHROMEDRIVER_DIR = (
    Path(_LOCAL_DATA) / "PFA_Scout_Messenger" / "chromedriver" if _LOCAL_DATA else None
)
CFT_LATEST_URL = "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major}"
CFT_DRIVER_URL = (
    "https://storage.googleapis.com/chrome-for-testing-public/{version}/win64/chromedriver-win64.zip"
)

# רשימת ברכות פתיחה לגיוון הטקסט (Text Spinning)
GREETINGS = ["אהלן", "שלום", "היי", "הי", "שלום רב"]

# טווח ההשהיה האקראית (בשניות) אחרי כל הודעה שנשלחה בהצלחה — מנגנון נגד ספאם
ANTI_SPAM_DELAY_RANGE = (25, 45)

# השהיה קצרה אחרי כישלון, כדי לא להעמיס על WhatsApp Web
FAILURE_PAUSE_SECONDS = 5

# זמן קצר לאחר טעינת הצ'אט לפני השליחה — לחיצה מוקדמת מדי נבלעת ע"י WhatsApp
PRE_SEND_PAUSE_SECONDS = 1.5

# זמן המתנה (לכל ניסיון שליחה) לכך שההודעה תעזוב את תיבת הכתיבה
SEND_CONFIRM_TIMEOUT = 6

# זמן נוסף לאחר השליחה כדי לאפשר להודעה לעבור לשרת לפני מעבר לצ'אט הבא
POST_SEND_PAUSE_SECONDS = 3

DEFAULT_TEMPLATE = (
    "{name}, כאן מ-Premier Football Agency.\n"
    "רציתי לשתף אותך בכמה שחקנים מעניינים שאנחנו מייצגים העונה.\n"
    "אשמח לשלוח פרטים אם זה רלוונטי עבורך."
)


# ============================================================
# עיבוד נתונים
# ============================================================

def normalize_phone(raw) -> str | None:
    """
    מנרמל מספר טלפון לפורמט בינלאומי ספרתי בלבד (למשל 972501234567).
    מחזיר None אם המספר לא תקין.
    """
    if pd.isna(raw):
        return None

    text = str(raw).strip()

    # אקסל לעיתים שומר מספרים כ-float, למשל "972501234567.0"
    if text.endswith(".0"):
        text = text[:-2]

    digits = re.sub(r"\D", "", text)

    # קידומת חיוג בינלאומית בפורמט 00
    if digits.startswith("00"):
        digits = digits[2:]
    # מספר ישראלי מקומי (050...) -> פורמט בינלאומי (97250...)
    elif digits.startswith("0"):
        digits = "972" + digits[1:]

    # מספר בינלאומי תקין הוא באורך 10 עד 15 ספרות
    if not 10 <= len(digits) <= 15:
        return None

    return digits


def load_contacts(uploaded_file) -> tuple[pd.DataFrame, list[str]]:
    """
    קורא את קובץ האקסל, מוודא שקיימות העמודות Name ו-Phone,
    מנקה ומנרמל את הנתונים. מחזיר את הטבלה הנקייה ורשימת אזהרות.
    """
    # קריאת כל העמודות כטקסט כדי לא לאבד ספרות במספרי הטלפון
    df = pd.read_excel(uploaded_file, dtype=str)

    # התאמת שמות עמודות ללא תלות ברווחים או באותיות גדולות/קטנות
    df.columns = [str(col).strip() for col in df.columns]
    column_map = {col.lower(): col for col in df.columns}
    missing = [req for req in ("Name", "Phone") if req.lower() not in column_map]
    if missing:
        raise ValueError(
            f"בקובץ חסרות העמודות: {', '.join(missing)}. "
            "הקובץ חייב לכלול עמודה Name ועמודה Phone."
        )

    df = df.rename(
        columns={column_map["name"]: "Name", column_map["phone"]: "Phone"}
    )[["Name", "Phone"]]

    warnings: list[str] = []
    clean_rows: list[dict] = []
    seen_phones: set[str] = set()

    for excel_row, (name, phone) in enumerate(
        zip(df["Name"], df["Phone"]), start=2  # שורה 1 באקסל היא הכותרות
    ):
        # דילוג על שורות ריקות לחלוטין
        if pd.isna(name) and pd.isna(phone):
            continue

        name = "" if pd.isna(name) else str(name).strip()
        normalized = normalize_phone(phone)

        if not name:
            warnings.append(f"שורה {excel_row}: חסר שם — השורה תדולג.")
            continue
        if normalized is None:
            warnings.append(
                f"שורה {excel_row} ({name}): מספר טלפון לא תקין '{phone}' — השורה תדולג."
            )
            continue
        if normalized in seen_phones:
            warnings.append(
                f"שורה {excel_row} ({name}): המספר {normalized} כפול — השורה תדולג."
            )
            continue

        seen_phones.add(normalized)
        clean_rows.append({"Name": name, "Phone": normalized})

    return pd.DataFrame(clean_rows, columns=["Name", "Phone"]), warnings


def build_message(template: str, name: str) -> str:
    """
    בונה את ההודעה האישית: מחליף את {name} בשם האמיתי
    ומוסיף ברכת פתיחה אקראית בתחילת ההודעה (Text Spinning).
    """
    body = template.replace("{name}", name).strip()
    greeting = random.choice(GREETINGS)
    return f"{greeting} {body}"


# ============================================================
# אוטומציית דפדפן (Selenium)
# ============================================================

def installed_chrome_version() -> str | None:
    """מחזיר את גרסת Google Chrome המותקנת ב-Windows (למשל '142.0.7444.59'), או None."""
    if sys.platform != "win32":
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, r"Software\Google\Chrome\BLBeacon") as key:
                return winreg.QueryValueEx(key, "version")[0]
        except OSError:
            pass
    # גיבוי: תיקיית הגרסה שליד chrome.exe (Application\<version>)
    versions = []
    for base in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        app_dir = Path(os.environ.get(base, "")) / "Google" / "Chrome" / "Application"
        versions += [p.name for p in app_dir.glob("*.*.*.*") if p.is_dir()]
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".") if x.isdigit()))


def ensure_chromedriver() -> str | None:
    """
    מוריד (פעם אחת לכל גרסה ראשית של Chrome) את ה-ChromeDriver הרשמי והחתום של Google.
    מחזיר את הנתיב ל-chromedriver.exe, או None אם לא הצליח — ואז Selenium Manager ינסה במקומו.
    """
    version = installed_chrome_version()
    if not version or CHROMEDRIVER_DIR is None:
        return None
    major = version.split(".")[0]
    driver_exe = CHROMEDRIVER_DIR / major / "chromedriver-win64" / "chromedriver.exe"
    if driver_exe.exists():
        return str(driver_exe)
    try:
        with urlopen(CFT_LATEST_URL.format(major=major), timeout=20) as resp:
            driver_version = resp.read().decode().strip()
        with urlopen(CFT_DRIVER_URL.format(version=driver_version), timeout=120) as resp:
            archive = resp.read()
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(CHROMEDRIVER_DIR / major)
    except (OSError, ValueError, zipfile.BadZipFile):
        return None
    return str(driver_exe) if driver_exe.exists() else None


def create_driver() -> webdriver.Chrome:
    """מפעיל דפדפן Chrome גלוי (לא headless) עם פרופיל קבוע."""
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    options.add_argument("--start-maximized")

    driver_path = ensure_chromedriver()
    service = Service(executable_path=driver_path) if driver_path else Service()
    driver = webdriver.Chrome(options=options, service=service)
    driver.implicitly_wait(0)  # כל ההמתנות מתבצעות במפורש עם WebDriverWait
    return driver


def is_logged_in(driver: webdriver.Chrome) -> bool:
    """בודק (ללא המתנה) האם רשימת הצ'אטים מוצגת — כלומר המשתמש מחובר."""
    return bool(driver.find_elements(By.CSS_SELECTOR, LOGGED_IN_CSS))


def _message_pending(driver: webdriver.Chrome) -> bool:
    """
    האם יש הודעה שממתינה לשליחה בתיבת הכתיבה (אחרי שליחה מוצלחת התיבה מתרוקנת).
    אם תיבת הכתיבה לא נמצאה בכלל — מסתמכים על כך שכפתור השליחה גלוי.
    """
    boxes = driver.find_elements(By.XPATH, COMPOSER_XPATH)
    if boxes:
        return any(box.text.strip() for box in boxes)
    return any(btn.is_displayed() for btn in driver.find_elements(By.XPATH, SEND_BUTTON_XPATH))


def _wait_until_sent(driver: webdriver.Chrome) -> bool:
    """ממתין שההודעה תעזוב את תיבת הכתיבה. מחזיר True אם נשלחה."""
    try:
        WebDriverWait(
            driver, SEND_CONFIRM_TIMEOUT, ignored_exceptions=(StaleElementReferenceException,)
        ).until(lambda d: not _message_pending(d))
        return True
    except TimeoutException:
        return False


def _click_send_button(driver: webdriver.Chrome) -> bool:
    """לוחץ על כפתור השליחה. מחזיר False אם הכפתור לא נמצא."""
    for icon in driver.find_elements(By.XPATH, SEND_BUTTON_XPATH):
        if not icon.is_displayed():
            continue
        # לחיצה על הכפתור עצמו ולא על האייקון שבתוכו — לחיצה על האייקון לפעמים לא נקלטת
        buttons = icon.find_elements(
            By.XPATH, './ancestor-or-self::*[self::button or @role="button"][1]'
        )
        target = buttons[0] if buttons else icon
        try:
            target.click()
        except (NoSuchWindowException, InvalidSessionIdException):
            raise
        except WebDriverException:
            # למשל כשאלמנט אחר מסתיר את הכפתור — לחיצה ישירה דרך JavaScript
            driver.execute_script("arguments[0].click();", target)
        return True
    return False


def _press_enter(driver: webdriver.Chrome) -> bool:
    """שולח את ההודעה בלחיצת Enter בתיבת הכתיבה. מחזיר False אם התיבה ריקה או לא נמצאה."""
    boxes = [box for box in driver.find_elements(By.XPATH, COMPOSER_XPATH) if box.text.strip()]
    if not boxes:
        return False
    boxes[-1].send_keys(Keys.ENTER)
    return True


def send_message(
    driver: webdriver.Chrome, phone: str, message: str, timeout: int
) -> str:
    """
    פותח צ'אט ישירות דרך כתובת URL עם ההודעה מוזרקת ושולח אותה.
    שיטת השליחה: לחיצה על כפתור השליחה, ואם WhatsApp לא הגיב — Enter בתיבת הכתיבה.
    מחזיר את שם השיטה שעבדה.
    זורק TimeoutException אם הצ'אט לא נטען (למשל: מספר לא רשום ב-WhatsApp),
    ו-MessageNotSentError אם ההודעה הוקלדה אך לא נשלחה.
    """
    encoded_message = quote(message, safe="")
    driver.get(SEND_URL_TEMPLATE.format(phone=phone, text=encoded_message))

    # הצ'אט מוכן כשההודעה המוזרקת מופיעה בתיבת הכתיבה / כפתור השליחה מופיע
    WebDriverWait(
        driver, timeout, ignored_exceptions=(StaleElementReferenceException,)
    ).until(_message_pending)
    time.sleep(PRE_SEND_PAUSE_SECONDS)

    for method_name, attempt in (("כפתור שליחה", _click_send_button), ("Enter", _press_enter)):
        try:
            acted = attempt(driver)
        except StaleElementReferenceException:
            continue  # הדף התעדכן בדיוק ברגע הלחיצה — עוברים לשיטה הבאה
        if acted and _wait_until_sent(driver):
            # זמן קצר לסנכרון ההודעה מול השרת לפני מעבר לצ'אט הבא
            time.sleep(POST_SEND_PAUSE_SECONDS)
            return method_name

    raise MessageNotSentError


# ============================================================
# רכיבי ממשק
# ============================================================

class MessageNotSentError(Exception):
    """הצ'אט נטען וההודעה הוקלדה, אבל היא לא יצאה גם אחרי כל ניסיונות השליחה."""


class LiveLog:
    """יומן ריצה שמתעדכן בזמן אמת בתוך placeholder של Streamlit (הרשומה החדשה למעלה)."""

    def __init__(self, placeholder):
        self._placeholder = placeholder
        self._lines: list[str] = []

    def _add(self, icon: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._lines.append(f"`{stamp}` {icon} {text}")
        self._placeholder.markdown("  \n".join(reversed(self._lines)))

    def info(self, text: str) -> None:
        self._add("ℹ️", text)

    def success(self, text: str) -> None:
        self._add("✅", text)

    def warning(self, text: str) -> None:
        self._add("⚠️", text)

    def error(self, text: str) -> None:
        self._add("❌", text)

    def wait(self, text: str) -> None:
        self._add("⏳", text)


def countdown(seconds: float, box, message: str) -> None:
    """
    ממתין את מספר השניות המבוקש תוך עדכון ספירה לאחור בממשק,
    כדי שהמשתמש יראה שהאפליקציה לא תקועה.
    """
    deadline = time.time() + seconds
    while (remaining := deadline - time.time()) > 0:
        box.info(f"⏳ {message} — נותרו {int(remaining) + 1} שניות")
        time.sleep(min(1.0, remaining))


def wait_for_login(driver, seconds: int, box, log: LiveLog) -> None:
    """
    ההשהיה הראשונית לסריקת קוד ה-QR. מסתיימת מוקדם אם זוהתה התחברות
    (למשל כשהפרופיל השמור כבר מחובר).
    """
    log.info(f"ממתין עד {seconds} שניות לסריקת קוד ה-QR...")
    deadline = time.time() + seconds
    while (remaining := deadline - time.time()) > 0:
        if is_logged_in(driver):
            log.success("זוהתה התחברות ל-WhatsApp Web.")
            return
        box.info(
            f"📱 סרוק את קוד ה-QR בחלון הכרום שנפתח — נותרו {int(remaining) + 1} שניות"
        )
        time.sleep(min(1.0, remaining))

    if is_logged_in(driver):
        log.success("זוהתה התחברות ל-WhatsApp Web.")
    else:
        log.warning(
            "לא זוהתה התחברות בתום זמן ההמתנה. ממשיך בכל זאת — "
            "אם ההודעות נכשלות, הגדל את זמן ההשהיה הראשונית."
        )


def run_automation(
    contacts: pd.DataFrame,
    template: str,
    initial_delay: int,
    chat_timeout: int,
    results: list[dict],
) -> None:
    """
    מריץ את תהליך השליחה המלא ומעדכן את הממשק בזמן אמת.
    התוצאות נרשמות לתוך results תוך כדי ריצה, כך שהן נשמרות גם אם הריצה נעצרת באמצע.
    """
    total = len(contacts)

    # רכיבי תצוגה דינמיים שמתעדכנים בזמן אמת
    status_box = st.empty()
    current_box = st.empty()
    progress_bar = st.progress(0.0, text=f"0 / {total}")
    st.markdown("**יומן ריצה**")
    log = LiveLog(st.container(height=320, border=True).empty())

    status_box.info("🔄 מפעיל את דפדפן Chrome...")
    try:
        driver = create_driver()
    except WebDriverException as exc:
        log.error(f"לא ניתן להפעיל את Chrome: {exc.msg or exc}")
        status_box.error("שגיאה בהפעלת הדפדפן. ודא ש-Google Chrome מותקן במחשב.")
        return

    try:
        log.info("הדפדפן נפתח. טוען את WhatsApp Web...")
        driver.get(WHATSAPP_URL)

        status_box.info("🔄 ממתין להתחברות ל-WhatsApp Web...")
        wait_for_login(driver, initial_delay, current_box, log)

        for index, (name, phone) in enumerate(
            zip(contacts["Name"], contacts["Phone"]), start=1
        ):
            status_box.info(f"🔄 שולח הודעה {index} מתוך {total}...")
            current_box.info(f"📤 מעבד כעת: **{name}** — `{phone}` ({index}/{total})")
            message = build_message(template, name)

            try:
                method = send_message(driver, phone, message, chat_timeout)
            except TimeoutException:
                # מספר לא תקין / לא רשום ב-WhatsApp / הצ'אט לא נטען בזמן
                log.error(
                    f"{name} ({phone}): הצ'אט לא נטען תוך {chat_timeout} שניות — "
                    "ככל הנראה למספר אין חשבון WhatsApp. ממשיך לשורה הבאה."
                )
                results.append({"שם": name, "טלפון": phone, "סטטוס": "נכשל — לא נטען"})
                time.sleep(FAILURE_PAUSE_SECONDS)
            except MessageNotSentError:
                log.error(
                    f"{name} ({phone}): ההודעה הוקלדה אך WhatsApp לא שלח אותה "
                    "(לא בלחיצה על כפתור השליחה ולא ב-Enter). ממשיך לשורה הבאה."
                )
                results.append({"שם": name, "טלפון": phone, "סטטוס": "נכשל — לא נשלח"})
                time.sleep(FAILURE_PAUSE_SECONDS)
            except (NoSuchWindowException, InvalidSessionIdException):
                raise  # הדפדפן נסגר — אין טעם להמשיך לשורות הבאות
            except WebDriverException as exc:
                log.error(f"{name} ({phone}): שגיאת דפדפן — {exc.msg or exc}")
                results.append({"שם": name, "טלפון": phone, "סטטוס": "נכשל — שגיאה"})
                time.sleep(FAILURE_PAUSE_SECONDS)
            else:
                log.success(f"ההודעה נשלחה אל {name} ({phone}) [שיטה: {method}].")
                results.append({"שם": name, "טלפון": phone, "סטטוס": "נשלח"})

                # השהיה אקראית נגד ספאם אחרי כל שליחה מוצלחת (לא אחרי האחרונה)
                if index < total:
                    delay = random.uniform(*ANTI_SPAM_DELAY_RANGE)
                    log.wait(f"השהיה נגד ספאם: {delay:.0f} שניות לפני ההודעה הבאה.")
                    countdown(delay, current_box, "השהיה נגד ספאם")

            progress_bar.progress(index / total, text=f"{index} / {total}")

        current_box.empty()
        sent = sum(r["סטטוס"] == "נשלח" for r in results)
        summary = f"הסתיים: {sent} נשלחו, {total - sent} נכשלו."
        if sent == total:
            status_box.success(f"✅ {summary}")
        else:
            status_box.warning(f"⚠️ {summary}")

    except (NoSuchWindowException, InvalidSessionIdException):
        current_box.empty()
        log.error("חלון הדפדפן נסגר — הריצה הופסקה.")
        status_box.error("הריצה הופסקה: חלון הדפדפן נסגר.")
    finally:
        try:
            driver.quit()
        except WebDriverException:
            pass  # הדפדפן כבר סגור


def inject_rtl_style() -> None:
    """עיצוב מימין לשמאל (RTL) לממשק בעברית."""
    st.markdown(
        """
        <style>
            .stApp, .stMarkdown, .stTextArea, .stNumberInput, .stFileUploader,
            .stAlert, .stExpander {
                direction: rtl;
                text-align: right;
            }
            textarea { direction: rtl; text-align: right; }
            code { direction: ltr; unicode-bidi: embed; }
            [data-testid="stDataFrame"] { direction: ltr; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# האפליקציה הראשית
# ============================================================

def main() -> None:
    st.set_page_config(page_title="PFA Scout Messenger", page_icon="⚽", layout="centered")
    inject_rtl_style()

    st.title("⚽ PFA Scout Messenger")
    st.caption("Premier Football Agency · שליחת הודעות WhatsApp אישיות לסקאוטים מתוך קובץ אקסל")

    # --- שלב 1: העלאת קובץ ---
    st.subheader("1. קובץ סקאוטים")
    uploaded_file = st.file_uploader(
        "העלה קובץ אקסל (‎.xlsx) עם שתי עמודות: Name, Phone",
        type=["xlsx"],
        help="מספרי טלפון בפורמט בינלאומי, למשל 972501234567. מספרים ישראליים בפורמט 050... יומרו אוטומטית.",
    )

    contacts: pd.DataFrame | None = None
    if uploaded_file is not None:
        try:
            contacts, warnings = load_contacts(uploaded_file)
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:  # קובץ פגום או לא קריא
            st.error(f"לא ניתן לקרוא את הקובץ: {exc}")
        else:
            st.success(f"נטענו {len(contacts)} סקאוטים תקינים.")
            if warnings:
                with st.expander(f"⚠️ {len(warnings)} שורות ידולגו"):
                    for warning in warnings:
                        st.write(warning)
            st.dataframe(contacts, width="stretch", hide_index=True)

    # --- שלב 2: תבנית הודעה ---
    st.subheader("2. תבנית ההודעה")
    template = st.text_area(
        "כתוב את ההודעה. המשתנה {name} יוחלף בשם הסקאוט.",
        value=DEFAULT_TEMPLATE,
        height=160,
    )
    st.caption(
        f"ברכת פתיחה אקראית ({' / '.join(GREETINGS)}) תתווסף אוטומטית בתחילת כל הודעה — "
        "אין צורך לפתוח את התבנית בברכה."
    )
    if "{name}" not in template:
        st.warning("התבנית לא כוללת {name} — כל הסקאוטים יקבלו הודעה זהה (מלבד הברכה).")

    if contacts is not None and not contacts.empty and template.strip():
        with st.expander("👁️ תצוגה מקדימה (סקאוט ראשון ברשימה)"):
            st.text(build_message(template, contacts["Name"].iloc[0]))

    # --- שלב 3: הגדרות ---
    st.subheader("3. הגדרות")
    initial_delay = st.number_input(
        "השהיה ראשונית לסריקת קוד QR (שניות)",
        min_value=0,
        max_value=600,
        value=60,
        step=5,
        help="הזמן המקסימלי לסריקת הקוד בטלפון. אם החיבור יזוהה מוקדם יותר — השליחה תתחיל מיד.",
    )
    with st.expander("הגדרות מתקדמות"):
        chat_timeout = st.number_input(
            "זמן המתנה מקסימלי לטעינת צ'אט (שניות)",
            min_value=10,
            max_value=180,
            value=45,
            step=5,
            help="אם כפתור השליחה לא מופיע בזמן הזה, המספר ייחשב כלא תקין והשליחה תעבור לשורה הבאה.",
        )

    # --- שלב 4: הפעלה ---
    st.subheader("4. הפעלה")
    ready = contacts is not None and not contacts.empty and bool(template.strip())
    min_delay, max_delay = ANTI_SPAM_DELAY_RANGE
    if ready:
        st.info(
            f"בין הודעה להודעה תהיה השהיה אקראית של {min_delay}–{max_delay} שניות. "
            f"זמן ריצה משוער ל-{len(contacts)} סקאוטים: כ-"
            f"{len(contacts) * (min_delay + max_delay) / 2 / 60:.0f} דקות ומעלה. "
            "אל תסגור את חלון הכרום ואל תשתמש בו במהלך הריצה."
        )

    start = st.button(
        "🚀 התחל אוטומציה (Start Automation)",
        type="primary",
        disabled=not ready,
        width="stretch",
    )

    if start and ready:
        st.session_state["results"] = []
        st.session_state["running"] = True
        # כל לחיצה בממשק במהלך הריצה גורמת ל-Streamlit להריץ מחדש את הסקריפט,
        # מה שעוצר את הלולאה, סוגר את הדפדפן ומציג את מה שנשלח עד כה.
        st.button("⏹️ עצור שליחה", width="stretch")
        st.caption("שים לב: שינוי של שדה כלשהו בדף במהלך הריצה יעצור אותה.")
        run_automation(
            contacts,
            template,
            int(initial_delay),
            int(chat_timeout),
            st.session_state["results"],
        )
        st.session_state["running"] = False

    render_summary()


def render_summary() -> None:
    """מציג את סיכום הריצה האחרונה (נשמר ב-session_state ושורד הרצות חוזרות של הדף)."""
    # הדגל נשאר דולק רק אם הריצה הקודמת נקטעה באמצע
    interrupted = st.session_state.pop("running", False)
    results = st.session_state.get("results")
    if not results:
        if interrupted:
            st.warning("הריצה נעצרה לפני שנשלחה הודעה כלשהי.")
        return

    st.subheader("סיכום הריצה האחרונה")
    if interrupted:
        st.warning("הריצה נעצרה לפני הסוף. הטבלה מציגה רק את מה שעובד עד העצירה.")

    sent = sum(r["סטטוס"] == "נשלח" for r in results)
    col_sent, col_failed = st.columns(2)
    col_sent.metric("נשלחו", sent)
    col_failed.metric("נכשלו", len(results) - sent)

    report = pd.DataFrame(results)
    st.dataframe(report, width="stretch", hide_index=True)
    st.download_button(
        "⬇️ הורד דוח (CSV)",
        # utf-8-sig כדי שאקסל יציג עברית תקין
        data=report.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"pfa_results_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
        on_click="ignore",
    )


if __name__ == "__main__":
    main()
