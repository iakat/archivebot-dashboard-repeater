# archivebot-dashboard-repeater

this is a simple repeater / proxy for the [archivebot.com dashboard](http://archivebot.com)

it only connects to the archivebot.com websocket when at least one client is connected to it.

mine runs at <http://85.215.151.231/>.

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
