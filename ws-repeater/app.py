# idea:

# ws-repeater
# resiliently (re)connect to a websocket, expose the websocket.
# use fastapi to expose /ws, get messages from upstream every 100ms
# and broadcast them to all connected clients.

# the monstrosity below... is the result of that idea.

import asyncio
import time
from contextlib import asynccontextmanager

import aiohttp
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


class WebsocketUpstream:
    def __init__(self, url="ws://archivebot.com:4568/stream"):
        self.url = url
        self._receivers = []
        self.stopped = False
        self.rps = 0
        self.max_rps = 1000
        self.message_count = 0

    @property
    def powersave(self):
        # should we run? only if we have any receivers.
        return len(self._receivers) == 0

    async def calculate_rps(self):
        while not self.stopped:
            start_time = time.time()
            await asyncio.sleep(1)
            elapsed_time = time.time() - start_time
            self.rps = int(self.message_count / elapsed_time)
            self.max_rps = max(self.rps, self.max_rps)
            self.message_count = 0  # reset the counter

    async def upstream_websocket(self):
        if self.powersave:
            await asyncio.sleep(1)
            return
        try:
            print("connecting to", self.url)
            async with websockets.connect(self.url) as ws:
                while not self.stopped:
                    if self.powersave:
                        break
                    message = await ws.recv()
                    await self.on_message(message)
                    self.message_count += 1
                    # ^ we use asyncio.Queue here,
                    # so we hope to keep ordering of messages.
        except Exception as e:
            print(e)
            await asyncio.sleep(1)

    async def on_message(self, message: websockets.Data):
        # we put things in the queue,

        async def task(socket: WebSocket, queue: asyncio.Queue):
            try:
                await queue.put(message)
            except Exception as e:
                print(e)
                await self.cleanup(socket, queue)

        tasks = [task(socket, queue) for socket, queue in self._receivers]
        await asyncio.gather(*tasks)

    async def broadcast(self, websocket: WebSocket, queue: asyncio.Queue):
        try:
            message = await queue.get()
            await websocket.send_text(message)
        except Exception as e:
            print(e)
            print("exited!")
            await self.cleanup(websocket, queue)

    async def cleanup(self, websocket: WebSocket, queue: asyncio.Queue):
        try:
            self._receivers.remove((websocket, queue))
        except:
            # fuzzy match remove
            self._receivers = [(ws, q) for ws, q in self._receivers if ws != websocket]

    async def print_stats(self):
        print(
            f"receivers={len(self._receivers)}, upstream rps={self.rps}, powersave={self.powersave}"
        )
        for i, (ws, q) in enumerate(self._receivers):
            print(i, ws, q.qsize())

    async def dispatcher(self, function: callable, interval=1.0, *args, **kwargs):
        while not self.stopped:
            start = time.time()
            await function(*args, **kwargs)
            if interval and (sleepytime := interval - (time.time() - start)) > 0:
                await asyncio.sleep(sleepytime)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # before startup
    print("starting up")
    app._ws = WebsocketUpstream()
    # dispatch a task for recv_loop
    asyncio.create_task(app._ws.dispatcher(app._ws.upstream_websocket))
    asyncio.create_task(app._ws.dispatcher(app._ws.calculate_rps))
    asyncio.create_task(app._ws.dispatcher(app._ws.print_stats))
    app._logs_recent = None, None
    # exit
    yield
    app._ws.stopped = True


app = FastAPI(lifespan=lifespan)


@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    print("connected")
    await websocket.accept()
    print("creating queue")
    our_queue = asyncio.Queue(maxsize=app._ws.max_rps * 5)
    # ^ we store up to 5 seconds of messages per client
    print("appending receiver")
    app._ws._receivers.append((websocket, our_queue))
    # dispatch a task to send messages to the websocket from the queue
    asyncio.create_task(app._ws.dispatcher(app._ws.broadcast, 0.0, websocket, our_queue))
    print("waiting for websocket to close")

    while not app._ws.stopped:
        await asyncio.sleep(1)
        if our_queue.full():
            print("queue full, closing")
            # buh-bye
            break
        # if we are not in receivers, close
        if (websocket, our_queue) not in app._ws._receivers:
            print("not in receivers, closing")
            break


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
