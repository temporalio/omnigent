"""The worker: hosts the OmnigentSession workflow and the turn activities.

Run one or many; they pull the same task queue, so any worker can drive any
session against the same Omnigent server.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import make_activities
from .config import from_env
from .workflow import OmnigentSession


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = from_env()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)

    print(f"omnigent-temporal worker on {cfg.address} / {cfg.namespace} / {cfg.task_queue}")
    print(f"omnigent server: {cfg.server_url}   agent: {cfg.agent}")

    worker = Worker(
        client,
        task_queue=cfg.task_queue,
        workflows=[OmnigentSession],
        activities=make_activities(cfg),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
