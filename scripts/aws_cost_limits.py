#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError

REDSHIFT_SERVERLESS_RPU_HOUR = 0.36
ATHENA_TB_SCANNED = 5.00
EMR_SERVERLESS_VCPU_HOUR = 0.052624
EMR_SERVERLESS_MEMORY_GB_HOUR = 0.0057785
LAMBDA_GB_SECOND = 0.0000166667
LAMBDA_REQUEST = 0.0000002

EC2_INSTANCE_COSTS = {
    "t2.micro": 0.0116,
    "t2.small": 0.023,
    "t2.medium": 0.0464,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m6i.large": 0.096,
    "m6i.xlarge": 0.192,
    "r5.large": 0.126,
    "r5.xlarge": 0.252,
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
}

DEFAULT_DAILY_SPEND = 5.00
DEFAULT_MONTHLY_BUDGET = 50.00


@dataclass
class ServiceLimit:
    service: str
    resource_name: str
    limit_type: str
    current_value: float | None
    unit: str
    daily_cost_estimate: float | None
    status: str = "active"


def get_client(service: str):
    return boto3.client(service)


def get_account_id() -> str:
    return get_client("sts").get_caller_identity()["Account"]


def get_region() -> str:
    session = boto3.session.Session()
    return session.region_name or "us-east-1"


def get_redshift_workgroups() -> list[dict[str, Any]]:
    client = get_client("redshift-serverless")
    try:
        response = client.list_workgroups()
        return response.get("workgroups", [])
    except ClientError:
        return []


def get_redshift_usage_limits() -> list[dict[str, Any]]:
    client = get_client("redshift-serverless")
    try:
        response = client.list_usage_limits()
        return response.get("usageLimits", [])
    except ClientError:
        return []


def calculate_redshift_rpu_hours(daily_spend: float) -> int:
    rpu_hours = daily_spend / REDSHIFT_SERVERLESS_RPU_HOUR
    return max(1, int(rpu_hours))


def set_redshift_limit(
    workgroup_arn: str,
    workgroup_name: str,
    daily_spend: float,
    breach_action: str = "deactivate",
) -> dict[str, Any]:
    client = get_client("redshift-serverless")
    rpu_hours = calculate_redshift_rpu_hours(daily_spend)

    existing_limits = get_redshift_usage_limits()
    existing = next(
        (
            lim
            for lim in existing_limits
            if lim["resourceArn"] == workgroup_arn
            and lim["usageType"] == "serverless-compute"
            and lim["period"] == "daily"
        ),
        None,
    )

    if existing:
        response = client.update_usage_limit(
            usageLimitId=existing["usageLimitId"],
            amount=rpu_hours,
            breachAction=breach_action,
        )
        action = "Updated"
    else:
        response = client.create_usage_limit(
            resourceArn=workgroup_arn,
            usageType="serverless-compute",
            amount=rpu_hours,
            period="daily",
            breachAction=breach_action,
        )
        action = "Created"

    actual_cost = rpu_hours * REDSHIFT_SERVERLESS_RPU_HOUR
    print(f"  {action} Redshift Serverless limit for '{workgroup_name}':")
    print(f"    RPU-hours/day: {rpu_hours}")
    print(f"    Max daily cost: ${actual_cost:.2f}")
    print(f"    Breach action: {breach_action}")

    return response


def remove_redshift_limit(workgroup_arn: str) -> bool:
    client = get_client("redshift-serverless")
    existing_limits = get_redshift_usage_limits()

    removed = False
    for lim in existing_limits:
        if lim["resourceArn"] == workgroup_arn:
            client.delete_usage_limit(usageLimitId=lim["usageLimitId"])
            print(f"  Removed limit: {lim['usageLimitId']}")
            removed = True

    return removed


def get_athena_workgroups() -> list[dict[str, Any]]:
    client = get_client("athena")
    try:
        response = client.list_work_groups()
        return response.get("WorkGroups", [])
    except ClientError:
        return []


def get_athena_workgroup_config(workgroup_name: str) -> dict[str, Any]:
    client = get_client("athena")
    response = client.get_work_group(WorkGroup=workgroup_name)
    return response.get("WorkGroup", {})


def calculate_athena_bytes(daily_spend: float) -> int:
    tb_allowed = daily_spend / ATHENA_TB_SCANNED
    bytes_allowed = int(tb_allowed * 1024 * 1024 * 1024 * 1024)
    return max(10 * 1024 * 1024, bytes_allowed)


def format_bytes(bytes_val: int) -> str:
    if bytes_val >= 1024**4:
        return f"{bytes_val / 1024**4:.2f} TB"
    elif bytes_val >= 1024**3:
        return f"{bytes_val / 1024**3:.2f} GB"
    elif bytes_val >= 1024**2:
        return f"{bytes_val / 1024**2:.2f} MB"
    else:
        return f"{bytes_val} bytes"


