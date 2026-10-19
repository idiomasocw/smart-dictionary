"""AWS CDK app for the dictionary host. Run it through the pinned CLI: `npx cdk deploy`."""

import os

import aws_cdk as cdk

from stack import DictionaryStack

app = cdk.App()
DictionaryStack(
    app,
    "SmartDictionary",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-2"),
    description="Smart Dictionary: API host, image registry, deploy access and alarms",
    tags={"project": "smart-dictionary"},
)
app.synth()
