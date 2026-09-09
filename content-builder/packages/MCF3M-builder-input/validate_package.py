"""Validate copied source text and import identities without network access."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent

def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))

def main():
    course = load('data/course.json')
    pack = load('data/lessons.json')
    lessons = pack['lessons']
    curriculum = load('data/curriculum.json')
    specific = curriculum['specific_expectations']
    overall = curriculum['overall_expectations']
    raw = (ROOT / 'sources/drive-specific-expectations.md').read_text(encoding='utf-8-sig')
    inventory = load('sources/drive-inventory.json')
    drive_folders = {f['id']: f for m in inventory['modules'] for f in m['children']['results']}
    drive_folders.update({f['id']: f for f in inventory['m1async']['results']})
    assert len(lessons) == pack['lesson_count'] == 39
    assert len({x['lesson_id'] for x in lessons}) == 39
    assert len({x['drive_folder_id'] for x in lessons}) == 39
    assert len(specific) == 53 and len(overall) == 9
    expected_codes = {f'{g}.{i}' for g,n in [('A1',8),('A2',11),('A3',3),('B1',6),('B2',3),('B3',7),('C1',5),('C2',7),('C3',3)] for i in range(1,n+1)}
    assert {e['code'] for e in specific} == expected_codes
    overall_codes = {e['code'] for e in overall}
    assert overall_codes == {s+str(n) for s in 'ABC' for n in range(1,4)}
    for e in specific:
        match = re.search(r'^## '+re.escape(e['code'])+r'\s*$([\s\S]*?)(?=^## |\Z)', raw, re.M)
        assert match, e['code']
        block = match.group(1).strip()
        assert e['source_block'] == block, e['code']
        parts = block.split('Sample problem:',1)
        assert e['text'] == parts[0].strip(), e['code']
        assert e['sample_problem'] == (parts[1].strip() if len(parts)>1 else None), e['code']
    for strand in 'ABC':
        raw_overall = (ROOT / f'sources/overall-{strand}-extracted.txt').read_text()
        starts = list(re.finditer(r'^\s*([123])\. ',raw_overall,re.M))
        for i,m in enumerate(starts):
            text = raw_overall[m.end():starts[i+1].start() if i+1<len(starts) else len(raw_overall)]
            e = next(e for e in overall if e['code'] == strand+m.group(1))
            assert e['text'] == ' '.join(text.split()),e['code']
    for l in lessons:
        f = drive_folders[l['drive_folder_id']]
        assert l['drive_title'] == l['title'] == f['title']
        assert l['drive_parent_folder_id'] in f['parent_ids']
        assert l['drive_folder_url'] == f['url']
        assert l['curriculum_mapping_status'] == 'proposed_review_required'
        assert set(l['proposed_specific_expectation_codes']) <= expected_codes
        assert set(l['proposed_overall_expectation_codes']) <= overall_codes
        assert set(l['proposed_overall_expectation_codes']) == {c.split('.')[0] for c in l['proposed_specific_expectation_codes']}
    counts = [sum(l['module_number']==m for l in lessons) for m in range(1,9)]
    assert counts == [7,6,5,5,5,3,3,5],counts
    assert [m['lesson_count'] for m in course['modules']] == counts
    changes = load('data/reconciliation.json')['title_differences']
    assert {d['lesson_id'] for d in changes} == {l['lesson_id'] for l in lessons if l['title'] != l['original_title']}
    assert len(changes)==3
    processes=load('data/curriculum-processes.json')['expectations']
    assert len(processes)==7 and len({p['name'] for p in processes})==7
    process_raw=(ROOT/'sources/processes-extracted.txt').read_text()
    process_text=' '.join('\n'.join(line[25:] for line in process_raw.splitlines()).split())
    for p in processes: assert p['text'] in process_text,p['name']
    assert load('data/resources.json')['approved_resources']==[]
    for file in ROOT.rglob('*.json'):
        json.loads(file.read_text())
    hashes_file=ROOT/'SHA256SUMS.json'
    if hashes_file.exists():
        for name,digest in load('SHA256SUMS.json').items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    return {'status':'passed','course':'MCF3M','lessons':39,'modules':8,'module_counts':counts,'specific_expectations':53,'overall_expectations':9,'mathematical_process_expectations':7,'unique_drive_folder_ids':39,'title_differences':3,'missing_folder_ids':0,'invalid_curriculum_references':0,'mapping_status':'proposed_review_required','curriculum_text_check':'Exact equality with saved source extraction, not a new full Ministry PDF glyph audit','network_calls':0,'drive_mutations':0}

if __name__=='__main__':
    print(json.dumps(main(),indent=2))
