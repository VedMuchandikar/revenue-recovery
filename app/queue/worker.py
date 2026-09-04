"""Worker implementation for processing revenue events from the database queue."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.queue.claim import claim_pending_event, recover_stale_processing_events
from app.config.settings import settings
from app.db.database import async_session_factory
from app.engine.orchestrator import process_event

logger = logging.getLogger(__name__)


class RevenueRecoveryWorker:
    """Worker that processes revenue events from the database queue."""

    def __init__(self, worker_id: Optional[str] = None):
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the worker processing loop."""
        if self.is_running:
            logger.warning("Worker %s is already running", self.worker_id)
            return

        self.is_running = True
        logger.info("Starting revenue recovery worker %s", self.worker_id)
        self._task = asyncio.create_task(self._processing_loop())

    async def stop(self):
        """Stop the worker processing loop."""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("Stopping revenue recovery worker %s", self.worker_id)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _processing_loop(self):
        """Main processing loop for the worker."""
        logger.info("Worker %s entering processing loop", self.worker_id)

        while self.is_running:
            try:
                # First, recover any stale processing events
                await self._recover_stale_events()

                # Then try to claim and process a pending event
                await self._process_next_event()

                # Wait before next iteration
                await asyncio.sleep(settings.worker_poll_interval_seconds)

            except asyncio.CancelledError:
                logger.info("Worker %s processing loop cancelled", self.worker_id)
                break
            except Exception as e:
                logger.exception("Unexpected error in worker %s processing loop: %s", self.worker_id, e)
                # Continue running despite errors
                await asyncio.sleep(settings.worker_poll_interval_seconds)

    async def _recover_stale_events(self):
        """Recover events stuck in PROCESSING state."""
        async with async_session_factory() as session:
            try:
                recovered_count = await recover_stale_processing_events(
                    session,
                    settings.processing_stale_minutes
                )
                if recovered_count > 0:
                    logger.info("Worker %s recovered %d stale processing events",
                              self.worker_id, recovered_count)
            except Exception as e:
                logger.exception("Error recovering stale events in worker %s: %s",
                               self.worker_id, e)

    async def _process_next_event(self):
        """Claim and process the next pending event."""
        async with async_session_factory() as session:
            try:
                event = await claim_pending_event(session)
                if event:
                    logger.info("Worker %s claimed event %s (type: %s, amount: %s %s)",
                              self.worker_id, event.id, event.type.value,
                              event.amount, event.currency)

                    # Process the event through the orchestrator
                    await process_event(event.id, session)

                    logger.info("Worker %s completed processing event %s",
                              self.worker_id, event.id)
                # else: no pending events available, continue loop

            except Exception as e:
                logger.exception("Error processing event in worker %s: %s",
                               self.worker_id, e)


# Import uuid here to avoid circular imports
import uuid

# Global worker instance for simple usage
worker = RevenueRecoveryWorker()


async def run_worker():
    """Run the worker until interrupted."""
    await worker.start()
    try:
        # Keep running until interrupted
        while worker.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down worker...")
    finally:
        await worker.stop()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the worker
    asyncio.run(run_worker())