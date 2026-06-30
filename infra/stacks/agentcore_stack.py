"""AgentCore Stack - ECR + CodeBuild + CfnRuntime (v2)

Uses source asset hash as image tag so CDK/CloudFormation detects changes
and forces the Runtime to pull the new image on every deploy.
"""

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_codebuild as codebuild,
    aws_iam as iam,
    aws_ssm as ssm,
    aws_s3_assets,
)
from constructs import Construct


class AgentCoreStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str,
        vpc: ec2.Vpc,
        neptune_endpoint_ssm: str,
        neptune_sg: ec2.SecurityGroup,
        cognito_user_pool_id: str,
        cognito_client_id: str,
        knowledge_graphs_bucket_name: str = "",
        **kwargs
    ):
        super().__init__(scope, construct_id, **kwargs)

        # Resolve Neptune endpoint from SSM
        neptune_endpoint = ssm.StringParameter.value_for_string_parameter(
            self, neptune_endpoint_ssm
        )

        # ECR Repository
        self.ecr_repo = ecr.Repository(
            self, "ECRRepository",
            repository_name="ua-v2-agent",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        # CodeBuild Role (least privilege)
        build_role = iam.Role(
            self, "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
        )
        self.ecr_repo.grant_pull_push(build_role)

        # Upload agentcore source to S3 for CodeBuild
        # The asset hash changes whenever source files change
        source_asset = aws_s3_assets.Asset(
            self, "AgentSourceAsset",
            path="../agentcore",
        )

        # Hash-based tag for traceability (CodeBuild pushes both :latest and :build-xxx)
        image_tag = f"build-{source_asset.asset_hash[:12]}"

        # CodeBuild project
        build_project = codebuild.Project(
            self, "AgentImageBuildProject",
            project_name="ua-v2-build",
            role=build_role,
            source=codebuild.Source.s3(
                bucket=source_asset.bucket,
                path=source_asset.s3_object_key,
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
                privileged=True,
            ),
            environment_variables={
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                    value=self.ecr_repo.repository_uri
                ),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(
                    value=self.account
                ),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(
                    value=image_tag
                ),
            },
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "pre_build": {
                        "commands": [
                            "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_REPO_URI",
                        ]
                    },
                    "build": {
                        "commands": [
                            "docker build -t $ECR_REPO_URI:latest -t $ECR_REPO_URI:$IMAGE_TAG .",
                            "docker push $ECR_REPO_URI:latest",
                            "docker push $ECR_REPO_URI:$IMAGE_TAG",
                        ]
                    },
                },
            }),
        )
        source_asset.grant_read(build_role)

        # AgentCore Runtime Role (least privilege - no FullAccess)
        runtime_role = iam.Role(
            self, "AgentCoreRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                     "bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"],
            resources=[
                "arn:aws:bedrock:*:*:inference-profile/*",
                "arn:aws:bedrock:*:*:application-inference-profile/*",
                "arn:aws:bedrock:*:*:foundation-model/*",
            ],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
            resources=["*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=["*"],
        ))
        if knowledge_graphs_bucket_name:
            runtime_role.add_to_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::{knowledge_graphs_bucket_name}",
                    f"arn:aws:s3:::{knowledge_graphs_bucket_name}/*",
                ],
            ))

        # Security Group for AgentCore Runtime
        runtime_sg = ec2.SecurityGroup(
            self, "AgentCoreSG",
            vpc=vpc,
            description="AgentCore Runtime SG",
            allow_all_outbound=True,
        )

        # AgentCore Runtime (L1 - schema validated in prior deployment)
        private_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        runtime = cdk.CfnResource(
            self, "AgentRuntime",
            type="AWS::BedrockAgentCore::Runtime",
            properties={
                "AgentRuntimeName": "ua_v2_agent",
                "Description": "poc-3step-v2",
                "AgentRuntimeArtifact": {
                    "ContainerConfiguration": {
                        "ContainerUri": f"{self.ecr_repo.repository_uri}:latest"
                    }
                },
                "RoleArn": runtime_role.role_arn,
                "NetworkConfiguration": {
                    "NetworkMode": "VPC",
                    "NetworkModeConfig": {
                        "SecurityGroups": [runtime_sg.security_group_id],
                        "Subnets": [private_subnets.subnet_ids[0]],
                    }
                },
                "ProtocolConfiguration": "HTTP",
                "AuthorizerConfiguration": {
                    "CustomJWTAuthorizer": {
                        "DiscoveryUrl": f"https://cognito-idp.us-east-1.amazonaws.com/{cognito_user_pool_id}/.well-known/openid-configuration",
                        "AllowedClients": [cognito_client_id],
                    }
                },
                "EnvironmentVariables": {
                    "AWS_DEFAULT_REGION": "us-east-1",
                    "NEPTUNE_ENDPOINT": neptune_endpoint,
                    "NEPTUNE_PORT": "8182",
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "S3_KNOWLEDGE_BUCKET": knowledge_graphs_bucket_name,
                },
                "FilesystemConfigurations": [{
                    "SessionStorage": {
                        "MountPath": "/mnt/workspace"
                    }
                }],
            },
        )

        cdk.CfnOutput(self, "EcrRepoUri", value=self.ecr_repo.repository_uri)
        cdk.CfnOutput(self, "RuntimeId",
                       value=runtime.get_att("AgentRuntimeId").to_string())
        cdk.CfnOutput(self, "RuntimeArn",
                       value=runtime.get_att("AgentRuntimeArn").to_string())

        # Expose ARN for other stacks
        self.runtime_arn = runtime.get_att("AgentRuntimeArn").to_string()
