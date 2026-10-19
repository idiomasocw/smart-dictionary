# Deploying the dictionary

The API runs on its own EC2 host in us-east-2 (design §11, integration §5).

| Path | What it is |
|---|---|
| `infra/cdk/` | The CDK app (Python) that creates the AWS resources; `npx cdk` runs the CLI version pinned in `package.json` |
| `deploy/` | What runs on the host: `compose.yaml` (Caddy and the API), the `Caddyfile` and `deploy.sh` |
| `.github/workflows/deploy.yml` | Builds the arm64 image, pushes it to ECR and deploys it through SSM |
| `backend/Dockerfile` | The image |

**What the stack creates** (stack `SmartDictionary`, about $30/month):
- **The host:** a t4g.medium on Amazon Linux 2023 (arm64), with an Elastic IP and a 20 GB encrypted disk.
  - Only ports 80 and 443 are open, with **no SSH**. You connect through SSM Session Manager.
  - Its metadata service answers the host only, so containers can't borrow its role.
- **The image registry:** the ECR repository `smart-dictionary`, with immutable tags. It keeps the last 20 images.
- **Storage:** an S3 bucket for deploy bundles; audio, exports and backups come later.
- **GitHub access:** a GitHub OIDC provider and the role `smart-dictionary-github-deploy`. Only this repository's `production` environment can assume the role, so no AWS keys are stored in GitHub.
- **Alerts:** email to admin@onecultureworld.com.
  - A hardware failure makes EC2 recover the host.
  - An unresponsive operating system makes EC2 reboot it.
  - CPU above 80% for 15 minutes sends a warning.
  - A $40 monthly budget covers everything in us-east-2.

Every command below runs in **Git Bash** from the repo root, signed in to the AWS CLI with the default profile (account 924231986837). Git Bash rewrites arguments that start with `/` into Windows paths, so commands that take SSM parameter names start with `MSYS_NO_PATHCONV=1`.

## One-time setup

**1. The API's database role.** Use the owner's **session pooler** URL. The containers on the host reach Supabase over IPv4, and the direct connection is IPv6 only:
`postgresql+psycopg://postgres.ydejdshoolmbckgxebms:<password>@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require`

```bash
cd backend
read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
uv run smart-dictionary migrate              # revision 0005 adds the dictionary_app role
APP_URL=$(uv run smart-dictionary db app-role)
```

`db app-role` gives `dictionary_app` a random password and logs in with it through the pooler to prove it works. It then hands the URL (`dictionary_app.ydejdshoolmbckgxebms@…`) to `APP_URL`, so the URL never shows on screen.
- **What the role can do:** read and write rows, but never change the schema.
- **Why the password never reaches a log:** the server only receives a SCRAM hash of it.

The seeding commands can keep using the direct connection from your PC.

**2. The secrets**, in the same shell:

```bash
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/app/DATABASE_URL --value "$APP_URL"
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/migrate/DATABASE_URL --value "$DATABASE_URL"
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/app/BBB_PROXY_SECRET --value "$(openssl rand -hex 32)"
unset DATABASE_URL APP_URL
cd ..
```

The BBB server's nginx gets the same proxy secret in Phase 4. To read it then:

```bash
MSYS_NO_PATHCONV=1 aws ssm get-parameter --region us-east-2 --with-decryption --name /smart-dictionary/prod/app/BBB_PROXY_SECRET --query Parameter.Value --output text
```

**3. The AWS resources.** The deploy takes about five minutes:

```bash
cd infra/cdk
npm ci
npx cdk bootstrap aws://924231986837/us-east-2   # once per region
npx cdk diff                                     # review what will change
npx cdk deploy --require-approval never
cd ../..
```

Git Bash doesn't give Node a real terminal, so CDK can't ask for confirmation there. Review with `cdk diff` and deploy with `--require-approval never`. In PowerShell, a plain `npx cdk deploy` asks.

