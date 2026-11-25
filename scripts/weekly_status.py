"""Lab13: factual GitHub weekly status, with optional local Ollama text drafting.
Run from repository root: python scripts/weekly_status.py --repo OWNER/REPO
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = 'https://api.github.com'


def get_json(session, path, params=None):
    result = session.get(f'{API}{path}', params=params, timeout=45)
    result.raise_for_status()
    return result.json()


def get_pages(session, path, params=None):
    params = dict(params or {})
    for page in range(1, 11):
        params.update({'per_page': 100, 'page': page})
        records = get_json(session, path, params)
        if not isinstance(records, list):
            raise ValueError('Expected a list from GitHub API')
        yield from records
        if len(records) < 100:
            return
    raise RuntimeError('More than 1000 results. Narrow the period.')


def between(iso, start, end):
    if not iso:
        return False
    date = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    return start <= date < end


def facts_for_period(session, repo, start, end):
    # GitHub issues endpoint also returns pull requests: remove them explicitly.
    issues = list(get_pages(session, f'/repos/{repo}/issues', {'state': 'all', 'since': start.isoformat()}))
    issues = [item for item in issues if 'pull_request' not in item]
    opened = [item for item in issues if between(item.get('created_at'), start, end)]
    closed = [item for item in issues if between(item.get('closed_at'), start, end)]
    # Open tasks as of the report cutoff, not only currently open tasks.
    unresolved_at_cutoff = [item for item in issues if
        datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')) < end and
        (not item.get('closed_at') or datetime.fromisoformat(item['closed_at'].replace('Z', '+00:00')) >= end)]
    # Workflow run success != deployment; include successes as CI only.
    runs = []
    # Workflow runs nested response: use pagination explicitly.
    for page in range(1, 11):
        chunk = get_json(session, f'/repos/{repo}/actions/runs', {'per_page': 100, 'page': page, 'created': f'{start.date()}..{(end - timedelta(days=1)).date()}'})['workflow_runs']
        runs.extend(chunk)
        if len(chunk) < 100:
            break
    # Avoid treating failed CI as production failures.
    return {
        'repository': repo,
        'period_start_utc': start.date().isoformat(),
        'period_end_inclusive_utc': (end - timedelta(days=1)).date().isoformat(),
        'opened_issues': len(opened),
        'closed_issues': len(closed),
        'remaining_open_at_cutoff': len(unresolved_at_cutoff),
        'opened_issue_numbers': [x['number'] for x in opened],
        'closed_issue_numbers': [x['number'] for x in closed],
        'ci_runs': len(runs),
        'successful_ci_runs': sum(x.get('conclusion') == 'success' for x in runs),
        'failed_ci_runs': sum(x.get('conclusion') == 'failure' for x in runs),
        'deployments': None,  # Requires checking deploy JOB status, not CI run count.
        'blocked_issues': None,  # Cannot infer without an agreed blocked label.
        'milestone_status': None,
    }


def factual_draft(f):
    return (f"## Атқарылды\n{f['closed_issues']} мәселе жабылды (Issues: {f['closed_issue_numbers']}). "
            f"{f['successful_ci_runs']} CI тексеруі сәтті аяқталды.\n\n"
            f"## Орындалуда\n{f['opened_issues']} жаңа мәселе ашылды; есептік кезең соңында "
            f"{f['remaining_open_at_cutoff']} мәселе ашық болды.\n\n"
            f"## Мәселелер\n{f['failed_ci_runs']} CI тексеруі сәтсіз аяқталды. "
            "Себебі және пайдаланушыға ықпалы тек журналды тексерген соң анықталады.\n\n"
            "## Шешім қажет\nБұғатталған міндеттер мен релиз нәтижелері жөнінде дерек жоқ: "
            "қосымша тексеру қажет.\n")



def ollama_draft(f, model):
    prompt = f"""
Сен IT жобасының менеджеріне көмектесетін
қазақ тіліндегі ЖИ-ассистентсің.

Төмендегі нақты деректер бойынша қысқа,
түсінікті апталық статус-есеп дайында.

