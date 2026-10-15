"""Shared status rules and a restricted, read-only roster representation."""


def person_status(person, day):
    if not person.get('enabled', True):
        return 'paused'
    if person.get('last_status') in ('queued', 'running'):
        return 'checking'
    if (person.get('last_status') in ('failed', 'interrupted') and
            (person.get('last_finished_at') or '') >= (person.get('checked_at') or '')):
        return 'failed'
    if person['checked_day'] == day:
        if person['completion'] == 1:
            return 'completed'
        if person['completion'] == 0:
            return 'pending'
    return 'unknown'


def build_roster(people, day, generated_at, source='live'):
    counts = dict(total=len(people), completed=0, pending=0, failed=0,
                  unknown=0, checking=0, paused=0)
    entries = []
    for person in people:
        status = person_status(person, day)
        counts[status] += 1
        checked_at = person.get('checked_at') if person['checked_day'] == day else None
        if status == 'failed':
            checked_at = person.get('last_finished_at')
        entries.append(dict(name=' '.join((person['employee_name'] or person['employee_id']).split()),
                            team=person.get('team', ''), status=status, checked_at=checked_at))
    return dict(date=day, timezone='Asia/Shanghai', generated_at=generated_at,
                source=source, counts=counts, people=entries)
