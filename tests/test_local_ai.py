import io
import json
from decimal import Decimal
import pytest
from app import ai
from app.config import Settings
from app.freshdirect.base import Product
from app.profile import DietaryProfile, ProfileSignals


def response(monkeypatch, content, **overrides):
    seen=[]
    class Opener:
        def open(self, request, timeout):
            seen.append((json.loads(request.data), timeout, request.full_url))
            return io.BytesIO(json.dumps({'done':True,'done_reason':'stop','message':{'content':content},**overrides}).encode())
    monkeypatch.setattr(ai.urllib.request,'build_opener',lambda *a:Opener())
    return seen


def test_local_request_uses_schema_and_explicit_bounds(monkeypatch):
    seen=response(monkeypatch,json.dumps({'sku':'milk','confidence':.8,'reason':'exact'}))
    result=ai.rank_products('milk',[Product(sku='milk',name='milk')],settings=Settings())
    assert result['sku']=='milk'
    body,timeout,url=seen[0]
    assert body['format']==ai.ProductChoice.model_json_schema()
    assert body['options']['num_ctx']==16384 and body['options']['num_predict']==2048
    assert body['stream'] is False and body['think'] is False
    assert url=='http://127.0.0.1:11434/api/chat' and timeout==90


@pytest.mark.parametrize('content',['not json','{}','{"sku":"x","confidence":2}',
    '{"sku":"x","confidence":"0.8"}','{"sku":"x","confidence":NaN}',
    '{"sku":"x","confidence":0.8,"command":"buy"}'])
def test_invalid_local_output_falls_back(monkeypatch,content):
    response(monkeypatch,content)
    assert ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings()) is None


def test_truncated_response_is_not_used(monkeypatch):
    response(monkeypatch,'{"sku":"x","confidence":0.8}',done_reason='length')
    assert ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings()) is None


def test_timeout_never_uses_cloud(monkeypatch):
    class Opener:
        def open(self,*a,**k):raise TimeoutError()
    monkeypatch.setattr(ai.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(ai,'_client',lambda *a:pytest.fail('cloud fallback'))
    assert ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings(anthropic_api_key='present')) is None


def test_profile_cannot_invent_restrictions(monkeypatch):
    response(monkeypatch,json.dumps({'diet_style':'vegan','organic_preference':'high',
        'plant_forward':True,'likely_avoids':['gluten','nuts'],'favored_proteins':['chicken','tofu'],
        'household_notes':['allergic to nuts'],'notes':['medical advice']}))
    draft=DietaryProfile(favored_proteins=['chicken'])
    result=ai.synthesize_profile(ProfileSignals(10,5),draft,Settings())
    assert result.diet_style==draft.diet_style
    assert result.likely_avoids==[] and result.household_notes==[] and result.notes==[]
    assert result.favored_proteins==['chicken'] and result.source=='heuristic'


def test_disabled_and_no_ai_never_call_local_model(monkeypatch,tmp_path):
    from app.match import resolve
    monkeypatch.setattr(ai,'_ask_json',lambda *a:pytest.fail('AI called'))
    assert not ai.is_configured(Settings(ai_provider='disabled'))
    assert ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings(ai_provider='disabled')) is None
    result=resolve('milk',[Product(sku='x',name='milk',price=Decimal('3'))],use_ai=False,settings=Settings(data_dir=tmp_path))
    assert result.method=='heuristic'


def test_failed_model_is_visible_in_request_line(monkeypatch,tmp_path):
    from app.planner import _line_for_request
    from app.models import RequestRow
    monkeypatch.setattr(ai,'rank_products',lambda *a,**k:None)
    line=_line_for_request(RequestRow(text='milk',source='manual'),[Product(sku='x',name='milk',price=Decimal('3'))],Settings(data_dir=tmp_path))
    assert 'AI unavailable' in line.reason


def test_oversize_input_does_not_silently_truncate(monkeypatch):
    monkeypatch.setattr(ai.urllib.request,'build_opener',lambda *a:pytest.fail('oversize request sent'))
    assert ai.rank_products('x'*20000,[Product(sku='x',name='milk')],settings=Settings()) is None


def test_transport_failure_only_waits_once_per_planning_run(monkeypatch):
    calls=[]
    class Opener:
        def open(self,*a,**k):
            calls.append(1)
            raise TimeoutError()
    monkeypatch.setattr(ai.urllib.request,'build_opener',lambda *a:Opener())
    def rank():return ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings())
    with ai.planning_session():
        assert rank() is None
        assert rank() is None
    assert len(calls)==1
    with ai.planning_session():assert rank() is None
    assert len(calls)==2


def test_invalid_choice_does_not_disable_other_plan_requests(monkeypatch):
    seen=response(monkeypatch,'{}')
    with ai.planning_session():
        for _ in range(2):
            assert ai.rank_products('milk',[Product(sku='x',name='milk')],settings=Settings()) is None
    assert len(seen)==2