НАҚТЫ ДЕРЕКТЕР:
- Жоба: {f['repository']}
- Кезең: {f['period_start_utc']} — {f['period_end_inclusive_utc']}
- Ашылған тапсырмалар: {f['opened_issues']}
- Жабылған тапсырмалар: {f['closed_issues']}
- Ашық қалған тапсырмалар: {f['remaining_open_at_cutoff']}
- Жабылған тапсырма нөмірлері: {f['closed_issue_numbers']}
- CI іске қосылуы: {f['ci_runs']}
- Сәтті CI: {f['successful_ci_runs']}
- Сәтсіз CI: {f['failed_ci_runs']}

ҚАТАҢ ЕРЕЖЕЛЕР:
1. Тек берілген деректерді пайдалан.
2. Ешқандай сан, күн немесе себеп ойдан қоспа.
3. Бір санды қайта-қайта қайталама.
4. Тапсырма нөмірлерін жеке сандық тізім ретінде қайталама.
5. CI мен deployment ұғымдарын шатастырма.
6. CI саны 0 болса, тексеру іске қосылмаған деп жаз.
7. Белгісіз ақпаратты "Дерек жоқ" деп көрсет.
8. Тек қазақша жаз.
9. Есеп 150 сөзден аспасын.
10. Артық түсіндірме немесе қорытынды қоспа.

МІНДЕТТІ ФОРМАТ:

## Атқарылды
Жабылған тапсырмалар туралы 1-2 сөйлем.

## Орындалуда
Ашық қалған тапсырмалар туралы 1-2 сөйлем.

## Мәселелер
Нақты белгілі мәселелер туралы жаз.
Дерек жоқ болса, солай көрсет.

## Шешім қажет
Нақты қажет шешімді көрсет.
Дерек жоқ болса, солай көрсет.

Тек осы төрт бөлімді шығар.
"""

    response = requests.post(
        "http://127.0.0.1:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 300,
            },
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"].strip() + "\n"



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default=os.getenv('SOURCE_REPO', 'margulanjanpeis-wq/DormDesk'))
    parser.add_argument('--weeks', type=int, default=2)
    parser.add_argument('--end', help='Exclusive UTC YYYY-MM-DD. Defaults to next Monday after today.')
    parser.add_argument('--ollama-model', default='', help='Optional model running locally, e.g. qwen2.5:1.5b')
    args = parser.parse_args()
    if not 1 <= args.weeks <= 8:
        parser.error('--weeks must be 1..8')
    today = datetime.now(timezone.utc).date()
    default_end = today - timedelta(days=today.weekday())  # most recent completed Sunday
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end else datetime.combine(default_end, datetime.min.time(), tzinfo=timezone.utc)
    if '/' not in args.repo:
        parser.error('--repo must be OWNER/REPO')
    session = requests.Session()
    session.headers.update({'Accept': 'application/vnd.github+json', 'User-Agent': 'lab13-weekly-status'})
    if os.getenv('GITHUB_TOKEN'):
        session.headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    directory = Path('reports')
    directory.mkdir(exist_ok=True)
    for i in range(args.weeks, 0, -1):
        week_end = end - timedelta(days=7 * (i - 1))
        week_start = week_end - timedelta(days=7)
        facts = facts_for_period(session, args.repo, week_start, week_end)
        tag = f'{week_start.date()}_{(week_end - timedelta(days=1)).date()}'
        (directory / f'facts-{tag}.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
        model_used = False
        if args.ollama_model:
            try:
                draft = ollama_draft(facts, args.ollama_model)
                model_used = True
            except (requests.RequestException, KeyError, ValueError) as exc:
                print('Ollama inaccessible; creating factual draft instead:', exc)
                draft = factual_draft(facts)
        else:
            draft = factual_draft(facts)
        disclosure = ('ЖИ арқылы жасалған мәтін; барлық деректі адам тексеруі тиіс.'
                      if model_used else 'Скрипт жасаған фактілік мәтін; ЖИ генерациясы қолданылмады.')
        output = (f'# DRAFT — {args.repo}: {week_start.date()} – {(week_end - timedelta(days=1)).date()}\n\n'
                  + draft + '\n---\n' + disclosure + '\nТексеруші: __________ | Тексеру күні: __________\n')
        path = directory / f'status-{tag}.md'
        path.write_text(output, encoding='utf-8')
        print('Generated:', path)
    print('IMPORTANT: Manual verification is required before sharing reports.')


if __name__ == '__main__':
    main()
