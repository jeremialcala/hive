#!/bin/bash
gh repo clone jeremialcala/hive
cd hive/ || exit
git checkout develop
git pull

docker rm -f hive-api
docker rmi hive-gateway
docker system prune -a -f
docker build -t hive-gateway:latest .
docker run -it -p 5002:5002 --name hive-api -d hive-gateway
