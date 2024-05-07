# idea:

# ws-repeater
# resiliently (re)connect to a websocket, expose the websocket.
# use fastapi to expose /ws, get messages from upstream every 100ms
# and broadcast them to all connected clients.

# the monstrosity below... is the result of that idea.

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from traceback import print_exc

import aiohttp
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


class Receiver:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.our_max_msg = time.time()


class WebsocketUpstream:
    def __init__(self, url="ws://archivebot.com:4568/stream"):
        self.url = url
        self._receivers = []
        self.stopped = False
        self.rps = 0
        self.max_rps = 300
        self.message_count = 0
        self.queue = deque(maxlen=3_000)

    @property
    def powersave(self):
        # should we run? only if we have any receivers.
        return len(self._receivers) == 0

    async def calculate_rps(self):
        while not self.stopped:
            start_time, start_msgs = time.time(), self.message_count
            await asyncio.sleep(1)
            self.rps = (self.message_count - start_msgs) / (time.time() - start_time)
            self.max_rps = max(self.rps, self.max_rps)
            self.message_count -= start_msgs  # reset

    async def upstream_websocket(self):
        if self.powersave:
            return
        try:
            print("connecting to upstream", self.url)
            async with websockets.connect(self.url) as ws:
                while not self.stopped:
                    if self.powersave:
                        self.queue.clear()
                        break
                    message = await ws.recv()
                    self.queue.append((time.time(), message))
                    self.message_count += 1
        except Exception as e:
            print(e)

    async def print_stats(self):
        print(f"receivers={len(self._receivers)} rps={self.rps:.2f}", end=" ")
        print(f"max_rps={self.max_rps:.2f} powersave={self.powersave}", end=" ")
        print(f"queue={len(self.queue)} time={time.time():.2f}")
        stale = []
        for i, receiver in enumerate(self._receivers):
            host, port = receiver.websocket.client.host, receiver.websocket.client.port
            behind = self.queue[-1][0] - receiver.our_max_msg if self.queue else 0
            print(f"receiver {i:3d} {host:15s} {port:5d} behind {behind:6.2f}", end=" ")
            if behind > 60:
                print("stale! closing")
                stale.append(self.cleanup(receiver))
            else:
                print()
        if stale:
            print(f"cleaning up {len(stale)} stale receivers")
            await asyncio.gather(*stale)



    async def dispatcher(self, function: callable, interval: float = 1.0):
        while not self.stopped:
            await function()
            if interval:
                await asyncio.sleep(interval)

    async def cleanup(self, receiver: Receiver):
        receiver.our_max_msg = time.time() + 1000
        with suppress(Exception):
            self._receivers.remove(receiver)
        with suppress(Exception):
            await receiver.websocket.close()

    async def broadcast(self, receiver: Receiver):
        messages = [msg for msg in self.queue if msg[0] > receiver.our_max_msg]
        try:
            for message in messages:
                await receiver.websocket.send_text(message[1])
        except:
            await self.cleanup(receiver)
            raise Exception("receiver disconnected")
        receiver.our_max_msg = messages[-1][0] if messages else receiver.our_max_msg

    async def freshen_queue(self):
        # remove old messages based on all receivers' max_msg
        if not self._receivers or not self.queue:
            return
        max_msg = min(receiver.our_max_msg for receiver in self._receivers)
        while self.queue and self.queue[0][0] < max_msg:
            self.queue.popleft()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # before startup
    print("starting up")
    app._ws = WebsocketUpstream()
    # dispatch a task for recv_loop
    asyncio.create_task(app._ws.dispatcher(app._ws.upstream_websocket))
    asyncio.create_task(app._ws.dispatcher(app._ws.calculate_rps))
    asyncio.create_task(app._ws.dispatcher(app._ws.print_stats))
    asyncio.create_task(app._ws.dispatcher(app._ws.freshen_queue, 0.1))
    app._logs_recent = None, None
    # exit
    yield
    app._ws.stopped = True


app = FastAPI(lifespan=lifespan)

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        receiver = Receiver(websocket)
        app._ws._receivers.append(receiver)
        while not app._ws.stopped:
            await asyncio.sleep(0.01)
            await app._ws.broadcast(receiver)
    except:
        await app._ws.cleanup(receiver)


# also serve static files:
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/beta")
async def beta():
    return FileResponse("static/beta.html")


# for logs/recent, send a request to upstream, or if we have one that's <3s old, return that
@app.get("/logs/recent")
async def api_logs_recent():
    last_time, logs_recent = app._logs_recent
    if last_time is None or time.time() - last_time > 3:
        # get it from http://archivebot.com/logs/recent
        async with aiohttp.ClientSession() as session:
            async with session.get("http://archivebot.com/logs/recent") as response:
                logs_recent = await response.text()
                app._logs_recent = time.time(), logs_recent
    # logs_recent is a json, return it as-is
    return Response(content=logs_recent, media_type="application/json")