def set_athena_limit(workgroup_name: str, daily_spend: float) -> dict[str, Any]:
    client = get_client("athena")
    bytes_limit = calculate_athena_bytes(daily_spend)

    per_query_bytes = max(10 * 1024 * 1024, bytes_limit // 20)

    response = client.update_work_group(
        WorkGroup=workgroup_name,
        ConfigurationUpdates={
            "BytesScannedCutoffPerQuery": per_query_bytes,
            "EnforceWorkGroupConfiguration": True,
        },
    )

    actual_cost_per_query = (per_query_bytes / 1024**4) * ATHENA_TB_SCANNED
    print(f"  Updated Athena workgroup '{workgroup_name}':")
    print(f"    Per-query limit: {format_bytes(per_query_bytes)}")
    print(f"    Max cost per query: ${actual_cost_per_query:.4f}")
    print(f"    Daily budget allows ~{int(daily_spend / actual_cost_per_query)} queries")

    return response


def remove_athena_limit(workgroup_name: str) -> bool:
    client = get_client("athena")
    try:
        client.update_work_group(
            WorkGroup=workgroup_name,
            ConfigurationUpdates={"RemoveBytesScannedCutoffPerQuery": True},
        )
        print(f"  Removed scan limit from workgroup '{workgroup_name}'")
        return True
    except ClientError as e:
        print(f"  Error removing limit: {e}")
        return False


def get_emr_serverless_applications() -> list[dict[str, Any]]:
    client = get_client("emr-serverless")
    try:
        response = client.list_applications()
        return response.get("applications", [])
    except ClientError:
        return []


def get_emr_serverless_application(app_id: str) -> dict[str, Any] | None:
    client = get_client("emr-serverless")
    try:
        response = client.get_application(applicationId=app_id)
        return response.get("application")
    except ClientError:
        return None


def calculate_emr_max_capacity(daily_spend: float) -> dict[str, str]:
    cost_per_unit_hour = EMR_SERVERLESS_VCPU_HOUR + (4 * EMR_SERVERLESS_MEMORY_GB_HOUR)

    hours_per_day = 8
    max_concurrent_units = daily_spend / (cost_per_unit_hour * hours_per_day)

    max_vcpu = max(1, int(max_concurrent_units))
    max_memory_gb = max_vcpu * 4

    return {
        "cpu": f"{max_vcpu} vCPU",
        "memory": f"{max_memory_gb} GB",
    }


def set_emr_serverless_limit(app_id: str, app_name: str, daily_spend: float) -> dict[str, Any] | None:
    client = get_client("emr-serverless")
    capacity = calculate_emr_max_capacity(daily_spend)

    try:
        response = client.update_application(
            applicationId=app_id,
            maximumCapacity=capacity,
        )

        print(f"  Updated EMR Serverless application '{app_name}':")
        print(f"    Max vCPU: {capacity['cpu']}")
        print(f"    Max memory: {capacity['memory']}")
        print(f"    Est. max daily cost: ${daily_spend:.2f} (at 8hr/day)")

        return response
    except ClientError as e:
        print(f"  Error updating EMR Serverless: {e}")
        return None


def remove_emr_serverless_limit(app_id: str, app_name: str) -> bool:
    client = get_client("emr-serverless")
    try:
        client.update_application(
            applicationId=app_id,
            maximumCapacity={
                "cpu": "400 vCPU",
                "memory": "3000 GB",
            },
        )
        print(f"  Removed capacity limits from '{app_name}'")
        return True
    except ClientError as e:
        print(f"  Error: {e}")
        return False


def get_lambda_functions() -> list[dict[str, Any]]:
    client = get_client("lambda")
    try:
        paginator = client.get_paginator("list_functions")
        functions = []
        for page in paginator.paginate():
            functions.extend(page.get("Functions", []))
        return functions
    except ClientError:
        return []


def get_lambda_concurrency(function_name: str) -> int | None:
    client = get_client("lambda")
    try:
        response = client.get_function_concurrency(FunctionName=function_name)
        return response.get("ReservedConcurrentExecutions")
    except ClientError:
        return None


def get_account_lambda_concurrency_limit() -> dict[str, Any]:
    client = get_client("lambda")
    try:
        response = client.get_account_settings()
        return {
            "total_limit": response["AccountLimit"]["ConcurrentExecutions"],
            "unreserved": response["AccountLimit"]["UnreservedConcurrentExecutions"],
        }
    except ClientError:
        return {}


def calculate_lambda_concurrency(daily_spend: float, avg_duration_ms: int = 1000, memory_mb: int = 128) -> int:
    gb_seconds_per_invocation = (memory_mb / 1024) * (avg_duration_ms / 1000)
    cost_per_invocation = (gb_seconds_per_invocation * LAMBDA_GB_SECOND) + LAMBDA_REQUEST

    seconds_per_day = 86400
    invocations_per_concurrent_per_day = seconds_per_day / (avg_duration_ms / 1000)

    cost_per_concurrent_per_day = cost_per_invocation * invocations_per_concurrent_per_day

    max_concurrency = daily_spend / cost_per_concurrent_per_day
    return max(1, int(max_concurrency))


def set_lambda_concurrency(function_name: str, concurrency: int) -> dict[str, Any] | None:
    client = get_client("lambda")
    try:
        response = client.put_function_concurrency(
            FunctionName=function_name,
            ReservedConcurrentExecutions=concurrency,
        )
        print(f"  Set Lambda '{function_name}' concurrency: {concurrency}")
        return response
    except ClientError as e:
        print(f"  Error setting Lambda concurrency: {e}")
        return None


def remove_lambda_concurrency(function_name: str) -> bool:
    client = get_client("lambda")
    try:
        client.delete_function_concurrency(FunctionName=function_name)
        print(f"  Removed concurrency limit from '{function_name}'")
        return True
    except ClientError as e:
        print(f"  Error: {e}")
        return False


def get_running_ec2_instances() -> list[dict[str, Any]]:
    client = get_client("ec2")
    try:
        response = client.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}]
        )

        instances = []
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_type = instance.get("InstanceType", "unknown")
                hourly_cost = EC2_INSTANCE_COSTS.get(instance_type, 0.10)

                name = "unnamed"
                for tag in instance.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break

                instances.append(
                    {
                        "id": instance["InstanceId"],
                        "name": name,
                        "type": instance_type,
                        "state": instance["State"]["Name"],
                        "hourly_cost": hourly_cost,
                        "daily_cost": hourly_cost * 24,
                        "launch_time": instance.get("LaunchTime"),
                    }
                )

        return instances
    except ClientError:
        return []


