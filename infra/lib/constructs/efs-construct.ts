import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as efs from 'aws-cdk-lib/aws-efs';
import { Construct } from 'constructs';

export interface EfsConstructProps {
  environment: string;
  vpc: ec2.Vpc;
  securityGroup: ec2.SecurityGroup;
}

export class EfsConstruct extends Construct {
  public readonly fileSystem: efs.FileSystem;
  public readonly mediaAccessPoint: efs.AccessPoint;

  constructor(scope: Construct, id: string, props: EfsConstructProps) {
    super(scope, id);

    this.fileSystem = new efs.FileSystem(this, 'MediaEfs', {
      vpc: props.vpc,
      securityGroup: props.securityGroup,
      lifecyclePolicy: efs.LifecyclePolicy.AFTER_30_DAYS,
      performanceMode: efs.PerformanceMode.GENERAL_PURPOSE,
      throughputMode: efs.ThroughputMode.BURSTING,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Access point for /code/media inside the backend container
    this.mediaAccessPoint = this.fileSystem.addAccessPoint('MediaAP', {
      path: '/media',
      createAcl: { ownerGid: '0', ownerUid: '0', permissions: '755' },
      posixUser: { gid: '0', uid: '0' },
    });

    cdk.Tags.of(this.fileSystem).add('Environment', props.environment);
    cdk.Tags.of(this.fileSystem).add('Name', `${props.environment}-adventurelog-media`);
  }
}
