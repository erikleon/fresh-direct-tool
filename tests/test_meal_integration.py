"""Cross-layer meal requirements: matching, persistence, and delivery edits."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from app import inbox, meals, planner, plans
from app.analytics import Prediction
from app.budget import DraftPlan
from app.config import Settings
from app.freshdirect.base import Product


def fake_catalog(monkeypatch, catalog):
    searches = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        @contextmanager
        def session(self):
            yield self

        def search_products(self, query, limit=20):
            searches.append(query)
            return catalog.get(query, [])

    monkeypatch.setattr(planner, 'FreshDirectClient', Client)
    return searches


def meal(title='Onion soup', ingredients=None):
    return {'title': title, 'day': '2026-11-02',
            'ingredients': ingredients or ['3 onion'], 'unmatched_ingredients': []}


def test_existing_staple_still_requires_review_for_recipe_quantity(tmp_path, monkeypatch):
    config = Settings(data_dir=tmp_path, ai_provider='disabled')
    monkeypatch.setattr(inbox, 'list_open', lambda *a: [])
    monkeypatch.setattr(planner, 'replenishment', lambda *a: [Prediction(
        key='onion', name='onion', times_bought=6, mean_interval_days=7,
        last_purchased=date(2026, 10, 20), days_since_last=8,
        predicted_next=date(2026, 10, 27), days_overdue=1, active=True)])
    monkeypatch.setattr(meals, 'suggest_meals', lambda *a: {'meals': [meal()]})
    searches = fake_catalog(monkeypatch, {'onion': [Product(
        sku='onion', product_id='onion', name='onion', price=Decimal('1'))]})
    draft = planner.build_plan(config)
    assert searches == ['onion']
    assert len(draft.lines) == 1
    assert draft.lines[0].needs_review
    assert '3 onion' in draft.lines[0].reason
    assert draft.lines[0].source == 'replenish'
    saved = plans.get_plan(plans.save_draft(draft, config), config)
    assert saved.lines[0].needs_review


def test_shared_ingredient_retains_both_recipe_requirements(tmp_path, monkeypatch):
    config = Settings(data_dir=tmp_path, ai_provider='disabled')
    monkeypatch.setattr(inbox, 'list_open', lambda *a: [])
    monkeypatch.setattr(planner, 'replenishment', lambda *a: [])
    monkeypatch.setattr(meals, 'suggest_meals', lambda *a: {'meals': [
        meal(), meal('Onion tart', ['2 onion'])]})
    searches = fake_catalog(monkeypatch, {'onion': [Product(
        sku='onion', name='onion', price=Decimal('1'))]})
    draft = planner.build_plan(config)
    assert searches == ['onion']
    assert len(draft.lines) == 1
    assert '3 onion' in draft.lines[0].reason
    assert '2 onion' in draft.lines[0].reason
    assert draft.lines[0].needs_review


def test_unmatched_recipe_ingredients_survive_persistence(tmp_path, monkeypatch):
    config = Settings(data_dir=tmp_path, ai_provider='disabled')
    monkeypatch.setattr(inbox, 'list_open', lambda *a: [])
    monkeypatch.setattr(planner, 'replenishment', lambda *a: [])
    monkeypatch.setattr(meals, 'suggest_meals', lambda *a: {'meals': [meal(
        ingredients=['3 onion', '4 cups broth'])]})
    fake_catalog(monkeypatch, {'onion': [Product(
        sku='onion', name='onion', price=Decimal('1'), sold_out=True)]})
    draft = planner.build_plan(config)
    assert not draft.lines
    plan_id = plans.save_draft(draft, config)
    assert plans.get_meal_context(plan_id, config)['meals'][0]['unmatched_ingredients'] == [
        '3 onion', '4 cups broth']


def test_later_delivery_marks_original_meal_schedule_stale(tmp_path):
    config = Settings(data_dir=tmp_path)
    context = {'meals': [meal()], 'forecast': [{'day': '2026-11-02', 'cooking_hint': 'soup'}], 'notes': []}
    plan_id = plans.save_draft(DraftPlan(week_of=date(2026, 11, 1), meal_context=context), config)
    plans.set_delivery(plan_id, delivery_date=date(2026, 11, 15), settings=config)
    revised = plans.get_meal_context(plan_id, config)
    # Stored dates may remain useful as history, but cannot look like a current
    # plan for groceries now arriving after every suggested meal.
    assert not revised.get('meals') or any(
        'delivery' in note.lower() and any(word in note.lower() for word in ('changed', 'stale', 'regenerat', 'review'))
        for note in revised.get('notes', []))


def test_meal_lines_require_explicit_inclusion_and_do_not_spend_budget(tmp_path, monkeypatch):
    from app.budget import PlanLine, reconcile
    config=Settings(data_dir=tmp_path,ai_provider='disabled')
    monkeypatch.setattr(inbox,'list_open',lambda *a:[])
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(meals,'suggest_meals',lambda *a:{'meals':[meal()]})
    fake_catalog(monkeypatch,{'onion':[Product(sku='onion',name='onion',price=Decimal('10')),
                                     Product(sku='cheap',name='onion',price=Decimal('1'))]})
    draft=planner.build_plan(config,budget=2)
    assert len(draft.lines)==1 and not draft.lines[0].included
    assert draft.subtotal_cents==0 and draft.lines[0].swap is None
    draft.lines.append(PlanLine(need='milk',product=Product(sku='milk',name='milk',price=Decimal('3'))))
    assert reconcile(draft)==[]  # Excluded onion is never a budget-saving swap.
    assert draft.subtotal_cents==300
    plan_id=plans.save_draft(draft,config)
    stored=plans.get_plan(plan_id,config)
    onion=next(line for line in stored.lines if line.source=='meal')
    assert not onion.included
    plans.set_quantity(plan_id,onion.id,3,config)
    plans.set_included(plan_id,onion.id,True,config)
    stored=plans.get_plan(plan_id,config)
    onion=next(line for line in stored.lines if line.source=='meal')
    assert onion.included and onion.quantity==3


def test_irrelevant_catalog_result_is_unmatched_not_added(tmp_path,monkeypatch):
    config=Settings(data_dir=tmp_path,ai_provider='disabled')
    monkeypatch.setattr(inbox,'list_open',lambda *a:[])
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(meals,'suggest_meals',lambda *a:{'meals':[meal(ingredients=['1 organic onion'])]})
    fake_catalog(monkeypatch,{'organic onion':[Product(sku='milk',name='Organic Milk',price=Decimal('2'))]})
    draft=planner.build_plan(config)
    assert not draft.lines
    assert draft.meal_context['meals'][0]['unmatched_ingredients']==['1 organic onion']


def test_reused_plan_id_replaces_orphaned_context_even_with_empty_context(tmp_path):
    from app.db import session_scope
    from app.models import PlanRow
    config=Settings(data_dir=tmp_path)
    old=plans.save_draft(DraftPlan(week_of=date.today(),meal_context={'meals':[meal()]}),config)
    # Simulate rollback to the old app: it deletes plans but knows no context table.
    with session_scope(config) as db:
        db.delete(db.get(PlanRow,old));db.commit()
    replacement=plans.save_draft(DraftPlan(week_of=date.today()),config)
    assert replacement==old
    assert plans.get_meal_context(replacement,config)=={}


def test_old_weather_is_hidden_without_modifying_recipe_evidence(tmp_path):
    from datetime import datetime,timedelta,timezone
    from app.db import session_scope
    from app.models import PlanContextRow
    config=Settings(data_dir=tmp_path)
    weather={'day':'2026-11-02','cooking_hint':'grill'}
    context={'generated_at':(datetime.now(timezone.utc)-timedelta(hours=13)).isoformat(),
             'forecast':[weather], 'meals':[{**meal(),'weather':weather,'reason':'Perfect grilling weather'}],
             'notes':['The forecast includes grilling weather, but there is no grill recipe in the house collection. No cooking-method substitution was invented.']}
    plan_id=plans.save_draft(DraftPlan(week_of=date.today(),meal_context=context),config)
    visible=plans.get_meal_context(plan_id,config)
    assert visible['forecast']==[] and visible['meals'][0]['weather'] is None
    assert 'Perfect grilling' not in visible['meals'][0]['reason']
    assert not any('forecast includes grilling' in item for item in visible['notes'])
    assert visible['meals'][0]['ingredients']==['3 onion']
    with session_scope(config) as db:
        assert db.get(PlanContextRow,plan_id).payload==context


def test_current_weather_visible_until_delivery_changes(tmp_path):
    from datetime import datetime,timezone
    config=Settings(data_dir=tmp_path)
    weather={'day':'2026-11-02','cooking_hint':'grill'}
    context={'generated_at':datetime.now(timezone.utc).isoformat(),
             'forecast':[weather], 'meals':[{**meal(),'weather':weather,'reason':'Grill weather'}]}
    plan_id=plans.save_draft(DraftPlan(week_of=date.today(),meal_context=context),config)
    assert plans.get_meal_context(plan_id,config)==context
    plans.set_delivery(plan_id,delivery_date=date(2026,11,15),settings=config)
    visible=plans.get_meal_context(plan_id,config)
    assert visible['forecast']==[] and visible['meals'][0]['weather'] is None


def test_cli_and_digest_distinguish_excluded_meal_candidates(tmp_path,monkeypatch):
    import io
    from rich.console import Console
    from app import cli
    from app.budget import PlanLine
    from app.notify.email import render_digest
    config=Settings(data_dir=tmp_path)
    draft=DraftPlan(week_of=date.today(),lines=[
        PlanLine(need='milk',product=Product(sku='milk',name='Milk',price=Decimal('3'))),
        PlanLine(need='onion',product=Product(sku='onion',name='Onion',price=Decimal('9')),
                 source='meal',included=False,confidence=.4)])
    monkeypatch.setattr(planner,'build_plan',lambda *a,**kw:draft)
    monkeypatch.setattr(cli,'get_settings',lambda:config)
    output=io.StringIO()
    monkeypatch.setattr(cli,'console',Console(file=output,width=120,color_system=None))
    cli.plan(horizon=7,max_items=25,budget=None,headed=False)
    assert 'excluded candidate' in output.getvalue()
    assert 'Subtotal: $3.00' in output.getvalue()
    persisted=plans.get_plan(plans.save_draft(draft,config),config)
    subject,text,html=render_digest(persisted,config)
    assert '1 items' in subject and '$3.00' in text
    assert 'Onion' not in text and 'Onion' not in html


def test_one_transport_failure_per_build_then_next_plan_retries(tmp_path,monkeypatch):
    from app import ai
    from app.models import RequestRow
    config=Settings(data_dir=tmp_path,meal_count=0)
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(inbox,'list_open',lambda *a:[
        RequestRow(id=1,text='milk'),RequestRow(id=2,text='onion')])
    fake_catalog(monkeypatch,{
        'milk':[Product(sku='milk',name='Milk',price=Decimal('3'))],
        'onion':[Product(sku='onion',name='Onion',price=Decimal('1'))]})
    calls=[]
    class Offline:
        def open(self,*args,**kwargs):
            calls.append(1)
            raise TimeoutError('offline')
    monkeypatch.setattr(ai.urllib.request,'build_opener',lambda *a:Offline())
    first=planner.build_plan(config)
    assert len(first.lines)==2 and len(calls)==1
    assert all('AI unavailable' in line.reason for line in first.lines)
    second=planner.build_plan(config)
    assert len(second.lines)==2 and len(calls)==2


def test_missing_timezone_skips_meals_but_keeps_and_saves_requests(tmp_path,monkeypatch):
    from app.models import RequestRow
    config=Settings(data_dir=tmp_path,planning_timezone='Missing/Zone',ai_provider='disabled')
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(inbox,'list_open',lambda *a:[RequestRow(id=123,text='milk')])
    monkeypatch.setattr(meals,'suggest_meals',lambda *a:(_ for _ in ()).throw(AssertionError('meals called')))
    fake_catalog(monkeypatch,{'milk':[Product(sku='milk',name='Milk',price=Decimal('3'))]})
    draft=planner.build_plan(config)
    assert len(draft.lines)==1
    assert 'timezone' in draft.meal_context['notes'][0]
    assert plans.get_plan(plans.save_draft(draft,config),config).delivery_start is not None


def test_disabled_meals_never_resolve_timezone_or_call_suggestions(tmp_path,monkeypatch):
    config=Settings(data_dir=tmp_path,meal_count=0)
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(inbox,'list_open',lambda *a:[])
    def forbidden(*args):
        raise AssertionError('Disabled meals performed optional work')
    monkeypatch.setattr(planner,'ZoneInfo',forbidden)
    monkeypatch.setattr(meals,'suggest_meals',forbidden)
    fake_catalog(monkeypatch,{})
    assert planner.build_plan(config).meal_context=={}


def test_saved_delivery_uses_same_local_date_as_meal_plan(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):
            # Monday UTC, still Sunday at the configured household location.
            return datetime(2026,11,2,1,tzinfo=timezone.utc).astimezone(tz)
    config=Settings(data_dir=tmp_path,planning_timezone='America/New_York')
    monkeypatch.setattr(planner,'datetime',Frozen)
    monkeypatch.setattr(plans,'datetime',Frozen)
    monkeypatch.setattr(planner,'replenishment',lambda *a:[])
    monkeypatch.setattr(inbox,'list_open',lambda *a:[])
    starts=[]
    def suggest(settings,start,*args):
        starts.append(start)
        return {}
    monkeypatch.setattr(meals,'suggest_meals',suggest)
    fake_catalog(monkeypatch,{})
    draft=planner.build_plan(config)
    saved=plans.get_plan(plans.save_draft(draft,config),config)
    assert starts==[date(2026,11,2)]
    assert saved.delivery_start.date()==date(2026,11,1)
