import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { VpcConstruct } from './constructs/vpc-construct';
import { DatabaseConstruct } from './constructs/database-construct';
import { EfsConstruct } from './constructs/efs-construct';
import { EcrConstruct } from './constructs/ecr-construct';
import { FargateConstruct } from './constructs/fargate-construct';
import { AlbConstruct } from './constructs/alb-construct';
import { PipelineConstruct } from './constructs/pipeline-construct';

export class AdventureLogStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const environment = this.node.tryGetContext('environment') as string || 'prod';
    const hostedZoneId = this.node.tryGetContext('hostedZoneId') as string || '';
    const domainName = this.node.tryGetContext('domainName') as string || 'tesem.dog';
    const subdomain = this.node.tryGetContext('subdomain') as string || 'travel';
    const siteUrl = hostedZoneId ? `https://${subdomain}.${domainName}` : '';

    // VPC and networking
    const vpc = new VpcConstruct(this, 'Vpc', { environment });

    // RDS PostgreSQL with PostGIS
    const database = new DatabaseConstruct(this, 'Database', {
      environment,
      vpc: vpc.vpc,
      securityGroup: vpc.databaseSecurityGroup,
    });

    // EFS for media uploads
    const efsStorage = new EfsConstruct(this, 'Efs', {
      environment,
      vpc: vpc.vpc,
      securityGroup: vpc.efsSecurityGroup,
    });

    // ECR repositories for both images
    const ecr = new EcrConstruct(this, 'Ecr', { environment });

    // Fargate services (frontend + backend)
    const fargate = new FargateConstruct(this, 'Fargate', {
      environment,
      vpc: vpc.vpc,
      fargateSecurityGroup: vpc.fargateSecurityGroup,
      backendRepo: ecr.backendRepo,
      frontendRepo: ecr.frontendRepo,
      fileSystem: efsStorage.fileSystem,
      mediaAccessPoint: efsStorage.mediaAccessPoint,
      dbSecret: database.secret,
      dbEndpoint: database.endpoint,
      dbPort: database.port,
      siteUrl: siteUrl || `http://adventurelog-alb.${this.region}.elb.amazonaws.com`,
    });

    // ALB with path-based routing
    const alb = new AlbConstruct(this, 'Alb', {
      environment,
      vpc: vpc.vpc,
      securityGroup: vpc.albSecurityGroup,
      backendService: fargate.backendService,
      frontendService: fargate.frontendService,
      hostedZoneId,
      domainName,
      subdomain,
    });

    // CodeBuild + CodePipeline CI/CD
    const pipeline = new PipelineConstruct(this, 'Pipeline', {
      environment,
      backendRepo: ecr.backendRepo,
      frontendRepo: ecr.frontendRepo,
      backendService: fargate.backendService,
      frontendService: fargate.frontendService,
      cluster: fargate.cluster,
    });

    // --- Outputs ---
    new cdk.CfnOutput(this, 'AlbUrl', {
      value: alb.loadBalancerDnsName,
      description: 'ALB DNS name to access AdventureLog',
    });
    new cdk.CfnOutput(this, 'CodeCommitRepoCloneUrlHttp', {
      value: pipeline.codeCommitRepo.repositoryCloneUrlHttp,
      description: 'CodeCommit HTTPS clone URL — push code here to trigger the pipeline',
    });
    new cdk.CfnOutput(this, 'CodeCommitRepoCloneUrlGrc', {
      value: pipeline.codeCommitRepo.repositoryCloneUrlGrc,
      description: 'CodeCommit GRC clone URL (recommended with git-remote-codecommit)',
    });
    new cdk.CfnOutput(this, 'BackendEcrUri', {
      value: ecr.backendRepo.repositoryUri,
      description: 'ECR URI for backend image',
    });
    new cdk.CfnOutput(this, 'FrontendEcrUri', {
      value: ecr.frontendRepo.repositoryUri,
      description: 'ECR URI for frontend image',
    });
    new cdk.CfnOutput(this, 'DatabaseEndpoint', {
      value: database.endpoint,
      description: 'RDS PostGIS endpoint',
    });
    new cdk.CfnOutput(this, 'DatabaseSecretArn', {
      value: database.secret.secretArn,
      description: 'Secrets Manager ARN for DB credentials',
    });
    new cdk.CfnOutput(this, 'EfsFileSystemId', {
      value: efsStorage.fileSystem.fileSystemId,
      description: 'EFS file system ID for media storage',
    });
  }
}
