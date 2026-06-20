#!/bin/bash
yum update -y
amazon-linux-extras install docker -y
service docker start
usermod -a -G docker ec2-user
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 763132995558.dkr.ecr.ap-south-1.amazonaws.com
docker pull 763132995558.dkr.ecr.ap-south-1.amazonaws.com/ratesentry:latest
docker run -d   -p 8000:8000   -e REDIS_HOST=ratesentry-redis.cm6y82.0001.aps1.cache.amazonaws.com   -e REDIS_PORT=6379   -e RATE_LIMIT_MAX_REQUESTS=100   -e RATE_LIMIT_WINDOW_SECONDS=60   --restart always   763132995558.dkr.ecr.ap-south-1.amazonaws.com/ratesentry:latest
