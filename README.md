# archivebot-dashboard-repeater

this is a simple repeater / proxy for the [archivebot.com dashboard](http://archivebot.com)

it only connects to the archivebot.com websocket when at least one client is connected to it.

mine runs at <http://85.215.151.231/>.

## Usage

```bash

docker-compose up -d

```

it should also work if you install [its dependencies](ws-repeater/requirements.txt) and run it as [such](ws-repeater/Dockerfile#L8) but then you have to take care to forward :80 to :4568 yourself as it will only listen on :4568.
