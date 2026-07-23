from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PORT = os.getenv("PORT", "8000")
LOCAL_URL = f"http://127.0.0.1:{PORT}"
CLOUDFLARED_PATH = os.getenv("CLOUDFLARED_PATH") or str(BASE_DIR / "cloudflared" / "cloudflared.exe")
URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def watch_cloudflared(proc: subprocess.Popen) -> None:
    from supabase_client import update_tunnel_url

    for line in proc.stdout:
        print(f"[cloudflared] {line}", end="", flush=True)
        match = URL_PATTERN.search(line)
        if not match:
            continue
        url = match.group(0)
        try:
            update_tunnel_url(url)
            print(f"[tunnel] Supabase demucs_server.url 갱신 완료: {url}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[tunnel] Supabase 업데이트 실패: {exc}", flush=True)


def start_tunnel() -> subprocess.Popen:
    if not Path(CLOUDFLARED_PATH).exists():
        raise FileNotFoundError(f"cloudflared 실행파일을 찾을 수 없습니다: {CLOUDFLARED_PATH}")

    proc = subprocess.Popen(
        [CLOUDFLARED_PATH, "tunnel", "--url", LOCAL_URL],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    threading.Thread(target=watch_cloudflared, args=(proc,), daemon=True).start()
    return proc


def main() -> None:
    import uvicorn

    tunnel_proc = start_tunnel()
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(PORT))
    finally:
        tunnel_proc.terminate()


if __name__ == "__main__":
    main()
