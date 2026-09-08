# -*- coding: utf-8 -*-
"""
代理池配置

每次注册随机抽取一个代理，保证不同 sid 之间彼此独立，避免风控关联。

Resin 模式开启时改为使用 PROXY_RESIN_TEMPLATE：把链接中的 [task_任务id]
替换为当前注册任务 ID（无任务上下文时用短随机 ID）。

协议说明：
    - http:// / https://   HTTP(S) 代理
    - socks5://            SOCKS5（DNS 本地解析，可能泄漏）
    - socks5h://           SOCKS5（DNS 在代理端解析，推荐，避免 DNS-IP 错配）
"""
from config.env_loader import apply_env_overrides
import logging
import random
import uuid


logger = logging.getLogger(__name__)

_RESIN_TASK_PLACEHOLDER = "[task_任务id]"

# 本地代理入口；实际出口地区以代理/分流规则为准。
# 推荐使用 socks5h://（DNS 在代理端解析），避免本地 DNS 与出口 IP 地区错配。
PROXY_POOL = [
    "socks5://127.0.0.1:7897",
]

# Resin 模式：不用代理池随机抽，而是用一条模板链接，把 [task_任务id] 换成当前任务 ID。
# 同一注册任务全程粘性 IP；不同任务互不串 session。
PROXY_RESIN_MODE = False
PROXY_RESIN_TEMPLATE = ""

# 套餐/Plus 试用资格查询与 Codex Agent Token 生成共用这组独立网络策略，
# 避免批量请求被注册代理池中的临时本地代理拖垮，也避免无条件直连造成出口策略失控。
#   auto   = 优先使用 PLAN_CHECK_PROXY 或代理池；本地代理端口未监听时回退直连
#   proxy  = 强制使用 PLAN_CHECK_PROXY 或代理池，失败直接报错
#   direct = 始终直连
PLAN_CHECK_PROXY_MODE = "auto"

# 套餐查询 / Codex Agent Token 生成专用代理。留空时 auto/proxy 模式从 PROXY_POOL 选择。
# 代理可能包含账号密码，因此 WebUI 会把它保存到 .env。
PLAN_CHECK_PROXY = ""

# 查套餐 / 生成 Codex Agent Token 使用独立的短超时和有限重试，避免后台任务长时间卡住。
PLAN_CHECK_TIMEOUT = 15.0
PLAN_CHECK_MAX_ATTEMPTS = 2
PLAN_CHECK_RETRY_DELAY = 1.5

# 新注册账号的权益可能存在短暂同步延迟。首次查询失败，或返回 free 且暂未发现
# Plus 试用资格时，等待该秒数后再复查一次；设为 0 可关闭复查。
PLAN_CHECK_REGISTRATION_RECHECK_DELAY = 2.0

# 自动、手动和批量套餐查询共用同一个后台队列；Codex Agent Token 使用独立队列，
# 但复用这里的网络模式、请求启动间隔与随机抖动，避免批量后台请求过于集中。
PLAN_CHECK_WORKERS = 3
PLAN_CHECK_QUEUE_LIMIT = 500
PLAN_CHECK_MIN_INTERVAL = 0.4
PLAN_CHECK_JITTER = 0.3


def _current_resin_task_id() -> str:
    """优先用当前注册任务 job_id；没有任务上下文时生成短随机 ID。"""
    try:
        from core import registration_service as _svc

        job_id = getattr(getattr(_svc, "_THREAD_CTX", None), "job_id", None)
        if job_id is not None and str(job_id).strip():
            return str(job_id).strip()
    except Exception:
        pass
    return uuid.uuid4().hex[:12]


def expand_resin_placeholders(url: str) -> str:
    """把代理 URL 中的 [task_任务id] 换成当前任务 ID；没有占位符则原样返回。"""
    text = str(url or "")
    if _RESIN_TASK_PLACEHOLDER not in text:
        return text
    return text.replace(_RESIN_TASK_PLACEHOLDER, _current_resin_task_id())


def _resolve_resin_proxy() -> str:
    template = str(PROXY_RESIN_TEMPLATE or "").strip()
    if not template:
        logger.warning("[代理] Resin 模式已开启，但未填写 Resin 代理链接，本次不使用代理")
        return ""
    if _RESIN_TASK_PLACEHOLDER not in template:
        logger.warning("[代理] Resin 模板未包含 [task_任务id]，将原样使用，所有任务会共用同一 session")
        return template
    return expand_resin_placeholders(template)


def pick_proxy() -> str:
    """返回本次任务使用的代理 URL；未配置时返回空串（即不使用代理）。

    Resin 模式开启时使用 PROXY_RESIN_TEMPLATE，并把 [task_任务id] 替换为当前任务 ID；
    否则从 PROXY_POOL 随机抽取。代理池里的 URL 若含 [task_任务id] 同样会替换。
    """
    if PROXY_RESIN_MODE:
        return _resolve_resin_proxy()
    selected = random.choice(PROXY_POOL) if PROXY_POOL else ""
    return expand_resin_placeholders(selected) if selected else ""


# 兼容入口：默认每次进程启动随机选一个，作为本次注册全程的固定代理
PROXY = pick_proxy()

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'PROXY_POOL': 'list_str_multiline',
    'PROXY_RESIN_MODE': 'bool',
    'PROXY_RESIN_TEMPLATE': 'str',
    'PLAN_CHECK_PROXY_MODE': 'str',
    'PLAN_CHECK_PROXY': 'str',
    'PLAN_CHECK_TIMEOUT': 'float',
    'PLAN_CHECK_MAX_ATTEMPTS': 'int',
    'PLAN_CHECK_RETRY_DELAY': 'float',
    'PLAN_CHECK_REGISTRATION_RECHECK_DELAY': 'float',
    'PLAN_CHECK_WORKERS': 'int',
    'PLAN_CHECK_QUEUE_LIMIT': 'int',
    'PLAN_CHECK_MIN_INTERVAL': 'float',
    'PLAN_CHECK_JITTER': 'float',
})
PROXY = pick_proxy()
