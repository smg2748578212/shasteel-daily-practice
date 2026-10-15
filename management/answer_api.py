"""Use the same question and whole-paper requests as the portal frontend."""
from datetime import datetime, timezone
import time

import config
from exceptions import LoginError, NetworkError, NoQuestionsTodayError, SubmissionError


def answer_records(questions):
    if not isinstance(questions,list):
        raise ValueError('Unexpected question response')
    if not questions:
        raise NoQuestionsTodayError('No questions today')
    records,seen = [],set()
    for question in questions:
        if not isinstance(question,dict):
            raise ValueError('Unexpected question structure')
        identifier,answer = question.get('id'),question.get('answer')
        options = question.get('options')
        if not isinstance(identifier,str) or not identifier or identifier in seen:
            raise ValueError('Missing or duplicate question ID')
        if not isinstance(options,list) or not isinstance(answer,str) or not answer:
            raise ValueError('Question does not provide a usable answer')
        if answer not in [option.get('option') for option in options if isinstance(option,dict)]:
            raise ValueError('Answer does not match question options')
        seen.add(identifier)
        records.append({'questionId':identifier,'answer':answer})
    return records


def complete_practice(client, progress):
    started = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    progress('answering')
    paper = client.authorized_post(config.GENERATE_API_URL)
    if paper.get('code') != 200:
        raise ValueError('Portal did not provide a question list')
    records = answer_records(paper.get('data'))
    total = len(records)
    progress('answering',total,total)
    payload = {'answerRecords':records,'startTime':started}
    try:
        submitted = client.authorized_post(config.SUBMIT_API_URL,payload)
    except (LoginError,NetworkError,ValueError):
        # The server may have accepted a request whose response was lost.
        # Check completion, and never repeat the submission automatically.
        try:
            if client.completed():
                return True
        except (LoginError,NetworkError,ValueError):
            pass
        raise SubmissionError('提交结果暂未确认，请先检查进度后再重试') from None
    progress('verifying',total,total)
    for attempt in range(3):
        if client.completed():
            return True
        if submitted.get('code') != 200:
            raise SubmissionError('门户未接受本次提交，请检查执行记录')
        if attempt<2:
            time.sleep(1)
    return False
