import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export interface VpcConstructProps {
  environment: string;
}

export class VpcConstruct extends Construct {
  public readonly vpc: ec2.Vpc;
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly fargateSecurityGroup: ec2.SecurityGroup;
  public readonly databaseSecurityGroup: ec2.SecurityGroup;
  public readonly efsSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: VpcConstructProps) {
    super(scope, id);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      subnetConfiguration: [
        { cidrMask: 24, name: 'public', subnetType: ec2.SubnetType.PUBLIC },
        { cidrMask: 24, name: 'private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      ],
      natGateways: 1,
      enableDnsHostnames: true,
      enableDnsSupport: true,
    });

    // ALB — CloudFront sits in front, ALB only needs HTTPS (443)
    // Port 80 removed to prevent DyePack from flagging the ALB ENI IPs
    this.albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc: this.vpc,
      description: 'ALB security group - HTTP/HTTPS from internet',
      allowAllOutbound: true,
    });
    this.albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'HTTPS');

    // Fargate — accepts traffic from ALB only
    this.fargateSecurityGroup = new ec2.SecurityGroup(this, 'FargateSg', {
      vpc: this.vpc,
      description: 'Fargate tasks - traffic from ALB',
      allowAllOutbound: true,
    });
    this.fargateSecurityGroup.addIngressRule(this.albSecurityGroup, ec2.Port.tcp(80), 'Backend from ALB');
    this.fargateSecurityGroup.addIngressRule(this.albSecurityGroup, ec2.Port.tcp(3000), 'Frontend from ALB');
    this.fargateSecurityGroup.addIngressRule(this.fargateSecurityGroup, ec2.Port.tcp(80), 'Backend from Fargate');
    this.fargateSecurityGroup.addIngressRule(this.fargateSecurityGroup, ec2.Port.tcp(8000), 'Backend direct from Fargate');

    // RDS — accepts Postgres from Fargate only
    this.databaseSecurityGroup = new ec2.SecurityGroup(this, 'DatabaseSg', {
      vpc: this.vpc,
      description: 'RDS PostGIS - Postgres from Fargate',
      allowAllOutbound: false,
    });
    this.databaseSecurityGroup.addIngressRule(this.fargateSecurityGroup, ec2.Port.tcp(5432), 'Postgres from Fargate');

    // EFS — NFS from Fargate only
    this.efsSecurityGroup = new ec2.SecurityGroup(this, 'EfsSg', {
      vpc: this.vpc,
      description: 'EFS - NFS from Fargate',
      allowAllOutbound: false,
    });
    this.efsSecurityGroup.addIngressRule(this.fargateSecurityGroup, ec2.Port.tcp(2049), 'NFS from Fargate');

    const resources = [this.vpc, this.albSecurityGroup, this.fargateSecurityGroup, this.databaseSecurityGroup, this.efsSecurityGroup];
    resources.forEach(r => cdk.Tags.of(r).add('Environment', props.environment));
  }
}
