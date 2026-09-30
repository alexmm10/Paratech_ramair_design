"""Shared, explicit production snapshot policy for all staged runners."""
from pathlib import Path
import json

def capture_policy(case: Path, stage: dict) -> dict:
    if not (stage.get('sampling') or str(stage.get('stage','')).upper()=='E'):
        return {}
    for parent in (case,*case.parents):
        path=parent/'CFD_2D/app_state/production_capture.json'
        if not path.is_file():continue
        settings=json.loads(path.read_text())
        if not settings.get('enabled'):return {}
        count=max(8,min(1000,int(settings.get('frames',120))))
        fraction=max(.01,min(1.,float(settings.get('tail_fraction',.1))))
        duration=float(stage['end_s'])-float(stage['start_s'])
        if duration<=0:return {}
        return {'write_interval_s':duration*fraction/(count-1), 'purge_write':count+3,
                'write_policy':'configured_interval','requested_frames':count,'tail_fraction':fraction,
                'note':'Dense writes throughout production; rolling retention keeps approximately the requested final interval. Actual snapshots depend on time-step/write alignment.'}
    return {}
