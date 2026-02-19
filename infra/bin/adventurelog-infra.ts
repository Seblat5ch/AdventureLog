#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AdventureLogStack } from '../lib/adventurelog-stack';

const app = new cdk.App();

const environment = app.node.tryGetContext('environment') as string || 'prod';

new AdventureLogStack(app, `AdventureLog-${environment}`, {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT || '844633438632',
    region: 'eu-west-1',
  },
  description: 'AdventureLog — self-hosted travel companion on AWS ECS Fargate',
});
