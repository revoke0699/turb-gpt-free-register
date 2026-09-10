# -*- coding: utf-8 -*-
"""chatgpt2api 号池对接配置。"""
from config.env_loader import apply_env_overrides

# 注册成功后是否自动把账号 access_token 推到 chatgpt2api 号池。
# 基址或 Auth Key 为空时仍会跳过，不影响注册结果。
CHATGPT2API_AUTO_EXPORT: bool = True

# chatgpt2api 服务地址，例如 http://127.0.0.1:3000
CHATGPT2API_API_BASE: str = ""

# 管理接口鉴权密钥；请求头使用 Authorization: Bearer <key>。
# 保存在 .env（CHATGPT2API_AUTH_KEY），不要写进 git。
CHATGPT2API_AUTH_KEY: str = ""

# 上传超时秒数。
CHATGPT2API_API_TIMEOUT: int = 20

# 写入 chatgpt2api 号池的 source_type；官网 session AT 用 web。
CHATGPT2API_SOURCE_TYPE: str = "web"

apply_env_overrides(globals(), {
    "CHATGPT2API_AUTO_EXPORT": "bool",
    "CHATGPT2API_API_BASE": "str",
    "CHATGPT2API_AUTH_KEY": "str",
    "CHATGPT2API_API_TIMEOUT": "int",
    "CHATGPT2API_SOURCE_TYPE": "str",
})
