"""Frontend Stack - S3 + CloudFront + config.json + Knowledge Graphs origin"""

import aws_cdk as cdk
from aws_cdk import (
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


class FrontendStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str,
        runtime_arn: str,
        cognito_user_pool_id: str,
        cognito_client_id: str,
        knowledge_graphs_bucket: s3.Bucket,
        knowledge_graphs_oai: cloudfront.OriginAccessIdentity,
        **kwargs
    ):
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for static assets
        bucket = s3.Bucket(
            self, "DashboardBucket",
            bucket_name=f"ua-v2-dashboard-{self.account}",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # CloudFront OAI for dashboard bucket
        oai = cloudfront.OriginAccessIdentity(self, "OAI")
        bucket.grant_read(oai)

        # CloudFront Distribution with two origins:
        # 1. Default: dashboard static files
        # 2. /graphs/*: knowledge graphs S3 bucket
        distribution = cloudfront.Distribution(
            self, "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(bucket, origin_access_identity=oai),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/graphs/*": cloudfront.BehaviorOptions(
                    origin=origins.S3Origin(
                        knowledge_graphs_bucket,
                        origin_access_identity=knowledge_graphs_oai,
                        origin_path="",
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                ),
            },
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
            ],
        )

        # Deploy pre-built frontend static files
        s3deploy.BucketDeployment(
            self, "DeployDashboard",
            sources=[s3deploy.Source.asset("../dashboard/dist")],
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            prune=False,
        )

        # Deploy config.json separately (CFN tokens resolved at deploy time)
        config_content = cdk.Fn.join("", [
            '{"region":"us-east-1","runtimeArn":"',
            runtime_arn,
            '","cognitoUserPoolId":"',
            cognito_user_pool_id,
            '","cognitoClientId":"',
            cognito_client_id,
            '","graphsBaseUrl":"/graphs"}'
        ])

        s3deploy.BucketDeployment(
            self, "DeployConfig",
            sources=[s3deploy.Source.data("config.json", config_content)],
            destination_bucket=bucket,
            prune=False,
        )

        # Outputs
        cdk.CfnOutput(self, "DashboardUrl",
                       value=f"https://{distribution.distribution_domain_name}")
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
