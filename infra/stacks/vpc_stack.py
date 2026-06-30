"""VPC Stack - 2 AZ, NAT Gateway, explicit AZ selection"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class VpcStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Explicit AZs: us-east-1b=use1-az1, us-east-1c=use1-az2
        # Both support AgentCore + Neptune. Excludes us-east-1a (use1-az6).
        self.vpc = ec2.Vpc(
            self, "Vpc",
            availability_zones=["us-east-1b", "us-east-1c"],
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
            restrict_default_security_group=False,
        )

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
