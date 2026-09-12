"""Tests for DownloadQueueManager concurrency limits and waiting queue positions."""

import asyncio
import pytest

from helpers.queue_manager import DownloadQueueManager


@pytest.mark.asyncio
async def test_queue_manager_concurrency_bounding():
    """Test that concurrent slots are strictly bounded by max_concurrent."""
    qm = DownloadQueueManager(max_concurrent=2)
    assert qm.available_slots == 2
    assert qm.active_count == 0

    running_tasks = []

    async def worker(worker_id: int):
        async with qm.acquire_slot():
            running_tasks.append(worker_id)
            await asyncio.sleep(0.1)

    # Launch 3 workers concurrently (limit is 2)
    t1 = asyncio.create_task(worker(1))
    t2 = asyncio.create_task(worker(2))
    t3 = asyncio.create_task(worker(3))

    await asyncio.sleep(0.02)
    # At this point, exactly 2 tasks should be actively running, and 1 waiting
    assert qm.active_count == 2
    assert qm.waiting_count == 1
    assert qm.available_slots == 0

    await asyncio.gather(t1, t2, t3)

    assert len(running_tasks) == 3
    assert qm.active_count == 0
    assert qm.waiting_count == 0
    assert qm.available_slots == 2


@pytest.mark.asyncio
async def test_queue_manager_position_notification():
    """Test that queued jobs trigger notify_callback with accurate position."""
    qm = DownloadQueueManager(max_concurrent=1)

    notified_positions = []

    async def on_queued(pos: int):
        notified_positions.append(pos)

    async def blocker():
        async with qm.acquire_slot():
            await asyncio.sleep(0.15)

    async def queued_job():
        async with qm.acquire_slot(notify_callback=on_queued):
            pass

    t_block = asyncio.create_task(blocker())
    await asyncio.sleep(0.02)

    t_queued = asyncio.create_task(queued_job())
    await asyncio.sleep(0.02)

    assert 1 in notified_positions

    await asyncio.gather(t_block, t_queued)
