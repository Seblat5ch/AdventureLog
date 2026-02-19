import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

export interface EcrConstructProps {
  environment: string;
}

export class EcrConstruct extends Construct {
  public readonly backendRepo: ecr.Repository;
  public readonly frontendRepo: ecr.Repository;

  constructor(scope: Construct, id: string, props: EcrConstructProps) {
    super(scope, id);

    this.backendRepo = new ecr.Repository(this, 'BackendRepo', {
      repositoryName: `${props.environment}-adventurelog-backend`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [{ maxImageCount: 10, description: 'Keep last 10 images' }],
      imageScanOnPush: true,
    });

    this.frontendRepo = new ecr.Repository(this, 'FrontendRepo', {
      repositoryName: `${props.environment}-adventurelog-frontend`,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [{ maxImageCount: 10, description: 'Keep last 10 images' }],
      imageScanOnPush: true,
    });

    cdk.Tags.of(this.backendRepo).add('Environment', props.environment);
    cdk.Tags.of(this.frontendRepo).add('Environment', props.environment);
  }
}
