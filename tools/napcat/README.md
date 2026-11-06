# NapCat (OneBot v11) on Mac mini

Install path: `~/xiaotian-wechat/tools/napcat`  
Runtime: Colima (Docker) + `mlikiowa/napcat-docker:latest`  
Why Docker: no `/Applications/QQ.app` for native Mac Installer; NapCat official docs recommend Docker on macOS when Shell is awkward.

## Start / stop

```bash
export PATH="/opt/homebrew/bin:$PATH"
colima start   # if not running
cd ~/xiaotian-wechat/tools/napcat
docker compose up -d
docker compose logs -f napcat
```

WebUI: `http://127.0.0.1:6099/webui` (token in local `.env` / `data/config/webui.json`, not committed).  
Forward WS: `ws://127.0.0.1:3001` (matches `persona.channels.qq.ws_uri`).

## Login

On first start NapCat prints a QR and writes `data/cache/qrcode.png` (copied from container `/app/napcat/cache/qrcode.png`). Scan with phone QQ; or open WebUI → QR login.

## Probe

```bash
cd ~/xiaotian-wechat
.venv/bin/python tools/onebot_qq_run.py --probe
```

Do not kill iLink (`tools/ilink_weixin_run.py`).
