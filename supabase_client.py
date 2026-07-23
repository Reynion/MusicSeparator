from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from supabase import Client, create_client

BUCKET = os.getenv("SUPABASE_BUCKET", "separated-audio")


@lru_cache
def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 설정되지 않았습니다.")
    return create_client(url, key)


def update_tunnel_url(url: str) -> None:
    import datetime

    client = get_supabase()
    client.table("demucs_server").upsert({
        "id": 1,
        "url": url,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).execute()


def upload_stem(job_id: str, stem_name: str, file_path: Path) -> str:
    client = get_supabase()
    storage_path = f"{job_id}/{stem_name}.wav"
    data = file_path.read_bytes()
    client.storage.from_(BUCKET).upload(
        storage_path,
        data,
        {"content-type": "audio/wav", "upsert": "true"},
    )
    return client.storage.from_(BUCKET).get_public_url(storage_path)