BUDGET_ACTION_POLICY = """{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BudgetActionDenyExpensiveServices",
            "Effect": "Deny",
            "Action": [
                "ec2:RunInstances",
                "ec2:StartInstances",
                "rds:CreateDBInstance",
                "rds:StartDBInstance",
                "redshift:CreateCluster",
                "redshift-serverless:CreateWorkgroup",
                "eks:CreateCluster",
                "sagemaker:CreateNotebookInstance",
                "sagemaker:CreateEndpoint",
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": "*"
        }
    ]
}"""


def get_budgets() -> list[dict[str, Any]]:
    client = get_client("budgets")
    account_id = get_account_id()
    try:
        response = client.describe_budgets(AccountId=account_id)
        return response.get("Budgets", [])
    except ClientError:
        return []


def get_budget_actions(budget_name: str) -> list[dict[str, Any]]:
    client = get_client("budgets")
    account_id = get_account_id()
    try:
        response = client.describe_budget_actions_for_budget(
            AccountId=account_id,
            BudgetName=budget_name,
        )
        return response.get("Actions", [])
    except ClientError:
        return []


def create_cost_control_budget(
    monthly_limit: float,
    budget_name: str = "CostControlBudget",
    alert_thresholds: list[int] | None = None,
    email: str | None = None,
) -> dict[str, Any] | None:
    client = get_client("budgets")
    account_id = get_account_id()

    if alert_thresholds is None:
        alert_thresholds = [50, 80, 100]

    existing = get_budgets()
    if any(b["BudgetName"] == budget_name for b in existing):
        print(f"  Budget '{budget_name}' already exists - updating...")
        try:
            client.update_budget(
                AccountId=account_id,
                NewBudget={
                    "BudgetName": budget_name,
                    "BudgetLimit": {"Amount": str(monthly_limit), "Unit": "USD"},
                    "BudgetType": "COST",
                    "TimeUnit": "MONTHLY",
                    "CostTypes": {
                        "IncludeTax": True,
                        "IncludeSubscription": True,
                        "UseBlended": False,
                        "IncludeRefund": True,
                        "IncludeCredit": True,
                        "IncludeUpfront": True,
                        "IncludeRecurring": True,
                        "IncludeOtherSubscription": True,
                        "IncludeSupport": True,
                        "IncludeDiscount": True,
                        "UseAmortized": False,
                    },
                },
            )
            print(f"  Updated budget limit to ${monthly_limit:.2f}/month")
            return {"BudgetName": budget_name, "updated": True}
        except ClientError as e:
            print(f"  Error updating budget: {e}")
            return None

    notifications = []
    if email:
        for threshold in alert_thresholds:
            notifications.append(
                {
                    "Notification": {
                        "NotificationType": "ACTUAL",
                        "ComparisonOperator": "GREATER_THAN",
                        "Threshold": threshold,
                        "ThresholdType": "PERCENTAGE",
                    },
                    "Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}],
                }
            )

    try:
        kwargs = {
            "AccountId": account_id,
            "Budget": {
                "BudgetName": budget_name,
                "BudgetLimit": {"Amount": str(monthly_limit), "Unit": "USD"},
                "BudgetType": "COST",
                "TimeUnit": "MONTHLY",
                "CostTypes": {
                    "IncludeTax": True,
                    "IncludeSubscription": True,
                    "UseBlended": False,
                    "IncludeRefund": True,
                    "IncludeCredit": True,
                    "IncludeUpfront": True,
                    "IncludeRecurring": True,
                    "IncludeOtherSubscription": True,
                    "IncludeSupport": True,
                    "IncludeDiscount": True,
                    "UseAmortized": False,
                },
            },
        }

        if notifications:
            kwargs["NotificationsWithSubscribers"] = notifications

        client.create_budget(**kwargs)

        print(f"  Created budget '{budget_name}':")
        print(f"    Monthly limit: ${monthly_limit:.2f}")
        if email:
            print(f"    Alert thresholds: {alert_thresholds}%")
            print(f"    Notifications to: {email}")

        return {"BudgetName": budget_name, "created": True}

    except ClientError as e:
        print(f"  Error creating budget: {e}")
        return None


