#!/usr/bin/env bash
# Sends the audio container's finished files to the audio bucket (design §10.4).
#
# It runs on the host, as root, every minute (smart-dictionary-audio-upload.timer), and
# uploads with the host's own AWS role. The containers can't reach that role (IMDSv2 hop
# limit 1), so no AWS credentials ever enter them.
#
# For each finished file in the outbox (*.m4a; files still being written end in .part):
# 1. upload it under the same path, with its content type and a year's immutable caching;
# 2. leave a receipt (<file>.done) and delete the file.
# The audio job turns each receipt into the row's uploaded_at, and only then do cards link
# the file. A failed upload leaves the file in place for the next run.
set -euo pipefail

OUTBOX=/opt/smart-dictionary/audio-outbox
PARAMETERS=/smart-dictionary/prod

exec 9>/var/lock/smart-dictionary-audio-upload
flock -n 9 || exit 0 # the previous run is still uploading
cd "$OUTBOX" 2>/dev/null || exit 0

# Paths relative to the outbox, which are the S3 keys. Hidden folders are skipped.
mapfile -d '' files < <(find . -path './.*' -prune -o -type f -name '*.m4a' -print0)
((${#files[@]})) || exit 0

token=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
AWS_REGION=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" \
  http://169.254.169.254/latest/meta-data/placement/region)
BUCKET=$(aws ssm get-parameter --region "$AWS_REGION" --name "$PARAMETERS/app/AUDIO_BUCKET" \
  --query Parameter.Value --output text)
export AWS_REGION BUCKET

upload() {
  local file=$1 key=${1#./}
  aws s3api put-object --bucket "$BUCKET" --key "$key" --body "$file" \
    --content-type audio/mp4 --cache-control "public, max-age=31536000, immutable" \
    --output text --query ETag >/dev/null &&
    touch "$file.done" && rm -f "$file"
}
export -f upload

# Eight at a time: each upload is a short request, so this clears hundreds a minute.
printf '%s\0' "${files[@]}" | xargs -0 -r -n 1 -P 8 bash -c 'upload "$1" || true' _
