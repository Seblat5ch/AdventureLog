import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as codecommit from 'aws-cdk-lib/aws-codecommit';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as codepipeline from 'aws-cdk-lib/aws-codepipeline';
import * as codepipeline_actions from 'aws-cdk-lib/aws-codepipeline-actions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface PipelineConstructProps {
  environment: string;
  backendRepo: ecr.Repository;
  frontendRepo: ecr.Repository;
  backendService: ecs.FargateService;
  frontendService: ecs.FargateService;
  cluster: ecs.Cluster;
}

export class PipelineConstruct extends Construct {
  public readonly codeCommitRepo: codecommit.Repository;

  constructor(scope: Construct, id: string, props: PipelineConstructProps) {
    super(scope, id);

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // ---------------------------------------------------------------
    // CodeCommit repository
    // ---------------------------------------------------------------
    this.codeCommitRepo = new codecommit.Repository(this, 'Repo', {
      repositoryName: `${props.environment}-adventurelog`,
      description: 'AdventureLog source repository',
    });

    // ---------------------------------------------------------------
    // CodeBuild — builds both Docker images and pushes to ECR
    // ---------------------------------------------------------------
    const buildProject = new codebuild.Project(this, 'BuildProject', {
      projectName: `${props.environment}-adventurelog-build`,
      source: codebuild.Source.codeCommit({ repository: this.codeCommitRepo }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        privileged: true,
        computeType: codebuild.ComputeType.MEDIUM,
      },
      environmentVariables: {
        AWS_ACCOUNT_ID: { value: account },
        AWS_DEFAULT_REGION: { value: region },
        BACKEND_REPO_URI: { value: props.backendRepo.repositoryUri },
        FRONTEND_REPO_URI: { value: props.frontendRepo.repositoryUri },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'echo Logging in to Amazon ECR...',
              'aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com',
              'COMMIT_HASH=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | cut -c 1-7)',
              'IMAGE_TAG=${COMMIT_HASH:=latest}',
            ],
          },
          build: {
            commands: [
              'echo Building backend image...',
              'docker build -t $BACKEND_REPO_URI:latest -t $BACKEND_REPO_URI:$IMAGE_TAG -f backend/Dockerfile backend/',
              'echo Building frontend image...',
              'docker build -t $FRONTEND_REPO_URI:latest -t $FRONTEND_REPO_URI:$IMAGE_TAG -f frontend/Dockerfile frontend/',
            ],
          },
          post_build: {
            commands: [
              'echo Pushing backend image...',
              'docker push $BACKEND_REPO_URI:latest',
              'docker push $BACKEND_REPO_URI:$IMAGE_TAG',
              'echo Pushing frontend image...',
              'docker push $FRONTEND_REPO_URI:latest',
              'docker push $FRONTEND_REPO_URI:$IMAGE_TAG',
              'echo Writing image definitions...',
              'printf \'[{"name":"backend","imageUri":"%s"}]\' $BACKEND_REPO_URI:$IMAGE_TAG > backend-imagedefinitions.json',
              'printf \'[{"name":"frontend","imageUri":"%s"}]\' $FRONTEND_REPO_URI:$IMAGE_TAG > frontend-imagedefinitions.json',
            ],
          },
        },
        artifacts: {
          files: [
            'backend-imagedefinitions.json',
            'frontend-imagedefinitions.json',
          ],
        },
      }),
      logging: {
        cloudWatch: {
          logGroup: new logs.LogGroup(scope, 'BuildLogs', {
            logGroupName: `/codebuild/${props.environment}-adventurelog`,
            retention: logs.RetentionDays.TWO_WEEKS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
          }),
        },
      },
    });

    // Grant CodeBuild push access to both ECR repos
    props.backendRepo.grantPullPush(buildProject);
    props.frontendRepo.grantPullPush(buildProject);

    // ---------------------------------------------------------------
    // CodePipeline — CodeCommit → Build → Deploy
    // ---------------------------------------------------------------
    const sourceOutput = new codepipeline.Artifact('SourceOutput');
    const buildOutput = new codepipeline.Artifact('BuildOutput');

    const sourceAction = new codepipeline_actions.CodeCommitSourceAction({
      actionName: 'CodeCommit_Source',
      repository: this.codeCommitRepo,
      branch: 'main',
      output: sourceOutput,
      trigger: codepipeline_actions.CodeCommitTrigger.EVENTS, // auto-trigger on push
    });

    const buildAction = new codepipeline_actions.CodeBuildAction({
      actionName: 'Build_Images',
      project: buildProject,
      input: sourceOutput,
      outputs: [buildOutput],
    });

    const deployBackendAction = new codepipeline_actions.EcsDeployAction({
      actionName: 'Deploy_Backend',
      service: props.backendService,
      imageFile: buildOutput.atPath('backend-imagedefinitions.json'),
    });

    const deployFrontendAction = new codepipeline_actions.EcsDeployAction({
      actionName: 'Deploy_Frontend',
      service: props.frontendService,
      imageFile: buildOutput.atPath('frontend-imagedefinitions.json'),
    });

    new codepipeline.Pipeline(this, 'Pipeline', {
      pipelineName: `${props.environment}-adventurelog`,
      pipelineType: codepipeline.PipelineType.V2,
      stages: [
        { stageName: 'Source', actions: [sourceAction] },
        { stageName: 'Build', actions: [buildAction] },
        { stageName: 'Deploy', actions: [deployBackendAction, deployFrontendAction] },
      ],
    });

    cdk.Tags.of(buildProject).add('Environment', props.environment);
  }
}
