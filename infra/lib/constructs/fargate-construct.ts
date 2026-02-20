import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as efs from 'aws-cdk-lib/aws-efs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as servicediscovery from 'aws-cdk-lib/aws-servicediscovery';
import { Construct } from 'constructs';

export interface FargateConstructProps {
  environment: string;
  vpc: ec2.Vpc;
  fargateSecurityGroup: ec2.SecurityGroup;
  backendRepo: ecr.Repository;
  frontendRepo: ecr.Repository;
  fileSystem: efs.FileSystem;
  mediaAccessPoint: efs.AccessPoint;
  dbSecret: secretsmanager.ISecret;
  dbEndpoint: string;
  dbPort: string;
  siteUrl: string; // e.g. https://travel.tesem.dog
}

export class FargateConstruct extends Construct {
  public readonly cluster: ecs.Cluster;
  public readonly backendService: ecs.FargateService;
  public readonly frontendService: ecs.FargateService;

  constructor(scope: Construct, id: string, props: FargateConstructProps) {
    super(scope, id);

    // Cloud Map namespace for service discovery (frontend → backend)
    const namespace = new servicediscovery.PrivateDnsNamespace(this, 'Namespace', {
      name: `${props.environment}-adventurelog.local`,
      vpc: props.vpc,
      description: 'Service discovery for AdventureLog services',
    });

    this.cluster = new ecs.Cluster(this, 'Cluster', {
      vpc: props.vpc,
      clusterName: `${props.environment}-adventurelog`,
      containerInsights: true,
    });

    // Shared execution role
    const executionRole = new iam.Role(this, 'ExecRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });
    props.dbSecret.grantRead(executionRole);

    // Separate Django SECRET_KEY in Secrets Manager (not reusing DB password)
    const djangoSecret = new secretsmanager.Secret(this, 'DjangoSecretKey', {
      secretName: `${props.environment}/adventurelog/django-secret-key`,
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 50,
      },
      description: 'Django SECRET_KEY for AdventureLog',
    });
    djangoSecret.grantRead(executionRole);

    // ---------------------------------------------------------------
    // BACKEND SERVICE
    // ---------------------------------------------------------------
    const backendTaskRole = new iam.Role(this, 'BackendTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });
    backendTaskRole.addToPolicy(new iam.PolicyStatement({
      actions: ['elasticfilesystem:ClientMount', 'elasticfilesystem:ClientWrite', 'elasticfilesystem:ClientRootAccess'],
      resources: [props.fileSystem.fileSystemArn],
    }));
    // ECS Exec support
    backendTaskRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'ssmmessages:CreateControlChannel',
        'ssmmessages:CreateDataChannel',
        'ssmmessages:OpenControlChannel',
        'ssmmessages:OpenDataChannel',
      ],
      resources: ['*'],
    }));
    // Bedrock access for Strands AI agent (PDF import)
    backendTaskRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        'arn:aws:bedrock:*::foundation-model/*',
        'arn:aws:bedrock:*:*:inference-profile/*',
      ],
    }));

    const backendLogGroup = new logs.LogGroup(this, 'BackendLogs', {
      logGroupName: `/ecs/${props.environment}-adventurelog-backend`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const backendTaskDef = new ecs.FargateTaskDefinition(this, 'BackendTaskDef', {
      family: `${props.environment}-adventurelog-backend`,
      cpu: 1024,
      memoryLimitMiB: 2048,
      executionRole,
      taskRole: backendTaskRole,
      volumes: [{
        name: 'media',
        efsVolumeConfiguration: {
          fileSystemId: props.fileSystem.fileSystemId,
          transitEncryption: 'ENABLED',
          authorizationConfig: {
            accessPointId: props.mediaAccessPoint.accessPointId,
            iam: 'ENABLED',
          },
        },
      }],
    });

    backendTaskDef.addContainer('backend', {
      // Use public GHCR image for initial deploy; CodePipeline switches to ECR after first build
      image: ecs.ContainerImage.fromRegistry('ghcr.io/seanmorley15/adventurelog-backend:latest'),
      essential: true,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'backend', logGroup: backendLogGroup }),
      portMappings: [{ containerPort: 80, protocol: ecs.Protocol.TCP }],
      environment: {
        PGHOST: props.dbEndpoint,
        PGPORT: props.dbPort,
        DEBUG: 'False',
        DJANGO_ADMIN_USERNAME: 'admin',
        DJANGO_ADMIN_EMAIL: 'admin@example.com',
        PUBLIC_URL: props.siteUrl,
        FRONTEND_URL: props.siteUrl,
        CSRF_TRUSTED_ORIGINS: props.siteUrl,
        DISABLE_REGISTRATION: 'False',
      },
      secrets: {
        POSTGRES_DB: ecs.Secret.fromSecretsManager(props.dbSecret, 'dbname'),
        POSTGRES_USER: ecs.Secret.fromSecretsManager(props.dbSecret, 'username'),
        POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(props.dbSecret, 'password'),
        SECRET_KEY: ecs.Secret.fromSecretsManager(djangoSecret),
        DJANGO_ADMIN_PASSWORD: ecs.Secret.fromSecretsManager(props.dbSecret, 'password'),
      },
      healthCheck: {
        // The backend container has nginx + python — use wget which is available in Debian slim
        command: ['CMD-SHELL', 'python -c "import urllib.request; urllib.request.urlopen(\'http://localhost:80/\')" || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        retries: 5,
        startPeriod: cdk.Duration.seconds(180), // backend needs time for migrations + country data download on first boot
      },
    }).addMountPoints({
      containerPath: '/code/media',
      sourceVolume: 'media',
      readOnly: false,
    });

    this.backendService = new ecs.FargateService(this, 'BackendService', {
      cluster: this.cluster,
      taskDefinition: backendTaskDef,
      desiredCount: 1,
      securityGroups: [props.fargateSecurityGroup],
      assignPublicIp: false,
      serviceName: `${props.environment}-adventurelog-backend`,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      platformVersion: ecs.FargatePlatformVersion.VERSION1_4, // Required for EFS
      enableExecuteCommand: true,
      cloudMapOptions: {
        name: 'server',
        cloudMapNamespace: namespace,
        dnsRecordType: servicediscovery.DnsRecordType.A,
      },
    });

    // Allow backend → EFS
    props.fileSystem.connections.allowDefaultPortFrom(this.backendService);

    // ---------------------------------------------------------------
    // FRONTEND SERVICE
    // ---------------------------------------------------------------
    const frontendLogGroup = new logs.LogGroup(this, 'FrontendLogs', {
      logGroupName: `/ecs/${props.environment}-adventurelog-frontend`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const frontendTaskDef = new ecs.FargateTaskDefinition(this, 'FrontendTaskDef', {
      family: `${props.environment}-adventurelog-frontend`,
      cpu: 512,
      memoryLimitMiB: 1024,
      executionRole,
    });

    // The Cloud Map DNS name: server.<env>-adventurelog.local
    const backendInternalUrl = `http://server.${props.environment}-adventurelog.local:80`;

    frontendTaskDef.addContainer('frontend', {
      // Use public GHCR image for initial deploy; CodePipeline switches to ECR after first build
      image: ecs.ContainerImage.fromRegistry('ghcr.io/seanmorley15/adventurelog-frontend:latest'),
      essential: true,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'frontend', logGroup: frontendLogGroup }),
      portMappings: [{ containerPort: 3000, protocol: ecs.Protocol.TCP }],
      environment: {
        PUBLIC_SERVER_URL: backendInternalUrl,
        BODY_SIZE_LIMIT: 'Infinity',
        ORIGIN: props.siteUrl,
      },
    });

    this.frontendService = new ecs.FargateService(this, 'FrontendService', {
      cluster: this.cluster,
      taskDefinition: frontendTaskDef,
      desiredCount: 1,
      securityGroups: [props.fargateSecurityGroup],
      assignPublicIp: false,
      serviceName: `${props.environment}-adventurelog-frontend`,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      enableExecuteCommand: true,
    });

    cdk.Tags.of(this.cluster).add('Environment', props.environment);
  }
}
