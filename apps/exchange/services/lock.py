import logging
from contextlib import contextmanager

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


@contextmanager
def sync_lock(account_id: int, ttl: int | None = None):
    ttl = ttl or settings.SYNC_LOCK_TTL_SECONDS
    client = redis.from_url(settings.CELERY_BROKER_URL)
    lock_key = f"sync:account:{account_id}"
    acquired = client.set(lock_key, "1", nx=True, ex=ttl)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        client.delete(lock_key)
