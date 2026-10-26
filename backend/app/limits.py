"""One configuration for website admission and Telegram admission/display."""
import os


def max_active_jobs():
    return max(1, int(os.getenv('MAX_ACTIVE_JOBS_PER_USER', os.getenv('TELEGRAM_MAX_ACTIVE_JOBS', '1'))))


def public_limits():
    return {
        'max_job_gb': float(os.getenv('MAX_JOB_GB', '8')),
        'job_ttl_minutes': int(os.getenv('JOB_TTL_MINUTES', '60')),
        'max_active_jobs_per_user': max_active_jobs(),
        'video_playlist_limit': int(os.getenv('VIDEO_PLAYLIST_LIMIT', '50')),
        'audio_playlist_limit': int(os.getenv('AUDIO_PLAYLIST_LIMIT', '100')),
    }
