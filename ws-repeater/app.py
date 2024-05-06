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


class Pointer:
    '''to keep state of the current message'''
    def __init__(self, limit):
        self.value = 0
        self.limit = limit
    # increment magic method, that can wrap around if needed
    def __iadd__(self, other):
        self.value = (self.value + other) % self.limit
        return self.value

class WebsocketUpstream:
    def __init__(self, url="ws://archivebot.com:4568/stream", limit=5000):
        self.url = url
        self._receivers = []
        self.stopped = False
        self.rps = 0
        self.max_rps = 300
        self.message_count = 0
        self.queue = asyncio.Queue(maxsize=limit)
        self.pointer = Pointer(limit)

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
            self.message_count  -= start_msgs  # reset

    async def upstream_websocket(self):
        if self.powersave:
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

    # async def on_message(self, message: websockets.Data):
    #     # we put things in the queue,

    #     async def task(socket: WebSocket, queue: asyncio.Queue):
    #         try:
    #             await queue.put(message)
    #         except Exception as e:
    #             print(e)
    #             await self.cleanup(socket, queue)

    #     tasks = [task(socket, queue) for socket, queue in self._receivers]
    #     await asyncio.gather(*tasks)
    # let's do stuff with the Pointer instead

    # async def broadcast(self, websocket: WebSocket, queue: asyncio.Queue):
    #     try:
    #         message = await queue.get()
    #         await websocket.send_text(message)
    #     except Exception as e:
    #         print("exited!")
    #         await self.cleanup(websocket, queue)
    # again, pointers.

    async def on_message(self, message: websockets.Data):
        self.queue.put_nowait(message)

    async def broadcast(self, websocket: WebSocket, pointer: Pointer):
        # ensure to wrap the pointer around
        pointer %= self.queue.maxsize
        try:
            message = self.queue[pointer]
            await websocket.send_text(message)
            pointer += 1
        except Exception as e:
            print("exited!")
            await self.cleanup(websocket, pointer)


    async def cleanup(self, websocket: WebSocket, pointer: Pointer):
        self._receivers.remove((websocket, pointer))

    async def print_stats(self):
        print(f"receivers={len(self._receivers)} rps={self.rps:.2f}", end=" ")
        print(f"max_rps={self.max_rps:.2f} powersave={self.powersave}")
        for i, (ws, q) in enumerate(self._receivers):
            print(
                f"receiver={i} host={ws.client.host} port={ws.client.port} qsize={q.qsize()}"
            )

    async def dispatcher(self, function: callable, interval=1.0, *args, **kwargs):
        while not self.stopped:
            await function(*args, **kwargs)
            if interval:
                await asyncio.sleep(interval)


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
    our_pointer = Pointer(app._ws.queue.maxsize)
    # ^ we store up to 5 seconds of messages per client
    print("appending receiver")
    app._ws._receivers.append((websocket, our_pointer))
    # dispatch a task to send messages to the websocket from the queue
    asyncio.create_task(
        app._ws.dispatcher(app._ws.broadcast, 0.0, websocket, our_pointer)
    )
    print("waiting for websocket to close")

    while not app._ws.stopped:
        await asyncio.sleep(1)
        if websocket.client_state == 3:
            break
        if (websocket, our_pointer) not in app._ws._receivers:
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
