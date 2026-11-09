"""
notify.py - Telegram ማሳወቂያ (ቁልፎቹ በ GitHub secrets ውስጥ ብቻ፣ በኮድ ውስጥ በፍጹም አይጻፉም)
ሙከራ (ከ GitHub): Actions -> alert-test -> Run workflow
"""
import os, sys
import requests


def notify(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    print("[ማሳወቂያ]", text.replace("\n", " | "))
    if not tok or not chat or "demo" in sys.argv:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text[:3500]}, timeout=30)
        if not r.ok:
            print("ማሳወቂያ አልተላከም: HTTP", r.status_code)
        return r.ok
    except Exception as ex:  # የስህተት ጽሑፍ ቁልፍ ሊይዝ ስለሚችል ዓይነቱን ብቻ እናሳያለን
        print("ማሳወቂያ አልተላከም:", type(ex).__name__)
        return False


if __name__ == "__main__" and "test" in sys.argv:
    print("ተልኳል" if notify("✅ ሙከራ: የማሳወቂያ ስርዓቱ ይሠራል። (paper trading፣ የገንዘብ ምክር አይደለም)") else "አልተላከም (ቁልፎቹን ፈትሽ)")
