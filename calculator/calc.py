#!/usr/bin/env python3
"""Small deterministic calculator for the decision costbook.

Design goal: fail loudly when the economic meaning of an input is ambiguous.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
ANCHORS_FILE = DATA / 'cost_anchors.csv'
SOURCES_FILE = DATA / 'sources.csv'
SCENARIOS_FILE = DATA / 'scenarios.yaml'
DOC_FILE = ROOT / 'COSTBOOK.md'
D = Decimal

REQUIRED_EVIDENCE = {'CURRENT_PRIMARY','DATED_PRIMARY','CURRENT_SECONDARY','HISTORICAL_SECONDARY','DERIVED','SCENARIO_ASSUMPTION','QUOTE_REQUIRED'}
REQUIRED_STATUS = {'INCLUDED','CONDITIONAL'}
EXPECTED_UNITS = {'A18':'per_60min_session','A19':'per_GB_month'}
ROLE_ANCHOR = {'BME':'A21','EE':'A22','Software':'A23','PM':'A24'}
MONEY_UNITS = {'USD': {'per_headset','per_board','per_system','per_kit','per_unit','per_10_pack','per_30_pack','per_container','per_hour','per_initial_review','per_60min_session','per_GB_month','per_small_parcel','per_hour','per_10_pack','per_program','per_510k_preparation','per_510k_program','per_small_US_study','per_small_US_trial','per_enrolled_subject_hour'}, 'EUR': {'per_headband'}}


def dec(v: object) -> Decimal:
    try:
        return D(str(v))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f'not a number: {v!r}') from e


def load_csv(path: Path, key: str) -> dict[str, dict[str,str]]:
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not reader.fieldnames or key not in reader.fieldnames:
        raise ValueError(f'{path.name}: missing key column {key}')
    out={}
    for r in rows:
        if not r.get(key): raise ValueError(f'{path.name}: blank {key}')
        if r[key] in out: raise ValueError(f'{path.name}: duplicate {key} {r[key]}')
        out[r[key]]=r
    return out


def load():
    anchors=load_csv(ANCHORS_FILE,'anchor_id')
    sources=load_csv(SOURCES_FILE,'source_id')
    scenarios=yaml.safe_load(SCENARIOS_FILE.read_text(encoding='utf-8'))
    if not isinstance(scenarios,dict) or 'scenarios' not in scenarios:
        raise ValueError('scenarios.yaml missing scenarios')
    return anchors,sources,scenarios


def anchor_range(a):
    lo,base,hi=(dec(a[k]) for k in ('low','base','high'))
    if min(lo,base,hi) < 0: raise ValueError(f"negative anchor {a['anchor_id']}")
    if not lo <= base <= hi: raise ValueError(f"range order failure {a['anchor_id']}")
    return lo,base,hi


def validate_sources(anchors,sources):
    for aid,a in anchors.items():
        sid=a.get('source_id')
        if sid not in sources: raise ValueError(f'orphan source {aid}:{sid}')
        if not a.get('price_date'): raise ValueError(f'{aid}: missing price/effective date')
        if not a.get('next_action'): raise ValueError(f'{aid}: missing next action')
        if a.get('status') not in REQUIRED_STATUS: raise ValueError(f'{aid}: invalid status {a.get("status")}')
        if a.get('evidence_class') not in REQUIRED_EVIDENCE: raise ValueError(f'{aid}: invalid evidence class')
        src=sources[sid]
        if a['evidence_class'] in {'CURRENT_PRIMARY','DATED_PRIMARY','CURRENT_SECONDARY','HISTORICAL_SECONDARY'} and a['evidence_class'] != src['evidence_quality']:
            raise ValueError(f'{aid}: anchor evidence class contradicts source evidence quality')
        if aid in EXPECTED_UNITS and a['unit'] != EXPECTED_UNITS[aid]: raise ValueError(f'{aid}: semantic unit mismatch')
        if a['evidence_class'] in {'DATED_PRIMARY','HISTORICAL_SECONDARY','CURRENT_SECONDARY','SCENARIO_ASSUMPTION','DERIVED'} and (not a.get('replace_with') or not a.get('next_action')):
            raise ValueError(f'{aid}: weak/derived item missing replacement path')
        anchor_range(a)
        if a['currency'] in MONEY_UNITS and a['unit'] not in MONEY_UNITS[a['currency']]:
            raise ValueError(f'{aid}: unsupported unit {a["unit"]} for {a["currency"]}')
        if a['currency']=='x' and a['unit']!='multiplier': raise ValueError(f'{aid}: burden unit mismatch')
    for sid,s in sources.items():
        if not s.get('url'):
            raise ValueError(f'{sid}: missing URL')


def check_qty(q):
    q=dec(q)
    if q <= 0: raise ValueError(f'invalid quantity {q}')
    return q


def cash_build(items,anchors,allow_conditional=False):
    out={'low':D(0),'base':D(0),'high':D(0),'lines':[]}
    seen=[]
    for item in items:
        aid=item['anchor']
        if aid not in anchors: raise ValueError(f'unknown anchor {aid}')
        a=anchors[aid]
        if a['currency']!='USD': raise ValueError(f'{aid}: non-USD anchor leaked into USD scenario')
        q=check_qty(item.get('qty'))
        if a['cost_type']=='CONDITIONAL' and not allow_conditional: raise ValueError(f'{aid}: conditional anchor placed in base cash')
        lo,base,hi=anchor_range(a)
        vals=[q*lo,q*base,q*hi]
        out['low']+=vals[0]; out['base']+=vals[1]; out['high']+=vals[2]
        out['lines'].append({'anchor':aid,'qty':q,'low':vals[0],'base':vals[1],'high':vals[2]})
        seen.append(aid)
    if len(seen)!=len(set(seen)):
        # Duplicates can be intentional quantities but must be represented as one line with qty instead of two lines.
        raise ValueError(f'duplicate anchor lines in one cash block: {seen}')
    return out


def labor_build(items,anchors,factor):
    total=D(0); lines=[]
    for item in items:
        role=item['role']; hours=dec(item['hours'])
        if hours < 0: raise ValueError('negative labor hours')
        if hours == 0: raise ValueError(f'{role}: zero hours must be omitted or explicitly scoped out')
        aid=ROLE_ANCHOR.get(role)
        if aid != item.get('anchor'): raise ValueError(f'{role}: role anchor mismatch; expected {aid}')
        a=anchors[aid]
        if a['unit']!='per_hour' or a['cost_type']!='INTERNAL_LABOR': raise ValueError(f'{aid}: invalid labor anchor')
        raw=dec(a['base']); rate=raw*factor; value=hours*rate; total+=value
        lines.append({'role':role,'hours':hours,'raw_rate':raw,'loaded_rate':rate,'value':value})
    return total,lines


def storage_cost(sc,anchors):
    p=sc['inputs']; completed=dec(p['completed_subjects']); sessions=dec(p['sessions_per_completed_subject'])
    hours=dec(p['recording_hours_per_subject']); channels=dec(p['channels']); sr=dec(p['sample_rate_hz']); bps=dec(p['bytes_per_sample']); mult=dec(p['storage_multiplier']); months=dec(p['retention_months'])
    gb=completed*sessions*hours*channels*sr*bps*D(3600)/D(1073741824)*mult
    price=dec(anchors[sc['base']['storage_anchor']]['base'])
    cost=gb*months*price
    return gb,cost


def compute(anchors,scenarios, name, factor=D('1.4287')):
    s=scenarios['scenarios'][name]
    if name=='garage':
        result={'scenario':name,'envelope':{}}
        for label,case in s['envelope'].items():
            cash=cash_build(case['cash_items'],anchors); labor,lt=labor_build(case['labor'],anchors,factor)
            result['envelope'][label]={'cash':cash,'labor':labor,'economic':cash['base']+labor,'labor_lines':lt}
        return result
    if name=='university_eeg':
        result={'scenario':name}
        for label in ('buy','access'):
            case=s[label]; cash=cash_build(case['cash_items'],anchors); labor,lt=labor_build(case['labor'],anchors,factor)
            result[label]={'cash':cash,'labor':labor,'economic':cash['base']+labor,'labor_lines':lt}
        return result
    if name=='tes_eeg':
        result={'scenario':name}
        for label in ('base','downside'):
            case=s[label]; cash=cash_build(case['cash_items'],anchors); labor,lt=labor_build(case['labor'],anchors,factor)
            result[label]={'cash':cash,'labor':labor,'economic':cash['base']+labor,'labor_lines':lt}
        return result
    if name=='pilot_20':
        case=s['base']; cash=cash_build(case['cash_items'],anchors); labor,lt=labor_build(case['labor'],anchors,factor)
        gb,storage=storage_cost(s,anchors)
        cash['low']+=storage; cash['base']+=storage; cash['high']+=storage
        return {'scenario':name,'cash':cash,'labor':labor,'economic':cash['base']+labor,'labor_lines':lt,'storage_gb':gb,'storage_cost':storage,'conditional':cash_build(s['conditional_items'],anchors,allow_conditional=True)}
    if name=='510k':
        result={'scenario':name}
        for label in ('small_business','standard_fee'):
            case=s[label]; cash=cash_build(case['cash_items'],anchors); labor,lt=labor_build(case['labor'],anchors,factor)
            result[label]={'cash':cash,'labor':labor,'economic':cash['base']+labor,'labor_lines':lt}
        result['conditional']=cash_build(s['conditional_items'],anchors,allow_conditional=True)
        return result
    if name=='implantable_boundary':
        a=anchors[s['reference_anchor']]; return {'scenario':name,'reference':anchor_range(a)}
    raise KeyError(name)


def fmt(x):
    return f'${dec(x).quantize(D("1"),rounding=ROUND_HALF_UP):,.0f}'


def assert_doc_snapshot(outputs):
    text=DOC_FILE.read_text(encoding='utf-8')
    marker='<!-- CALC_SNAPSHOT\n'
    if marker not in text or '\nCALC_SNAPSHOT -->' not in text: raise ValueError('COSTBOOK.md missing calculation snapshot')
    block=text.split(marker,1)[1].split('\nCALC_SNAPSHOT -->',1)[0]
    expected={}
    for line in block.splitlines():
        if not line.strip(): continue
        k,v=line.split('=',1); expected[k]=D(v)
    checks={
        'garage.min_cash':outputs['garage']['envelope']['minimum']['cash']['base'],
        'garage.base_cash':outputs['garage']['envelope']['base']['cash']['base'],
        'garage.downside_cash':outputs['garage']['envelope']['downside']['cash']['base'],
        'university_eeg.access_cash':outputs['university_eeg']['access']['cash']['base'],
        'university_eeg.buy_low_cash':outputs['university_eeg']['buy']['cash']['low'],
        'university_eeg.buy_high_cash':outputs['university_eeg']['buy']['cash']['high'],
        'tes_eeg.base_cash':outputs['tes_eeg']['base']['cash']['base'],
        'tes_eeg.downside_cash':outputs['tes_eeg']['downside']['cash']['base'],
        'pilot.cash':outputs['pilot_20']['cash']['base'],
        'pilot.labor':outputs['pilot_20']['labor'],
        '510k.small_business_cash':outputs['510k']['small_business']['cash']['base'],
        '510k.standard_cash':outputs['510k']['standard_fee']['cash']['base'],
    }
    for k,v in checks.items():
        if k not in expected: raise ValueError(f'document snapshot missing {k}')
        if expected[k] != v: raise ValueError(f'document mismatch {k}: {expected[k]} != {v}')


def semantic_self_tests(anchors,scenarios):
    # Missing source.
    bad=deepcopy(anchors); del bad['A01']
    try: cash_build([{'anchor':'A01','qty':1}],bad); raise AssertionError('missing anchor test did not fail')
    except ValueError: pass
    # Negative quantity.
    try: cash_build([{'anchor':'A02','qty':-1}],anchors); raise AssertionError('negative qty test did not fail')
    except ValueError: pass
    # EUR into USD scenario.
    try: cash_build([{'anchor':'A09','qty':1}],anchors); raise AssertionError('currency leak test did not fail')
    except ValueError: pass
    # Wrong unit for labor.
    bad=deepcopy(anchors); bad['A21']['unit']='per_session'
    try: labor_build([{'role':'BME','anchor':'A21','hours':10}],bad,D('1.4287')); raise AssertionError('labor unit test did not fail')
    except ValueError: pass
    # Conditional branch leakage.
    try: cash_build([{'anchor':'A29','qty':1}],anchors); raise AssertionError('conditional leak test did not fail')
    except ValueError: pass
    # Duplicate anchor lines.
    try: cash_build([{'anchor':'A02','qty':1},{'anchor':'A02','qty':1}],anchors); raise AssertionError('duplicate-line test did not fail')
    except ValueError: pass
    # Range order.
    bad=deepcopy(anchors); bad['A02']['base']='999'; bad['A02']['high']='500'
    try: anchor_range(bad['A02']); raise AssertionError('range test did not fail')
    except ValueError: pass
    # Historical/current confusion must be rejected by source-quality consistency.
    bad=deepcopy(anchors); bad['A14']['evidence_class']='CURRENT_PRIMARY'
    try: validate_sources(bad, load_csv(SOURCES_FILE,'source_id')); raise AssertionError('historical/current test did not fail')
    except ValueError: pass
    # Participant unit confusion must be rejected.
    bad=deepcopy(anchors); bad['A18']['unit']='per_subject'
    try: validate_sources(bad, load_csv(SOURCES_FILE,'source_id')); raise AssertionError('participant unit test did not fail')
    except ValueError: pass
    # Participant/session mismatch.
    badsc=deepcopy(scenarios); badsc['scenarios']['pilot_20']['base']['cash_items'][1]['qty']=25
    try:
        validate_scenario_semantics(anchors,badsc); raise AssertionError('participant/session mismatch test did not fail')
    except ValueError: pass
    # Labor arithmetic exactness.
    total,_=labor_build([{'role':'BME','anchor':'A21','hours':10}],anchors,D('1.30'))
    if total != D('10')*D('52.58')*D('1.30'): raise AssertionError('labor arithmetic')
    # Storage model sanity: 20 subjects, 32ch, 500Hz, 3 byte/sample, 3x multiplier, 12mo is materially more than 3 GB.
    o=compute(anchors,scenarios,'pilot_20');
    if not (o['storage_gb'] > D('9') and o['storage_gb'] < D('11')): raise AssertionError(f'pilot storage plausibility: {o["storage_gb"]}')



def validate_scenario_semantics(anchors,scenarios):
    pilot=scenarios['scenarios']['pilot_20']
    if anchors['A18']['unit'] != 'per_60min_session': raise ValueError('participant unit mismatch')
    if anchors['A19']['unit'] != 'per_GB_month': raise ValueError('storage unit mismatch')
    if pilot['inputs']['sessions_per_completed_subject'] <= 0: raise ValueError('invalid sessions per subject')
    if pilot['inputs']['enrolled_subjects'] <= pilot['inputs']['completed_subjects']: raise ValueError('pilot must distinguish enrolled from completed subjects')
    pilot_cash=pilot['base']['cash_items']
    participant_lines=[x for x in pilot_cash if x['anchor']=='A18']
    facility_lines=[x for x in pilot_cash if x['anchor']=='A16']
    if len(participant_lines)!=1 or dec(participant_lines[0]['qty']) != dec(pilot['inputs']['completed_subjects']): raise ValueError('pilot participant count mismatch')
    expected_room_hours=dec(pilot['inputs']['completed_subjects'])*dec(pilot['inputs']['room_hours_per_completed_subject'])
    if len(facility_lines)!=1 or dec(facility_lines[0]['qty']) != expected_room_hours: raise ValueError('pilot facility-hour mismatch')


def validate():
    anchors,sources,scenarios=load(); validate_sources(anchors,sources)
    factor=D(str(scenarios['loaded_rate_model']['base_factor']))
    validate_scenario_semantics(anchors,scenarios)
    outputs={name:compute(anchors,scenarios,name,factor) for name in ('garage','university_eeg','tes_eeg','pilot_20','510k','implantable_boundary')}
    semantic_self_tests(anchors,scenarios)
    # University buy-vs-access must be visibly calculable from different paths.
    access=outputs['university_eeg']['access']['cash']['base']
    if access <= 0: raise ValueError('access path cannot be zero')
    # Conditional branch must not be in pilot/510k base totals.
    # Conditional branches are computed separately and must never be folded into base cash.
    assert_doc_snapshot(outputs)
    return outputs


def printable(outputs,name=None):
    selected=outputs if name is None else {name:outputs[name]}
    def clean(x):
        if isinstance(x,Decimal): return str(x)
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items()}
        if isinstance(x,list): return [clean(v) for v in x]
        return x
    print(json.dumps(clean(selected),indent=2))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--validate',action='store_true')
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--scenario')
    args=ap.parse_args()
    outputs=validate()
    if args.self_test:
        print('SELF-TEST PASS (12 adversarial semantic tests)')
    elif args.scenario:
        if args.scenario not in outputs: raise SystemExit(f'unknown scenario: {args.scenario}')
        printable(outputs,args.scenario)
    elif args.validate:
        print('VALIDATION PASS')
        for k in ('garage','university_eeg','tes_eeg','pilot_20','510k'):
            print(k, 'OK')
    else:
        printable(outputs)

if __name__=='__main__': main()
