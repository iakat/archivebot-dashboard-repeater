#!/usr/bin/env bash
set -ex
# cd to this dir
cd "$(dirname "$0")"
# get the archivebot.com assets
mkdir -p "assets"

# /
curl -fsL "http://archivebot.com/" -o "index.html"
# /assets/dashboard.js
curl -fsL "http://archivebot.com/assets/dashboard.js" -o "assets/dashboard.js"
# /assets/favicon.png
curl -fsL "http://archivebot.com/assets/favicon.png" -o "assets/favicon.png"
# /beta/
curl -fsL "http://archivebot.com/beta/" -o "beta.html"
# /assets/dashboard3.js
curl -fsL "http://archivebot.com/assets/dashboard3.js" -o "assets/dashboard3.js"
