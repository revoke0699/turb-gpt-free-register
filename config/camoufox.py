# -*- coding: utf-8 -*-
"""Camoufox（Firefox 反检测浏览器）自动化注册配置。"""
from config.env_loader import apply_env_overrides

# 是否无头启动：False=显示窗口，True=无头。
CAMOUFOX_HEADLESS: bool = False

# 是否启用 Camoufox humanize 鼠标轨迹。
CAMOUFOX_HUMANIZE: bool = True

# 使用当前出口 IP 自动匹配时区/经纬度。
CAMOUFOX_GEOIP: bool = True

# 显式指定语言/时区；留空则在 CAMOUFOX_GEOIP=True 时由 Camoufox 与出口画像推断。
# 例如：CAMOUFOX_LOCALE="ja-JP"，CAMOUFOX_TIMEZONE="Asia/Tokyo"。
CAMOUFOX_LOCALE: str = ""
CAMOUFOX_TIMEZONE: str = ""

# 是否把本项目传入/代理池抽取的代理传给 Camoufox。
CAMOUFOX_USE_PROXY: bool = True

# 阻止 WebRTC，避免 STUN 泄漏真实 IP。
CAMOUFOX_BLOCK_WEBRTC: bool = True

# 指纹操作系统；留空则由 Camoufox/BrowserForge 随机选择。
# 可选：windows / macos / linux。
CAMOUFOX_OS: str = ""

# 持久化用户目录；留空则每次创建临时 persistent profile，任务结束删除。
CAMOUFOX_USER_DATA_DIR: str = ""

# 与原 Roxy Selenium 流程共用的超时时间。
CAMOUFOX_SELENIUM_TIMEOUT: int = 90

# 调试时保留浏览器不自动关闭。
CAMOUFOX_KEEP_BROWSER_OPEN: bool = False

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'CAMOUFOX_HEADLESS': 'bool',
    'CAMOUFOX_HUMANIZE': 'bool',
    'CAMOUFOX_GEOIP': 'bool',
    'CAMOUFOX_LOCALE': 'str',
    'CAMOUFOX_TIMEZONE': 'str',
    'CAMOUFOX_USE_PROXY': 'bool',
    'CAMOUFOX_BLOCK_WEBRTC': 'bool',
    'CAMOUFOX_OS': 'str',
    'CAMOUFOX_USER_DATA_DIR': 'str',
    'CAMOUFOX_SELENIUM_TIMEOUT': 'int',
    'CAMOUFOX_KEEP_BROWSER_OPEN': 'bool',
})
