"""Shared scripted tcgcsv server for the live-ingest tests (no network)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

UPDATE = "2026-10-24T20:05:44+0000"
OK_BODY = b'{"success": true, "errors": [], "results": [{"productId": 1}]}'
EMPTY_BODY = b'{"success": true, "results": []}'
OLD_LM = "Thu, 31 Oct 2024 12:00:00 GMT"

Reply = Callable[[], httpx.Response]


def ok(body: bytes = OK_BODY, lm: str = OLD_LM) -> Reply:
    return lambda: httpx.Response(200, content=body, headers={"Last-Modified": lm, "ETag": '"e1"'})


def status(code: int) -> Reply:
    return lambda: httpx.Response(code)


class Server:
    """Scripted tcgcsv: per-gid reply lists (last one repeats) and counters."""

    def __init__(self, updates: list[str] | None = None) -> None:
        self.updates = list(updates or [UPDATE])
        self.replies: dict[int, list[Reply]] = {}
        self.counts: dict[int, int] = {}
        self.lu_reads = 0
        self.group_ids: list[int] = []
        self.products_requests = 0

    def script(self, gid: int, *replies: Reply) -> None:
        self.replies[gid] = list(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/last-updated.txt":
            text = self.updates[min(self.lu_reads, len(self.updates) - 1)]
            self.lu_reads += 1
            return httpx.Response(200, text=text + "\n")
        if path.endswith("/groups"):
            rows = [
                {
                    "groupId": g,
                    "name": f"G{g}",
                    "abbreviation": f"g{g}",
                    "publishedOn": "2026-01-01T00:00:00",
                }
                for g in self.group_ids
            ]
            return httpx.Response(200, json={"success": True, "results": rows})
        if path.endswith("/products"):
            self.products_requests += 1
            gid = int(path.split("/")[-2])
            prod = {"productId": gid * 1000, "groupId": gid, "name": f"P{gid}", "extendedData": []}
            return httpx.Response(200, json={"success": True, "results": [prod]})
        gid = int(path.split("/")[-2])
        n = self.counts.get(gid, 0)
        self.counts[gid] = n + 1
        queue = self.replies[gid]
        return queue[min(n, len(queue) - 1)]()

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
