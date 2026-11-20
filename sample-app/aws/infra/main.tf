terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

# --- Receipts (S3) -----------------------------------------------------------
resource "aws_s3_bucket" "receipts" {
  bucket = "receipts"
}

# Side effect: every new receipt triggers a Lambda that renders a thumbnail.
resource "aws_lambda_function" "receipt_thumbnailer" {
  function_name = "receipt-thumbnailer"
  filename      = "thumbnailer.zip"
  handler       = "handler.main"
  runtime       = "python3.12"
  role          = aws_iam_role.shoplite_task.arn
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.receipt_thumbnailer.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.receipts.arn
}

resource "aws_s3_bucket_notification" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.receipt_thumbnailer.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "receipts/"
  }
  depends_on = [aws_lambda_permission.allow_s3]
}

# --- Orders (DynamoDB) -------------------------------------------------------
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
  attribute {
    name = "customer_id"
    type = "S"
  }

  global_secondary_index {
    name            = "customer_id-index"
    hash_key        = "customer_id"
    projection_type = "ALL"
  }
}

# --- Order events (SQS + DLQ) ------------------------------------------------
resource "aws_sqs_queue" "order_events_dlq" {
  name = "order-events-dlq"
}

resource "aws_sqs_queue" "order_events" {
  name                       = "order-events"
  visibility_timeout_seconds = 30
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.order_events_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- Credentials model: IAM role for the API/worker task --------------------
resource "aws_iam_role" "shoplite_task" {
  name = "shoplite-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["ecs-tasks.amazonaws.com", "lambda.amazonaws.com"] }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "shoplite_task" {
  name = "shoplite-data-access"
  role = aws_iam_role.shoplite_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"],
        Resource = [aws_dynamodb_table.orders.arn, "${aws_dynamodb_table.orders.arn}/index/*"] },
      { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.receipts.arn}/*" },
      { Effect = "Allow", Action = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueUrl"],
        Resource = aws_sqs_queue.order_events.arn },
    ]
  })
}

# --- Monitoring --------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "shoplite-dlq-not-empty"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.order_events_dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
}
