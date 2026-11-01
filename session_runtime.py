"""One owner task manages each chat's MCP connection and schema cache."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents import Runner

from client import ObservedModel, connect_server, decode_output


@dataclass
class Request:
    message: str
    history: list
    agent_factory: object
    updates: asyncio.Queue


class SessionRuntime:
    def __init__(self, database, *, idle_timeout=900):
        self.database = database
        self.loop = asyncio.get_running_loop()
        self.jobs = asyncio.Queue()
        self.idle_timeout = idle_timeout
        self.server = None
        self.closing = False
        self.close_error = None
        self.owner = asyncio.create_task(self._serve())

    async def _serve(self):
        """Connection entry, requests, and cleanup all execute in this task."""
        current = None
        try:
            # Delay connection startup until the first real request.
            current = await asyncio.wait_for(self.jobs.get(), self.idle_timeout)
            if current is None:
                return
            async with connect_server(self.database) as server:
                self.server = server
                while current is not None:
                    server.turn += 1
                    server.emit = lambda **event: current.updates.put_nowait(("event", event))
                    try:
                        await server.list_tools()
                        agent = current.agent_factory(server)
                        agent.model = ObservedModel(agent.model, server.emit)
                        result = Runner.run_streamed(agent, current.history + [{"role": "user", "content": current.message}], max_turns=8)
                        async for event in result.stream_events():
                            if event.type == "run_item_stream_event" and event.name == "tool_output":
                                server.emit(direction="MCP Client → Agent", operation="tool result delivered",
                                    result=decode_output(event.item.output))
                        current.updates.put_nowait(("answer", (str(result.final_output), result.to_input_list())))
                    except Exception as error:
                        # Retire the connection after a failed turn; never claim a stale cache hit.
                        self.closing = True
                        current.updates.put_nowait(("error", error))
                        return
                    finally:
                        server.emit = lambda **event: None
                        current.updates.put_nowait(("done", None))
                        current = None
                    current = await asyncio.wait_for(self.jobs.get(), self.idle_timeout)
        except TimeoutError:
            pass
        except BaseException as error:
            self.close_error = error
            if current is not None:
                current.updates.put_nowait(("error", error))
                current.updates.put_nowait(("done", None))
        finally:
            self.closing = True
            self.server = None
            while not self.jobs.empty():
                pending = self.jobs.get_nowait()
                if pending is not None:
                    pending.updates.put_nowait(("error", RuntimeError("MCP connection closed; retry to reconnect.")))
                    pending.updates.put_nowait(("done", None))

    async def run(self, message, history, agent_factory):
        if self.closing or self.owner.done() or self.loop is not asyncio.get_running_loop():
            raise RuntimeError("MCP session is closed or belongs to a different event loop.")
        updates = asyncio.Queue()
        self.jobs.put_nowait(Request(message, list(history), agent_factory, updates))
        while True:
            kind, payload = await updates.get()
            if kind == "done":
                break
            yield kind, payload

    def request_close(self):
        if not self.closing and not self.loop.is_closed():
            self.closing = True
            self.loop.call_soon_threadsafe(self.jobs.put_nowait, None)

    async def close(self):
        self.request_close()
        if self.loop is asyncio.get_running_loop():
            await self.owner
