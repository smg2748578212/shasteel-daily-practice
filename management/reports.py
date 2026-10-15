"""Render one daily summary without listing completed people's names."""
from management.roster import person_status


def format_daily_report(people, day, roster_url=None):
    completed = 0
    pending, failed, unknown = [], [], []
    for person in people:
        name = ' '.join((person['employee_name'] or person['employee_id']).split())
        status = person_status(person, day)
        if status == 'failed':
            failed.append(name)
        elif status == 'completed':
            completed += 1
        elif status == 'pending':
            pending.append(name)
        else:
            unknown.append(name)
    lines = ['查询计划：北京时间 16:00', f'统计人数：{len(people)} 人', f'已完成：{completed} 人']
    for label, names in [('未完成',pending),('查询失败',failed),('尚未查询',unknown)]:
        lines.append(f'{label}：{len(names)} 人' + ('（' + '、'.join(names) + '）' if names else ''))
    if roster_url:
        lines.extend(['', '查看全部名单：' + roster_url])
    return {'title':f'每日一练进度汇总 · {day}','content':'\n'.join(lines)}
