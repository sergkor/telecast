import subprocess
from pathlib import Path

from loguru import logger


def make_thumbnail(video_path: Path, out_dir: Path) -> Path | None:
    out = out_dir / f"{video_path.stem}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", "-q:v", "4", str(out)],
            check=True, capture_output=True, timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning(f"thumbnail failed for {video_path}: {e}")
        return None
    return out if out.exists() else None
