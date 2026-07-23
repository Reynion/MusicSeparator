from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from jobs import JobStatus, jobs
from supabase_client import upload_stem

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# winget으로 설치한 FFmpeg는 PATH가 갱신되려면 셸/로그인을 다시 해야 하므로,
# 재부팅 없이도 바로 동작하도록 이 프로세스의 PATH에 직접 추가한다.
FFMPEG_DIR = os.getenv("FFMPEG_DIR")
if FFMPEG_DIR:
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
STEMS = ("vocals", "drums", "bass", "other")
DEMUCS_MODEL = "htdemucs"
API_KEY = os.getenv("API_KEY")

# CPU 한 대에서 Demucs(-j 16)를 동시에 여러 개 돌리면 코어를 나눠 쓰게 되어
# 오히려 전체 처리 시간이 늘어나므로, 워커 1개로 작업을 순차 처리한다.
executor = ThreadPoolExecutor(max_workers=1)

app = FastAPI(title="Demucs Separator Server")


def verify_api_key(x_api_key: str | None) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/separate")
async def separate(
    file: UploadFile = File(...),
    x_api_key: str | None = Header(default=None),
) -> dict:
    verify_api_key(x_api_key)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="mp3, wav 파일만 업로드할 수 있습니다.")

    job_id = uuid.uuid4().hex
    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_upload_dir / f"input{ext}"

    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    jobs.create(job_id, filename=file.filename or input_path.name)
    executor.submit(process_job, job_id, input_path)

    return {"job_id": job_id, "status": JobStatus.QUEUED.value}


@app.get("/status/{job_id}")
def status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 job_id 입니다.")
    return job.to_dict()


def process_job(job_id: str, input_path: Path) -> None:
    job_output_dir = OUTPUT_DIR / job_id
    try:
        jobs.update(job_id, status=JobStatus.PROCESSING)

        cmd = [
            sys.executable, "-m", "demucs",
            "-n", DEMUCS_MODEL,
            "--device", "cpu",
            "-j", "16",
            "-o", str(job_output_dir),
            str(input_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Demucs 처리 실패: {result.stderr[-2000:]}")

        stem_dir = job_output_dir / DEMUCS_MODEL / input_path.stem

        jobs.update(job_id, status=JobStatus.UPLOADING)
        urls: dict[str, str] = {}
        for stem in STEMS:
            stem_file = stem_dir / f"{stem}.wav"
            if stem_file.exists():
                urls[stem] = upload_stem(job_id, stem, stem_file)

        if not urls:
            raise RuntimeError("분리된 결과 파일을 찾을 수 없습니다.")

        jobs.update(job_id, status=JobStatus.COMPLETED, urls=urls)
    except Exception as exc:  # noqa: BLE001
        jobs.update(job_id, status=JobStatus.FAILED, error=str(exc))
    finally:
        shutil.rmtree(input_path.parent, ignore_errors=True)
        shutil.rmtree(job_output_dir, ignore_errors=True)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})
