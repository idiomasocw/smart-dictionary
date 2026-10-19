#!/usr/bin/env bash
# Deploys one image on the dictionary host. The deploy workflow copies this directory to
# /opt/smart-dictionary and runs it as root through SSM Run Command:
#
#   deploy.sh <image>   e.g. <account>.dkr.ecr.us-east-2.amazonaws.com/smart-dictionary:<sha>
#
# 1. Writes the env files from SSM Parameter Store (see compose.yaml).
# 2. Pulls the image and applies the migrations as the owner.
# 3. Starts the new containers and waits for the API's health check. If it doesn't pass,
#    the previous image goes back up and the script fails. Migrations stay applied, so
#    each one must work with the code before it.
# 4. Starts the audio container, and installs the timer that uploads its files
#    (audio-upload.sh). Audio trouble is reported but never fails the deploy.
set -euo pipefail

IMAGE=${1:?usage: deploy.sh <image>}
DIR=/opt/smart-dictionary
PARAMETERS=/smart-dictionary/prod
cd "$DIR"

exec 9>/var/lock/smart-dictionary-deploy
flock -n 9 || { echo "another deploy is running" >&2; exit 1; }

token=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
AWS_REGION=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" \
  http://169.254.169.254/latest/meta-data/placement/region)
export AWS_REGION

# write_env <path> <file>: one KEY='value' line per parameter under the path, mode 600.
# Single quotes keep Compose from expanding $ in passwords.
write_env() {
  local tmp
  tmp=$(mktemp "$DIR/.env.XXXXXX")
  chmod 600 "$tmp"
  aws ssm get-parameters-by-path --path "$PARAMETERS/$1" --recursive --with-decryption \
    --query 'Parameters[].[Name,Value]' --output text |
    while IFS=$'\t' read -r name value; do
      if [[ $value == *"'"* ]]; then
        echo "$name contains a single quote, which env files can't hold" >&2
        exit 1
      fi
      printf "%s='%s'\n" "${name##*/}" "$value"
    done >"$tmp"
  mv "$tmp" "$DIR/$2"
}
write_env app app.env
write_env migrate migrate.env
write_env caddy caddy.env
for required in app.env:DATABASE_URL app.env:BBB_PROXY_SECRET migrate.env:DATABASE_URL \
  caddy.env:SITE_ADDRESS; do
  file=${required%%:*} key=${required#*:}
  grep -q "^$key=" "$file" || { echo "missing parameter: $key for $file" >&2; exit 1; }
done

aws ecr get-login-password | docker login --username AWS --password-stdin "${IMAGE%%/*}"
export IMAGE
docker compose pull --quiet
docker compose run --rm migrate

previous=$(cat current-image 2>/dev/null || true)
if docker compose up --detach --wait --wait-timeout 180 --remove-orphans caddy api; then
  echo "$IMAGE" >current-image
  echo "deployed $IMAGE"
else
  echo "the new containers didn't pass their health checks" >&2
  docker compose logs --tail 80 api >&2 || true
  if [[ -n $previous && $previous != "$IMAGE" ]]; then
    IMAGE=$previous docker compose up --detach --wait --wait-timeout 180 --remove-orphans \
      caddy api && echo "rolled back to $previous" >&2
  fi
  exit 1
fi

# Audio (design §10.4). The container writes to the outbox as its own user (uid 10001);
# the timer uploads with this host's role every minute.
install -d -o 10001 -g 10001 -m 750 "$DIR/audio-outbox"
install -m 644 "$DIR/smart-dictionary-audio-upload.service" \
  "$DIR/smart-dictionary-audio-upload.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now smart-dictionary-audio-upload.timer
docker compose up --detach audio || echo "warning: the audio container didn't start" >&2

# Keep a week of images for quick rollbacks; ECR keeps the last 20.
docker image prune --all --force --filter "until=168h" >/dev/null
