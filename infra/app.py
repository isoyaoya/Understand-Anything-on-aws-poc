#!/usr/bin/env python3
"""
CDK App - Understand Anything Cloud POC (v2)
6 stacks, all in same VPC, Neptune decoupled via SSM.
"""

import os
import aws_cdk as cdk
from stacks.vpc_stack import VpcStack
from stacks.neptune_stack import NeptuneStack, SSM_NEPTUNE_ENDPOINT
from stacks.cognito_stack import CognitoStack
from stacks.agentcore_stack import AgentCoreStack
from stacks.s3_stack import S3Stack
from stacks.frontend_stack import FrontendStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT", os.environ.get("AWS_ACCOUNT_ID")),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)
prefix = "UA-v2"

# Stack 1: VPC (explicit AZs: use1-az1 + use1-az2, both support AgentCore + Neptune)
vpc_stack = VpcStack(app, f"{prefix}-Vpc", env=env)

# Stack 2: Neptune Serverless (IAM auth off for POC, private subnet isolation)
neptune_stack = NeptuneStack(app, f"{prefix}-Neptune", vpc=vpc_stack.vpc, env=env)

# Stack 3: Cognito (self-signup disabled)
cognito_stack = CognitoStack(app, f"{prefix}-Cognito", env=env)

# Stack 5: S3 Knowledge Graphs bucket
s3_stack = S3Stack(app, f"{prefix}-S3", env=env)

# Stack 4: AgentCore Runtime
agentcore_stack = AgentCoreStack(
    app, f"{prefix}-AgentCore",
    vpc=vpc_stack.vpc,
    neptune_endpoint_ssm=SSM_NEPTUNE_ENDPOINT,
    neptune_sg=neptune_stack.security_group,
    cognito_user_pool_id=cognito_stack.user_pool.user_pool_id,
    cognito_client_id=cognito_stack.user_pool_client.user_pool_client_id,
    knowledge_graphs_bucket_name=s3_stack.bucket.bucket_name,
    env=env,
)
agentcore_stack.add_dependency(neptune_stack)
agentcore_stack.add_dependency(cognito_stack)
agentcore_stack.add_dependency(s3_stack)

# Stack 6: Frontend (S3 + CloudFront + runtime config.json)
frontend_stack = FrontendStack(
    app, f"{prefix}-Frontend",
    runtime_arn=agentcore_stack.runtime_arn,
    cognito_user_pool_id=cognito_stack.user_pool.user_pool_id,
    cognito_client_id=cognito_stack.user_pool_client.user_pool_client_id,
    knowledge_graphs_bucket=s3_stack.bucket,
    knowledge_graphs_oai=s3_stack.oai,
    env=env,
)
frontend_stack.add_dependency(s3_stack)

app.synth()
