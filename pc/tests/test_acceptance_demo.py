"""Launcher/viewer contracts only. Fixtures are SYNTHETIC; no model inference."""
import csv, gzip, importlib.util, json
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/'tools/acceptance_demo.py'
spec=importlib.util.spec_from_file_location('acceptance_demo',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(path, duplicate=False):
    rows=[dict(condition='production',arm='mobilio',session='TEST_SYNTHETIC',frame=i,
               safety_state=s,authorized_motion=('STRAIGHT' if s=='CLEAR' else ''),
               effective_command=s, danger_any='False',confirmed_clear='True',
               surface_state=s,occupancy_state=s,observation_hint='',reason='fixture_not_real',depth_source='synthetic')
          for i,s in enumerate(['CLEAR','UNKNOWN','OBSERVE'])]
    if duplicate: rows.append(rows[0].copy())
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    return rows


def test_gzip_and_counters_do_not_turn_unknown_into_directions(tmp_path):
    p=tmp_path/'a.csv.gz';fixture(p);rows=m.read_frames(p);s=m.summarize(rows)
    assert s['frames']==3 and s['authorized']==1 and s['unknown']==1
    assert s['clear_proxy']==3 and s['authorized_on_clear_proxy']==1


def test_duplicate_fails_not_double_counted(tmp_path):
    p=tmp_path/'a.csv';fixture(p,True)
    with pytest.raises(ValueError,match='Duplicate'): m.read_frames(p)


def test_viewer_does_not_execute_csv_html_or_claim_live(tmp_path):
    p=tmp_path/'a.csv';fixture(p)
    page=m.build(tmp_path,p,tmp_path/'view','IMPORTED_RESULTS')
    html=page.read_text()
    assert '导入结果回放' in html and '不是实时 VIO' in html
    assert '</script><script>' not in m.safe_json({'reason':'</script><script>alert(1)</script>'})
    with pytest.raises(ValueError,match='overwrite'):
        m.build(tmp_path,p,tmp_path/'view','IMPORTED_RESULTS')


def test_production_command_reuses_existing_runner_and_no_ablation(tmp_path):
    c=m.inference_command(tmp_path,tmp_path/'data',tmp_path/'weights',tmp_path/'out','cpu')
    assert c[c.index('--conditions')+1]=='production'
    assert '--e3-frozen' not in c and '--ray-trace-out' not in c
    assert 'controlled_2x2_replay.py' not in ' '.join(c)


def test_inference_requires_real_data_not_automatic_archive(tmp_path):
    with pytest.raises(SystemExit) as e:
        m.main(['--infer','--data-root',str(tmp_path/'missing'),'--checkpoint',str(tmp_path/'missing.pth')])
    assert e.value.code==2


def test_new_inference_never_uses_old_images(tmp_path):
    assert m.examples(tmp_path,[{'session':'s','frame':0}],False)=={}