def create_budget_action_iam_policy(
    budget_name: str,
    threshold: int = 100,
    action_name: str = "DenyExpensiveServices",
) -> dict[str, Any] | None:
    client = get_client("budgets")
    iam_client = get_client("iam")
    account_id = get_account_id()

    policy_name = f"BudgetAction-{action_name}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    try:
        iam_client.get_policy(PolicyArn=policy_arn)
        print(f"  IAM policy '{policy_name}' already exists")
    except iam_client.exceptions.NoSuchEntityException:
        try:
            iam_client.create_policy(
                PolicyName=policy_name,
                PolicyDocument=BUDGET_ACTION_POLICY,
                Description="Deny policy applied by AWS Budgets when spending threshold is exceeded",
            )
            print(f"  Created IAM policy '{policy_name}'")
        except ClientError as e:
            print(f"  Error creating IAM policy: {e}")
            return None

    try:
        existing_actions = get_budget_actions(budget_name)
        if any(
            a.get("Definition", {}).get("IamActionDefinition", {}).get("PolicyArn") == policy_arn
            for a in existing_actions
        ):
            print(f"  Budget action already exists for '{budget_name}'")
            return {"exists": True}

        role_name = "AWSBudgetsActionsRole"
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

        try:
            iam_client.get_role(RoleName=role_name)
        except iam_client.exceptions.NoSuchEntityException:
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "budgets.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for AWS Budgets to execute actions",
            )
            iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/AWSBudgetsActionsWithAWSResourceControlAccess",
            )
            print(f"  Created IAM role '{role_name}'")

        users = iam_client.list_users().get("Users", [])
        groups = iam_client.list_groups().get("Groups", [])

        user_arns = [u["Arn"] for u in users if u["UserName"] != "root"]
        group_arns = [g["Arn"] for g in groups]

        if not user_arns and not group_arns:
            print("  Warning: No IAM users or groups found to apply policy to")
            print("  The budget action won't have any effect without targets")
            return None

        response = client.create_budget_action(
            AccountId=account_id,
            BudgetName=budget_name,
            NotificationType="ACTUAL",
            ActionType="APPLY_IAM_POLICY",
            ActionThreshold={
                "ActionThresholdValue": threshold,
                "ActionThresholdType": "PERCENTAGE",
            },
            Definition={
                "IamActionDefinition": {
                    "PolicyArn": policy_arn,
                    "Users": [u["UserName"] for u in users if u["UserName"] != "root"][:10],
                    "Groups": [g["GroupName"] for g in groups][:10],
                }
            },
            ExecutionRoleArn=role_arn,
            ApprovalModel="AUTOMATIC",
            Subscribers=[],
        )

        print(f"  Created budget action for '{budget_name}':")
        print(f"    Action: Apply deny policy at {threshold}% of budget")
        print(f"    Policy: {policy_name}")
        print(f"    Targets: {len(user_arns)} users, {len(group_arns)} groups")
        print("    Mode: AUTOMATIC (no approval required)")

        return response

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "DuplicateRecordException":
            print("  Budget action already exists")
            return {"exists": True}
        print(f"  Error creating budget action: {e}")
        return None


def create_budget_action_stop_ec2(
    budget_name: str,
    threshold: int = 100,
) -> dict[str, Any] | None:
    client = get_client("budgets")
    iam_client = get_client("iam")
    account_id = get_account_id()
    region = get_region()

    try:
        role_name = "AWSBudgetsActionsRole"
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

        try:
            iam_client.get_role(RoleName=role_name)
        except iam_client.exceptions.NoSuchEntityException:
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "budgets.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for AWS Budgets to execute actions",
            )
            iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/AWSBudgetsActionsWithAWSResourceControlAccess",
            )
            print(f"  Created IAM role '{role_name}'")

        existing_actions = get_budget_actions(budget_name)
        ssm_actions = [a for a in existing_actions if a.get("ActionType") == "RUN_SSM_DOCUMENTS"]
        if ssm_actions:
            print(f"  SSM budget action already exists for '{budget_name}'")
            return {"exists": True}

        instances = get_running_ec2_instances()
        if not instances:
            print("  No running EC2 instances found")
            print("  Creating action anyway - will apply to future instances")

        instance_ids = [i["id"] for i in instances]

        response = client.create_budget_action(
            AccountId=account_id,
            BudgetName=budget_name,
            NotificationType="ACTUAL",
            ActionType="RUN_SSM_DOCUMENTS",
            ActionThreshold={
                "ActionThresholdValue": threshold,
                "ActionThresholdType": "PERCENTAGE",
            },
            Definition={
                "SsmActionDefinition": {
                    "ActionSubType": "STOP_EC2_INSTANCES",
                    "Region": region,
                    "InstanceIds": instance_ids if instance_ids else ["i-placeholder"],
                }
            },
            ExecutionRoleArn=role_arn,
            ApprovalModel="AUTOMATIC",
            Subscribers=[],
        )

        print(f"  Created EC2 stop action for '{budget_name}':")
        print(f"    Trigger: {threshold}% of budget")
        print("    Action: Stop EC2 instances")
        print(f"    Instances: {len(instance_ids)} currently running")
        print("    Mode: AUTOMATIC")

        return response

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "DuplicateRecordException":
            print("  Budget action already exists")
            return {"exists": True}
        print(f"  Error creating budget action: {e}")
        return None


