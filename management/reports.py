"""Render one daily summary without listing completed people's names."""


def format_daily_report(people, day):
    completed = 0
    pending, failed, unknown = [], [], []
    for person in people:
        name = ' '.join((person['employee_name'] or person['employee_id']).split())
        checked_today = person['checked_day'] == day
        newest_failed = (person.get('last_status') in ('failed','interrupted') and
                         (person.get('last_finished_at') or '') >= (person['checked_at'] or ''))
        if newest_failed:
            failed.append(name)
        elif checked_today and person['completion'] == 1:
            completed += 1
        elif checked_today and person['completion'] == 0:
            pending.append(name)
        else:
            unknown.append(name)
    lines = ['查询计划：北京时间 16:00', f'统计人数：{len(people)} 人', f'已完成：{completed} 人']
    for label, names in [('未完成',pending),('查询失败',failed),('尚未查询',unknown)]:
        lines.append(f'{label}：{len(names)} 人' + ('（' + '、'.join(names) + '）' if names else ''))
    return {'title':f'每日一练进度汇总 · {day}','content':'\n'.join(lines)}
