#!/bin/bash -e
# This script will deploy a cicd pipeline (codepipeline, codedeploy, codebuild)
# Example usage: deploy/pipeline.sh -n your-stack-name -e dev
# To delete a stack: aws cloudformation delete-stack --stack-name your-stack-name

while getopts :t:n:e: option; do
  case "${option}" in
    n)
      STACK_NAME=${OPTARG}
    ;;
    t)
      GIT_TOKEN=${OPTARG}
    ;;
    e)
      ENVIRONMENT=${OPTARG}
    ;;
    \?)
      echo "Usage: deploy/pipeline.sh [-n STACK_NAME] [-e ENVIRONMENT]"
      exit 1
    ;;
  esac
done

if [ "$STACK_NAME" == "" ]; then
  echo "Stack name not set. Please pass one via the -n parameter."
  exit 1
fi

if [ "$GIT_TOKEN" == "" ]; then
  echo "GitHub Token not provided"
  exit 1
fi

# If env not set, default to dev
if [ "$ENVIRONMENT" == "" ]; then
  ENVIRONMENT=dev
fi

echo "STACK_NAME: $STACK_NAME"
echo "ENVIRONMENT: $ENVIRONMENT"

# Set variables. Set paths relative to the root.
SERVICE_NAME='confluence-to-degreed'
FINAL_STACK_NAME='confluence-to-degreed'
SSMPATHROOT=/adsk/confluence-to-degreed
TEMPLATE_FILE=confluence-to-degreed.yaml
TOPICARN=arn:aws:sns:us-east-1:964355697993:Entarch_Lambda_Notification_Channel
AWS_ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text)

#Get AWS Region if not set. TODO: investigate why `aws configure get region` doesn't work properly in CodeBuild; however, it sets $AWS_REGION, so we can leverage that for now.
if [ -z "$AWS_REGION" ]; then
    AWS_REGION=$(aws configure get region)
    if [ -z "$AWS_REGION" ]; then
        echo "ERROR: AWS_REGION not set"
        exit 1
    fi
else
    AWS_REGION="$AWS_REGION"
fi
echo "AWS_REGION: ${AWS_REGION}"

# Get the core networking info
VPC_ID=''
SUBNETS=''
INETSUBNETS=''
if [ "$VPC_GENERATED_BY" == "Alfred" ]; then
  VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:generated-by,Values=Alfred"   \
  --query "Vpcs[0].VpcId" \
  --output text)
  SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*private*" \
  --query "Subnets[*].SubnetId" \
  --output text | sed -e "s/\s/,/g")
else
  VPC_ID=$(aws ec2 describe-vpcs \
  --filters Name=tag:aws:cloudformation:stack-name,Values=core \
  --query "Vpcs[0].VpcId" \
  --output text)
  SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=application" \
  --query "Subnets[*].SubnetId" \
  --output text | perl -pe 's/\s/,/g; chop();')
  INETSUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" 'Name=tag:Name,Values=internal load balancers' \
  --query "Subnets[*].SubnetId" \
  --output text | perl -pe 's/\s/,/g; chop();')
fi
echo "VpcId: $VPC_ID"
echo "Subnets: $SUBNETS"
echo "Inet Subnets: $INETSUBNETS"

S3_PREFIX=${STACK_NAME}
DEPLOY_BUCKET=deploy-${AWS_ACCOUNT}-${AWS_REGION}
CFN_TEMPLATE_FILE=sam/cfn/pipeline.yaml
OUTPUT_CFN_TEMPLATE_FILE=${CFN_TEMPLATE_FILE}.tmp

# Set some cfn stack parameter overrides here
GIT_BRANCH='master'
GIT_USER='autodesk-lxp'
GIT_REPO='confluence-to-degreed'

echo "GitBranch: $GIT_BRANCH"

# the name of the service stack name the pipeline will deploy (with "-$env" at the end)
SERVICE_STACK_NAME='learning-confluence-to-degreed'
echo "ServiceStackName: $SERVICE_STACK_NAME"

# If not present, create S3 buckets for deployment support files.
aws s3api get-bucket-location \
--bucket "${DEPLOY_BUCKET}" 2>&1 >/dev/null \
|| aws s3 mb \
--region "$AWS_REGION" \
"s3://${DEPLOY_BUCKET}" 2>&1 >/dev/null

# Prepare CloudFormation package
echo "Packaging deployment artifacts for template..."
aws cloudformation package \
--template-file ${CFN_TEMPLATE_FILE} \
--output-template-file ${OUTPUT_CFN_TEMPLATE_FILE} \
--s3-bucket "$DEPLOY_BUCKET" \
--s3-prefix "${S3_PREFIX}"

# Deploy CloudFormation stack
echo "Deploying to stack ${STACK_NAME}..."
aws cloudformation deploy \
--template-file "${OUTPUT_CFN_TEMPLATE_FILE}" \
--role-arn "arn:aws:iam::${AWS_ACCOUNT}:role/AdskCfnAdministratorAccessExecutionRole" \
--capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
--s3-bucket "$DEPLOY_BUCKET" \
--s3-prefix "${S3_PREFIX}" \
--stack-name "${STACK_NAME}" \
--parameter-overrides \
AwsAccountId="${AWS_ACCOUNT}" \
CfnDeployBucket="${DEPLOY_BUCKET}" \
FinalStackName="${FINAL_STACK_NAME}" \
Git2S3StackName="${GIT_2_S3_STACK_NAME}" \
GitHubBranch="${GIT_BRANCH}" \
GitHubToken="${GIT_TOKEN}" \
GitHubUser="${GIT_USER}" \
GitHubRepo="${GIT_REPO}" \
InetSubnets="${INETSUBNETS}" \
VpcId="${VPC_ID}" \
ServiceStackName="${SERVICE_STACK_NAME}" \
SSMPathRoot="${SSMPATHROOT}" \
TemplateFile="${TEMPLATE_FILE}" \
TopicArn="${TOPICARN}" \
Subnets="${SUBNETS}"

# Clean-up tmp files
rm -f ${OUTPUT_CFN_TEMPLATE_FILE}

echo "Deploy Complete - CICD Pipeline: ${STACK_NAME}"
