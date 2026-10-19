"""The dictionary's AWS resources in us-east-2 (design §11, integration §5).

One t4g.medium runs Caddy and the API in Docker Compose. Nobody logs in over SSH: port 22
stays closed, people connect with SSM Session Manager, and GitHub Actions deploys with SSM
Run Command through a role it assumes with OIDC, so no AWS keys are stored anywhere.
Secrets live in SSM Parameter Store, and only the host can read them.
"""

from aws_cdk import (
    CfnOutput,
    CfnTag,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_ssm as ssm
from constructs import Construct

# deploy/deploy.sh writes the containers' env files from these paths.
PARAMETERS = "/smart-dictionary/prod"

# Compose isn't packaged for Amazon Linux 2023, so the host installs the release binary.
COMPOSE_VERSION = "v2.40.3"
COMPOSE_SHA256 = "d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137"

# What a page's script may read from an audio response: the size and ranges a player needs
# to seek, and the ETag.
AUDIO_EXPOSED_HEADERS = ["Content-Length", "Content-Range", "Accept-Ranges", "ETag"]


class DictionaryStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        domain = self.node.get_context("domain")
        alert_email = self.node.get_context("alert_email")
        oidc_subject = self.node.get_context("github_oidc_subject")
        monthly_budget = float(self.node.get_context("monthly_budget_usd"))

        # Public subnets only, so no NAT gateway. IPv4 only: the containers reach Supabase
        # through its session pooler, and Docker's default network has no IPv6. The host gets
        # a public IP at launch (the Elastic IP then replaces it) for its first-boot setup.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.40.0.0/16"),
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                    map_public_ip_on_launch=True,
                )
            ],
        )

        security_group = ec2.SecurityGroup(
            self,
            "HostSecurityGroup",
            vpc=vpc,
            description="HTTPS, and HTTP for certificates and redirects; no SSH",
            allow_all_outbound=True,
        )
        anyone = ec2.Peer.any_ipv4()
        security_group.add_ingress_rule(anyone, ec2.Port.tcp(80), "HTTP")
        security_group.add_ingress_rule(anyone, ec2.Port.tcp(443), "HTTPS")
        security_group.add_ingress_rule(anyone, ec2.Port.udp(443), "HTTP/3")

        images = ecr.Repository(
            self,
            "Images",
            repository_name="smart-dictionary",
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=20, description="the last 20, for rollbacks")
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Deploy bundles now; exports and backups later (design §11.5). Audio has its own
        # bucket, below.
        storage = s3.Bucket(
            self,
            "Storage",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="deploy-bundles", prefix="deploy/", expiration=Duration.days(30)
                )
            ],
        )

        host_role = iam.Role(
            self,
            "HostRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
        )
        images.grant_pull(host_role)
        storage.grant_read(host_role, "deploy/*")
        parameters = PARAMETERS.lstrip("/")
        host_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"],
                resources=[
                    self.format_arn(service="ssm", resource="parameter", resource_name=name)
                    for name in (parameters, f"{parameters}/*")
                ],
            )
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "dnf install -y docker",
            "systemctl enable --now docker",
            "mkdir -p /usr/local/lib/docker/cli-plugins",
            "curl -fsSL --retry 8 --retry-all-errors -o /tmp/docker-compose"
            f" https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}"
            "/docker-compose-linux-aarch64",
            f"echo '{COMPOSE_SHA256}  /tmp/docker-compose' | sha256sum -c -",
            "install -m 0755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose",
            # Insurance while old and new containers overlap during a deploy.
            "if [ ! -f /swapfile ]; then fallocate -l 2G /swapfile && chmod 600 /swapfile"
            " && mkswap /swapfile && echo '/swapfile none swap defaults 0 0' >> /etc/fstab; fi",
            "swapon -a",
            "install -d -m 700 /opt/smart-dictionary",
        )

        host = ec2.Instance(
            self,
            "Host",
            instance_name="smart-dictionary",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t4g.medium"),
            # Cached in cdk.context.json, so a newer AMI never replaces the host on its own.
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64, cached_in_context=True
            ),
            security_group=security_group,
            role=host_role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True
                    ),
                )
            ],
        )
        # IMDSv2 with a hop limit of 1: the host reaches the metadata service, containers
        # don't, so the API can't borrow the host role to read the owner's DATABASE_URL.
        metadata = ec2.LaunchTemplate(
            self,
            "HostMetadataOptions",
            http_endpoint=True,
            http_tokens=ec2.LaunchTemplateHttpTokens.REQUIRED,
            http_put_response_hop_limit=1,
        )
        host.instance.launch_template = ec2.CfnInstance.LaunchTemplateSpecificationProperty(
            launch_template_id=metadata.launch_template_id,
            version=metadata.latest_version_number,
        )

        address = ec2.CfnEIP(
            self,
            "Address",
            domain="vpc",
            instance_id=host.instance_id,
            tags=[CfnTag(key="Name", value="smart-dictionary")],
        )

        # Pronunciation and example audio (design §10.4). The bucket is private: only the
        # CloudFront distribution reads it, through Origin Access Control. Each file is named
        # after what it says (docs/03 §10.4), so it never changes and the CDN keeps it for a
        # year. Versioning keeps an overwritten or deleted file recoverable for 90 days.
        audio = s3.Bucket(
            self,
            "Audio",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            # S3 answers CORS preflights itself, since CloudFront forwards them; CloudFront
            # adds the CORS headers to every other response (AudioHeaders).
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.HEAD],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    exposed_headers=AUDIO_EXPOSED_HEADERS,
                    max_age=86_400,
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="old-versions",
                    noncurrent_version_expiration=Duration.days(90),
                    expired_object_delete_marker=True,
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
        )
        # Every response carries the CORS headers, whatever the request. Otherwise a copy
        # cached from a request without an Origin header (a plain <audio> tag) would reach a
        # later fetch() without them, and the browser would block it.
        audio_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "AudioHeaders",
            comment="Smart Dictionary audio: CORS for any origin, nosniff, HSTS",
            cors_behavior=cloudfront.ResponseHeadersCorsBehavior(
                access_control_allow_credentials=False,
                access_control_allow_headers=["*"],
                access_control_allow_methods=["GET", "HEAD", "OPTIONS"],
                access_control_allow_origins=["*"],
                access_control_expose_headers=AUDIO_EXPOSED_HEADERS,
                access_control_max_age=Duration.days(1),
                origin_override=True,
            ),
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=False,
                    override=True,
                ),
            ),
        )
        audio_cdn = cloudfront.Distribution(
            self,
            "AudioCdn",
            comment="Smart Dictionary audio",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(audio),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                # Honors the files' own Cache-Control (a year, immutable) and leaves query
                # strings and cookies out of the cache key.
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=cloudfront.OriginRequestPolicy.CORS_S3_ORIGIN,
                response_headers_policy=audio_headers,
                compress=False,  # AAC audio is compressed already
            ),
            # Most students are in Latin America, and only the full price class includes
            # the South American edge locations.
            price_class=cloudfront.PriceClass.PRICE_CLASS_ALL,
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            enable_ipv6=True,
            # A missing file is a 403 (CloudFront may read files, not list the bucket).
            # Cache that answer only briefly, so a file uploaded a moment later shows up.
            error_responses=[cloudfront.ErrorResponse(http_status=403, ttl=Duration.seconds(30))],
        )
        # The host uploads the audio container's files (deploy/audio-upload.sh): it may add
        # files under the layout's folder, never read, list or delete them.
        audio.grant_put(host_role, "v1/*")
        ssm.StringParameter(
            self,
            "AudioBaseUrl",
            parameter_name=f"{PARAMETERS}/app/AUDIO_BASE_URL",
            string_value=f"https://{audio_cdn.distribution_domain_name}",
            description="The base of every audio link the API returns: the CloudFront address",
        )
        ssm.StringParameter(
            self,
            "AudioBucketName",
            parameter_name=f"{PARAMETERS}/app/AUDIO_BUCKET",
            string_value=audio.bucket_name,
            description="The bucket audio files are uploaded to",
        )

        ssm.StringParameter(
            self,
            "SiteAddress",
            parameter_name=f"{PARAMETERS}/caddy/SITE_ADDRESS",
            string_value=domain,
            description="The API's public name; Caddy gets its certificate for it",
        )

        alerts = sns.Topic(self, "Alerts", display_name="Smart Dictionary")
        alerts.add_subscription(subscriptions.EmailSubscription(alert_email))
        notify = cw_actions.SnsAction(alerts)

        def ec2_metric(name: str, statistic: str, period: Duration) -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/EC2",
                metric_name=name,
                dimensions_map={"InstanceId": host.instance_id},
                statistic=statistic,
                period=period,
            )

        at_least = cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
        hardware = ec2_metric(
            "StatusCheckFailed_System", cloudwatch.Stats.MAXIMUM, Duration.minutes(1)
        ).create_alarm(
            self,
            "HostHardware",
            alarm_description="AWS hardware problem under the host: EC2 recovers it",
            threshold=1,
            evaluation_periods=2,
            comparison_operator=at_least,
        )
        hardware.add_alarm_action(cw_actions.Ec2Action(cw_actions.Ec2InstanceAction.RECOVER))
        hardware.add_alarm_action(notify)
        unresponsive = ec2_metric(
            "StatusCheckFailed_Instance", cloudwatch.Stats.MAXIMUM, Duration.minutes(1)
        ).create_alarm(
            self,
            "HostUnresponsive",
            alarm_description="The host's operating system stopped answering: EC2 reboots it",
            threshold=1,
            evaluation_periods=3,
            comparison_operator=at_least,
        )
        unresponsive.add_alarm_action(cw_actions.Ec2Action(cw_actions.Ec2InstanceAction.REBOOT))
        unresponsive.add_alarm_action(notify)
        ec2_metric("CPUUtilization", cloudwatch.Stats.AVERAGE, Duration.minutes(5)).create_alarm(
            self,
            "HostBusy",
            alarm_description="CPU above 80% for 15 minutes",
            threshold=80,
            evaluation_periods=3,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        ).add_alarm_action(notify)

        # Everything this stack creates is in us-east-2, and nothing else in the account is.
        def budget_alert(
            kind: str, percent: int
        ) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=kind,
                    comparison_operator="GREATER_THAN",
                    threshold=percent,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[
                    budgets.CfnBudget.SubscriberProperty(
                        subscription_type="EMAIL", address=alert_email
                    )
                ],
            )

        budgets.CfnBudget(
            self,
            "Budget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="smart-dictionary-us-east-2",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=monthly_budget, unit="USD"),
                cost_filters={"Region": ["us-east-2"]},
            ),
            notifications_with_subscribers=[
                budget_alert("ACTUAL", 80),
                budget_alert("FORECASTED", 100),
            ],
        )

        # GitHub Actions, in the repository's `production` environment only.
        github = iam.OidcProviderNative(
            self,
            "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )
        deployer = iam.Role(
            self,
            "GitHubDeployRole",
            role_name="smart-dictionary-github-deploy",
            description="GitHub Actions: push images and deploy over SSM",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                github.oidc_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        # The repository uses GitHub's immutable subject: owner and repo
                        # with their numeric IDs, so a re-created repo can't take over.
                        "token.actions.githubusercontent.com:sub": oidc_subject,
                    }
                },
            ),
        )
        images.grant_pull_push(deployer)
        deployer.add_to_policy(
            iam.PolicyStatement(actions=["ecr:DescribeImages"], resources=[images.repository_arn])
        )
        storage.grant_put(deployer, "deploy/*")
        deployer.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:SendCommand"],
                resources=[
                    self.format_arn(
                        service="ec2", resource="instance", resource_name=host.instance_id
                    ),
                    self.format_arn(
                        service="ssm",
                        resource="document",
                        resource_name="AWS-RunShellScript",
                        account="",
                    ),
                ],
            )
        )
        deployer.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"],
                resources=["*"],
            )
        )
        deployer.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"], resources=[self.stack_id]
            )
        )

        CfnOutput(self, "ElasticIp", value=address.ref, description="Cloudflare A record, DNS only")
        CfnOutput(self, "SiteUrl", value=f"https://{domain}")
        CfnOutput(self, "InstanceId", value=host.instance_id)
        CfnOutput(
            self,
            "SessionCommand",
            value=f"aws ssm start-session --region {self.region} --target {host.instance_id}",
        )
        CfnOutput(self, "ImageRepository", value=images.repository_uri)
        CfnOutput(self, "DeployBucket", value=storage.bucket_name)
        CfnOutput(self, "AudioBucket", value=audio.bucket_name)
        CfnOutput(self, "AudioCdnUrl", value=f"https://{audio_cdn.distribution_domain_name}")
        CfnOutput(self, "DeployRoleArn", value=deployer.role_arn)