def delete_budget(budget_name: str) -> bool:
    client = get_client("budgets")
    account_id = get_account_id()

    try:
        actions = get_budget_actions(budget_name)
        for action in actions:
            try:
                client.delete_budget_action(
                    AccountId=account_id,
                    BudgetName=budget_name,
                    ActionId=action["ActionId"],
                )
                print(f"  Deleted action: {action['ActionId']}")
            except ClientError as e:
                print(f"  Error deleting action: {e}")

        client.delete_budget(AccountId=account_id, BudgetName=budget_name)
        print(f"  Deleted budget '{budget_name}'")
        return True

    except ClientError as e:
        print(f"  Error deleting budget: {e}")
        return False


def _collect_redshift_limits(log) -> list[ServiceLimit]:
    log("\n=== Redshift Serverless ===")
    workgroups = get_redshift_workgroups()
    usage_limits = get_redshift_usage_limits()
    limits: list[ServiceLimit] = []

    if not workgroups:
        log("  No workgroups found")
        return limits

    for wg in workgroups:
        wg_name = wg["workgroupName"]
        wg_arn = wg["workgroupArn"]
        wg_limits = [lim for lim in usage_limits if lim["resourceArn"] == wg_arn]

        if not wg_limits:
            log(f"  Workgroup: {wg_name}")
            log("    Limit: NONE (unlimited)")
            limits.append(
                ServiceLimit(
                    service="redshift-serverless",
                    resource_name=wg_name,
                    limit_type="none",
                    current_value=None,
                    unit="RPU-hours",
                    daily_cost_estimate=None,
                    status="unlimited",
                )
            )
            continue

        for lim in wg_limits:
            if lim["usageType"] != "serverless-compute":
                continue
            amount = lim["amount"]
            period = lim["period"]
            daily_cost = amount * REDSHIFT_SERVERLESS_RPU_HOUR
            log(f"  Workgroup: {wg_name}")
            log(f"    Limit: {amount} RPU-hours/{period}")
            log(f"    Est. max cost: ${daily_cost:.2f}/{period}")
            log(f"    Breach action: {lim['breachAction']}")
            limits.append(
                ServiceLimit(
                    service="redshift-serverless",
                    resource_name=wg_name,
                    limit_type=f"RPU-hours/{period}",
                    current_value=amount,
                    unit="RPU-hours",
                    daily_cost_estimate=daily_cost if period == "daily" else None,
                )
            )
    return limits


def _collect_athena_limits(log) -> list[ServiceLimit]:
    log("\n=== Athena ===")
    athena_workgroups = get_athena_workgroups()
    limits: list[ServiceLimit] = []

    if not athena_workgroups:
        log("  No workgroups found")
        return limits

    for wg in athena_workgroups:
        wg_name = wg["Name"]
        if wg.get("State") != "ENABLED":
            continue
        try:
            config = get_athena_workgroup_config(wg_name)
        except ClientError:
            log(f"  Workgroup: {wg_name} (access denied)")
            continue

        wg_config = config.get("Configuration", {})
        bytes_limit = wg_config.get("BytesScannedCutoffPerQuery")
        log(f"  Workgroup: {wg_name}")

        if bytes_limit:
            tb_limit = bytes_limit / 1024**4
            cost_per_query = tb_limit * ATHENA_TB_SCANNED
            log(f"    Per-query limit: {format_bytes(bytes_limit)}")
            log(f"    Est. cost per query: ${cost_per_query:.4f}")
            limits.append(
                ServiceLimit(
                    service="athena",
                    resource_name=wg_name,
                    limit_type="bytes-per-query",
                    current_value=bytes_limit,
                    unit="bytes",
                    daily_cost_estimate=None,
                )
            )
        else:
            log("    Per-query limit: NONE (unlimited)")
            limits.append(
                ServiceLimit(
                    service="athena",
                    resource_name=wg_name,
                    limit_type="none",
                    current_value=None,
                    unit="bytes",
                    daily_cost_estimate=None,
                    status="unlimited",
                )
            )
    return limits


