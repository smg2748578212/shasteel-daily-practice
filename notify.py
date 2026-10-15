"""Optional AstrBot notifications, configured through the environment."""
import json
import os
import urllib.request

TIMEOUT = 10


def send(title: str, content: str = "", targets: list | None = None) -> bool:
    endpoint = os.environ.get("ASTROBOT_API_URL", "")
    api_key = os.environ.get("ASTROBOT_API_KEY", "")
    if targets is None:
        targets = [item.strip() for item in os.environ.get("ASTROBOT_TARGETS", "").split(",") if item.strip()]
    if not endpoint or not api_key or not targets:
        return False
    message = f"{title}\n{content}" if content else title
    all_ok = True
    for target in targets:
        try:
            data = json.dumps({"umo": target, "message": message}).encode("utf-8")
            request = urllib.request.Request(endpoint, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                if json.loads(response.read()).get("status") != "ok":
                    all_ok = False
                    print("[notify] Notification service rejected the request")
        except Exception as exc:
            print(f"[notify] Request failed ({type(exc).__name__})")
            all_ok = False
    return all_ok
