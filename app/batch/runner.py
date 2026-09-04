"""Batch runner for orchestrating all batch processors."""

import asyncio
import logging
import signal
import sys
from typing import List

from app.batch.abandonment_poller import abandonment_poller
from app.batch.receivables_generator import receivables_generator
from app.queue.worker import worker
logger = logging.getLogger(__name__)


class BatchRunner:
    """Orchestrates all batch processors for revenue recovery."""

    def __init__(self):
        self.processors = [
            abandonment_poller,
            receivables_generator,
            worker,
        ]
        self._shutdown_event = asyncio.Event()

    async def start_all(self):
        """Start all batch processors."""
        logger.info("Starting all batch processors...")

        # Start each processor
        start_tasks = [processor.start() for processor in self.processors]
        await asyncio.gather(*start_tasks)

        logger.info("All batch processors started")

    async def stop_all(self):
        """Stop all batch processors."""
        logger.info("Stopping all batch processors...")

        # Stop each processor
        stop_tasks = [processor.stop() for processor in self.processors]
        await asyncio.gather(*stop_tasks)

        logger.info("All batch processors stopped")

    async def run_forever(self):
        """Run all processors until shutdown signal."""
        # Set up signal handlers
        def signal_handler():
            logger.info("Received shutdown signal")
            self._shutdown_event.set()

        # Register signal handlers for graceful shutdown
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, signal_handler)

        try:
            # Start all processors
            await self.start_all()

            # Wait for shutdown signal
            await self._shutdown_event.wait()

        finally:
            # Ensure cleanup
            await self.stop_all()


# Global batch runner instance
batch_runner = BatchRunner()


async def run_batch_processors():
    """Run all batch processors until interrupted."""
    await batch_runner.run_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        asyncio.run(run_batch_processors())
    except KeyboardInterrupt:
        logger.info("Batch runner interrupted by user")