"""Default life_order config blob (merged into tracked config.json)."""

DEFAULTS = {
    "enabled": False,
    "dry_run": True,
    "amount_cap_yuan": 50,
    "confirm_expiry_sec": 180,
    "allowed_contact_id": "demo_contact",
    "allowed_peers": [
        "wechat_ilink:demo@im.wechat",
        "qq_onebot:10001",
    ],
    "confirm_words": ["好", "求你", "嗯", "下吧"],
    "cancel_words": ["算了", "不要"],
    "soft_confirm_phrases": ["你求求我嘛～", "那我给你下单了哦"],
    "mcd_user_must_pay": True,
    "didi_mode": "link_only",
    "amap_mode": "read_only",
    "luckin": {"default_dept_id": 605488},
    "mcd": {"order_type": 1, "be_type": 1, "user_must_pay": True},
    "didi": {"mode": "link_only"},
    "mcp": {
        "enabled": False,
        "luckin_url": "https://gwmcp.lkcoffee.com/order/user/mcp",
        "mcd_url": "https://mcp.mcd.cn",
        "env_file": "config/life_order_mcp.env",
        "_note": "mcp.enabled 默认 false；true 时用 Streamable HTTP + Bearer。createOrder 等写工具在 mcp_http 硬拒绝。密钥只读 env 文件，勿提交。",
    },
    "_note": (
        "生活点单闸门：enabled 默认 false、dry_run 默认 true；"
        "单笔硬顶 ¥50 由代码 AMOUNT_CAP_YUAN 强制，配置只能收紧；"
        "仅 demo_contact + 允许 peer；须 pending 确认；禁止 proactive 触发；"
        "用户批准前不部署。阶段 1–6：守卫/高德/瑞幸/麦当劳自取/滴滴链接/brain 闸门；默认 enabled=false dry_run=true。"
    ),
}
