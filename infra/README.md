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

## The three credentials

Three secret values make this work. Knowing them makes every setup step make sense.

| Credential | What it is | Who uses it | Where it's stored |
|---|---|---|---|
| **Owner database URL** | Logs in as `postgres`, which can do anything, schema changes included | Only the one-off migration container, during a deploy | SSM: `/smart-dictionary/prod/migrate/DATABASE_URL` |
| **API database URL** | Logs in as `dictionary_app`, which reads and writes rows but can't change the schema | The API, all the time | SSM: `/smart-dictionary/prod/app/DATABASE_URL` |
| **`BBB_PROXY_SECRET`** | A random value that you generate, known to the dictionary and your BBB server only | The API checks it on every student request | SSM: `/smart-dictionary/prod/app/BBB_PROXY_SECRET`, and in Phase 4 also your BBB server's nginx |

Why two database URLs? If someone ever broke into the API, the API's own login couldn't drop or alter tables. Only migrations, which run for a few seconds at deploy time, get the owner's login.

**What `BBB_PROXY_SECRET` is, and isn't.**
- **What it proves.** Students will reach the dictionary from the BBB plugin, through your BBB server. That server's nginx checks the student's BBB session, then forwards the request with the student's identity and this secret. The dictionary believes "this is student X" only when the secret matches, so nobody on the internet can fake a student.
- **What it isn't.** It's not the BBB secret from `sudo bbb-conf --secret`, which signs BBB API calls such as create and join, and which your LMS uses. It's not anything else your LMS uses. Never reuse either of those here.
- **Its scope.** It's one secret for this dictionary deployment, shared between it and your BBB server. It isn't tied to a tenant: every request that comes through it counts as the `ocw` tenant, the only one there is. If another organization ever connects its own BBB server, it gets its own credential (the design's `api_clients`).
- **Why it's needed now, though nginx only arrives in Phase 4.** In production the API refuses to start without it. Until Phase 4 nothing else knows it, so every student endpoint answers 401 and only the health checks respond. That's what you want before the plugin exists.

**SSM Parameter Store** is AWS's encrypted store for settings and secrets. You write to it from your PC with the AWS CLI. The server reads it at every deploy and writes the values into files only its containers can read. Nothing secret lives in the repo or in GitHub.

## Two Git Bash habits used below

- **`read -rsp "…" NAME`** asks for a value without showing it on screen or saving it in your shell history.
- **`MSYS_NO_PATHCONV=1`** goes before AWS commands that take a parameter name like `/smart-dictionary/prod/...`. Without it, Git Bash turns that name into a Windows path (`C:/Program Files/Git/smart-dictionary/...`), and the parameter gets stored under the wrong name.

Run everything in **one Git Bash window**, from the repo root, signed in to the AWS CLI as usual (default profile, account 924231986837).

## One-time setup

Steps 1 to 3 prepare the database and the secrets, and must run in order. Steps 4 to 7 create the server and connect it to the outside world. Step 8 is the first deploy. All of it takes about 30 minutes, mostly waiting.

### Step 1. Apply today's database change to production

**What it does.** It creates the `dictionary_app` role, with no password yet, and gives it row access to every table. That's revision 0005, written today.

**Why it's needed.**
- Production is at revision 0004, the last one you applied before seeding. 0005 is new.
- Step 2 can only give the role a password once the role exists.
- From now on, every deploy applies new revisions by itself, so this is the last time you run `migrate` against production by hand.

You need the owner's **session pooler** URL. It's the one you used for `migrate` before, with `+psycopg` added and SSL required:
`postgresql+psycopg://postgres.ydejdshoolmbckgxebms:<password>@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require`
The server must use the pooler, because its containers can only reach Supabase over IPv4 and the direct connection is IPv6 only. The seeding commands on your PC can keep using the direct one.

```bash
cd backend
read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
uv run smart-dictionary migrate
```

**What you should see:** `Running upgrade 0004 -> 0005, A least-privilege role for the API…`. If nothing is listed, 0005 was already applied, which is harmless.

**Careful:** until step 3 ends with `unset`, every `smart-dictionary` command in this window talks to **production**.

### Step 2. Give the API's role a password

**What it does.**
1. It generates a long random password for `dictionary_app`.
2. It logs in with it through the pooler, to prove the login works.
3. It puts the resulting URL in the variable `APP_URL`, without printing it.

**Why it's needed.** The API needs its own login. A generated password means you never have to invent one or see it, and the server only ever receives a SCRAM hash of it, so it can't show up in Supabase's logs.

```bash
APP_URL=$(uv run smart-dictionary db app-role)
echo "$APP_URL" | cut -d: -f1-2
```

**What you should see:**
- `dictionary_app has a new password and logs in. Its DATABASE_URL:`
- Then, from the `echo` line, the URL without its password: `postgresql+psycopg://dictionary_app.ydejdshoolmbckgxebms`.

If the command fails instead, the pooler refused the login. Stop and send me the message; nothing has been deployed.

### Step 3. Store the three credentials in AWS

**What it does.** It saves the API's URL, the owner's URL and a newly generated proxy secret in SSM Parameter Store, encrypted (`SecureString`).

**Why it's needed.** The first deploy reads them from there. Without them, the API can't start.

```bash
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/app/DATABASE_URL --value "$APP_URL"
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/migrate/DATABASE_URL --value "$DATABASE_URL"
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --name /smart-dictionary/prod/app/BBB_PROXY_SECRET --value "$(openssl rand -hex 32)"
unset DATABASE_URL APP_URL
cd ..
```

`openssl rand -hex 32` makes the proxy secret: 64 random hexadecimal characters.

**What you should see:** each `put-parameter` prints `"Version": 1`. To check, list the names (never the values):

```bash
MSYS_NO_PATHCONV=1 aws ssm get-parameters-by-path --region us-east-2 --path /smart-dictionary/prod --recursive --query "Parameters[].Name"
```

That should list the three names. After step 4 a fourth, `/smart-dictionary/prod/caddy/SITE_ADDRESS`, appears; the stack creates it.

### Step 4. Create the AWS resources

**What it does.** It creates the server and everything around it (the list at the top of this page) as one CloudFormation stack, `SmartDictionary`, in us-east-2.

```bash
cd infra/cdk
npm ci
npx cdk bootstrap aws://924231986837/us-east-2
npx cdk diff
npx cdk deploy --require-approval never
cd ../..
```

**What each line does:**
- **`npm ci`** installs the exact CDK command-line tool recorded in `package-lock.json` into `infra/cdk/node_modules`.
  - `npx cdk` then runs that version, never whatever `cdk` might be installed globally.
- **`cdk bootstrap`** runs once per region. It creates CDK's helper stack, `CDKToolkit`: a bucket for uploads and the roles CDK deploys with.
  - us-east-1 already has one for your LMS; us-east-2 doesn't.
  - Running it again later is harmless.
- **`cdk diff`** is a preview that changes nothing. It lists what will be created, including the IAM roles and the open ports (80 and 443, no 22).
- **`cdk deploy`** creates everything, in about 5 minutes.
  - The `--require-approval never` flag is there because Git Bash can't answer CDK's yes/no question. You reviewed the changes with `diff` instead.
  - In PowerShell, a plain `npx cdk deploy` asks.

**What you should see:** `✅  SmartDictionary`, then the **Outputs**: `ElasticIp`, `InstanceId`, `SiteUrl`, `SessionCommand` and others. Keep that window open: step 6 needs the `ElasticIp`.

While you continue, the server boots and installs Docker by itself, in about 3 minutes.

### Step 5. Confirm the alert email

**What it does.** You click *Confirm subscription* in the "AWS Notification - Subscription Confirmation" email sent to admin@onecultureworld.com.

**Why it's needed.** AWS won't send alarm emails to an address that hasn't opted in. Budget alerts don't need this.

### Step 6. Point the name at the server (Cloudflare)

**What it does.** It makes `dictionary.onecultureworld.com` lead to the server. Add an A record:
- **Name:** `dictionary`
- **IPv4 address:** the `ElasticIp` from step 4
- **Proxy status:** **DNS only** (grey cloud)

**Why "DNS only":**
- Caddy, on the server, gets its HTTPS certificate from Let's Encrypt by proving it answers for that name. With Cloudflare's proxy in between, Let's Encrypt can't reach it.
- Later, the BBB-only routes check the caller's real IP, which the proxy would hide.

**How to check:** `nslookup dictionary.onecultureworld.com` should show the Elastic IP. It can take a minute.

### Step 7. Tell GitHub which AWS role to use

**What it does.** It saves the deploy role's ARN as a repository variable.

**Why it's needed.** The Deploy workflow asks AWS for this role through OIDC. An ARN is an address, not a secret, so it's a variable rather than a secret. Only this repository's `production` environment can assume the role.

The role trusts exactly the identity GitHub presents for this repository: `github_oidc_subject` in `infra/cdk/cdk.json`. The repository uses GitHub's immutable subject, which adds the owner's and the repository's numeric IDs (`repo:idiomasocw@128176786/smart-dictionary@1424556005:…`), so a deleted and re-created repository with the same name can't deploy. If the repository is ever renamed or moved, read the new value with `gh api repos/idiomasocw/smart-dictionary/actions/oidc/customization/sub` (the `sub_claim_prefix`, plus `:environment:production`), put it in `cdk.json` and run `npx cdk deploy` again.

```bash
gh variable set AWS_DEPLOY_ROLE_ARN --repo idiomasocw/smart-dictionary --body "arn:aws:iam::924231986837:role/smart-dictionary-github-deploy"
git push
```

`git push` is there because GitHub only offers a manual workflow once it's on `main`. If you've already pushed, it just says everything is up to date.

GitHub creates the `production` environment on the first run. Optionally, add yourself as a required reviewer under *Settings → Environments → production*, so every deploy waits for your click.

### Step 8. The first deploy

**What happens:**
1. GitHub builds the image for the server's ARM processor. That takes a few minutes the first time; later builds are cached.
2. It pushes the image to ECR, copies `deploy/` to S3 and asks SSM to run `deploy.sh` on the server.
3. On the server, `deploy.sh` writes the env files from step 3 and pulls the image.
4. It runs the migrations, which finds nothing new: you applied 0005 in step 1.
5. It starts Caddy and the API, and waits for the API's health check.
6. Finally, the workflow checks `https://dictionary.onecultureworld.com/readyz` from outside.

```bash
gh workflow run deploy.yml --repo idiomasocw/smart-dictionary
```

```bash
gh run watch --repo idiomasocw/smart-dictionary "$(gh run list --repo idiomasocw/smart-dictionary --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

```bash
curl https://dictionary.onecultureworld.com/readyz
```

**What you should see:** the run ends successfully, and `curl` prints `{"status":"ok","database":"up"}`.

If the run fails, `gh run view --repo idiomasocw/smart-dictionary --log-failed` shows the failing step's log. Send it to me.

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

**Secrets and AI keys.** Each deploy copies every parameter under `/smart-dictionary/prod/app/` into the API's environment. The AI features read `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY` and `GROQ_API_KEY` from there; today that's the missing-word entries (design §6.5).
- **Each provider is optional.** One without a key is skipped, and with no key at all a missing word just isn't generated; lookups never depend on AI.
- **Anthropic alone is enough to start.** DeepSeek and Groq are fallbacks for when it's down or the month's $5 is spent.
- **The cap is `AI_MONTHLY_BUDGET_USD`,** 5 by default; store that parameter only to change it.

Store each key like this, then deploy:

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
read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
APP_URL=$(uv run smart-dictionary db app-role)
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region us-east-2 --type SecureString --overwrite --name /smart-dictionary/prod/app/DATABASE_URL --value "$APP_URL"
unset DATABASE_URL APP_URL
```

**The proxy secret, when Phase 4 configures your BBB server:**

```bash
MSYS_NO_PATHCONV=1 aws ssm get-parameter --region us-east-2 --with-decryption --name /smart-dictionary/prod/app/BBB_PROXY_SECRET --query Parameter.Value --output text
```

**Changing the AWS resources.** Edit `infra/cdk/stack.py`, then run `npx cdk diff` and `npx cdk deploy --require-approval never` from `infra/cdk`.

The host's image is pinned in `cdk.context.json`. Refreshing it (`npx cdk context --reset <key>`) replaces the host, so deploy again afterwards.

## Not yet

These arrive with the features that need them (design §11):
- the worker;
- the nightly `pg_dump` to S3;
- Uptime Kuma;
- CloudFront.