def _collect_emr_limits(log) -> list[ServiceLimit]:
    log("\n=== EMR Serverless ===")
    emr_apps = get_emr_serverless_applications()
    limits: list[ServiceLimit] = []

    if not emr_apps:
        log("  No applications found")
        return limits

    for app in emr_apps:
        app_details = get_emr_serverless_application(app["id"])
        if not app_details:
            continue
        app_name = app["name"]
        max_cap = app_details.get("maximumCapacity", {})
        cpu = max_cap.get("cpu", "unlimited")
        memory = max_cap.get("memory", "unlimited")
        log(f"  Application: {app_name} ({app_details.get('state', 'unknown')})")
        log(f"    Max CPU: {cpu}")
        log(f"    Max Memory: {memory}")
        limits.append(
            ServiceLimit(
                service="emr-serverless",
                resource_name=app_name,
                limit_type="max-capacity",
                current_value=None,
                unit=f"cpu={cpu}, memory={memory}",
                daily_cost_estimate=None,
            )
        )
    return limits


def _collect_lambda_limits(log) -> list[ServiceLimit]:
    log("\n=== Lambda ===")
    account_limits = get_account_lambda_concurrency_limit()
    if account_limits:
        log(f"  Account concurrency limit: {account_limits.get('total_limit', 'unknown')}")
        log(f"  Unreserved concurrency: {account_limits.get('unreserved', 'unknown')}")

    functions = get_lambda_functions()
    limits: list[ServiceLimit] = []
    functions_with_limits: list[tuple[str, int]] = []

    for fn in functions:
        concurrency = get_lambda_concurrency(fn["FunctionName"])
        if concurrency is None:
            continue
        functions_with_limits.append((fn["FunctionName"], concurrency))
        limits.append(
            ServiceLimit(
                service="lambda",
                resource_name=fn["FunctionName"],
                limit_type="reserved-concurrency",
                current_value=concurrency,
                unit="concurrent executions",
                daily_cost_estimate=None,
            )
        )

    if functions_with_limits:
        for fn_name, conc in functions_with_limits:
            log(f"  Function: {fn_name}")
            log(f"    Reserved concurrency: {conc}")
    else:
        log(f"  {len(functions)} functions, none with reserved concurrency")
    return limits


def _log_ec2_instances(log) -> None:
    log("\n=== EC2 (Running Instances) ===")
    instances = get_running_ec2_instances()
    if not instances:
        log("  No running instances")
        return
    total_daily = sum(i["daily_cost"] for i in instances)
    for inst in instances:
        log(f"  {inst['name']} ({inst['id']})")
        log(f"    Type: {inst['type']}, Est. cost: ${inst['daily_cost']:.2f}/day")
    log(f"  Total EC2 daily cost: ${total_daily:.2f}")


def _collect_budget_limits(log) -> list[ServiceLimit]:
    log("\n=== AWS Budgets ===")
    budgets = get_budgets()
    limits: list[ServiceLimit] = []

    if not budgets:
        log("  No budgets configured")
        return limits

    for budget in budgets:
        name = budget["BudgetName"]
        limit = budget.get("BudgetLimit", {})
        amount = limit.get("Amount", "unknown")
        spent = budget.get("CalculatedSpend", {}).get("ActualSpend", {}).get("Amount", "0")

        log(f"  Budget: {name}")
        log(f"    Limit: ${float(amount):.2f}/month")
        log(f"    Current spend: ${float(spent):.2f}")

        actions = get_budget_actions(name)
        if actions:
            log(f"    Actions: {len(actions)}")
            for action in actions:
                action_type = action.get("ActionType", "unknown")
                threshold = action.get("ActionThreshold", {}).get("ActionThresholdValue", "?")
                status = action.get("Status", "unknown")
                log(f"      - {action_type} at {threshold}% ({status})")

        limits.append(
            ServiceLimit(
                service="budget",
                resource_name=name,
                limit_type="monthly-cost",
                current_value=float(amount),
                unit="USD/month",
                daily_cost_estimate=float(amount) / 30,
            )
        )
    return limits


def list_current_limits(quiet: bool = False) -> list[ServiceLimit]:

    def log(msg: str) -> None:
        if not quiet:
            print(msg)

    limits: list[ServiceLimit] = []
    limits.extend(_collect_redshift_limits(log))
    limits.extend(_collect_athena_limits(log))
    limits.extend(_collect_emr_limits(log))
    limits.extend(_collect_lambda_limits(log))
    _log_ec2_instances(log)
    limits.extend(_collect_budget_limits(log))
    return limits


