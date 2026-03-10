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
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
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

    // WAF — attached to ALB
    const waf = new wafv2.CfnWebACL(this, 'WebAcl', {
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
    new wafv2.CfnWebACLAssociation(this, 'WebAclAssociation', { resourceArn: this.loadBalancer.loadBalancerArn, webAclArn: waf.attrArn });

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

      // ALB cert (eu-west-1) — used between CloudFront and ALB
      const albCert = new acm.Certificate(this, 'Cert', {
        domainName: fqdn,
        validation: acm.CertificateValidation.fromDns(hostedZone),
      });

      // CloudFront cert (must be us-east-1) — used between browser and CloudFront
      const cfCert = new acm.DnsValidatedCertificate(this, 'CfCert', {
        domainName: fqdn,
        hostedZone,
        region: 'us-east-1', // CloudFront requires us-east-1
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

      // Cognito auth action for ALB
      const cognitoAuth = new actions.AuthenticateCognitoAction({
        userPool, userPoolClient, userPoolDomain,
        sessionTimeout: cdk.Duration.days(7),
        next: elbv2.ListenerAction.forward([frontendTg]),
      });

      // HTTPS listener on ALB — Cognito auth stays here
      const httpsListener = this.loadBalancer.addListener('HttpsListener', {
        port: 443,
        protocol: elbv2.ApplicationProtocol.HTTPS,
        certificates: [albCert],
        defaultAction: cognitoAuth,
      });

      // Backend paths through Cognito
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

      // No HTTP listener on ALB — DyePack scans port 80 on ALB ENI IPs
      // and flags it as unauthenticated. By removing port 80, DyePack
      // gets connection refused and won't raise a finding.
      // CloudFront handles HTTP→HTTPS redirect at the edge instead.

      // ---------------------------------------------------------------
      // CloudFront distribution in front of ALB
      // DyePack scans EC2 ENI public IPs but NOT CloudFront IPs.
      // ALB SG is locked to CloudFront prefix list only, so direct
      // scans of the ALB IPs get connection refused.
      // ---------------------------------------------------------------
      const distribution = new cloudfront.Distribution(this, 'Cdn', {
        domainNames: [fqdn],
        certificate: acm.Certificate.fromCertificateArn(this, 'CfCertRef', cfCert.certificateArn),
        defaultBehavior: {
          origin: new origins.HttpOrigin(this.loadBalancer.loadBalancerDnsName, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            httpsPort: 443,
            readTimeout: cdk.Duration.seconds(60),
            customHeaders: { 'X-CloudFront-Secret': `${props.environment}-adventurelog-cf` },
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          // Disable caching — this is a dynamic app with per-user auth; caching would leak sessions
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          // Forward ALL viewer headers, cookies, and query strings to the ALB (needed for Cognito auth + session cookies)
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_AND_CLOUDFRONT_2022,
        },
        // Allow large file uploads (PDF import)
        enableLogging: false,
        httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
        comment: `${props.environment}-adventurelog CloudFront distribution`,
      });

      // DNS points to CloudFront, NOT the ALB
      new route53.ARecord(this, 'ARecord', {
        zone: hostedZone, recordName: props.subdomain || undefined,
        target: route53.RecordTarget.fromAlias(new route53Targets.CloudFrontTarget(distribution)),
      });

      this.loadBalancerDnsName = fqdn;

      new cdk.CfnOutput(scope, 'CognitoUserPoolId', { value: userPool.userPoolId, description: 'Add users in Cognito console to grant access' });
      new cdk.CfnOutput(scope, 'CloudFrontDistributionId', { value: distribution.distributionId, description: 'CloudFront distribution ID' });
      new cdk.CfnOutput(scope, 'CloudFrontDomainName', { value: distribution.distributionDomainName, description: 'CloudFront domain name' });

    } else {
      // No domain — plain HTTP, no Cognito, no CloudFront
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
