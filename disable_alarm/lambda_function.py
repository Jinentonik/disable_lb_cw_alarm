import boto3
import os
import logging
cw_client = boto3.client('cloudwatch')
IS_INFO_LOG_ENABLED = os.environ.get('IS_INFO_LOG_ENABLED')

if IS_INFO_LOG_ENABLED == "True":
    logging.getLogger().setLevel(logging.INFO)

def find_instance_id_from_ip(ec2_client, target_id):
    ec2_response = ec2_client.describe_instances(
        Filters=[
            {
                "Name": "network-interface.addresses.private-ip-address",
                "Values": [target_id]
            }
        ]
    )
    instance_id = "N/A"
    logging.info(f"ec2 response {ec2_response}")
    for item in ec2_response.get('Reservations',[]):
        for instance in item.get('Instances',[]):
            instance_id = instance.get('InstanceId')
    while 'NextToken' in ec2_response:
        ec2_response = ec2_client.describe_instances(
            Filters=[
                {
                    "Name": "network-interface.addresses.private-ip-address",
                    "Values": [target_id]
                }
            ],
            NextToken = ec2_response.get('NextToken')
        )   
        for item in ec2_response.get('Reservations'):
            for instance in item.get('Instances',[]):
                instance_id = instance.get('InstanceId')
    logging.info(f"instanceID: {instance_id}")
    return instance_id

def get_target_instance_id(elbv2_client, ec2_client, target_group_arn):
    target_group_response = elbv2_client.describe_target_health(TargetGroupArn = target_group_arn)
    instance_id_list = []
    logging.info(f"target group response: {target_group_response}")
    for target in target_group_response.get('TargetHealthDescriptions',[]):
        target_id = target.get('Target',{}).get('Id')
        instance_id = target_id
        if not target_id.startswith('i-'):
            instance_id = find_instance_id_from_ip(ec2_client, target_id)
        instance_id_list.append(instance_id)
    return instance_id_list
    
def get_cloud_watch_alarm(region,account_id):
    elbv2_client = boto3.client('elbv2')
    ec2_client = boto3.client('ec2')
    cw_response_obj = cw_client.describe_alarms()
    cw_response_list = cw_response_obj.get('MetricAlarms',[])
    cw_alarm_dict_list = []

    while 'NextToken' in cw_response_obj:
        cw_response_obj = cw_client.describe_alarms({
            'NextToken': cw_response_obj.get('NextToken','N/A')
        })
        cw_response_list += cw_response_obj.get('MetricAlarms',[])
    logging.info(f"All Cloudwatch alarm list: {cw_response_list}")
    lb_cw_alarm_list = [alarm for alarm in cw_response_list if "Load Balancer Status" in alarm.get('AlarmName','')]
    logging.info(f"LoadBalancer Cloudwatch alarm: {lb_cw_alarm_list}")
    for lb_cw_alarm in lb_cw_alarm_list:
        for dimension in lb_cw_alarm.get('Dimensions',[]):
            if dimension.get('Name') == 'TargetGroup':
                target_value = dimension.get('Value')
                instance_id_list = get_target_instance_id(elbv2_client, ec2_client, f'arn:aws:elasticloadbalancing:{region}:{account_id}:{target_value}')
                cw_alarm_dict_list.append({
                    "cw_alarm_name": lb_cw_alarm.get('AlarmName'),
                    "target_instance_id_list": instance_id_list
                })
    return cw_alarm_dict_list

def get_ec2_change_state_status_dict(records):
    change_state_ec2 = {}
    region = ''
    account_id = ''
    for record in records:
        ddb = record.get('dynamodb',{})
        account_region_list = (ddb.get('Keys',{}).get('account-region',{}).get('S')).split(':')
        account_id = account_region_list[0]
        region = account_region_list[1]
        
        new_img = ddb.get("NewImage")
        # old_img = ddb.get("OldImage")
        
        new_img_ec2 = dict(filter(lambda item: item[0].startswith("i-"), new_img.items()))
        # old_img_ec2 = dict(filter(lambda item: item[0].startswith("i-"), old_img.items()))

        for instance_id, state in new_img_ec2.items():
            change_state_ec2.update({
                instance_id: {
                    "new_state": state.get('S'),
                    # "old_state": old_img_ec2.get(instance_id).get('S')
                }
            })
    return change_state_ec2, region, account_id

def enable_or_disable_cw_alarm_list(change_state_ec2, cw_alarm_dict_list):
    disable_cw_alarm_list = []
    enable_cw_alarm_list = []
    for instance_id in change_state_ec2.keys():
        for alarm in cw_alarm_dict_list:
            if instance_id in alarm.get('target_instance_id_list'):
                if change_state_ec2.get(instance_id, {}).get('new_state') == "stopped":
                    disable_cw_alarm_list.append(alarm.get('cw_alarm_name'))
                else:
                    enable_cw_alarm_list.append(alarm.get('cw_alarm_name'))
    return enable_cw_alarm_list, disable_cw_alarm_list

def lambda_handler(event, context):
    # TODO implement
    try:
        logging.info(event)
        records = event.get('Records')

        change_state_ec2, region, account_id = get_ec2_change_state_status_dict(records)
        logging.info('change state ec2:')
        logging.info(change_state_ec2)

        cw_alarm_dict_list = get_cloud_watch_alarm(region, account_id)
        logging.info('cw alarm dict list')
        logging.info(cw_alarm_dict_list)

        enable_cw_alarm_list, disable_cw_alarm_list = enable_or_disable_cw_alarm_list(change_state_ec2, cw_alarm_dict_list)
        logging.info('enable cw alarm list: ')
        logging.info(enable_cw_alarm_list)
        logging.info('disable cw alarm list: ')
        logging.info(disable_cw_alarm_list)
        
        cw_client.disable_alarm_actions(AlarmNames=disable_cw_alarm_list)
        cw_client.enable_alarm_actions(AlarmNames=enable_cw_alarm_list)
    except Exception as ex:
        logging.warning(str(ex))
        