def set_all_limits(daily_spend: float) -> None:
    print(f"\nSetting limits for ${daily_spend:.2f}/day per service...")

    print("\n--- Redshift Serverless ---")
    workgroups = get_redshift_workgroups()
    if workgroups:
        for wg in workgroups:
            set_redshift_limit(wg["workgroupArn"], wg["workgroupName"], daily_spend)
    else:
        print("  No workgroups found - skipping")

    print("\n--- Athena ---")
    athena_workgroups = get_athena_workgroups()
    if athena_workgroups:
        for wg in athena_workgroups:
            if wg.get("State") == "ENABLED":
                try:
                    set_athena_limit(wg["Name"], daily_spend)
                except ClientError as e:
                    print(f"  Skipping {wg['Name']}: {e}")
    else:
        print("  No workgroups found - skipping")

    print("\n--- EMR Serverless ---")
    emr_apps = get_emr_serverless_applications()
    if emr_apps:
        for app in emr_apps:
            if app.get("state") in ("CREATED", "STARTED"):
                set_emr_serverless_limit(app["id"], app["name"], daily_spend)
    else:
        print("  No applications found - skipping")

    print("\n--- Lambda ---")
    functions = get_lambda_functions()
    if functions:
        concurrency = calculate_lambda_concurrency(daily_spend)
        print(f"  Calculated concurrency limit for ${daily_spend:.2f}/day: {concurrency}")
        print("  Note: Lambda limits must be set per-function. Use --service lambda to configure.")
        print(f"  Found {len(functions)} functions")
    else:
        print("  No functions found - skipping")

    print("\n" + "=" * 50)
    print("Service limits configured. Run with --list to verify.")
    print("\nTip: Use --budget to create a budget with automatic actions for additional protection.")


def _set_athena_limits(daily_spend: float, resource_name: str | None) -> None:
    print(f"\nSetting Athena limit for ${daily_spend:.2f}/day...")
    athena_workgroups = get_athena_workgroups()
    if not athena_workgroups:
        print("  No workgroups found")
        return
    for wg in athena_workgroups:
        if wg.get("State") == "ENABLED" and (resource_name is None or wg["Name"] == resource_name):
            try:
                set_athena_limit(wg["Name"], daily_spend)
            except ClientError as e:
                print(f"  Skipping {wg['Name']}: {e}")


def _set_lambda_limits(daily_spend: float, resource_name: str | None) -> None:
    print("\nSetting Lambda concurrency limits...")
    functions = get_lambda_functions()
    if resource_name:
        concurrency = calculate_lambda_concurrency(daily_spend)
        set_lambda_concurrency(resource_name, concurrency)
    elif functions:
        concurrency = calculate_lambda_concurrency(daily_spend)
        print(f"  Calculated concurrency: {concurrency} for ${daily_spend:.2f}/day")
        print(f"  Found {len(functions)} functions:")
        for fn in functions[:10]:
            print(f"    - {fn['FunctionName']}")
        if len(functions) > 10:
            print(f"    ... and {len(functions) - 10} more")
        print("\n  To set limits, specify function name:")
        print(f"    --service lambda --resource FUNCTION_NAME --daily-spend {daily_spend}")
    else:
        print("  No functions found")


def set_service_limit(service: str, daily_spend: float, resource_name: str | None = None) -> None:
    service = service.lower()

    if service in ("redshift", "redshift-serverless"):
        print(f"\nSetting Redshift Serverless limit for ${daily_spend:.2f}/day...")
        workgroups = get_redshift_workgroups()
        if workgroups:
            for wg in workgroups:
                if resource_name is None or wg["workgroupName"] == resource_name:
                    set_redshift_limit(wg["workgroupArn"], wg["workgroupName"], daily_spend)
        else:
            print("  No workgroups found")

    elif service == "athena":
        _set_athena_limits(daily_spend, resource_name)

    elif service in ("emr", "emr-serverless"):
        print(f"\nSetting EMR Serverless limit for ${daily_spend:.2f}/day...")
        emr_apps = get_emr_serverless_applications()
        if emr_apps:
            for app in emr_apps:
                if resource_name is None or app["name"] == resource_name:
                    set_emr_serverless_limit(app["id"], app["name"], daily_spend)
        else:
            print("  No applications found")

    elif service == "lambda":
        _set_lambda_limits(daily_spend, resource_name)

    else:
        print(f"Unknown service: {service}")
        print("Supported services: redshift, athena, emr-serverless, lambda")
        sys.exit(1)


