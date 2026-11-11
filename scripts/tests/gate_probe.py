"""Run with the server environment; no sockets, LLM, or external network."""
import asyncio
from pathlib import Path
import sys
import unittest

import httpx
from starlette.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduction_server import SimulationClock, TaskGate


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.mutations = []

        async def app(scope, receive, send):
            if scope["path"] == "/slow":
                self.entered.set()
                await self.release.wait()
            self.mutations.append(scope["path"])
            await JSONResponse({"ok": True})(scope, receive, send)

        self.clock = SimulationClock()
        self.gate = TaskGate(app, "test-admin", self.clock)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.gate), base_url="http://test")
        self.admin = {"X-API-Key": "test-admin"}
        self.agent = {"X-Reproduction-Lease": "old"}

    async def asyncTearDown(self):
        await self.client.aclose()

    async def control(self, op, **body):
        return await self.client.post("/_reproduction/" + op, headers=self.admin, json=body)

    async def test_expired_and_stale_lease_never_reach_simulator(self):
        self.assertEqual((await self.client.post("/_reproduction/open", json={})).status_code, 403)
        await self.control("open", lease="old", timeout_seconds=.02)
        self.assertEqual((await self.client.post("/move", headers=self.agent)).status_code, 200)
        await asyncio.sleep(.03)
        self.assertEqual((await self.client.post("/late", headers=self.agent)).status_code, 403)
        response = await self.control("close", lease="old", drain_seconds=1)
        self.assertTrue(response.json()["quiescent"])
        self.assertTrue(self.clock.paused)
        await self.control("prepare")
        self.assertFalse(self.clock.paused)
        await self.control("open", lease="new", timeout_seconds=1)
        self.assertEqual((await self.client.post("/stale", headers=self.agent)).status_code, 403)
        self.assertEqual((await self.client.post("/new", headers={"X-Reproduction-Lease": "new"})).status_code, 200)
        self.assertEqual(self.mutations, ["/move", "/new"])

    async def test_close_drains_inflight_then_freezes_and_can_grade(self):
        await self.control("open", lease="old", timeout_seconds=1)
        pending = asyncio.create_task(self.client.post("/slow", headers=self.agent))
        await self.entered.wait()
        close = asyncio.create_task(self.control("close", lease="old", drain_seconds=1))
        await asyncio.sleep(.01)
        self.assertFalse(close.done())
        self.assertFalse(self.clock.paused)
        self.assertEqual((await self.client.post("/late", headers=self.agent)).status_code, 403)
        self.release.set()
        await pending
        barrier = (await close).json()
        self.assertEqual(barrier["inflight_at_close"], 1)
        self.assertEqual(barrier["inflight"], 0)
        self.assertTrue(self.clock.paused)
        self.assertEqual((await self.client.get("/check/test", headers=self.admin)).status_code, 200)
        self.assertEqual(self.mutations, ["/slow", "/check/test"])

    async def test_failed_drain_does_not_claim_quiescence_or_reopen(self):
        await self.control("open", lease="old", timeout_seconds=1)
        pending = asyncio.create_task(self.client.post("/slow", headers=self.agent))
        await self.entered.wait()
        result = await self.control("close", lease="old", drain_seconds=.01)
        self.assertEqual(result.status_code, 503)
        self.assertFalse(result.json()["quiescent"])
        self.assertEqual((await self.control("open", lease="new", timeout_seconds=1)).status_code, 409)
        self.assertEqual((await self.client.post("/late", headers=self.agent)).status_code, 403)
        self.release.set()
        await pending


if __name__ == "__main__":
    unittest.main()
