"""Run separately: python -m app.worker [--once]. Queue data stays on local disk."""

import argparse
import logging
import time

from app.config import get_settings
from app.services.hybrid_kb import HybridKnowledgeBase
from app.services.index_jobs import (
    IndexBusyError,
    JobStore,
    index_writer,
    queue_path,
    queue_scope,
)


def run_once(config, factory=HybridKnowledgeBase):
    store = JobStore(queue_path(config))
    try:
        with index_writer(config):
            job = store.claim(queue_scope(config))
            if not job:
                return False
            try:
                base = factory(config)
                report = base.sync(config.knowledge_base_dir, progress=lambda done, total, phase:
                                   store.progress(job["id"], done, total, phase))
                retry = None
                if report.failed and report.retryable_errors == report.failed:
                    retry = max(report.retry_after_seconds, min(300, 5 * 2 ** job["attempts"]))
                error = (report.blocked_reason or "indexing_failed") if report.failed else None
                store.finish(job, report, error, retry)
            except Exception as exc:
                # Never persist provider bodies, keys or raw document text as errors.
                store.finish(job, error_code=type(exc).__name__)
            return True
    except IndexBusyError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = get_settings()
    while True:
        try:
            worked = run_once(config)
        except Exception:
            logging.error("Index queue unavailable; check the local database volume")
            worked = False
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
