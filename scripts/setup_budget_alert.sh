#!/bin/sh
# An AWS Budgets alert: email when the month's actual spend passes 80 % of
# the limit, and when it is forecast to pass 100 %.
#
#   BUDGET_EMAIL=you@example.com BUDGET_USD=20 scripts/setup_budget_alert.sh
#
# Needs the AWS CLI with permission for budgets:CreateBudget. Budgets are
# free for the first two per account.
set -eu
EMAIL=${BUDGET_EMAIL:?set BUDGET_EMAIL}
LIMIT=${BUDGET_USD:-20}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

aws budgets create-budget --account-id "$ACCOUNT" \
  --budget "{\"BudgetName\": \"fleetops-monthly\", \"BudgetLimit\": {\"Amount\": \"$LIMIT\", \"Unit\": \"USD\"}, \"TimeUnit\": \"MONTHLY\", \"BudgetType\": \"COST\"}" \
  --notifications-with-subscribers "[
    {\"Notification\": {\"NotificationType\": \"ACTUAL\", \"ComparisonOperator\": \"GREATER_THAN\", \"Threshold\": 80, \"ThresholdType\": \"PERCENTAGE\"},
     \"Subscribers\": [{\"SubscriptionType\": \"EMAIL\", \"Address\": \"$EMAIL\"}]},
    {\"Notification\": {\"NotificationType\": \"FORECASTED\", \"ComparisonOperator\": \"GREATER_THAN\", \"Threshold\": 100, \"ThresholdType\": \"PERCENTAGE\"},
     \"Subscribers\": [{\"SubscriptionType\": \"EMAIL\", \"Address\": \"$EMAIL\"}]}
  ]"
echo "Budget fleetops-monthly: \$$LIMIT a month, alerts to $EMAIL"
