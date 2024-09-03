# archivebot-dashboard-repeater

this is a simple repeater / proxy for the [archivebot.com dashboard](http://archivebot.com)

it only connects to the archivebot.com websocket when at least one client is connected to it.

mine runs at <http://85.215.151.231/>.

it also supports connecting to zeromq via setting the UPSTREAM environment variable

acting as a drop in replacement for [the websocket component of archivebot](https://github.com/ArchiveTeam/ArchiveBot/blob/ad9703c489168bb88cd53b3f4ea3b6dadfe8820f/INSTALL.backend#L155-L160)

if UPSTREAM is not set, it will connect to the archivebot.com:4568/stream websocket.

## Usage

```bash
cd ws-repeater
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
gunicorn app:app -b [::]:80 -b [::]:4568 --worker-class uvicorn.workers.UvicornWorker --max-requests 50 --reload
```

or

```bash

docker-compose up -d

```
