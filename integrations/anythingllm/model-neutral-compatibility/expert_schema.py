"""Opt-in schema projection; legacy schema files remain byte-identical."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def provider_schema(name):
    if name not in ('aag-image-task','aag-image-batch'):raise ValueError('Unsupported Expert tool')
    schema=json.loads((ROOT/'schemas'/('provider-task.schema.json' if name=='aag-image-task' else 'provider-batch.schema.json')).read_text())
    plan=json.loads((ROOT/'expert-prompt/schemas/visual-plan.schema.json').read_text())
    item=schema if name=='aag-image-task' else schema['properties']['items']['items']
    item['properties']['prompt']['maxLength']=8000
    item['properties']['expert_plan']=plan
    item['required']=[*item['required'],'expert_plan']
    return schema
