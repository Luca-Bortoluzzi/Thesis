#!/bin/bash
docker run -d \
  --name=wireshark \
  --net=host \
  --cap-add=NET_ADMIN \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Etc/UTC \
  -p 3000:3000 `#optional` \
  -p 3001:3001 `#optional` \
  -v /path/to/wireshark/config:/config \
  --shm-size="1gb" \
  --restart unless-stopped \
  lscr.io/linuxserver/wireshark:latest