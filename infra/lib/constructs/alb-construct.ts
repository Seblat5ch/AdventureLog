import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as route53Targets from 'aws-cdk-lib/aws-route53-targets';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as actions from 'aws-cdk-lib/aws-elasticloadbalancingv2-actions';
import { Construct } from 'constructs';

export interface AlbConstructProps {
  environment: string;
  vpc: ec2.Vpc;
  securityGroup: ec2.SecurityGroup;
  backendService: ecs.FargateService;
  frontendService: ecs.FargateService;
  hostedZoneId?: string;
  domainName?: string;
  subdomain?: string;
}

export class AlbConstruct extends Construct {
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  public readonly loadBalancerDnsName: string;

  constructor(scope: Construct, id: string, props: AlbConstructProps) {
    super(scope, id);

    this.loadBalancer = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc: props.vpc,
      internetFacing: true,
      securityGroup: props.securityGroup,
      loadBalancerName: `${props.environment}-adventurelog`,
    });

    this.loadBalancerDnsName = this.loadBalancer.loadBalancerDnsName;

    // WAF
    const waf = new wafv2.CfnWebACL(this, 'Waf', {
      name: `${props.environment}-adventurelog-waf`,
      scope: 'REGIONAL',
      defaultAction: { allow: {} },
      visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: `${props.environment}-adventurelog-waf`, sampledRequestsEnabled: true },
      rules: [
        { name: 'CommonRuleSet', priority: 1, overrideAction: { none: {} }, statement: { managedRuleGroupStatement: { vendorName: 'AWS', name: 'AWSManagedRulesCommonRuleSet' } }, visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'CommonRuleSet', sampledRequestsEnabled: true } },
        { name: 'KnownBadInputs', priority: 2, overrideAction: { none: {} }, statement: { managedRuleGroupStatement: { vendorName: 'AWS', name: 'AWSManagedRulesKnownBadInputsRuleSet' } }, visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'KnownBadInputs', sampledRequestsEnabled: true } },
        { name: 'IpReputation', priority: 3, overrideAction: { none: {} }, statement: { managedRuleGroupStatement: { vendorName: 'AWS', name: 'AWSManagedRulesAmazonIpReputationList' } }, visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'IpReputation', sampledRequestsEnabled: true } },
      ],
    });
    new wafv2.CfnWebACLAssociation(this, 'WafAssoc', { resourceArn: this.loadBalancer.loadBalancerArn, webAclArn: waf.attrArn });

    // Target groups
    const backendTg = new elbv2.ApplicationTargetGroup(this, 'BackendTg', {
      vpc: props.vpc, port: 80, protocol: elbv2.ApplicationProtocol.HTTP, targetType: elbv2.TargetType.IP,
      healthCheck: { path: '/api/', protocol: elbv2.Protocol.HTTP, interval: cdk.Duration.seconds(30), timeout: cdk.Duration.seconds(10), healthyThresholdCount: 2, unhealthyThresholdCount: 3 },
    });
    props.backendService.attachToApplicationTargetGroup(backendTg);

    const frontendTg = new elbv2.ApplicationTargetGroup(this, 'FrontendTg', {
      vpc: props.vpc, port: 3000, protocol: elbv2.ApplicationProtocol.HTTP, targetType: elbv2.TargetType.IP,
      healthCheck: { path: '/', protocol: elbv2.Protocol.HTTP, interval: cdk.Duration.seconds(30), timeout: cdk.Duration.seconds(10), healthyThresholdCount: 2, unhealthyThresholdCount: 3 },
    });
    props.frontendService.attachToApplicationTargetGroup(frontendTg);

    if (props.hostedZoneId && props.domainName) {
      const hostedZone = route53.HostedZone.fromLookup(this, 'Zone', { domainName: props.domainName });
      const fqdn = props.subdomain ? `${props.subdomain}.${props.domainName}` : props.domainName;

      const cert = new acm.Certificate(this, 'Cert', {
        domainName: fqdn,
        validation: acm.CertificateValidation.fromDns(hostedZone),
      });

      // Cognito — single sign-on gate, Django middleware auto-creates users
      const userPool = new cognito.UserPool(this, 'UserPool', {
        userPoolName: `${props.environment}-adventurelog`,
        selfSignUpEnabled: false,
        signInAliases: { email: true },
        autoVerify: { email: true },
        passwordPolicy: { minLength: 8, requireLowercase: true, requireUppercase: true, requireDigits: true, requireSymbols: false },
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });

      // Seed admin user
      new cognito.CfnUserPoolUser(this, 'AdminUser', {
        userPoolId: userPool.userPoolId,
        username: 'admin',
        userAttributes: [
          { name: 'email', value: 'admin@tesem.dog' },
          { name: 'email_verified', value: 'true' },
        ],
      });

      const userPoolClient = userPool.addClient('AlbClient', {
        userPoolClientName: `${props.environment}-adventurelog-alb`,
        generateSecret: true,
        oAuth: {
          flows: { authorizationCodeGrant: true },
          scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
          callbackUrls: [`https://${fqdn}/oauth2/idpresponse`],
          logoutUrls: [`https://${fqdn}`],
        },
        supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      });

      const userPoolDomain = userPool.addDomain('Domain', {
        cognitoDomain: { domainPrefix: `${props.environment}-adventurelog` },
      });

      // Cognito auth action — used as default on all traffic
      const cognitoAuth = new actions.AuthenticateCognitoAction({
        userPool,
        userPoolClient,
        userPoolDomain,
        sessionTimeout: cdk.Duration.days(7), // Login once, valid for 7 days
        next: elbv2.ListenerAction.forward([frontendTg]),
      });

      // HTTPS listener — everything goes through Cognito first
      const httpsListener = this.loadBalancer.addListener('Https', {
        port: 443,
        protocol: elbv2.ApplicationProtocol.HTTPS,
        certificates: [cert],
        defaultAction: cognitoAuth,
      });

      // Backend paths — also through Cognito, then forward to backend
      // ALB passes x-amzn-oidc-* headers, Django middleware reads them
      httpsListener.addAction('BackendApiRoutes', {
        priority: 10,
        conditions: [elbv2.ListenerCondition.pathPatterns(['/api/*', '/auth/*', '/admin/*', '/media/*', '/static/*'])],
        action: new actions.AuthenticateCognitoAction({
          userPool, userPoolClient, userPoolDomain,
          sessionTimeout: cdk.Duration.days(7),
          next: elbv2.ListenerAction.forward([backendTg]),
        }),
      });
      httpsListener.addAction('BackendAccountRoutes', {
        priority: 11,
        conditions: [elbv2.ListenerCondition.pathPatterns(['/accounts/*'])],
        action: new actions.AuthenticateCognitoAction({
          userPool, userPoolClient, userPoolDomain,
          sessionTimeout: cdk.Duration.days(7),
          next: elbv2.ListenerAction.forward([backendTg]),
        }),
      });

      // HTTP redirect
      this.loadBalancer.addListener('HttpRedirect', {
        port: 80, protocol: elbv2.ApplicationProtocol.HTTP,
        defaultAction: elbv2.ListenerAction.redirect({ port: '443', protocol: 'HTTPS', permanent: true }),
      });

      // DNS
      new route53.ARecord(this, 'ARecord', {
        zone: hostedZone, recordName: props.subdomain || undefined,
        target: route53.RecordTarget.fromAlias(new route53Targets.LoadBalancerTarget(this.loadBalancer)),
      });

      this.loadBalancerDnsName = fqdn;

      new cdk.CfnOutput(scope, 'CognitoUserPoolId', { value: userPool.userPoolId, description: 'Add users in Cognito console to grant access' });

    } else {
      // No domain — plain HTTP, no Cognito
      const httpListener = this.loadBalancer.addListener('Http', {
        port: 80, protocol: elbv2.ApplicationProtocol.HTTP, defaultTargetGroups: [frontendTg],
      });
      httpListener.addTargetGroups('BackendApiRoutes', {
        targetGroups: [backendTg], priority: 10,
        conditions: [elbv2.ListenerCondition.pathPatterns(['/api/*', '/auth/*', '/admin/*', '/media/*', '/static/*'])],
      });
      httpListener.addTargetGroups('BackendAccountRoutes', {
        targetGroups: [backendTg], priority: 11,
        conditions: [elbv2.ListenerCondition.pathPatterns(['/accounts/*'])],
      });
    }

    cdk.Tags.of(this.loadBalancer).add('Environment', props.environment);
  }
}
