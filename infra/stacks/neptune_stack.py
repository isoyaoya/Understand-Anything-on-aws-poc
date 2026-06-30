"""Neptune Serverless Stack - VPC private, IAM auth disabled (POC), SSM decoupled"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_neptune as neptune, aws_ssm as ssm
from constructs import Construct

SSM_NEPTUNE_ENDPOINT = "/ua-v2/neptune/endpoint"
SSM_NEPTUNE_PORT = "/ua-v2/neptune/port"


class NeptuneStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Security Group - allow 8182 from VPC only
        self.security_group = ec2.SecurityGroup(
            self, "NeptuneSG",
            vpc=vpc,
            description="Neptune Serverless SG",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(8182),
            "Allow Gremlin from VPC",
        )

        # Subnet group (private subnets only)
        private_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        subnet_group = neptune.CfnDBSubnetGroup(
            self, "NeptuneSubnetGroup",
            db_subnet_group_description="Neptune v2 subnet group",
            subnet_ids=private_subnets.subnet_ids,
        )

        # Neptune Serverless cluster - IAM auth disabled for POC
        # Security: VPC private subnet + SG already isolates access
        self.cluster = neptune.CfnDBCluster(
            self, "NeptuneCluster",
            engine_version="1.3.1.0",
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[self.security_group.security_group_id],
            serverless_scaling_configuration=neptune.CfnDBCluster.ServerlessScalingConfigurationProperty(
                min_capacity=1,
                max_capacity=4,
            ),
            iam_auth_enabled=False,
        )

        # Neptune Serverless still requires at least one DB instance
        neptune.CfnDBInstance(
            self, "NeptuneInstance",
            db_instance_class="db.serverless",
            db_cluster_identifier=self.cluster.ref,
        )

        # SSM Parameters (decoupled - no cross-stack export)
        ssm.StringParameter(
            self, "NeptuneEndpointParam",
            parameter_name=SSM_NEPTUNE_ENDPOINT,
            string_value=self.cluster.attr_endpoint,
        )
        ssm.StringParameter(
            self, "NeptunePortParam",
            parameter_name=SSM_NEPTUNE_PORT,
            string_value=self.cluster.attr_port,
        )

        cdk.CfnOutput(self, "NeptuneEndpoint", value=self.cluster.attr_endpoint)
        cdk.CfnOutput(self, "NeptunePort", value=self.cluster.attr_port)
