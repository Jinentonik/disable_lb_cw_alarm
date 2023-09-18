import json
import boto3
cw_client = boto3.client('cloudwatch')

def get_target_instance_id(target_group_arn):
    elbv2_client = boto3.client('elbv2')
    target_group_response = elbv2_client.describe_target_health(TargetGroupArn = target_group_arn)
    instance_id_list = []
    for target in target_group_response.get('TargetHealthDescriptions',[]):
        instance_id_list.append(target.get('Target',{}).get('Id'))
    return instance_id_list
    
def get_cloud_watch_alarm(region,account_id):
    cw_response_obj = cw_client.describe_alarms()
    cw_response_list = cw_response_obj.get('MetricAlarms',[])
    cw_alarm_dict_list = []
        
    lb_cw_alarm_list = [alarm for alarm in cw_response_list if "AWS - Load Balancer Status" in alarm.get('AlarmName','')]
    for lb_cw_alarm in lb_cw_alarm_list:
        for dimension in lb_cw_alarm.get('Dimensions',[]):
            if dimension.get('Name') == 'TargetGroup':
                target_value = dimension.get('Value')
                instance_id_list = get_target_instance_id(f'arn:aws:elasticloadbalancing:{region}:{account_id}:{target_value}')
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
        old_img = ddb.get("OldImage")
        
        new_img_ec2 = dict(filter(lambda item: item[0].startswith("i-"), new_img.items()))
        old_img_ec2 = dict(filter(lambda item: item[0].startswith("i-"), old_img.items()))

        for instance_id, state in new_img_ec2.items():
            if state == old_img_ec2.get(instance_id):
                continue
            change_state_ec2.update({
                instance_id: {
                    "new_state": state.get('S'),
                    "old_state": old_img_ec2.get(instance_id).get('S')
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
    records = event.get('Records')
    change_state_ec2, region, account_id = get_ec2_change_state_status_dict(records)
    cw_alarm_dict_list = get_cloud_watch_alarm(region, account_id)
    enable_cw_alarm_list, disable_cw_alarm_list = enable_or_disable_cw_alarm_list(change_state_ec2, cw_alarm_dict_list)
    cw_client.disable_alarm_actions(AlarmNames=disable_cw_alarm_list)
    cw_client.enable_alarm_actions(AlarmNames=enable_cw_alarm_list)
    