def remove_service_limit(service: str, resource_name: str | None = None) -> None:
    service = service.lower()

    if service in ("redshift", "redshift-serverless"):
        print("\nRemoving Redshift Serverless limits...")
        workgroups = get_redshift_workgroups()
        for wg in workgroups:
            if resource_name is None or wg["workgroupName"] == resource_name:
                remove_redshift_limit(wg["workgroupArn"])

    elif service == "athena":
        print("\nRemoving Athena limits...")
        athena_workgroups = get_athena_workgroups()
        for wg in athena_workgroups:
            if wg.get("State") == "ENABLED":
                if resource_name is None or wg["Name"] == resource_name:
                    remove_athena_limit(wg["Name"])

    elif service in ("emr", "emr-serverless"):
        print("\nRemoving EMR Serverless limits...")
        emr_apps = get_emr_serverless_applications()
        for app in emr_apps:
            if resource_name is None or app["name"] == resource_name:
                remove_emr_serverless_limit(app["id"], app["name"])

    elif service == "lambda":
        print("\nRemoving Lambda concurrency limits...")
        if resource_name:
            remove_lambda_concurrency(resource_name)
        else:
            functions = get_lambda_functions()
            for fn in functions:
                if get_lambda_concurrency(fn["FunctionName"]) is not None:
                    remove_lambda_concurrency(fn["FunctionName"])

    else:
        print(f"Unknown service: {service}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Manage AWS service cost limits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                              # Show all current limits
  %(prog)s --all                               # Set $5/day limits on all services
  %(prog)s --all --daily-spend 10              # Set $10/day limits on all services
  %(prog)s --service redshift --daily-spend 3  # Set $3/day limit for Redshift
  %(prog)s --service lambda --resource myFunc  # Set limit for specific Lambda
  %(prog)s --service athena --remove           # Remove Athena limits

Budget Actions (strongest protection):
  %(prog)s --budget --monthly-limit 50                    # Create $50/month budget
  %(prog)s --budget --monthly-limit 50 --email you@x.com  # With email alerts
  %(prog)s --budget --action deny-policy                  # Add IAM deny action
  %(prog)s --budget --action stop-ec2                     # Add EC2 stop action
  %(prog)s --budget --remove                              # Remove budget

Supported services:
  redshift      Redshift Serverless (RPU-hour limits)
  athena        Athena (per-query byte scan limits)
  emr-serverless EMR Serverless (max capacity)
  lambda        Lambda (reserved concurrency)

Cost calculations:
  Redshift Serverless: $0.36/RPU-hour
  Athena: $5.00/TB scanned
  EMR Serverless: $0.052/vCPU-hour + $0.006/GB-hour
  Lambda: $0.0000167/GB-second
        """,
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List current limits for all services",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Set limits for all supported services",
    )
    parser.add_argument(
        "--service",
        "-s",
        type=str,
        help="Service to configure (redshift, athena, emr-serverless, lambda)",
    )
    parser.add_argument(
        "--resource",
        type=str,
        help="Specific resource name (workgroup, application, function)",
    )
    parser.add_argument(
        "--daily-spend",
        "-d",
        type=float,
        default=DEFAULT_DAILY_SPEND,
        help=f"Target daily spend in USD (default: ${DEFAULT_DAILY_SPEND:.2f})",
    )
    parser.add_argument(
        "--remove",
        "-r",
        action="store_true",
        help="Remove limits instead of setting them",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format (for --list)",
    )

    parser.add_argument(
        "--budget",
        "-b",
        action="store_true",
        help="Create/manage AWS Budget with actions",
    )
    parser.add_argument(
        "--budget-name",
        type=str,
        default="CostControlBudget",
        help="Name for the budget (default: CostControlBudget)",
    )
    parser.add_argument(
        "--monthly-limit",
        "-m",
        type=float,
        default=DEFAULT_MONTHLY_BUDGET,
        help=f"Monthly budget limit in USD (default: ${DEFAULT_MONTHLY_BUDGET:.2f})",
    )
    parser.add_argument(
        "--email",
        type=str,
        help="Email address for budget alerts",
    )
    parser.add_argument(
        "--action",
        type=str,
        choices=["deny-policy", "stop-ec2"],
        help="Budget action to create (deny-policy: block expensive services, stop-ec2: stop instances)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=100,
        help="Budget threshold percentage for action (default: 100)",
    )

    args = parser.parse_args()

    if not any([args.list, args.all, args.service, args.budget]):
        parser.print_help()
        sys.exit(1)

    if args.remove and not (args.service or args.budget):
        print("Error: --remove requires --service or --budget")
        sys.exit(1)

    try:
        if args.list:
            limits = list_current_limits(quiet=args.json)
            if args.json:
                print(
                    json.dumps(
                        [
                            {
                                "service": lim.service,
                                "resource_name": lim.resource_name,
                                "limit_type": lim.limit_type,
                                "current_value": lim.current_value,
                                "unit": lim.unit,
                                "daily_cost_estimate": lim.daily_cost_estimate,
                                "status": lim.status,
                            }
                            for lim in limits
                        ],
                        indent=2,
                    )
                )

        elif args.budget:
            if args.remove:
                delete_budget(args.budget_name)
            else:
                print(f"\n--- AWS Budget: {args.budget_name} ---")
                create_cost_control_budget(
                    monthly_limit=args.monthly_limit,
                    budget_name=args.budget_name,
                    email=args.email,
                )

                if args.action == "deny-policy":
                    print("\n--- Creating IAM Deny Policy Action ---")
                    create_budget_action_iam_policy(
                        budget_name=args.budget_name,
                        threshold=args.threshold,
                    )
                elif args.action == "stop-ec2":
                    print("\n--- Creating EC2 Stop Action ---")
                    create_budget_action_stop_ec2(
                        budget_name=args.budget_name,
                        threshold=args.threshold,
                    )

                print("\n" + "=" * 50)
                print("Budget configured. Run with --list to verify.")

        elif args.all:
            set_all_limits(args.daily_spend)

        elif args.service:
            if args.remove:
                remove_service_limit(args.service, args.resource)
            else:
                set_service_limit(args.service, args.daily_spend, args.resource)

    except ClientError as e:
        print(f"AWS Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
