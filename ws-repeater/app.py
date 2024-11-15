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
from sys import stdout
from os import getenv
from traceback import print_exc
import logging
from queue import Queue

import aiohttp
import websockets
import zmq.asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

UPSTREAM = getenv("UPSTREAM", "ws://archivebot.com:4568/stream")

# prepare for terminal output with multi-line status display
if logging.root.level > logging.DEBUG and stdout.isatty():
    # record some terminal properties
    from curses import tigetstr, setupterm
    setupterm()
    erase_line = tigetstr('el') or bytes()
    erase_line_go_next = erase_line + b'\n'
    up_one_line = tigetstr('cuu1') or tigetstr('up') or bytes()

    # logging will overwrite a multi-line terminal status display
    # so override the logging handler with one that saves to a queue,
    # the status display will then synchronise display of the log queue.

    # first setup a formatter for the default logger
    logging.basicConfig()
    # these loggers now have a default formatter set
    logger_names = ('', 'gunicorn.access', 'gunicorn.error')
    log_queue = Queue()
    for name in logger_names:
        # scrounge the formatter for use by the queue handler
        logger = logging.getLogger(name)
        if logger.handlers:
            formatter = logger.handlers[0].formatter
        else:
            formatter = logging.Formatter()
        # setup logging to the queue for terminal sync purposes
        handler = logging.handlers.QueueHandler(log_queue)
        handler.setFormatter(formatter)
        # replace the default handlers with the queue
        logger.handlers.clear()
        logger.addHandler(handler)
else:
    log_queue = None
    erase_line = erase_line_go_next = up_one_line = bytes()

class Receiver:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.our_max_msg = time.time()


class WebsocketUpstream:
    def __init__(self, url="ws://archivebot.com:4568/stream"):
        self.url = url
        # url can also be zmq://.
        self._receivers = []
        self.stopped = False
        self.rps = 0
        self.max_rps = 300
        self.message_count = 0
        self.queue = deque(maxlen=10_000)
        self.erase_lines = 0

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
        if self.url.startswith("tcp://"):
            context = zmq.asyncio.Context()
            socket = context.socket(zmq.SUB)
            socket.setsockopt_string(zmq.SUBSCRIBE, "")
            socket.setsockopt(zmq.LINGER, 1)
            socket.connect(self.url)
            while not self.stopped:
                try:
                    message = await socket.recv_string()
                    self.queue.append((time.time(), message))
                    self.message_count += 1
                except Exception as e:
                    print(e)
        if self.powersave:
            return
        try:
            msg = ("connecting to upstream", self.url)
            if log_queue:
                log_queue.put_nowait(msg)
            else:
                print(*msg)
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
        if log_queue:
            # Erase status lines from previous status
            stdout.buffer.write(up_one_line*self.erase_lines)
            stdout.buffer.write(erase_line_go_next*self.erase_lines)
            stdout.buffer.write(up_one_line*self.erase_lines)

            # Print log messages since previous status
            while not log_queue.empty():
                item = log_queue.get_nowait()
                if isinstance(item, logging.LogRecord):
                    print(item.getMessage())
                elif isinstance(item, (tuple, list)):
                    print(*item)
                else:
                    print(item)

        # Print new multi-line status display
        self.erase_lines = 0
        print(f"receivers={len(self._receivers)} rps={self.rps:.2f}", end=" ")
        print(f"max_rps={self.max_rps:.2f} powersave={self.powersave}", end=" ")
        print(f"queue={len(self.queue)} time={time.time():.2f}")
        self.erase_lines += 1
        stale = []
        for i, receiver in enumerate(self._receivers):
            host, port = receiver.websocket.client.host, receiver.websocket.client.port
            behind = self.queue[-1][0] - receiver.our_max_msg if self.queue else 0
            print(f"receiver {i:3d} {host:39s} {port:5d} behind {behind:6.2f}", end=" ")
            if behind > 60:
                print("stale! closing")
                stale.append(self.cleanup(receiver))
            else:
                print()
            self.erase_lines += 1
        if stale:
            print(f"cleaning up {len(stale)} stale receivers")
            self.erase_lines += 1
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
    app._ws = WebsocketUpstream(UPSTREAM)
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
