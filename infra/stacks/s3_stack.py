"""S3 Stack - Knowledge Graph storage bucket with CloudFront OAI access"""

import aws_cdk as cdk
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_ssm as ssm,
)
from constructs import Construct


class S3Stack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for knowledge graph JSON files
        self.bucket = s3.Bucket(
            self, "KnowledgeGraphsBucket",
            bucket_name=f"ua-v2-knowledge-graphs-{self.account}",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

        # CloudFront OAI for secure bucket access
        self.oai = cloudfront.OriginAccessIdentity(
            self, "KnowledgeGraphsOAI",
            comment="OAI for ua-v2 knowledge graphs bucket",
        )
        self.bucket.grant_read(self.oai)

        # SSM Parameter for cross-stack bucket name lookup
        ssm.StringParameter(
            self, "BucketNameParam",
            parameter_name="/ua-v2/s3/knowledge-graphs-bucket",
            string_value=self.bucket.bucket_name,
        )

        # Outputs
        cdk.CfnOutput(self, "BucketArn", value=self.bucket.bucket_arn)
        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        cdk.CfnOutput(self, "OaiId", value=self.oai.origin_access_identity_id)
