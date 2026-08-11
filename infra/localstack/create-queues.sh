#!/bin/bash
# V4 (Asynchronous Processing): create the order-events queue + DLQ on
# LocalStack startup, with the same visibility timeout / max receive count
# (-> redrive to DLQ) as infra/modules/sqs's AWS configuration, so local and
# AWS behave the same way -- see docs/adr/ADR-005-async-processing.md.
#
# Runs automatically via LocalStack's init hooks: docker-compose.yml mounts
# this file into /etc/localstack/init/ready.d/, which LocalStack executes
# once its services are up, before it reports itself healthy.
set -euo pipefail

REGION="us-east-1"
DLQ_NAME="order-events-dlq"
QUEUE_NAME="order-events"
# V5: 60s, matching infra/modules/sqs's raised default -- the worker's
# in-process retry ladder has to fit inside the visibility window. Keeping
# these in step is the whole point of this script: a local experiment that
# behaves differently from AWS teaches the wrong lesson.
VISIBILITY_TIMEOUT="60"
RECEIVE_WAIT_TIME="20"
MAX_RECEIVE_COUNT="5"

dlq_url=$(awslocal sqs create-queue --region "$REGION" --queue-name "$DLQ_NAME" --query QueueUrl --output text)
dlq_arn=$(awslocal sqs get-queue-attributes --region "$REGION" --queue-url "$dlq_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)

# RedrivePolicy's value is itself a JSON string (not a nested object), per
# the SQS API -- hence the escaped inner quotes here.
attributes=$(cat <<EOF
{"VisibilityTimeout":"$VISIBILITY_TIMEOUT","ReceiveMessageWaitTimeSeconds":"$RECEIVE_WAIT_TIME","RedrivePolicy":"{\"deadLetterTargetArn\":\"$dlq_arn\",\"maxReceiveCount\":\"$MAX_RECEIVE_COUNT\"}"}
EOF
)

queue_url=$(awslocal sqs create-queue --region "$REGION" --queue-name "$QUEUE_NAME" --attributes "$attributes" --query QueueUrl --output text)
queue_arn=$(awslocal sqs get-queue-attributes --region "$REGION" --queue-url "$queue_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)

# V5: authorize AWS-native DLQ redrive back to the source queue, matching
# aws_sqs_queue_redrive_allow_policy in infra/modules/sqs.
redrive_allow=$(cat <<EOF
{"RedriveAllowPolicy":"{\"redrivePermission\":\"byQueue\",\"sourceQueueArns\":[\"$queue_arn\"]}"}
EOF
)

awslocal sqs set-queue-attributes --region "$REGION" --queue-url "$dlq_url" --attributes "$redrive_allow"

echo "V4/V5: order-events queue + DLQ ready on LocalStack"
