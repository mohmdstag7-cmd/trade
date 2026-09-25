"""UI strings for English and Persian.

Keys are dot-namespaced and identical across languages (guarded by tests).
``{placeholders}`` are filled by ``Translator.translate``. Persian is fully
RTL-capable; the full RTL polish ships in Phase 14 (SPEC G3-14), but the
string set and layout direction already work from Phase 1.
"""

from __future__ import annotations

EN: dict[str, str] = {
    "app.title": "MT5 Trading Workstation",
    # navigation groups
    "nav.group.trade": "TRADE",
    "nav.group.analyze": "ANALYZE",
    "nav.group.system": "SYSTEM",
    # pages
    "nav.dashboard": "Dashboard",
    "nav.market": "Market",
    "nav.signals": "Signals",
    "nav.positions": "Positions & Trades",
    "nav.analytics": "Analytics",
    "nav.journal": "Journal",
    "nav.backtest": "Backtest",
    "nav.model": "Model",
    "nav.ai_lab": "AI Lab",
    "nav.strategies": "Strategies",
    "nav.risk": "Risk",
    "nav.logs": "Logs",
    "nav.health": "Health",
    "nav.settings": "Settings",
    # status bar
    "status.disconnected": "Disconnected",
    "status.connected": "Connected",
    "status.mode.none": "Mode: —",
    "status.clock.tooltip": "Local time. Broker time arrives in Phase 5.",
    "status.killswitch": "Kill switch",
    "status.killswitch.tooltip": "Kill switch arrives with the execution engine (Phase 8).",
    "status.theme.tooltip": "Toggle dark / light theme",
    "status.language.tooltip": "Switch language (English / فارسی)",
    # command palette
    "palette.placeholder": "Type a command…",
    "palette.empty": "No matching command",
    "palette.title": "Command palette",
    "command.goto": "Go to {page}",
    "command.toggle_theme": "Toggle dark / light theme",
    "command.toggle_language": "Switch language (English / فارسی)",
    "command.quit": "Quit",
    # empty states
    "empty.title": "Nothing here yet",
    "empty.phase": "This page is built in Phase {phase}.",
    "empty.dashboard.desc": "KPIs, equity, open positions and risk usage will appear here.",
    "empty.settings.desc": "Appearance is available now; accounts and integrations arrive in Phase 4.",
    # settings page
    "settings.appearance": "Appearance",
    "settings.theme": "Theme",
    "settings.language": "Language",
    "settings.theme.dark": "Dark",
    "settings.theme.light": "Light",
    "settings.language.en": "English",
    "settings.language.fa": "فارسی (Persian)",
    "settings.about": "About",
    "settings.version": "Version",
    "settings.python": "Python",
    # window
    "window.quit.confirm": "Quit the application?",
}

FA: dict[str, str] = {
    "app.title": "ایستگاه معاملات MT5",
    "nav.group.trade": "معامله",
    "nav.group.analyze": "تحلیل",
    "nav.group.system": "سیستم",
    "nav.dashboard": "داشبورد",
    "nav.market": "بازار",
    "nav.signals": "سیگنال‌ها",
    "nav.positions": "پوزیشن‌ها و معاملات",
    "nav.analytics": "آنالیتیکس",
    "nav.journal": "ژورنال",
    "nav.backtest": "بک‌تست",
    "nav.model": "مدل",
    "nav.ai_lab": "آزمایشگاه AI",
    "nav.strategies": "استراتژی‌ها",
    "nav.risk": "ریسک",
    "nav.logs": "لاگ‌ها",
    "nav.health": "سلامت",
    "nav.settings": "تنظیمات",
    "status.disconnected": "قطع اتصال",
    "status.connected": "متصل",
    "status.mode.none": "حالت: —",
    "status.clock.tooltip": "ساعت محلی. ساعت بروکر در فاز ۵ اضافه می‌شود.",
    "status.killswitch": "توقف اضطراری",
    "status.killswitch.tooltip": "توقف اضطراری با موتور اجرا در فاز ۸ اضافه می‌شود.",
    "status.theme.tooltip": "تغییر تم تیره / روشن",
    "status.language.tooltip": "تغییر زبان (English / فارسی)",
    "palette.placeholder": "یک دستور بنویسید…",
    "palette.empty": "دستوری پیدا نشد",
    "palette.title": "پالت دستورات",
    "command.goto": "رفتن به {page}",
    "command.toggle_theme": "تغییر تم تیره / روشن",
    "command.toggle_language": "تغییر زبان (English / فارسی)",
    "command.quit": "خروج",
    "empty.title": "هنوز چیزی اینجا نیست",
    "empty.phase": "این صفحه در فاز {phase} ساخته می‌شود.",
    "empty.dashboard.desc": "شاخص‌ها، موجودی، پوزیشن‌های باز و مصرف ریسک اینجا نمایش داده می‌شوند.",
    "empty.settings.desc": "ظاهر اکنون در دسترس است؛ حساب‌ها و اتصال‌ها در فاز ۴ اضافه می‌شوند.",
    "settings.appearance": "ظاهر",
    "settings.theme": "تم",
    "settings.language": "زبان",
    "settings.theme.dark": "تیره",
    "settings.theme.light": "روشن",
    "settings.language.en": "English",
    "settings.language.fa": "فارسی",
    "settings.about": "درباره",
    "settings.version": "نسخه",
    "settings.python": "پایتون",
    "window.quit.confirm": "از برنامه خارج می‌شوید؟",
}

#: All string dictionaries, keyed by language code.
STRINGS: dict[str, dict[str, str]] = {"en": EN, "fa": FA}