The deploy prints `ElasticIp`, `SiteUrl`, `InstanceId`, `SessionCommand` and the rest.

**4. Confirm the alert email.** AWS sends admin@onecultureworld.com an "AWS Notification - Subscription Confirmation" email; click *Confirm subscription*. The budget alerts need no confirmation.

**5. DNS in Cloudflare.** Add an A record with these settings:
- **Name:** `dictionary`
- **IPv4 address:** the `ElasticIp` output
- **Proxy status:** **DNS only** (grey cloud)

The record must stay unproxied for two reasons:
- Caddy gets its Let's Encrypt certificate directly.
- Later, the BBB-only routes check the caller's real IP.

**6. GitHub.** Commit and push this work first: GitHub only runs manual workflows that exist on the default branch. Then:

```bash
gh variable set AWS_DEPLOY_ROLE_ARN --repo idiomasocw/smart-dictionary --body "arn:aws:iam::924231986837:role/smart-dictionary-github-deploy"
```

The `production` environment is created on the first run. Optionally, add yourself as a required reviewer under *Settings → Environments → production*, so every deploy waits for your approval.

**7. The first deploy.** The first image takes a few minutes to build under emulation; later builds are cached:

```bash
gh workflow run deploy.yml --repo idiomasocw/smart-dictionary
```

```bash
gh run watch --repo idiomasocw/smart-dictionary "$(gh run list --repo idiomasocw/smart-dictionary --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

```bash
curl https://dictionary.onecultureworld.com/readyz
```

The last command should print `{"status":"ok","database":"up"}`.

## Everyday

**Deploy or roll back.** A rollback is just a deploy of an earlier commit, and it reuses that commit's image. Migrations only go forward, so each one must keep working with the code before it.

```bash
gh workflow run deploy.yml --repo idiomasocw/smart-dictionary                  # main
gh workflow run deploy.yml --repo idiomasocw/smart-dictionary -f ref=<commit>   # any commit
```

**Connect to the host.** In Git Bash, `winpty` gives the session a real console; in PowerShell, leave it out:

```bash
INSTANCE=$(aws cloudformation describe-stacks --region us-east-2 --stack-name SmartDictionary --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
winpty aws ssm start-session --region us-east-2 --target "$INSTANCE"
```

Inside the session:

```bash
sudo -i
cd /opt/smart-dictionary
docker compose ps
docker compose logs --tail 100 --follow api
cat current-image
```

**Secrets and AI keys.** Each deploy copies every parameter under `/smart-dictionary/prod/app/` into the API's environment. The AI features (Phase 5) read `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY` and `GROQ_API_KEY` from there. Store each key like this, then deploy:

```bash
read -rsp "Anthropic API key: " KEY && echo && MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/app/ANTHROPIC_API_KEY --value "$KEY"; unset KEY
```

Add `--overwrite` to change an existing value. This lists what's stored, names only:

```bash
MSYS_NO_PATHCONV=1 aws ssm get-parameters-by-path --region us-east-2 --path /smart-dictionary/prod --recursive --query "Parameters[].Name"
```

**A new database password for the API.** The old password stops working at once, so deploy right after:

```bash
cd backend
read -rsp "Owner DATABASE_URL: " DATABASE_URL && echo && export DATABASE_URL
APP_URL=$(uv run smart-dictionary db app-role)
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --overwrite --name /smart-dictionary/prod/app/DATABASE_URL --value "$APP_URL"
unset DATABASE_URL APP_URL
```

**Changing the AWS resources.** Edit `infra/cdk/stack.py`, then run `npx cdk diff` and `npx cdk deploy --require-approval never` from `infra/cdk`.

The host's image is pinned in `cdk.context.json`. Refreshing it (`npx cdk context --reset <key>`) replaces the host, so deploy again afterwards.

## Not yet

These arrive with the features that need them (design §11):
- the worker;
- the nightly `pg_dump` to S3;
- Uptime Kuma;
- CloudFront.
