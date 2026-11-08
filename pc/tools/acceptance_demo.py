#!/usr/bin/env python3
"""PC acceptance launcher: reuse production replay, then build an offline viewer.

No training, no downloads, no new algorithm/safety rules. Default = historical
CSV playback. --infer = invoke the EXISTING production evaluator in the existing
Python environment. Neither mode is live navigation or actual VIO.
"""
from __future__ import annotations
import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import webbrowser

DIRECTIONS = {'STRAIGHT', 'LEFT', 'RIGHT'}
FIELDS = ('session', 'frame', 'surface_state', 'occupancy_state', 'safety_state',
          'effective_command', 'authorized_motion', 'observation_hint', 'reason',
          'depth_source', 'pose_source', 'relative_time_ms', 'danger_any', 'confirmed_clear')
ARCHIVE = 'docs/phase_reports/e3_surface_6813160'


def yes(value):
    return str(value).strip().lower() in ('1', 'true', 'yes')


def read_frames(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        required = {'condition', 'arm', 'session', 'frame', 'safety_state', 'authorized_motion'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('CSV missing required fields: ' + ', '.join(sorted(required - set(reader.fieldnames or []))))
        rows, seen = [], set()
        for source in reader:
            if source['arm'] != 'mobilio' or source['condition'] not in ('production', 'D_v5_dav2'):
                continue
            r = {k: source.get(k, '') for k in FIELDS}
            r['frame'] = int(r['frame'])
            key = (r['session'], r['frame'])
            if key in seen:
                raise ValueError('Duplicate production frame; do not mix experiments: ' + str(key))
            if yes(r['danger_any']) and yes(r['confirmed_clear']):
                raise ValueError('Overlapping proxy labels: ' + str(key))
            seen.add(key)
            rows.append(r)
    if not rows:
        raise ValueError('No production/Mobilio rows. Do not substitute synthetic data or another arm.')
    return rows


def summarize(rows):
    auth = lambda r: r['authorized_motion'] in DIRECTIONS
    clear = [r for r in rows if yes(r['confirmed_clear'])]
    danger = [r for r in rows if yes(r['danger_any'])]
    return {'frames': len(rows), 'authorized': sum(auth(r) for r in rows),
            'unknown': sum(r['safety_state'] == 'UNKNOWN' for r in rows),
            'clear_proxy': len(clear), 'authorized_on_clear_proxy': sum(auth(r) for r in clear),
            'danger_proxy': len(danger), 'authorized_on_danger_proxy': sum(auth(r) for r in danger)}


def safe_json(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False)
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def examples(root, rows, allow_archive):
    """Exact frame matching only; historical images never decorate a new inference."""
    if not allow_archive:
        return {}
    folder = root / ARCHIVE / 'visualizations'
    found = {}
    # Iterate trusted local filenames; CSV text cannot escape this directory.
    for image in sorted(folder.glob('*_privacy.png')):
        for i, row in enumerate(rows):
            if image.name == f"{row['session']}_{row['frame']}_privacy.png":
                found[str(i)] = 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')
                break
    return found


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BlindNav · PC 验收演示</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;background:#101a26;color:#e8eff5}*{box-sizing:border-box}body{margin:0}main{max-width:1320px;margin:auto;padding:26px}header{display:flex;justify-content:space-between;gap:20px;align-items:center}h1{margin:0;font-size:29px}h2{font-size:18px}p{line-height:1.65}.sub,small{color:#adbdcd}.badge{background:#29415a;padding:9px 14px;border-radius:8px;font-weight:700}.notice{background:#3c3021;color:#ffe2a6;padding:13px 16px;border-left:4px solid #edbd63;margin:20px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,.panel{background:#192939;border:1px solid #324658;border-radius:10px;padding:18px}.card b{display:block;font-size:27px;margin-top:8px}.layout{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,1fr);gap:16px;margin-top:18px}.panel{min-width:0}button,select{background:#28465c;color:#fff;border:1px solid #658198;border-radius:6px;padding:10px;cursor:pointer;font:inherit}button:hover{background:#375e78}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #f6c968}input[type=range]{width:100%;margin:20px 0}.controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.image{min-height:260px;display:flex;align-items:center;justify-content:center;background:#0d1925;border-radius:7px;margin-top:16px}img,video{max-width:100%;height:auto}.image p{padding:30px;color:#a8b9ca}.state{font-size:34px;color:#93d5e3;margin:10px 0}.grid{display:grid;grid-template-columns:110px 1fr;gap:12px;margin-top:20px}.grid div{overflow-wrap:anywhere}.muted{color:#adbdcd}#frameTitle{overflow-wrap:anywhere}pre{white-space:pre-wrap;word-break:break-word;max-height:310px;overflow:auto;font-size:12px}summary{cursor:pointer;padding:12px 0}.sample{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}footer{margin-top:24px;color:#adbdcd;line-height:1.6}.source{overflow-wrap:anywhere;font-size:12px}@media(max-width:850px){.layout{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}header{align-items:flex-start;flex-direction:column}main{padding:16px}}
</style></head><body><main>
<header><div><h1>BlindNav · PC 验收演示</h1><p class="sub">v5 分割 → DAV2 + 地面标定 → Surface / Occupancy → 冻结决策</p></div><span class="badge" id="mode"></span></header>
<div class="notice"><b>研究演示，不指导实际行走。</b> 使用数据集记录位姿，不是实时 VIO；方向是算法历史／离线输出，不构成通行许可。UNKNOWN 不等于已识别危险。</div>
<section class="stats"><div class="card">本次显示帧数<b id="n"></b></div><div class="card">有方向授权的记录<b id="auth"></b></div><div class="card">UNKNOWN 记录<b id="unk"></b></div><div class="card">运行性质<b style="font-size:19px" id="type"></b></div></section>
<div class="layout"><section class="panel"><h2>逐帧查看</h2><div class="controls"><select id="session" aria-label="选择场景"></select><button id="prev">上一帧</button><button id="play">播放</button><button id="next">下一帧</button><button id="speak">朗读状态说明</button></div><input id="seek" type="range" min="0" value="0" aria-label="帧位置"><div id="frameTitle"></div><small>播放间隔为演示设置，不代表原视频帧率或模型推理速度。</small><div class="image" id="imageBox"></div><div id="samples" class="sample"></div><details><summary>运行来源与可追溯信息</summary><pre id="provenance"></pre></details></section>
<aside class="panel"><h2>模型链路输出（只展示，不执行）</h2><div id="state" class="state"></div><div class="grid"><div class="muted">地面地图</div><div id="surface"></div><div class="muted">障碍地图</div><div id="occupancy"></div><div class="muted">最终指令字段</div><div id="command"></div><div class="muted">方向授权字段</div><div id="motion"></div><div class="muted">观察提示字段</div><div id="hint"></div><div class="muted">原因</div><div id="reason"></div><div class="muted">深度来源</div><div id="depth"></div><div class="muted">标注（仅评分）</div><div id="truth"></div></div><details><summary>原始帧记录</summary><pre id="row"></pre></details></aside></div>
<section class="panel" style="margin-top:18px"><h2>完整性与限制</h2><p id="metrics"></p><div id="movie"></div><p>所有符合条件的记录都显示，包括失败和 UNKNOWN。只有准确对应当前帧的归档图才显示；缺图不会用邻帧或生成图替代。新增推理不配旧版诊断图。</p><p>语音是浏览器对当前状态的展示性朗读，不是原系统 TTS 的验收。对四例归档图片的像素化处理不保证完全匿名。</p></section>
<footer>当前交付验收的是“能启动、能处理／回放、能查看输出、能导出证据”，不是认证助行设备或无监督导航。图像及派生证据来源为原仓库 SANPO 实验，保留其原始署名和使用限制。文件仅在本机打开，不连接云端服务。</footer>
</main><script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';
const D=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id);let ids=[],pos=0,timer=null;
const labels={UNKNOWN:'信息不足 / UNKNOWN',OBSERVE:'需要观察 / OBSERVE',BLOCKED:'阻断 / BLOCKED',CLEAR:'算法标记 CLEAR（未作安全保证）'};
const text=(id,v)=>$(id).textContent=(v===undefined||v===null||v==='')?'未提供':String(v);
text('mode',D.mode_label);text('type',D.mode_label);text('n',D.summary.frames);text('auth',D.summary.authorized+'/'+D.summary.frames);text('unk',D.summary.unknown+'/'+D.summary.frames);text('provenance',JSON.stringify(D.provenance,null,2));
const ratio=(n,d)=>d?n+'/'+d:'无可评标注';text('metrics','已标为可通行的代理帧：方向授权 '+ratio(D.summary.authorized_on_clear_proxy,D.summary.clear_proxy)+'；危险代理帧：方向授权 '+ratio(D.summary.authorized_on_danger_proxy,D.summary.danger_proxy)+'。这些不是方向正确率或现实事故率。');
const select=$('session');let op=document.createElement('option');op.value='';op.textContent='全部场景';select.append(op);for(const s of [...new Set(D.rows.map(r=>r.session))]){op=document.createElement('option');op.value=s;op.textContent=s;select.append(op)}
function pause(){if(timer!==null)clearInterval(timer);timer=null;text('play','播放')}
function draw(){const index=ids[pos],r=D.rows[index];$('seek').value=pos;text('frameTitle',`${pos+1} / ${ids.length} · ${r.session} · 帧 ${r.frame}`);text('state',labels[r.safety_state]||r.safety_state);text('surface',r.surface_state);text('occupancy',r.occupancy_state);text('command',r.effective_command);text('motion',r.authorized_motion||'无');text('hint',r.observation_hint||'无');text('reason',r.reason);text('depth',r.depth_source);text('row',JSON.stringify(r,null,2));text('truth',String(r.danger_any).toLowerCase()==='true'?'危险代理标签':String(r.confirmed_clear).toLowerCase()==='true'?'可通行代理标签':'其他／未提供');const box=$('imageBox');box.replaceChildren();if(D.images[index]){const im=document.createElement('img');im.alt='该帧历史实验诊断图（像素化）';im.src=D.images[index];box.append(im)}else{const p=document.createElement('p');p.textContent='这一帧没有随结果归档的诊断图。此处只展示真实记录；有本次输出视频时可在下方播放。';box.append(p)}}
function reset(){pause();ids=D.rows.map((r,i)=>i).filter(i=>!select.value||D.rows[i].session===select.value);pos=0;$('seek').max=ids.length-1;draw()}
$('prev').onclick=()=>{pause();pos=Math.max(0,pos-1);draw()};$('next').onclick=()=>{pause();pos=Math.min(ids.length-1,pos+1);draw()};$('seek').oninput=()=>{pause();pos=Number($('seek').value);draw()};select.onchange=reset;
$('play').onclick=()=>{if(timer!==null){pause();return}if(pos===ids.length-1)pos=0;text('play','暂停');timer=setInterval(()=>{draw();if(pos===ids.length-1)pause();else pos++},600)};
$('speak').onclick=()=>{if(!('speechSynthesis' in window)){text('speak','此浏览器无朗读支持');return}speechSynthesis.cancel();const u=new SpeechSynthesisUtterance('离线研究演示。当前状态：'+(labels[D.rows[ids[pos]].safety_state]||'未知')+'。请勿根据此演示实际行走。');u.lang='zh-CN';speechSynthesis.speak(u)};
for(const k of Object.keys(D.images)){const b=document.createElement('button');b.textContent='诊断图：帧 '+D.rows[k].frame;b.onclick=()=>{select.value='';reset();pos=Number(k);draw()};$('samples').append(b)}
if(D.video){const p=document.createElement('p');p.textContent='本次离线推理输出视频（不是实时摄像头；不与上方滑块自动同步）。编码不受浏览器支持时使用本机播放器打开。';const v=document.createElement('video');v.controls=true;v.preload='metadata';v.src=D.video;$('movie').append(p,v)}reset();
</script></body></html>'''


def build(root, csv_path, out, mode, video=None):
    rows = read_frames(csv_path)
    meta = {'source_csv_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            'generated_utc': datetime.now(timezone.utc).isoformat(),
            'mode': mode, 'pose': 'recorded dataset pose; NOT actual VIO',
            'no_safety_or_realtime_claim': True}
    try:
        meta['code_commit'] = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        meta['code_commit'] = 'UNAVAILABLE'
    labels = {'ARCHIVE_REPLAY': '历史结果回放 · 不运行模型',
              'NEW_OFFLINE_INFERENCE': '本次离线模型推理 · 结果回放',
              'IMPORTED_RESULTS': '导入结果回放 · 来源待核验'}
    payload = {'mode_label': labels[mode], 'provenance': meta, 'summary': summarize(rows),
               'rows': rows, 'images': examples(root, rows, mode == 'ARCHIVE_REPLAY'),
               'video': video}
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'index.html'
    if target.exists():
        raise ValueError('Refusing to overwrite index.html')
    target.write_text(HTML.replace('__PAYLOAD__', safe_json(payload)), encoding='utf-8')
    (out / 'demo_manifest.json').write_text(safe_json({'provenance': meta, 'summary': payload['summary']}), encoding='utf-8')
    return target


def inference_command(root, data, checkpoint, out, device):
    # No new algorithms and no 2x2/trace replay. Existing production entry only.
    return [sys.executable, str(root / 'pc/tools/mobilio_sanpo_ab.py'),
            '--data-root', str(data), '--v5-checkpoint', str(checkpoint),
            '--conditions', 'production', '--device', device, '--max-width', '640',
            '--seed', '0', '--save-failures', '4', '--out', str(out),
            '--video-out', str(out / 'production_replay.mp4')]


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--infer', action='store_true', help='actually run existing production models, then make the viewer')
    p.add_argument('--data-root', type=Path, default=os.environ.get('SANPO_REAL'))
    p.add_argument('--checkpoint', type=Path, default=os.environ.get('V5_CHECKPOINT'))
    p.add_argument('--device', choices=['cpu', 'mps', 'cuda'], default='cpu')
    p.add_argument('--csv', type=Path, help='import a production result CSV; explicitly labelled as imported')
    p.add_argument('--out', type=Path)
    p.add_argument('--open', action='store_true', dest='open_browser')
    a = p.parse_args(argv)
    if a.infer and a.csv:
        p.error('--infer and --csv are mutually exclusive')
    out = (a.out or root / 'pc/out' / ('acceptance_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
    if out.exists():
        p.error('Choose a new output directory; nothing will be overwritten')
    mode, video = 'ARCHIVE_REPLAY', None
    if a.infer:
        if not a.data_root or not a.data_root.is_dir() or not a.checkpoint or not a.checkpoint.is_file():
            p.error('Use the existing E3 data and v5 checkpoint (--data-root/--checkpoint or SANPO_REAL/V5_CHECKPOINT). No download fallback.')
        cmd = inference_command(root, a.data_root.resolve(), a.checkpoint.resolve(), out, a.device)
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONNOUSERSITE='1')
        env['PYTHONPATH'] = os.pathsep.join([str(root / 'pc'), str(root / 'pc/tools')])
        out.mkdir(parents=True, exist_ok=False)
        print('Running existing production pipeline; no new models. Local logs may contain personal paths.', flush=True)
        with (out / 'inference.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            try:
                for line in process.stdout:
                    print(line, end='', flush=True)
                    log.write(line)
                    log.flush()
                code = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        if code:
            print(f'INFERENCE_FAILED exit={code}; preserved {out / "inference.log"}. No automatic replay pretending to be inference.', file=sys.stderr)
            return code if code > 0 else 1
        csv_path = out / 'per_frame.csv'
        video_path = out / 'production_replay.mp4'
        video = video_path.name if video_path.is_file() and video_path.stat().st_size > 0 else None
        mode = 'NEW_OFFLINE_INFERENCE'
    elif a.csv:
        csv_path, mode = a.csv.resolve(), 'IMPORTED_RESULTS'
    else:
        csv_path = root / ARCHIVE / 'on_per_frame.csv.gz'
    if not csv_path.is_file():
        raise FileNotFoundError(f'Required result file missing: {csv_path}. No generated substitute.')
    page = build(root, csv_path, out, mode, video)
    print(f'VIEWER_READY mode={mode}\n{page}\nThis is not autonomous navigation. Open index.html locally.')
    if a.open_browser:
        webbrowser.open(page.as_uri())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error) as exc:
        print(f'FAILED: {exc}', file=sys.stderr)
        raise SystemExit(2)
